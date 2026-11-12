"""Selling, delivery, and stock validations for the manual glass MVP."""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt

from erpnext.stock.doctype.item.item import get_item_defaults

from glass_factory.glass_factory.glass_stock_entry_types import GLASS_REPACK_STOCK_ENTRY_TYPES
from glass_factory.glass_factory.item_resolver import item_role, resolve_row_items, validate_final_item_matches_row
from glass_factory.glass_factory.selling_uom import delivered_pieces, row_pieces
from glass_factory.glass_factory.settings_validation import get_default_selling_warehouse
from glass_factory.glass_factory.spec_transaction import SPEC_TRANSACTION_ROW_FIELDS, is_spec_transaction_row

NON_COMMERCIAL_ROLES = ("Raw Sheet", "Cut WIP", "Remnant", "Scrap")

GLASS_ROW_FIELDS = (
	"gf_is_glass_item",
	"gf_glass_specification",
	"gf_raw_sheet_item",
	"gf_cut_wip_item",
	"gf_final_item",
	"gf_length_mm",
	"gf_width_mm",
	"gf_thickness_mm",
	"gf_processing_flags",
	"gf_area_m2",
	"gf_source_row_id",
)


def resolve_glass_items(doc, method=None):
	"""Resolve exact final Items on Quotation/Sales Order rows before save."""
	if doc.doctype not in ("Quotation", "Sales Order"):
		return

	if doc.doctype == "Sales Order":
		_sync_glass_fields_from_quotation(doc)

	for row in doc.get("items") or []:
		if is_spec_transaction_row(row):
			_reject_non_commercial_item(row, doc.doctype)
			# A Sales Order made from a Quotation has no delivery warehouse on its rows yet.
			_ensure_glass_row_warehouse(doc, row)
			continue
		if cint(row.get("gf_is_glass_item")):
			if flt(row.qty) <= 0:
				frappe.throw(f"Row {row.idx}: Quantity must be greater than zero.")
			_reject_non_commercial_item(row, doc.doctype)
			validate_no_manufacturing_for_glass(row)
			resolve_row_items(row)
			_ensure_glass_row_warehouse(doc, row)
		else:
			_reject_non_commercial_item(row, doc.doctype)


def validate_glass_selling_document(doc, method=None):
	"""Before submit guard: glass rows must sell exact final Items only."""
	if doc.doctype not in ("Quotation", "Sales Order"):
		return
	for row in doc.get("items") or []:
		if cint(row.get("gf_is_glass_item")):
			_reject_non_commercial_item(row, doc.doctype)
			validate_no_manufacturing_for_glass(row)
		validate_final_item_matches_row(row)


def _delivers_stock(doc) -> bool:
	"""A Delivery Note, or a Sales Invoice that updates stock itself, hands glass to the customer."""
	if doc.doctype == "Delivery Note":
		return True
	return doc.doctype == "Sales Invoice" and bool(cint(doc.get("update_stock")))


def validate_delivery_note(doc, method=None):
	"""Delivery must preserve Sales Order final Item links for glass rows.

	The same limits apply to a Sales Invoice with Update Stock, which delivers glass without a
	Delivery Note. Otherwise it could hand over pieces that were never processed for the order,
	for example another order's stock of the same finished Item.
	"""
	if not _delivers_stock(doc):
		return
	label = doc.doctype
	is_invoice = doc.doctype == "Sales Invoice"

	for row in doc.get("items") or []:
		is_glass = cint(row.get("gf_is_glass_item"))
		if not is_glass and row.get("so_detail"):
			so_flags = frappe.db.get_value(
				"Sales Order Item",
				row.so_detail,
				["gf_is_glass_item", "gf_from_glass_specification"],
				as_dict=True,
			)
			if so_flags:
				is_glass = cint(so_flags.gf_is_glass_item) or cint(so_flags.gf_from_glass_specification)
			if is_glass:
				if not is_invoice:
					row.gf_is_glass_item = 1
					if cint(so_flags.gf_from_glass_specification):
						row.gf_from_glass_specification = 1

		if not is_glass:
			_reject_non_commercial_item(row, label)
			continue

		if not (row.get("against_sales_order") or row.get("sales_order")) or not row.get("so_detail"):
			frappe.throw(f"{label} row {row.idx}: glass delivery must be created from a Sales Order.")

		so_values = frappe.db.get_value(
			"Sales Order Item",
			row.so_detail,
			[
				"item_code",
				"gf_final_item",
				"gf_cutting_job",
				"gf_processing_job",
				"gf_glass_specification",
				"gf_from_glass_specification",
				"gf_processed_qty",
				"gf_technical_summary",
				"delivered_qty",
			],
			as_dict=True,
		)
		if not so_values:
			frappe.throw(f"{label} row {row.idx}: linked Sales Order Item was not found.")

		final_item = so_values.gf_final_item or so_values.item_code
		if row.item_code != final_item:
			frappe.throw(
				f"{label} row {row.idx}: glass delivery Item must be the Sales Order final Item {final_item}."
			)
		if item_role(row.item_code) != "Final":
			frappe.throw(f"{label} row {row.idx}: only final glass Items can be delivered.")

		# Produced and delivered limits are piece counts. The row quantity may be square metres,
		# so compare through stock_qty (pieces) on both sides.
		processed_qty = flt(so_values.gf_processed_qty)
		if processed_qty <= 0:
			frappe.throw(
				f"{label} row {row.idx}: glass must be processed before delivery."
			)

		if cint(doc.get("is_return")):
			continue

		pieces_in_this_note = sum(
			row_pieces(dn_row)
			for dn_row in doc.items
			if dn_row.so_detail == row.so_detail and flt(dn_row.qty) > 0
		)
		already_delivered = delivered_pieces(row.so_detail, exclude_parent=doc.get("name"))
		if flt(already_delivered + pieces_in_this_note, 3) > flt(processed_qty, 3):
			frappe.throw(
				f"{label} row {row.idx}: delivering {flt(pieces_in_this_note, 3)} pieces would "
				f"exceed the processed quantity of {flt(processed_qty, 3)} pieces "
				f"({flt(already_delivered, 3)} already delivered)."
			)

		if is_invoice:
			continue  # Sales Invoice rows carry no glass trace columns; the checks above are what matter
		row.gf_sales_order_item = row.so_detail
		row.gf_cutting_job = row.get("gf_cutting_job") or so_values.gf_cutting_job
		row.gf_processing_job = row.get("gf_processing_job") or so_values.gf_processing_job
		row.gf_glass_specification = row.get("gf_glass_specification") or so_values.gf_glass_specification
		row.gf_from_glass_specification = cint(
			row.get("gf_from_glass_specification") or so_values.gf_from_glass_specification
		)
		row.gf_technical_summary = row.get("gf_technical_summary") or so_values.gf_technical_summary


def on_delivery_note_submit(doc, method=None):
	"""Mirror delivered pieces onto the Sales Order row, for a Delivery Note or a Sales Invoice that updates stock.

	ERPNext's delivered_qty is in the selling UOM (m² for glass rows). The glass mirror is
	always pieces so it compares directly with cut and processed pieces. The same sync runs
	on cancel so a cancelled delivery gives its pieces back.
	"""
	if not _delivers_stock(doc):
		return

	updated = set()
	for row in doc.get("items") or []:
		if not row.get("so_detail"):
			continue
		is_glass = cint(row.get("gf_is_glass_item")) or cint(
			frappe.db.get_value("Sales Order Item", row.so_detail, "gf_from_glass_specification")
		)
		if not is_glass:
			is_glass = cint(frappe.db.get_value("Sales Order Item", row.so_detail, "gf_is_glass_item"))
		if not is_glass or row.so_detail in updated:
			continue
		frappe.db.set_value(
			"Sales Order Item",
			row.so_detail,
			"gf_delivered_qty",
			delivered_pieces(row.so_detail),
			update_modified=False,
		)
		updated.add(row.so_detail)


def validate_stock_entry(doc, method=None):
	"""Constrain Phase 0 glass stock entries to the approved glass movement flows."""
	if doc.doctype != "Stock Entry":
		return
	if not cint(doc.get("gf_created_by_glass_factory")) and not (
		doc.get("gf_cutting_job") or doc.get("gf_processing_job") or doc.get("gf_glass_stock_flow")
	):
		return

	if doc.purpose != "Repack" or doc.stock_entry_type not in GLASS_REPACK_STOCK_ENTRY_TYPES:
		frappe.throw("Simple glass production must use a Repack Stock Entry type.")

	flow = doc.get("gf_glass_stock_flow")
	if doc.get("gf_cutting_job") and doc.get("gf_processing_job") and flow not in ("Raw to Cut WIP", "Cut WIP to Final"):
		frappe.throw("Stock Entry cannot link both Cutting Job and Glass Processing Job for this stock flow.")
	if flow not in ("Raw to Cut WIP", "Cut WIP to Final", "Remnant/Scrap"):
		frappe.throw("Glass Stock Flow must be Raw to Cut WIP, Cut WIP to Final, or Remnant/Scrap.")

	for row in doc.get("items") or []:
		role = row.get("gf_source_item_role") or item_role(row.item_code)
		if flow == "Raw to Cut WIP":
			if row.s_warehouse and role not in ("Raw Sheet", "Remnant"):
				frappe.throw(f"Stock Entry row {row.idx}: source must be Raw Sheet or Remnant.")
			if row.t_warehouse and role not in ("Cut WIP", "Remnant", "Scrap"):
				frappe.throw(f"Stock Entry row {row.idx}: target must be Cut WIP, Remnant, or Scrap.")
		elif flow == "Cut WIP to Final":
			if row.s_warehouse and role != "Cut WIP":
				frappe.throw(f"Stock Entry row {row.idx}: source must be Cut WIP.")
			if row.t_warehouse and role != "Final":
				frappe.throw(f"Stock Entry row {row.idx}: target must be Final.")


def _reject_non_commercial_item(row, context: str) -> None:
	"""Block selling or delivering raw, WIP, remnant, or scrap Items."""
	item_code = row.get("item_code")
	if not item_code:
		return

	role = item_role(item_code)
	if role not in NON_COMMERCIAL_ROLES:
		return

	label = {
		"Raw Sheet": "raw sheet",
		"Cut WIP": "cut WIP",
		"Remnant": "remnant",
		"Scrap": "scrap",
	}[role]
	frappe.throw(f"{context} row {row.idx}: cannot use {label} Item {item_code} as a commercial line.")


def validate_no_manufacturing_for_glass(row) -> None:
	"""Simple glass rows must not depend on BOM / manufacturing documents."""
	if not cint(row.get("gf_is_glass_item")):
		return

	item_code = row.get("item_code") or row.get("gf_final_item")
	if not item_code:
		return

	if frappe.db.get_value("Item", item_code, "default_bom"):
		frappe.throw(f"Row {row.idx}: glass Item {item_code} must not have a default BOM.")

	if frappe.db.exists("BOM", {"item": item_code, "is_active": 1, "docstatus": 1}):
		frappe.throw(f"Row {row.idx}: glass Item {item_code} must not have an active BOM.")


def _ensure_glass_row_warehouse(doc, row) -> None:
	"""Fill Sales Order delivery warehouse from header, item default, or settings."""
	if row.get("warehouse") or doc.doctype != "Sales Order":
		return

	warehouse = doc.get("set_warehouse")
	if not warehouse and row.get("item_code"):
		company = doc.get("company") or frappe.defaults.get_defaults().company
		if company:
			warehouse = get_item_defaults(row.item_code, company).get("default_warehouse")

	if not warehouse:
		warehouse = get_default_selling_warehouse()

	if warehouse:
		row.warehouse = warehouse


def _sync_glass_fields_from_quotation(doc) -> None:
	"""Copy gf_* metadata from Quotation Item when making a Sales Order."""
	for row in doc.get("items") or []:
		if not row.get("quotation_item") or cint(row.get("gf_is_glass_item")):
			continue

		source = frappe.db.get_value("Quotation Item", row.quotation_item, GLASS_ROW_FIELDS, as_dict=True)
		if not source or not cint(source.gf_is_glass_item):
			continue

		for fieldname in GLASS_ROW_FIELDS + SPEC_TRANSACTION_ROW_FIELDS:
			if not row.get(fieldname) and source.get(fieldname) not in (None, ""):
				row.set(fieldname, source.get(fieldname))
