"""Glass Cutting Yield and Waste script report."""
from __future__ import annotations

from typing import Dict, List

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt

from glass_factory.glass_factory.report.report_utils import (
	apply_creation_date_filter,
	pct,
	raw_sheet_area_m2,
	totals_summary,
)


def execute(filters=None):
	filters = filters or {}
	columns = _columns()
	data = _data(filters)
	summary = totals_summary(
		data,
		field_map={
			"waste_area_m2": "Total Waste Area (m²)",
		},
	)
	if data:
		avg_waste = sum(flt(r.get("waste_percent")) for r in data) / len(data)
		summary.append({
			"label": _("Average Waste %"),
			"value": f"{avg_waste:.2f}%",
			"indicator": "Orange",
			"datatype": "Data",
		})
	return columns, data, None, None, summary


def _columns() -> List[Dict]:
	return [
		{"fieldname": "cutting_job", "label": _("Cutting Job"), "fieldtype": "Link",
		 "options": "Cutting Job", "width": 130},
		{"fieldname": "company", "label": _("Company"), "fieldtype": "Link",
		 "options": "Company", "width": 110},
		{"fieldname": "raw_sheet_item", "label": _("Raw Sheet Item"), "fieldtype": "Link",
		 "options": "Item", "width": 180},
		{"fieldname": "raw_dimensions", "label": _("Raw Dimensions (mm)"), "fieldtype": "Data", "width": 130},
		{"fieldname": "raw_area_m2", "label": _("Raw Area (m²)"), "fieldtype": "Float", "precision": 3, "width": 100},
		{"fieldname": "finished_area_m2", "label": _("Finished Area (m²)"), "fieldtype": "Float",
		 "precision": 3, "width": 120},
		{"fieldname": "qty_produced", "label": _("Qty Produced"), "fieldtype": "Float", "width": 100},
		{"fieldname": "waste_area_m2", "label": _("Waste Area (m²)"), "fieldtype": "Float",
		 "precision": 3, "width": 110},
		{"fieldname": "waste_percent", "label": _("Waste %"), "fieldtype": "Percent", "width": 80},
		{"fieldname": "source_warehouse", "label": _("Source Warehouse"), "fieldtype": "Link",
		 "options": "Warehouse", "width": 150},
		{"fieldname": "wip_warehouse", "label": _("WIP Warehouse"), "fieldtype": "Link",
		 "options": "Warehouse", "width": 150},
		{"fieldname": "cutting_stock_entry", "label": _("Cutting Stock Entry"), "fieldtype": "Link",
		 "options": "Stock Entry", "width": 140},
	]


def _data(filters: Dict) -> List[Dict]:
	CJ = DocType("Cutting Job")
	query = (
		frappe.qb.from_(CJ)
		.select(
			CJ.name,
			CJ.company,
			CJ.linked_stock_entry,
			CJ.optimization_waste_area_m2,
			CJ.creation,
		)
		.where(CJ.docstatus < 2)
		.where(CJ.status != "Cancelled")
	)
	if filters.get("company"):
		query = query.where(CJ.company == filters["company"])
	query = apply_creation_date_filter(query, CJ, filters.get("from_date"), filters.get("to_date"))
	jobs = query.orderby(CJ.creation, order=frappe.qb.desc).run(as_dict=True)

	if not jobs:
		return []

	job_names = [j["name"] for j in jobs]
	sources = _source_sheets_by_job(job_names)
	pieces = _pieces_by_job(job_names)
	wip_wh = _wip_warehouse()

	out: List[Dict] = []
	for job in jobs:
		source_rows = sources.get(job["name"], [])
		piece_rows = pieces.get(job["name"], [])
		if not source_rows and not piece_rows:
			continue

		raw_area = 0.0
		raw_item = ""
		raw_dims = "—"
		source_wh = ""
		for src in source_rows:
			area = raw_sheet_area_m2(src.get("length_mm"), src.get("width_mm"))
			if not area and src.get("item_code"):
				meta = frappe.db.get_value(
					"Item",
					src["item_code"],
					["gf_length_mm", "gf_width_mm"],
					as_dict=True,
				) or {}
				area = raw_sheet_area_m2(meta.get("gf_length_mm"), meta.get("gf_width_mm"))
			qty = flt(src.get("qty_consumed")) or 1
			raw_area += area * qty
			raw_item = raw_item or src.get("item_code") or ""
			source_wh = source_wh or src.get("warehouse") or ""
			length = int(flt(src.get("length_mm")))
			width = int(flt(src.get("width_mm")))
			if length and width:
				raw_dims = f"{length}×{width}"

		finished_area = sum(flt(p.get("total_area_m2") or p.get("area_m2")) for p in piece_rows)
		qty_produced = sum(flt(p.get("qty_cut")) for p in piece_rows)

		waste = flt(job.get("optimization_waste_area_m2"))
		if waste <= 0 and raw_area > 0:
			waste = max(raw_area - finished_area, 0)

		if filters.get("cutting_job") and job["name"] != filters["cutting_job"]:
			continue

		out.append({
			"cutting_job": job["name"],
			"company": job.get("company") or "",
			"raw_sheet_item": raw_item,
			"raw_dimensions": raw_dims,
			"raw_area_m2": flt(raw_area, 3),
			"finished_area_m2": flt(finished_area, 3),
			"qty_produced": qty_produced,
			"waste_area_m2": flt(waste, 3),
			"waste_percent": pct(waste, raw_area),
			"source_warehouse": source_wh,
			"wip_warehouse": wip_wh,
			"cutting_stock_entry": job.get("linked_stock_entry") or "",
		})
	return out


def _source_sheets_by_job(job_names: List[str]) -> Dict[str, List[Dict]]:
	child = DocType("Cutting Job Source Sheet")
	parent = DocType("Cutting Job")
	rows = (
		frappe.qb.from_(child)
		.inner_join(parent)
		.on(child.parent == parent.name)
		.select(
			parent.name.as_("job_name"),
			child.item_code,
			child.warehouse,
			child.length_mm,
			child.width_mm,
			child.qty_consumed,
		)
		.where(parent.name.isin(job_names))
		.run(as_dict=True)
	)
	out: Dict[str, List[Dict]] = {}
	for r in rows:
		out.setdefault(r["job_name"], []).append(r)
	return out


def _pieces_by_job(job_names: List[str]) -> Dict[str, List[Dict]]:
	child = DocType("Cutting Job Piece")
	parent = DocType("Cutting Job")
	rows = (
		frappe.qb.from_(child)
		.inner_join(parent)
		.on(child.parent == parent.name)
		.select(
			parent.name.as_("job_name"),
			child.area_m2,
			child.total_area_m2,
			child.qty_cut,
		)
		.where(parent.name.isin(job_names))
		.run(as_dict=True)
	)
	out: Dict[str, List[Dict]] = {}
	for r in rows:
		out.setdefault(r["job_name"], []).append(r)
	return out


def _wip_warehouse() -> str:
	if frappe.db.exists("DocType", "Glass Factory Settings"):
		return frappe.db.get_single_value("Glass Factory Settings", "cut_wip_warehouse") or ""
	return ""
