"""Glass Product Specification Summary script report."""
from __future__ import annotations

from typing import Dict, List

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt

from glass_factory.glass_factory.report.report_utils import (
	apply_creation_date_filter,
	compute_spec_internal_processing_cost,
	margin_summary_card,
	operations_summary_from_spec,
	totals_summary,
)


def execute(filters=None):
	from glass_factory.glass_factory.authorization import require_cost_access

	require_cost_access()
	filters = filters or {}
	columns = _columns()
	data = _data(filters)
	currency = filters.get("currency") or ""
	if data and not currency:
		currency = data[0].get("currency") or ""
	summary = totals_summary(
		data,
		currency=currency,
		field_map={
			"material_cost": "Total Material Cost",
			"processing_cost": "Total Processing Cost",
			"total_cost": "Total Cost",
			"selling_amount": "Total Selling Amount",
			"gross_profit": "Gross Profit",
		},
	)
	margin_card = margin_summary_card(data, "margin_percent")
	if margin_card:
		summary.append(margin_card)
	return columns, data, None, None, summary


def _columns() -> List[Dict]:
	return [
		{"fieldname": "specification", "label": _("Specification"), "fieldtype": "Link",
		 "options": "Glass Product Specification", "width": 160},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link",
		 "options": "Customer", "width": 140},
		{"fieldname": "company", "label": _("Company"), "fieldtype": "Link",
		 "options": "Company", "width": 120},
		{"fieldname": "glass_type", "label": _("Glass Type"), "fieldtype": "Data", "width": 90},
		{"fieldname": "raw_sheet_item", "label": _("Raw Sheet Item"), "fieldtype": "Link",
		 "options": "Item", "width": 180},
		{"fieldname": "thickness_mm", "label": _("Thickness (mm)"), "fieldtype": "Float", "width": 90},
		{"fieldname": "dimensions", "label": _("Dimensions (mm)"), "fieldtype": "Data", "width": 110},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 70},
		{"fieldname": "area_m2", "label": _("Area (m²)"), "fieldtype": "Float", "precision": 3, "width": 90},
		{"fieldname": "operations", "label": _("Operations"), "fieldtype": "Data", "width": 160},
		{"fieldname": "material_cost", "label": _("Material Cost"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "processing_cost", "label": _("Processing Cost"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "total_cost", "label": _("Total Cost"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "selling_amount", "label": _("Selling Amount"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "gross_profit", "label": _("Gross Profit"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "margin_percent", "label": _("Margin %"), "fieldtype": "Percent", "width": 80},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 110},
		{"fieldname": "creation", "label": _("Created On"), "fieldtype": "Datetime", "width": 140},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link",
		 "options": "Currency", "hidden": 1},
	]


def _data(filters: Dict) -> List[Dict]:
	Spec = DocType("Glass Product Specification")
	query = (
		frappe.qb.from_(Spec)
		.select(
			Spec.name,
			Spec.customer,
			Spec.company,
			Spec.currency,
			Spec.glass_type,
			Spec.raw_sheet_item,
			Spec.thickness_mm,
			Spec.length_mm,
			Spec.width_mm,
			Spec.qty,
			Spec.area_m2,
			Spec.polish,
			Spec.bevel,
			Spec.temper,
			Spec.sandblast,
			Spec.laminate,
			Spec.hole_count,
			Spec.special_hole_count,
			Spec.slot_count,
			Spec.special_slot_count,
			Spec.raw_cost_per_finished_piece,
			Spec.rate_per_piece,
			Spec.amount,
			Spec.gross_profit_percent,
			Spec.status,
			Spec.creation,
		)
		.where(Spec.docstatus < 2)
	)

	if filters.get("company"):
		query = query.where(Spec.company == filters["company"])
	if filters.get("customer"):
		query = query.where(Spec.customer == filters["customer"])
	if filters.get("status"):
		query = query.where(Spec.status == filters["status"])
	if filters.get("glass_item"):
		query = query.where(
			(Spec.raw_sheet_item == filters["glass_item"])
			| (Spec.final_item_code == filters["glass_item"])
		)
	query = apply_creation_date_filter(query, Spec, filters.get("from_date"), filters.get("to_date"))
	rows = query.orderby(Spec.creation, order=frappe.qb.desc).run(as_dict=True)

	spec_names = [r["name"] for r in rows]
	op_pricing = _operation_pricing_by_spec(spec_names)

	out: List[Dict] = []
	for row in rows:
		qty = flt(row.get("qty")) or 1
		material_per_piece = flt(row.get("raw_cost_per_finished_piece"))
		spec_dict = {**row, "operation_pricing": op_pricing.get(row["name"], [])}
		processing_per_piece = compute_spec_internal_processing_cost(spec_dict)
		total_cost_per_piece = material_per_piece + processing_per_piece
		selling_per_piece = flt(row.get("rate_per_piece"))
		selling_amount = flt(row.get("amount")) or selling_per_piece * qty
		gross_profit = selling_amount - total_cost_per_piece * qty
		margin = (
			flt(gross_profit / selling_amount * 100, 2) if selling_amount else flt(row.get("gross_profit_percent"))
		)
		length = int(flt(row.get("length_mm")))
		width = int(flt(row.get("width_mm")))
		out.append({
			"specification": row["name"],
			"customer": row.get("customer") or "",
			"company": row.get("company") or "",
			"glass_type": row.get("glass_type") or "",
			"raw_sheet_item": row.get("raw_sheet_item") or "",
			"thickness_mm": flt(row.get("thickness_mm")),
			"dimensions": f"{length}×{width}" if length and width else "—",
			"qty": qty,
			"area_m2": flt(row.get("area_m2")),
			"operations": operations_summary_from_spec(row),
			"material_cost": flt(material_per_piece * qty, 2),
			"processing_cost": flt(processing_per_piece * qty, 2),
			"total_cost": flt(total_cost_per_piece * qty, 2),
			"selling_amount": flt(selling_amount, 2),
			"gross_profit": flt(gross_profit, 2),
			"margin_percent": margin,
			"status": row.get("status") or "",
			"creation": row.get("creation"),
			"currency": row.get("currency") or "",
		})
	return out


def _operation_pricing_by_spec(spec_names: List[str]) -> Dict[str, List[Dict]]:
	if not spec_names:
		return {}
	child = DocType("Glass Spec Operation Pricing")
	parent = DocType("Glass Product Specification")
	rows = (
		frappe.qb.from_(child)
		.inner_join(parent)
		.on(child.parent == parent.name)
		.select(
			parent.name.as_("spec_name"),
			child.operation,
			child.pricing_basis,
			child.quantity,
		)
		.where(parent.name.isin(spec_names))
		.run(as_dict=True)
	)
	out: Dict[str, List[Dict]] = {}
	for r in rows:
		out.setdefault(r["spec_name"], []).append(r)
	return out
