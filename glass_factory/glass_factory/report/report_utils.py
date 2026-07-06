"""Shared helpers for Glass Factory script reports."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import IfNull
from frappe.utils import cint, flt, getdate

from glass_factory.glass_factory.spec_pricing import get_operation_cost_rate

PRODUCTION_STAGES = (
	"Spec Created",
	"Quoted",
	"Sales Order Created",
	"Cutting Pending",
	"Cut WIP",
	"Processing Pending",
	"Finished Goods",
	"Delivered",
)


def default_company() -> str:
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)


def apply_creation_date_filter(query, doctype_table, from_date: Optional[str], to_date: Optional[str]):
	"""Restrict a frappe.qb query by creation date (inclusive)."""
	if from_date:
		query = query.where(doctype_table.creation >= getdate(from_date))
	if to_date:
		query = query.where(doctype_table.creation <= f"{getdate(to_date)} 23:59:59")
	return query


def operations_summary_from_spec(spec: Dict[str, Any]) -> str:
	"""Compact operations label from spec boolean/count fields."""
	parts: List[str] = []
	for label, field in (
		("Polish", "polish"),
		("Bevel", "bevel"),
		("Temper", "temper"),
		("Sandblast", "sandblast"),
		("Laminate", "laminate"),
	):
		if cint(spec.get(field)):
			parts.append(label)
	for label, field in (
		("Hole", "hole_count"),
		("Sp.Hole", "special_hole_count"),
		("Slot", "slot_count"),
		("Sp.Slot", "special_slot_count"),
	):
		count = cint(spec.get(field))
		if count:
			parts.append(f"{label}×{count}")
	return ", ".join(parts) if parts else "—"


def compute_spec_internal_processing_cost(spec: Dict[str, Any]) -> float:
	"""Internal processing cost from operation cost rates (not selling rates)."""
	currency = spec.get("currency") or frappe.get_cached_value(
		"Company", spec.get("company"), "default_currency"
	) or "USD"
	total = 0.0
	for row in spec.get("operation_pricing") or []:
		operation = row.get("operation") if isinstance(row, dict) else row.operation
		basis = row.get("pricing_basis") if isinstance(row, dict) else row.pricing_basis
		qty = flt(row.get("quantity") if isinstance(row, dict) else row.quantity)
		cost_rate = get_operation_cost_rate(operation, currency, basis)
		total += flt(cost_rate * qty, 2)
	return flt(total, 2)


def derive_production_stage(
	*,
	quotation: str = "",
	sales_order: str = "",
	qty_ordered: float = 0,
	cut_qty: float = 0,
	processed_qty: float = 0,
	delivered_qty: float = 0,
	cutting_job_status: str = "",
	processing_job_status: str = "",
	has_cutting_job: bool = False,
	has_processing_job: bool = False,
) -> str:
	"""Return the most advanced production stage for an order/spec line."""
	ordered = flt(qty_ordered)
	delivered = flt(delivered_qty)
	processed = flt(processed_qty)
	cut = flt(cut_qty)

	if ordered > 0 and delivered >= ordered:
		return "Delivered"
	if processed > 0:
		return "Finished Goods"
	if has_processing_job and processing_job_status not in ("", "Draft", "Cancelled"):
		return "Processing Pending"
	if cut > 0 or cutting_job_status in (
		"Cut Stock Posted",
		"Processing Started",
		"Completed",
	):
		return "Cut WIP"
	if has_cutting_job:
		return "Cutting Pending"
	if sales_order:
		return "Sales Order Created"
	if quotation:
		return "Quoted"
	return "Spec Created"


def pct(numerator: float, denominator: float) -> float:
	if not denominator:
		return 0.0
	return flt(numerator / denominator * 100, 2)


def totals_summary(
	rows: List[Dict],
	*,
	currency: str = "",
	field_map: Optional[Dict[str, str]] = None,
) -> List[Dict]:
	"""Build report_summary cards from numeric row fields."""
	if not rows:
		return []
	field_map = field_map or {}
	cards: List[Dict] = []
	for field, label in field_map.items():
		total = sum(flt(r.get(field)) for r in rows)
		cards.append(
			{
				"label": _(label),
				"value": f"{total:,.2f} {currency}".strip(),
				"indicator": "Blue",
				"datatype": "Data",
			}
		)
	return cards


def margin_summary_card(rows: List[Dict], margin_field: str = "margin_percent") -> Optional[Dict]:
	if not rows:
		return None
	avg = sum(flt(r.get(margin_field)) for r in rows) / len(rows)
	return {
		"label": _("Average Margin %"),
		"value": f"{avg:.2f}%",
		"indicator": "Green",
		"datatype": "Data",
	}


def fetch_linked_quotation(spec_name: str) -> str:
	"""Return the most recent Quotation linked to a glass spec."""
	if not spec_name:
		return ""
	qi = DocType("Quotation Item")
	q = DocType("Quotation")
	row = (
		frappe.qb.from_(qi)
		.inner_join(q)
		.on(qi.parent == q.name)
		.select(q.name)
		.where(qi.gf_glass_specification == spec_name)
		.where(q.docstatus < 2)
		.orderby(q.creation, order=frappe.qb.desc)
		.limit(1)
	).run()
	return row[0][0] if row else ""


def fetch_so_for_spec(spec_name: str) -> Optional[Dict[str, Any]]:
	"""Return the primary Sales Order Item row for a glass spec."""
	if not spec_name:
		return None
	soi = DocType("Sales Order Item")
	so = DocType("Sales Order")
	rows = (
		frappe.qb.from_(soi)
		.inner_join(so)
		.on(soi.parent == so.name)
		.select(
			soi.name,
			soi.parent.as_("sales_order"),
			soi.qty,
			soi.rate,
			soi.amount,
			soi.gf_cut_qty,
			soi.gf_processed_qty,
			soi.gf_delivered_qty,
			soi.gf_cutting_job,
			soi.gf_processing_job,
			so.customer,
			so.currency,
			so.company,
		)
		.where(soi.gf_glass_specification == spec_name)
		.where(so.docstatus < 2)
		.orderby(so.creation, order=frappe.qb.desc)
		.limit(1)
	).run(as_dict=True)
	return rows[0] if rows else None


def fetch_delivery_note_for_so_item(so_item_name: str) -> str:
	if not so_item_name:
		return ""
	dni = DocType("Delivery Note Item")
	dn = DocType("Delivery Note")
	row = (
		frappe.qb.from_(dni)
		.inner_join(dn)
		.on(dni.parent == dn.name)
		.select(dn.name)
		.where(dni.so_detail == so_item_name)
		.where(dn.docstatus == 1)
		.orderby(dn.creation, order=frappe.qb.desc)
		.limit(1)
	).run()
	return row[0][0] if row else ""


def raw_sheet_area_m2(length_mm: float, width_mm: float) -> float:
	length = flt(length_mm)
	width = flt(width_mm)
	if length <= 0 or width <= 0:
		return 0.0
	return flt(length * width / 1_000_000, 4)


def standard_report_filters_js() -> str:
	"""Not used at runtime — reference for JS filter blocks."""
	return ""
