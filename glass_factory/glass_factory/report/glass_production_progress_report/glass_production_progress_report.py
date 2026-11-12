"""Glass Production Progress script report."""
from __future__ import annotations

from typing import Dict, List

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt

from glass_factory.glass_factory.report.report_utils import (
	apply_creation_date_filter,
	derive_production_stage,
	fetch_delivery_note_for_so_item,
	fetch_linked_quotation,
)


def execute(filters=None):
	filters = filters or {}
	columns = _columns()
	data = _data(filters)
	return columns, data


def _columns() -> List[Dict]:
	return [
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link",
		 "options": "Customer", "width": 130},
		{"fieldname": "sales_order", "label": _("Sales Order"), "fieldtype": "Link",
		 "options": "Sales Order", "width": 130},
		{"fieldname": "specification", "label": _("Glass Product Specification"), "fieldtype": "Link",
		 "options": "Glass Product Specification", "width": 160},
		{"fieldname": "cutting_job", "label": _("Cutting Job"), "fieldtype": "Link",
		 "options": "Cutting Job", "width": 130},
		{"fieldname": "processing_job", "label": _("Processing Job"), "fieldtype": "Link",
		 "options": "Glass Processing Job", "width": 140},
		{"fieldname": "cutting_stock_entry", "label": _("Cutting Stock Entry"), "fieldtype": "Link",
		 "options": "Stock Entry", "width": 140},
		{"fieldname": "processing_stock_entry", "label": _("Processing Stock Entry"), "fieldtype": "Link",
		 "options": "Stock Entry", "width": 150},
		{"fieldname": "delivery_note", "label": _("Delivery Note"), "fieldtype": "Link",
		 "options": "Delivery Note", "width": 130},
		{"fieldname": "qty_ordered", "label": _("Qty Ordered"), "fieldtype": "Float", "width": 90},
		{"fieldname": "qty_cut", "label": _("Qty Cut"), "fieldtype": "Float", "width": 80},
		{"fieldname": "qty_processed", "label": _("Qty Processed"), "fieldtype": "Float", "width": 100},
		{"fieldname": "qty_delivered", "label": _("Qty Delivered"), "fieldtype": "Float", "width": 100},
		{"fieldname": "current_stage", "label": _("Current Stage"), "fieldtype": "Data", "width": 140},
		{"fieldname": "company", "label": _("Company"), "fieldtype": "Link",
		 "options": "Company", "width": 110},
	]


def _data(filters: Dict) -> List[Dict]:
	Spec = DocType("Glass Product Specification")
	query = (
		frappe.qb.from_(Spec)
		.select(Spec.name, Spec.customer, Spec.company, Spec.creation)
		.where(Spec.docstatus < 2)
	)
	if filters.get("company"):
		query = query.where(Spec.company == filters["company"])
	if filters.get("customer"):
		query = query.where(Spec.customer == filters["customer"])
	query = apply_creation_date_filter(query, Spec, filters.get("from_date"), filters.get("to_date"))
	spec_rows = query.orderby(Spec.creation, order=frappe.qb.desc).run(as_dict=True)

	out: List[Dict] = []
	for spec in spec_rows:
		so_row = _fetch_best_so_row(spec["name"], filters)
		row = build_progress_row(spec, so_row, filters)
		if row:
			out.append(row)
	return out


def build_progress_row(spec: Dict, so_row: Dict | None, filters: Dict | None = None) -> Dict:
	"""Build one production-progress row from spec + optional SO line."""
	filters = filters or {}
	quotation = fetch_linked_quotation(spec["name"])
	cutting_job = (so_row or {}).get("gf_cutting_job") or ""
	processing_job = (so_row or {}).get("gf_processing_job") or ""
	cutting_se = _linked_stock_entry("Cutting Job", cutting_job) if cutting_job else ""
	processing_se = _linked_stock_entry("Glass Processing Job", processing_job) if processing_job else ""
	so_item_name = (so_row or {}).get("name") or ""
	delivery_note = fetch_delivery_note_for_so_item(so_item_name) if so_item_name else ""

	cj_status = frappe.db.get_value("Cutting Job", cutting_job, "status") if cutting_job else ""
	pj_status = frappe.db.get_value("Glass Processing Job", processing_job, "status") if processing_job else ""

	# Ordered pieces (stock qty): cut, processed, and delivered are all piece counts.
	qty_ordered = flt((so_row or {}).get("stock_qty") or (so_row or {}).get("qty"))
	qty_cut = flt((so_row or {}).get("gf_cut_qty"))
	qty_processed = flt((so_row or {}).get("gf_processed_qty"))
	qty_delivered = flt((so_row or {}).get("gf_delivered_qty"))

	stage = derive_production_stage(
		quotation=quotation,
		sales_order=(so_row or {}).get("sales_order") or "",
		qty_ordered=qty_ordered,
		cut_qty=qty_cut,
		processed_qty=qty_processed,
		delivered_qty=qty_delivered,
		cutting_job_status=cj_status or "",
		processing_job_status=pj_status or "",
		has_cutting_job=bool(cutting_job),
		has_processing_job=bool(processing_job),
	)

	if filters.get("stage") and stage != filters["stage"]:
		return {}

	return {
		"customer": spec.get("customer") or (so_row or {}).get("customer") or "",
		"sales_order": (so_row or {}).get("sales_order") or "",
		"specification": spec["name"],
		"cutting_job": cutting_job,
		"processing_job": processing_job,
		"cutting_stock_entry": cutting_se,
		"processing_stock_entry": processing_se,
		"delivery_note": delivery_note,
		"qty_ordered": qty_ordered,
		"qty_cut": qty_cut,
		"qty_processed": qty_processed,
		"qty_delivered": qty_delivered,
		"current_stage": stage,
		"company": spec.get("company") or (so_row or {}).get("company") or "",
	}


def _fetch_best_so_row(spec_name: str, filters: Dict) -> Dict | None:
	soi = DocType("Sales Order Item")
	so = DocType("Sales Order")
	query = (
		frappe.qb.from_(soi)
		.inner_join(so)
		.on(soi.parent == so.name)
		.select(
			soi.name,
			soi.parent.as_("sales_order"),
			soi.qty,
			soi.stock_qty,
			soi.gf_cut_qty,
			soi.gf_processed_qty,
			soi.gf_delivered_qty,
			soi.gf_cutting_job,
			soi.gf_processing_job,
			so.customer,
			so.company,
		)
		.where(soi.gf_glass_specification == spec_name)
		.where(so.docstatus < 2)
	)
	if filters.get("company"):
		query = query.where(so.company == filters["company"])
	rows = query.orderby(so.creation, order=frappe.qb.desc).limit(1).run(as_dict=True)
	return rows[0] if rows else None


def _linked_stock_entry(doctype: str, name: str) -> str:
	if not name:
		return ""
	return frappe.db.get_value(doctype, name, "linked_stock_entry") or ""
