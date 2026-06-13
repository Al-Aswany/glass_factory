"""Glass child-table sync into standard selling document Item rows."""

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt

from glass_factory.glass_factory.item_resolver import PROCESS_ORDER, get_item_glass_meta, resolve_row_items
from glass_factory.glass_factory.piece_pricing import apply_piece_rates, calculate_piece_rates
from glass_factory.glass_factory.settings_validation import get_default_selling_warehouse

PIECE_FLAG_FIELDS = (
	("process_polish", "POL"),
	("process_bevel", "BEV"),
	("process_holes", "HOL"),
	("process_slots", "SLT"),
	("process_temper", "TMP"),
	("process_sandblast", "SBL"),
	("process_laminate", "LAM"),
)


def sync_glass_pieces_to_items(doc, method=None):
	"""Build/update standard Item rows from the glass child table."""
	if doc.doctype not in ("Quotation", "Sales Order"):
		return

	glass_pieces = doc.get("glass_pieces") or []
	if not glass_pieces:
		return

	manual_rows = [
		row.as_dict(convert_dates_to_str=True)
		for row in doc.get("items") or []
		if not cint(row.get("gf_is_glass_item")) and row.get("item_code")
	]
	existing_rates = _existing_glass_rates(doc.get("items") or [])
	existing_delivery_dates = _existing_glass_delivery_dates(doc.get("items") or [])
	existing_warehouses = _existing_glass_warehouses(doc.get("items") or [])
	synced_rows = []

	price_list, company = _quotation_pricing_context(doc)

	for piece in glass_pieces:
		_validate_piece(piece)
		source_id = piece.name or f"new-{piece.idx}"
		_apply_rates_to_piece(piece, price_list=price_list, company=company)

		row_data = _build_item_row(
			doc,
			piece,
			source_id,
			existing_rate=existing_rates.get(source_id),
			existing_delivery_date=existing_delivery_dates.get(source_id),
			existing_warehouse=existing_warehouses.get(source_id),
		)
		piece.area_m2 = row_data["gf_area_m2"]
		piece.final_item = row_data["gf_final_item"]
		piece.description = row_data.get("description") or row_data["item_name"]
		synced_rows.append(row_data)

	# Drop stale auto-generated glass lines; keep manual non-glass lines.
	doc.set("items", [])
	for row in manual_rows + synced_rows:
		doc.append("items", row)


def processing_flags_from_piece(piece) -> str:
	"""Convert user-friendly checkboxes to canonical flag string."""
	flags = [code for fieldname, code in PIECE_FLAG_FIELDS if cint(piece.get(fieldname))]
	flags = [flag for flag in PROCESS_ORDER if flag in flags]
	return "-".join(flags)


def _validate_piece(piece) -> None:
	if not piece.raw_sheet_item:
		frappe.throw(f"Glass row {piece.idx}: Glass Sheet is required.")
	if flt(piece.length_mm) <= 0 or flt(piece.width_mm) <= 0:
		frappe.throw(f"Glass row {piece.idx}: Length and width must be greater than zero.")
	if flt(piece.qty) <= 0:
		frappe.throw(f"Glass row {piece.idx}: Quantity must be greater than zero.")


def _sales_order_item_delivery_date(doc, delivery_date=None):
	if doc and doc.doctype == "Sales Order":
		return doc.get("delivery_date")
	return delivery_date


def _sales_order_item_warehouse(doc):
	if doc and doc.doctype == "Sales Order":
		return doc.get("set_warehouse")
	return None


def _resolve_item_warehouse(doc, existing_warehouse=None, item_code=None):
	warehouse = existing_warehouse or _sales_order_item_warehouse(doc)
	if warehouse:
		return warehouse
	if doc and doc.doctype == "Sales Order":
		if item_code:
			company = doc.get("company") or frappe.defaults.get_defaults().company
			if company:
				from erpnext.stock.doctype.item.item import get_item_defaults

				warehouse = get_item_defaults(item_code, company).get("default_warehouse")
		if not warehouse:
			return get_default_selling_warehouse()
	return warehouse


def _build_item_row(
	doc,
	piece,
	source_id: str,
	existing_rate=None,
	existing_delivery_date=None,
	existing_warehouse=None,
) -> dict:
	thickness = flt(piece.thickness_mm)
	if thickness <= 0:
		thickness = flt(get_item_glass_meta(piece.raw_sheet_item).get("gf_thickness_mm"))

	resolved = frappe._dict({
		"idx": piece.idx,
		"gf_is_glass_item": 1,
		"gf_raw_sheet_item": piece.raw_sheet_item,
		"gf_length_mm": flt(piece.length_mm),
		"gf_width_mm": flt(piece.width_mm),
		"gf_thickness_mm": thickness,
		"gf_processing_flags": processing_flags_from_piece(piece),
		"qty": flt(piece.qty),
	})
	resolve_row_items(resolved)

	item = frappe.get_doc("Item", resolved.item_code)
	rate = flt(existing_rate) if existing_rate not in (None, "") else flt(piece.rate)
	qty = flt(piece.qty)
	amount = flt(qty * rate, 2)

	row = {
		"gf_is_glass_item": 1,
		"gf_glass_specification": resolved.gf_glass_specification,
		"gf_raw_sheet_item": resolved.gf_raw_sheet_item,
		"gf_cut_wip_item": resolved.gf_cut_wip_item,
		"gf_final_item": resolved.gf_final_item,
		"gf_length_mm": resolved.gf_length_mm,
		"gf_width_mm": resolved.gf_width_mm,
		"gf_thickness_mm": resolved.gf_thickness_mm,
		"gf_processing_flags": resolved.gf_processing_flags,
		"gf_area_m2": resolved.gf_area_m2,
		"gf_source_row_id": source_id,
		"item_code": resolved.item_code,
		"item_name": item.item_name or item.name,
		"description": item.item_name or item.name,
		"qty": qty,
		"rate": rate,
		"amount": amount,
		"net_rate": rate,
		"base_rate": rate,
		"base_amount": amount,
		"net_amount": amount,
		"uom": item.stock_uom or "Nos",
		"stock_uom": item.stock_uom or "Nos",
		"conversion_factor": 1,
	}
	delivery_date = existing_delivery_date or _sales_order_item_delivery_date(doc)
	if delivery_date:
		row["delivery_date"] = delivery_date
	warehouse = _resolve_item_warehouse(doc, existing_warehouse, item_code=resolved.item_code)
	if warehouse:
		row["warehouse"] = warehouse
	return row


def quotation_has_glass_pieces(doc) -> bool:
	return bool(doc.get("glass_pieces"))


def item_table_editable_fields() -> tuple[str, ...]:
	"""Fields normal users may edit on generated glass Item rows."""
	return ("rate",)


def _existing_glass_rates(rows) -> dict[str, float]:
	rates = {}
	for row in rows or []:
		if cint(row.get("gf_is_glass_item")) and row.get("gf_source_row_id"):
			rates[row.get("gf_source_row_id")] = row.get("rate")
	return rates


def _existing_glass_delivery_dates(rows) -> dict[str, str]:
	dates = {}
	for row in rows or []:
		if cint(row.get("gf_is_glass_item")) and row.get("gf_source_row_id") and row.get("delivery_date"):
			dates[row.get("gf_source_row_id")] = row.get("delivery_date")
	return dates


def _existing_glass_warehouses(rows) -> dict[str, str]:
	warehouses = {}
	for row in rows or []:
		if cint(row.get("gf_is_glass_item")) and row.get("gf_source_row_id") and row.get("warehouse"):
			warehouses[row.get("gf_source_row_id")] = row.get("warehouse")
	return warehouses


@frappe.whitelist()
def build_quotation_items_from_glass(
	glass_pieces,
	manual_items=None,
	price_list=None,
	company=None,
	existing_glass_rates=None,
	existing_glass_delivery_dates=None,
	existing_glass_warehouses=None,
	delivery_date=None,
	set_warehouse=None,
	parent_doctype=None,
):
	"""Build Item rows from glass pieces for client-side pre-save sync."""
	for attempt in range(2):
		try:
			return _build_quotation_items_from_glass(
				glass_pieces,
				manual_items=manual_items,
				price_list=price_list,
				company=company,
				existing_glass_rates=existing_glass_rates,
				existing_glass_delivery_dates=existing_glass_delivery_dates,
				existing_glass_warehouses=existing_glass_warehouses,
				delivery_date=delivery_date,
				set_warehouse=set_warehouse,
				parent_doctype=parent_doctype,
			)
		except frappe.QueryDeadlockError:
			if attempt:
				raise
			frappe.db.rollback()


def _build_quotation_items_from_glass(
	glass_pieces,
	manual_items=None,
	price_list=None,
	company=None,
	existing_glass_rates=None,
	existing_glass_delivery_dates=None,
	existing_glass_warehouses=None,
	delivery_date=None,
	set_warehouse=None,
	parent_doctype=None,
):
	glass_pieces = frappe.parse_json(glass_pieces)
	manual_items = frappe.parse_json(manual_items) if manual_items else []
	existing_glass_rates = frappe.parse_json(existing_glass_rates) if existing_glass_rates else {}
	existing_glass_delivery_dates = (
		frappe.parse_json(existing_glass_delivery_dates) if existing_glass_delivery_dates else {}
	)
	existing_glass_warehouses = (
		frappe.parse_json(existing_glass_warehouses) if existing_glass_warehouses else {}
	)
	doc = None
	if parent_doctype == "Sales Order":
		doc = frappe._dict(
			doctype="Sales Order",
			delivery_date=delivery_date,
			set_warehouse=set_warehouse,
		)

	synced_rows = []
	updated_pieces = []

	for piece in glass_pieces:
		piece = frappe._dict(piece)
		_validate_piece(piece)
		source_id = piece.name or f"new-{piece.idx}"
		_apply_rates_to_piece(piece, price_list=price_list, company=company)
		row_data = _build_item_row(
			doc,
			piece,
			source_id,
			existing_rate=existing_glass_rates.get(source_id),
			existing_delivery_date=existing_glass_delivery_dates.get(source_id),
			existing_warehouse=existing_glass_warehouses.get(source_id),
		)
		synced_rows.append(row_data)
		updated_pieces.append({
			**piece,
			"area_m2": row_data["gf_area_m2"],
			"final_item": row_data["gf_final_item"],
			"description": row_data.get("description") or row_data["item_name"],
		})

	return {
		"items": manual_items + synced_rows,
		"glass_pieces": updated_pieces,
	}


@frappe.whitelist()
def calculate_glass_piece_rates(glass_pieces, price_list=None, company=None):
	"""Recalculate glass piece rates for client-side grid updates."""
	glass_pieces = frappe.parse_json(glass_pieces)
	return [
		apply_piece_rates(piece, price_list=price_list, company=company)
		for piece in glass_pieces
	]


def _apply_rates_to_piece(piece, price_list=None, company=None) -> None:
	rates = calculate_piece_rates(piece, price_list=price_list, company=company)
	if isinstance(piece, Document):
		for fieldname, value in rates.items():
			piece.set(fieldname, value)
	else:
		piece.update(rates)


def _quotation_pricing_context(doc):
	if not doc:
		return None, None
	return doc.get("selling_price_list"), doc.get("company")
