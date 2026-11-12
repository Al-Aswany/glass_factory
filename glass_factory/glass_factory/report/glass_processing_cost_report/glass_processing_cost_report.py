"""Glass Processing Cost script report — cost vs selling by operation."""
from __future__ import annotations

from typing import Dict, List

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt

from glass_factory.glass_factory.report.report_utils import (
	apply_creation_date_filter,
	totals_summary,
)
from glass_factory.glass_factory.spec_pricing import get_operation_cost_rate


def execute(filters=None):
	from glass_factory.glass_factory.authorization import require_cost_access

	require_cost_access()
	filters = filters or {}
	columns = _columns()
	data = _data(filters)
	currency = data[0].get("currency") if data else ""
	summary = totals_summary(
		data,
		currency=currency,
		field_map={
			"cost_amount": "Total Cost Amount",
			"selling_amount": "Total Selling Amount",
			"profit": "Total Profit",
		},
	)
	return columns, data, None, None, summary


def _columns() -> List[Dict]:
	return [
		{"fieldname": "specification", "label": _("Specification"), "fieldtype": "Link",
		 "options": "Glass Product Specification", "width": 150},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link",
		 "options": "Customer", "width": 130},
		{"fieldname": "company", "label": _("Company"), "fieldtype": "Link",
		 "options": "Company", "width": 110},
		{"fieldname": "operation", "label": _("Operation"), "fieldtype": "Data", "width": 110},
		{"fieldname": "pricing_basis", "label": _("Pricing Basis"), "fieldtype": "Data", "width": 120},
		{"fieldname": "quantity", "label": _("Qty / Area / Edge m"), "fieldtype": "Float",
		 "precision": 4, "width": 120},
		{"fieldname": "cost_rate", "label": _("Cost Rate"), "fieldtype": "Currency", "width": 100},
		{"fieldname": "cost_amount", "label": _("Cost Amount"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "selling_rate", "label": _("Selling Rate"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "selling_amount", "label": _("Selling Amount"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "profit", "label": _("Profit"), "fieldtype": "Currency", "width": 100},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link",
		 "options": "Currency", "hidden": 1},
	]


def _data(filters: Dict) -> List[Dict]:
	Spec = DocType("Glass Product Specification")
	Op = DocType("Glass Spec Operation Pricing")

	query = (
		frappe.qb.from_(Op)
		.inner_join(Spec)
		.on(Op.parent == Spec.name)
		.select(
			Spec.name.as_("specification"),
			Spec.customer,
			Spec.company,
			Spec.currency,
			Spec.creation,
			Op.operation,
			Op.pricing_basis,
			Op.quantity,
			Op.rate,
			Op.amount,
			Op.cost_rate,
			Op.cost_amount,
		)
		.where(Spec.docstatus < 2)
	)
	if filters.get("company"):
		query = query.where(Spec.company == filters["company"])
	if filters.get("customer"):
		query = query.where(Spec.customer == filters["customer"])
	if filters.get("operation"):
		query = query.where(Op.operation == filters["operation"])
	query = apply_creation_date_filter(query, Spec, filters.get("from_date"), filters.get("to_date"))
	rows = query.orderby(Spec.creation, order=frappe.qb.desc).run(as_dict=True)

	out: List[Dict] = []
	for row in rows:
		out.append(build_operation_cost_row(row))
	return out


def build_operation_cost_row(row: Dict) -> Dict:
	"""Map a joined spec/operation-pricing row to report output."""
	currency = row.get("currency") or ""
	qty = flt(row.get("quantity"))
	# Prefer the internal cost stored on the specification row so the report, the form, and the
	# margin agree. Rows priced before cost columns existed fall back to the current cost rate.
	cost_rate = flt(row.get("cost_rate"))
	cost_amount = flt(row.get("cost_amount"))
	if not cost_rate and not cost_amount:
		cost_rate = get_operation_cost_rate(row["operation"], currency, row["pricing_basis"])
		cost_amount = flt(cost_rate * qty, 2)
	selling_rate = flt(row.get("rate"))
	selling_amount = flt(row.get("amount")) or flt(selling_rate * qty, 2)
	return {
		"specification": row["specification"],
		"customer": row.get("customer") or "",
		"company": row.get("company") or "",
		"operation": row.get("operation") or "",
		"pricing_basis": row.get("pricing_basis") or "",
		"quantity": qty,
		"cost_rate": cost_rate,
		"cost_amount": cost_amount,
		"selling_rate": selling_rate,
		"selling_amount": selling_amount,
		"profit": flt(selling_amount - cost_amount, 2),
		"currency": currency,
	}
