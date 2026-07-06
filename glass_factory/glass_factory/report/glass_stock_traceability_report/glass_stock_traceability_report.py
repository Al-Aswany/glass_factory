"""Glass Stock Traceability script report."""
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
	fetch_so_for_spec,
)


def execute(filters=None):
	filters = filters or {}
	columns = _columns()
	data = _data(filters)
	return columns, data


def _columns() -> List[Dict]:
	return [
		{"fieldname": "specification", "label": _("Specification"), "fieldtype": "Link",
		 "options": "Glass Product Specification", "width": 150},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link",
		 "options": "Customer", "width": 130},
		{"fieldname": "sales_order", "label": _("Sales Order"), "fieldtype": "Link",
		 "options": "Sales Order", "width": 130},
		{"fieldname": "raw_item", "label": _("Raw Item"), "fieldtype": "Link",
		 "options": "Item", "width": 170},
		{"fieldname": "wip_item", "label": _("WIP Item"), "fieldtype": "Link",
		 "options": "Item", "width": 170},
		{"fieldname": "finished_item", "label": _("Finished Item"), "fieldtype": "Link",
		 "options": "Item", "width": 170},
		{"fieldname": "raw_warehouse", "label": _("Raw Warehouse"), "fieldtype": "Link",
		 "options": "Warehouse", "width": 140},
		{"fieldname": "wip_warehouse", "label": _("WIP Warehouse"), "fieldtype": "Link",
		 "options": "Warehouse", "width": 140},
		{"fieldname": "finished_warehouse", "label": _("Finished Warehouse"), "fieldtype": "Link",
		 "options": "Warehouse", "width": 150},
		{"fieldname": "cutting_stock_entry", "label": _("Cutting Stock Entry"), "fieldtype": "Link",
		 "options": "Stock Entry", "width": 140},
		{"fieldname": "processing_stock_entry", "label": _("Processing Stock Entry"), "fieldtype": "Link",
		 "options": "Stock Entry", "width": 150},
		{"fieldname": "delivery_note", "label": _("Delivery Note"), "fieldtype": "Link",
		 "options": "Delivery Note", "width": 130},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 130},
		{"fieldname": "company", "label": _("Company"), "fieldtype": "Link",
		 "options": "Company", "width": 110},
	]


def _data(filters: Dict) -> List[Dict]:
	Spec = DocType("Glass Product Specification")
	query = (
		frappe.qb.from_(Spec)
		.select(
			Spec.name,
			Spec.customer,
			Spec.company,
			Spec.raw_item_code,
			Spec.cut_wip_item_code,
			Spec.final_item_code,
			Spec.raw_sheet_item,
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

	settings = _glass_settings()
	out: List[Dict] = []
	for spec in spec_rows:
		so_row = fetch_so_for_spec(spec["name"])
		if filters.get("company") and so_row and so_row.get("company") != filters["company"]:
			if spec.get("company") != filters["company"]:
				continue
		row = build_traceability_row(spec, so_row, _glass_settings())
		if row:
			out.append(row)
	return out


def build_traceability_row(
	spec: Dict,
	so_row: Dict | None,
	settings: Dict | None = None,
) -> Dict:
	"""Build one stock-traceability row."""
	settings = settings or {}
	cutting_job = (so_row or {}).get("gf_cutting_job") or ""
	processing_job = (so_row or {}).get("gf_processing_job") or ""
	cutting_se = _linked_stock_entry("Cutting Job", cutting_job)
	processing_se = _linked_stock_entry("Glass Processing Job", processing_job)
	so_item = (so_row or {}).get("name") or ""
	delivery_note = fetch_delivery_note_for_so_item(so_item) if so_item else ""

	cj_status = frappe.db.get_value("Cutting Job", cutting_job, "status") if cutting_job else ""
	pj_status = frappe.db.get_value("Glass Processing Job", processing_job, "status") if processing_job else ""

	status = derive_production_stage(
		sales_order=(so_row or {}).get("sales_order") or "",
		qty_ordered=flt((so_row or {}).get("qty")),
		cut_qty=flt((so_row or {}).get("gf_cut_qty")),
		processed_qty=flt((so_row or {}).get("gf_processed_qty")),
		delivered_qty=flt((so_row or {}).get("gf_delivered_qty")),
		cutting_job_status=cj_status or "",
		processing_job_status=pj_status or "",
		has_cutting_job=bool(cutting_job),
		has_processing_job=bool(processing_job),
	)

	return {
		"specification": spec["name"],
		"customer": spec.get("customer") or (so_row or {}).get("customer") or "",
		"sales_order": (so_row or {}).get("sales_order") or "",
		"raw_item": spec.get("raw_item_code") or spec.get("raw_sheet_item") or "",
		"wip_item": spec.get("cut_wip_item_code") or "",
		"finished_item": spec.get("final_item_code") or "",
		"raw_warehouse": settings.get("raw_warehouse") or "",
		"wip_warehouse": settings.get("cut_wip_warehouse") or "",
		"finished_warehouse": settings.get("final_goods_warehouse") or "",
		"cutting_stock_entry": cutting_se,
		"processing_stock_entry": processing_se,
		"delivery_note": delivery_note,
		"status": status,
		"company": spec.get("company") or (so_row or {}).get("company") or "",
	}


def _linked_stock_entry(doctype: str, name: str) -> str:
	if not name:
		return ""
	return frappe.db.get_value(doctype, name, "linked_stock_entry") or ""


def _glass_settings() -> Dict:
	if not frappe.db.exists("DocType", "Glass Factory Settings"):
		return {}
	return frappe.db.get_value(
		"Glass Factory Settings",
		"Glass Factory Settings",
		["raw_warehouse", "cut_wip_warehouse", "final_goods_warehouse"],
		as_dict=True,
	) or {}
