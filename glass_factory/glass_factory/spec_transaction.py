"""Glass Product Specification to Quotation / Sales Order integration."""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt, getdate, today

from glass_factory.glass_factory.settings_validation import get_default_selling_warehouse
from glass_factory.glass_factory.spec_production import enrich_spec_transaction_row

SUPPORTED_TARGET_DOCTYPES = ("Quotation", "Sales Order")

SPEC_TRANSACTION_ROW_FIELDS = (
	"gf_glass_specification",
	"gf_from_glass_specification",
	"gf_area_m2",
	"gf_total_area_m2",
	"gf_selling_rate_per_m2",
	"gf_calculated_rate_per_m2",
	"gf_manual_selling_rate_per_m2",
	"gf_price_override",
	"gf_price_difference_per_m2",
	"gf_rate_per_piece",
	"gf_raw_sheet_item",
	"gf_cut_wip_item",
	"gf_final_item",
	"gf_technical_summary",
	"gf_design_attachment_summary",
	"gf_transaction_rate_overridden",
)


def validate_spec_ready_for_transaction(spec, *, allow_zero_price: bool = False) -> None:
	"""Ensure a specification is ready to be copied into a selling document."""
	if not spec or not spec.name:
		frappe.throw("Glass Product Specification was not found.")

	if not cint(spec.get("items_generated")):
		frappe.throw("Generate items before adding this specification to a transaction.")

	final_item = spec.get("final_item_code") or spec.get("generated_item")
	if not final_item:
		frappe.throw("Generate items before adding this specification to a transaction.")

	if spec.get("generation_status") == "Regeneration Required":
		frappe.throw("This specification requires regeneration before it can be used.")

	if spec.get("generation_status") != "Generated":
		frappe.throw("Generate items before adding this specification to a transaction.")

	status = spec.get("status") or "Draft"
	if status not in ("Ready", "Used"):
		frappe.throw("This specification must be Ready before it can be used in a transaction.")

	if flt(spec.get("area_m2")) <= 0:
		frappe.throw("Area must be greater than zero.")

	if not allow_zero_price and flt(spec.get("rate_per_piece")) <= 0:
		frappe.throw("Refresh pricing before adding this specification to a transaction.")

	if not allow_zero_price and flt(spec.get("selling_rate_per_m2")) <= 0:
		frappe.throw("Refresh pricing before adding this specification to a transaction.")


def build_design_attachment_summary(spec) -> str:
	attachments = spec.get("design_attachments") or []
	if not attachments:
		return ""

	primary = next((row for row in attachments if cint(row.get("is_primary"))), None)
	other_count = len(attachments) - (1 if primary else 0)
	parts: list[str] = []

	if primary:
		label = primary.get("file_name") or primary.get("design_file") or "Primary file"
		if "/" in label:
			label = label.rsplit("/", 1)[-1]
		parts.append(f"Primary: {label}")
	if other_count:
		parts.append(f"Other files: {other_count}")
	elif not primary:
		parts.append(f"Files: {len(attachments)}")

	return "; ".join(parts)


def map_spec_to_transaction_row(spec) -> dict:
	final_item = spec.get("final_item_code") or spec.get("generated_item")
	item = frappe.get_doc("Item", final_item)
	qty = 1
	rate = flt(spec.rate_per_piece)
	amount = flt(qty * rate, 2)
	total_area_m2 = flt(spec.area_m2)

	return {
		"item_code": final_item,
		"item_name": item.item_name or final_item,
		"description": spec.get("technical_summary") or item.item_name or final_item,
		"qty": qty,
		"uom": item.stock_uom or "Nos",
		"stock_uom": item.stock_uom or "Nos",
		"conversion_factor": 1,
		"rate": rate,
		"amount": amount,
		"net_rate": rate,
		"net_amount": amount,
		"base_rate": rate,
		"base_amount": amount,
		"gf_glass_specification": spec.name,
		"gf_from_glass_specification": 1,
		"gf_area_m2": flt(spec.area_m2),
		"gf_total_area_m2": total_area_m2,
		"gf_selling_rate_per_m2": flt(spec.selling_rate_per_m2),
		"gf_calculated_rate_per_m2": flt(spec.calculated_rate_per_m2),
		"gf_manual_selling_rate_per_m2": flt(spec.manual_selling_rate_per_m2),
		"gf_price_override": cint(spec.price_override),
		"gf_price_difference_per_m2": flt(spec.price_difference_per_m2),
		"gf_rate_per_piece": rate,
		"gf_raw_sheet_item": spec.get("raw_item_code") or spec.get("raw_sheet_item"),
		"gf_cut_wip_item": spec.get("cut_wip_item_code"),
		"gf_final_item": final_item,
		"gf_technical_summary": spec.get("technical_summary") or "",
		"gf_design_attachment_summary": build_design_attachment_summary(spec),
		"gf_transaction_rate_overridden": 0,
		**enrich_spec_transaction_row(spec),
	}


def _find_existing_spec_row(doc, spec_name: str):
	for row in doc.get("items") or []:
		if row.get("gf_glass_specification") == spec_name and cint(row.get("gf_from_glass_specification")):
			return row
	return None


def _row_rate_manually_edited(row) -> bool:
	spec_rate = row.get("gf_rate_per_piece")
	if spec_rate in (None, ""):
		return False
	return flt(row.get("rate")) != flt(spec_rate)


def _apply_spec_row_update(existing_row, row_data: dict) -> None:
	manually_edited = _row_rate_manually_edited(existing_row)
	preserved_rate = flt(existing_row.rate) if manually_edited else None

	for fieldname, value in row_data.items():
		if fieldname in ("rate", "amount", "net_rate", "net_amount", "base_rate", "base_amount"):
			continue
		existing_row.set(fieldname, value)

	qty = flt(row_data.get("qty"))
	if manually_edited:
		rate = preserved_rate
	else:
		rate = flt(row_data.get("rate"))

	amount = flt(qty * rate, 2)
	existing_row.qty = qty
	existing_row.rate = rate
	existing_row.amount = amount
	existing_row.net_rate = rate
	existing_row.net_amount = amount
	existing_row.base_rate = rate
	existing_row.base_amount = amount
	existing_row.gf_total_area_m2 = flt(flt(row_data.get("gf_area_m2")) * qty, 6)


def mark_transaction_rate_overrides(doc, method=None) -> None:
	"""Set audit flags when a transaction row rate differs from the spec rate."""
	if doc.doctype not in SUPPORTED_TARGET_DOCTYPES:
		return

	for row in doc.get("items") or []:
		if not cint(row.get("gf_from_glass_specification")):
			continue
		row.gf_transaction_rate_overridden = 1 if _row_rate_manually_edited(row) else 0


def _set_transaction_currency(doc, company: str, currency: str) -> None:
	doc.currency = currency
	company_currency = frappe.db.get_value("Company", company, "default_currency")
	if currency == company_currency:
		doc.conversion_rate = 1
		return

	from erpnext.setup.utils import get_exchange_rate

	transaction_date = doc.get("transaction_date") or today()
	doc.conversion_rate = get_exchange_rate(currency, company_currency, transaction_date)


def _new_transaction_from_spec(target_doctype: str, spec):
	doc = frappe.new_doc(target_doctype)
	company = spec.get("company") or frappe.defaults.get_defaults().company
	if not company:
		frappe.throw("Company is required to create a transaction.")

	doc.company = company
	currency = spec.get("currency") or frappe.db.get_value("Company", company, "default_currency")
	_set_transaction_currency(doc, company, currency)

	price_list = spec.get("price_list")
	if not price_list:
		price_list = frappe.db.get_value(
			"Price List",
			{"selling": 1, "currency": currency, "enabled": 1},
			"name",
		)
	if price_list:
		doc.selling_price_list = price_list

	if target_doctype == "Quotation":
		doc.quotation_to = "Customer"
		doc.transaction_date = getdate(today())
		if spec.get("customer"):
			doc.party_name = spec.customer
			doc.customer = spec.customer
	else:
		doc.transaction_date = getdate(today())
		doc.delivery_date = getdate(today())
		if spec.get("customer"):
			doc.customer = spec.customer

	return doc


def _warehouse_belongs_to_company(warehouse: str | None, company: str | None) -> bool:
	if not warehouse or not company:
		return False
	return frappe.db.get_value("Warehouse", warehouse, "company") == company


def _resolve_sales_order_warehouse(doc, item_code: str | None = None) -> str | None:
	warehouse = doc.get("set_warehouse") if doc.doctype == "Sales Order" else None
	if _warehouse_belongs_to_company(warehouse, doc.company):
		return warehouse

	if item_code:
		from erpnext.stock.doctype.item.item import get_item_defaults

		warehouse = get_item_defaults(item_code, doc.company).get("default_warehouse")
		if _warehouse_belongs_to_company(warehouse, doc.company):
			return warehouse

	warehouse = get_default_selling_warehouse()
	if _warehouse_belongs_to_company(warehouse, doc.company):
		return warehouse
	return None


def _ensure_sales_order_row_warehouse(doc, row) -> None:
	if doc.doctype != "Sales Order" or row.get("warehouse"):
		return

	warehouse = _resolve_sales_order_warehouse(doc, row.get("item_code"))
	if warehouse:
		row.warehouse = warehouse
	if warehouse and not doc.get("set_warehouse"):
		doc.set_warehouse = warehouse


@frappe.whitelist()
def add_spec_to_transaction(
	spec_name,
	target_doctype,
	target_name=None,
	update_existing=False,
	allow_zero_price=False,
):
	"""Add or update a Glass Product Specification row on Quotation / Sales Order."""
	target_doctype = (target_doctype or "").strip()
	if target_doctype not in SUPPORTED_TARGET_DOCTYPES:
		frappe.throw(f"Unsupported target doctype: {target_doctype}")

	spec = frappe.get_doc("Glass Product Specification", spec_name)
	validate_spec_ready_for_transaction(spec, allow_zero_price=cint(allow_zero_price))

	if target_name:
		doc = frappe.get_doc(target_doctype, target_name)
		if doc.docstatus != 0:
			frappe.throw(f"Cannot add a specification to submitted {target_doctype}.")
	else:
		doc = _new_transaction_from_spec(target_doctype, spec)

	existing_row = _find_existing_spec_row(doc, spec.name)
	update_existing = cint(update_existing)
	if existing_row and not update_existing:
		frappe.throw("This Glass Product Specification already exists in this transaction.")

	row_data = map_spec_to_transaction_row(spec)
	if existing_row:
		_apply_spec_row_update(existing_row, row_data)
		item_row = existing_row
	else:
		doc.append("items", row_data)
		item_row = doc.items[-1]

	if doc.doctype == "Sales Order":
		_ensure_sales_order_row_warehouse(doc, item_row)
		if doc.delivery_date and not item_row.get("delivery_date"):
			item_row.delivery_date = doc.delivery_date

	mark_transaction_rate_overrides(doc)
	doc.save()

	return {
		"doctype": doc.doctype,
		"name": doc.name,
		"item_name": item_row.name,
	}


def is_spec_transaction_row(row) -> bool:
	return cint(row.get("gf_from_glass_specification")) == 1
