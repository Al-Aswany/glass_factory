"""Stock Entry hooks for glass factory repack valuation."""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt

from glass_factory.glass_factory.item_resolver import item_role
from glass_factory.glass_factory.stock_posting import (
	_allocate_cutting_repack_rates,
	_sync_manual_row_amounts,
)


def prepare_glass_stock_entry(doc, method=None):
	"""Ensure glass repack rows have valuation before ERPNext rate calculation."""
	if doc.doctype != "Stock Entry" or not cint(doc.get("gf_created_by_glass_factory")):
		return

	flow = doc.get("gf_glass_stock_flow")
	if flow == "Raw to Cut WIP":
		_prepare_cutting_repack(doc)
	elif flow == "Cut WIP to Final":
		_backfill_cut_wip_bin_from_repack1(doc)


def _prepare_cutting_repack(doc) -> None:
	cutting_job_name = doc.get("gf_cutting_job")
	if not cutting_job_name or not frappe.db.exists("Cutting Job", cutting_job_name):
		_sync_manual_row_amounts(doc)
		return

	needs_allocation = any(
		row.is_finished_item
		and row.t_warehouse
		and not row.s_warehouse
		and not row.allow_zero_valuation_rate
		and flt(row.basic_rate) <= 0
		for row in doc.items
	)
	if needs_allocation:
		cutting_job = frappe.get_doc("Cutting Job", cutting_job_name)
		_allocate_cutting_repack_rates(doc, cutting_job)
	else:
		_sync_manual_row_amounts(doc)


def _backfill_cut_wip_bin_from_repack1(doc) -> None:
	"""Use Repack #1 posted rates when Cut WIP bin valuation is still zero."""
	processing_job_name = doc.get("gf_processing_job")
	if not processing_job_name:
		return

	cutting_job_name = frappe.db.get_value("Glass Processing Job", processing_job_name, "cutting_job")
	if not cutting_job_name:
		return

	repack1_name = frappe.db.get_value("Cutting Job", cutting_job_name, "linked_stock_entry")
	if not repack1_name or frappe.db.get_value("Stock Entry", repack1_name, "docstatus") != 1:
		return

	for row in doc.items:
		if not row.s_warehouse or item_role(row.item_code) != "Cut WIP":
			continue

		warehouse = row.s_warehouse
		bin_rate = flt(
			frappe.db.get_value(
				"Bin",
				{"item_code": row.item_code, "warehouse": warehouse},
				"valuation_rate",
			)
		)
		if bin_rate > 0:
			continue

		repack_rate = _repack1_incoming_rate(repack1_name, row.item_code)
		if repack_rate <= 0:
			continue

		bin_name = frappe.db.get_value("Bin", {"item_code": row.item_code, "warehouse": warehouse}, "name")
		if not bin_name:
			continue

		actual_qty = flt(frappe.db.get_value("Bin", bin_name, "actual_qty"))
		frappe.db.set_value(
			"Bin",
			bin_name,
			{
				"valuation_rate": repack_rate,
				"stock_value": flt(actual_qty) * repack_rate,
			},
			update_modified=False,
		)


def _repack1_incoming_rate(stock_entry_name: str, item_code: str) -> float:
	for fieldname in ("valuation_rate", "basic_rate"):
		rate = flt(
			frappe.db.get_value(
				"Stock Entry Detail",
				{"parent": stock_entry_name, "item_code": item_code, "t_warehouse": ["is", "set"]},
				fieldname,
			)
		)
		if rate > 0:
			return rate
	return 0


@frappe.whitelist()
def start_processing_from_stock_entry(stock_entry_name: str):
	"""Create or open the Processing Job related to a submitted cutting movement."""
	se = frappe.get_doc("Stock Entry", stock_entry_name)
	if se.docstatus != 1:
		frappe.throw("Submit the cutting stock movement before starting processing.")
	if se.get("gf_glass_stock_flow") != "Raw to Cut WIP" or not se.get("gf_cutting_job"):
		frappe.throw("Start Processing is only available from a submitted cutting stock movement.")
	job = frappe.get_doc("Cutting Job", se.gf_cutting_job)
	result = job.start_processing()
	processing_job = result.get("processing_job")
	if processing_job and se.get("gf_processing_job") != processing_job:
		frappe.db.set_value("Stock Entry", se.name, "gf_processing_job", processing_job, update_modified=False)
	return {"processing_job": processing_job}
