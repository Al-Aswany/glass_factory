"""Glass Costing and Margin script report."""
from __future__ import annotations

from typing import Dict, List, Optional

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt

from glass_factory.glass_factory.report.report_utils import (
	apply_creation_date_filter,
	compute_spec_internal_processing_cost,
	fetch_linked_quotation,
	fetch_so_for_spec,
	margin_summary_card,
	pct,
	totals_summary,
)


def execute(filters=None):
	filters = filters or {}
	columns = _columns()
	data = _data(filters)
	currency = ""
	if data:
		currency = data[0].get("currency") or ""
	summary = totals_summary(
		data,
		currency=currency,
		field_map={
			"material_cost": "Total Material Cost",
			"processing_cost": "Total Processing Cost",
			"finished_cost": "Total Finished Cost",
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
		 "options": "Glass Product Specification", "width": 150},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link",
		 "options": "Customer", "width": 130},
		{"fieldname": "company", "label": _("Company"), "fieldtype": "Link",
		 "options": "Company", "width": 110},
		{"fieldname": "material_cost", "label": _("Material Cost"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "processing_cost", "label": _("Processing Cost"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "finished_cost", "label": _("Finished Cost"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "selling_amount", "label": _("Selling Amount"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "gross_profit", "label": _("Gross Profit"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "margin_percent", "label": _("Margin %"), "fieldtype": "Percent", "width": 80},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link",
		 "options": "Currency", "width": 70},
		{"fieldname": "quotation", "label": _("Quotation"), "fieldtype": "Link",
		 "options": "Quotation", "width": 130},
		{"fieldname": "sales_order", "label": _("Sales Order"), "fieldtype": "Link",
		 "options": "Sales Order", "width": 130},
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
			Spec.qty,
			Spec.raw_cost_per_finished_piece,
			Spec.rate_per_piece,
			Spec.amount,
			Spec.creation,
		)
		.where(Spec.docstatus < 2)
	)
	if filters.get("company"):
		query = query.where(Spec.company == filters["company"])
	if filters.get("customer"):
		query = query.where(Spec.customer == filters["customer"])
	query = apply_creation_date_filter(query, Spec, filters.get("from_date"), filters.get("to_date"))
	spec_rows = query.orderby(Spec.creation, order=frappe.qb.desc).run(as_dict=True)

	spec_names = [r["name"] for r in spec_rows]
	op_pricing = _operation_pricing_by_spec(spec_names)

	out: List[Dict] = []
	for spec in spec_rows:
		qty = flt(spec.get("qty")) or 1
		spec_dict = {**spec, "operation_pricing": op_pricing.get(spec["name"], [])}
		material = flt(spec.get("raw_cost_per_finished_piece")) * qty
		processing = compute_spec_internal_processing_cost(spec_dict) * qty
		finished = material + processing

		so_row = fetch_so_for_spec(spec["name"])
		quotation = fetch_linked_quotation(spec["name"])
		selling_amount, currency = _resolve_selling(spec, so_row, quotation)

		gross_profit = selling_amount - finished
		out.append({
			"specification": spec["name"],
			"customer": spec.get("customer") or (so_row or {}).get("customer") or "",
			"company": spec.get("company") or (so_row or {}).get("company") or "",
			"material_cost": flt(material, 2),
			"processing_cost": flt(processing, 2),
			"finished_cost": flt(finished, 2),
			"selling_amount": flt(selling_amount, 2),
			"gross_profit": flt(gross_profit, 2),
			"margin_percent": pct(gross_profit, selling_amount),
			"currency": currency or spec.get("currency") or "",
			"quotation": quotation,
			"sales_order": (so_row or {}).get("sales_order") or "",
		})
	return out


def _resolve_selling(
	spec: Dict,
	so_row: Optional[Dict],
	quotation: str,
) -> tuple[float, str]:
	"""Selling price from SO, Quotation, or spec — never stock valuation."""
	qty = flt(spec.get("qty")) or 1
	currency = spec.get("currency") or ""

	if so_row:
		amount = flt(so_row.get("amount"))
		if amount:
			return amount, so_row.get("currency") or currency
		rate = flt(so_row.get("rate"))
		if rate:
			return rate * flt(so_row.get("qty") or qty), so_row.get("currency") or currency

	if quotation:
		amount = _quotation_selling_amount(quotation, spec["name"], qty)
		if amount:
			return amount, currency

	amount = flt(spec.get("amount"))
	if amount:
		return amount, currency
	return flt(spec.get("rate_per_piece")) * qty, currency


def _quotation_selling_amount(quotation: str, spec_name: str, qty: float) -> float:
	qi = DocType("Quotation Item")
	row = (
		frappe.qb.from_(qi)
		.select(qi.amount, qi.rate, qi.qty)
		.where(qi.parent == quotation)
		.where(qi.gf_glass_specification == spec_name)
		.limit(1)
	).run(as_dict=True)
	if not row:
		return 0
	item = row[0]
	amount = flt(item.get("amount"))
	if amount:
		return amount
	return flt(item.get("rate")) * flt(item.get("qty") or qty)


def _operation_pricing_by_spec(spec_names: List[str]) -> Dict[str, List[Dict]]:
	if not spec_names:
		return {}
	child = DocType("Glass Spec Operation Pricing")
	parent = DocType("Glass Product Specification")
	rows = (
		frappe.qb.from_(child)
		.inner_join(parent)
		.on(child.parent == parent.name)
		.select(parent.name.as_("spec_name"), child.operation, child.pricing_basis, child.quantity)
		.where(parent.name.isin(spec_names))
		.run(as_dict=True)
	)
	out: Dict[str, List[Dict]] = {}
	for r in rows:
		out.setdefault(r["spec_name"], []).append(r)
	return out
