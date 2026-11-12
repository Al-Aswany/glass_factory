"""Stock Entry hooks for glass factory repacks."""

from __future__ import annotations

import frappe

from glass_factory.glass_factory.authorization import require_doc_permission
from glass_factory.glass_factory.stock_movements import before_cancel_stock_entry, on_cancel_stock_entry
from glass_factory.glass_factory.stock_valuation import existing_scrap_item, finished_rows, glass_flow, is_scrap_row


def prepare_glass_stock_entry(doc, method=None):
	"""Mark the finished rows of a glass repack as rated by Glass Factory.

	ERPNext insists on manually rated finished rows when a Repack has several finished Items.
	The rates themselves are derived from the consumed rows by ``GlassStockEntryMixin`` every
	time ERPNext recalculates the entry, so they also follow later re-valuation of the source stock.
	"""
	if not glass_flow(doc):
		return
	scrap_item = existing_scrap_item()
	for row in finished_rows(doc):
		row.set_basic_rate_manually = 1
		if is_scrap_row(row, scrap_item):
			row.allow_zero_valuation_rate = 1


def before_cancel_glass_stock_entry(doc, method=None):
	before_cancel_stock_entry(doc)


def on_cancel_glass_stock_entry(doc, method=None):
	on_cancel_stock_entry(doc)


@frappe.whitelist()
def start_processing_from_stock_entry(stock_entry_name: str):
	"""Create or open the Processing Job related to a submitted cutting movement."""
	se = frappe.get_doc("Stock Entry", stock_entry_name)
	require_doc_permission(se, "read")
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
