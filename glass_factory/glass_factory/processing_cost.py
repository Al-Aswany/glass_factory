"""Internal processing cost calculation for production stock valuation."""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt

from glass_factory.glass_factory.operation_rates import OPERATION_PRICING_BASIS
from glass_factory.glass_factory.piece_pricing import chargeable_area_m2
from glass_factory.glass_factory.spec_pricing import get_operation_cost_rate

OPERATION_CODE_TO_NAME = {
	"POL": "Polish",
	"BEV": "Bevel",
	"HOL": "Hole",
	"SHOL": "Special Hole",
	"SLT": "Slot",
	"SSLT": "Special Slot",
	"TMP": "Temper",
	"SBL": "Sandblast",
	"LAM": "Laminate",
}


def _edge_meter(length_mm: float, width_mm: float) -> float:
	length = flt(length_mm)
	width = flt(width_mm)
	if length <= 0 or width <= 0:
		return 0
	return flt((2 * (length + width)) / 1000, 6)


def _operation_unit_quantity(
	operation_name: str,
	pricing_basis: str,
	*,
	length_mm: float,
	width_mm: float,
	operation_count: float,
) -> float:
	if pricing_basis == "Per Edge Meter":
		return _edge_meter(length_mm, width_mm)
	if pricing_basis == "Per Square Meter":
		return chargeable_area_m2(length_mm, width_mm)
	if pricing_basis == "Per Unit":
		return flt(operation_count)
	return 0


def compute_processing_job_internal_cost(processing_job, company: str) -> float:
	"""Return total internal processing cost for a Processing Job using cost rates."""
	currency = frappe.get_cached_value("Company", company, "default_currency") or "USD"
	input_dims: dict[str, dict[str, float]] = {}
	for row in processing_job.get("inputs") or []:
		key = row.get("sales_order_item")
		if not key:
			continue
		input_dims[key] = {
			"length_mm": flt(row.get("length_mm")),
			"width_mm": flt(row.get("width_mm")),
		}

	total = 0.0
	for operation in processing_job.get("operations") or []:
		operation_name = OPERATION_CODE_TO_NAME.get(operation.get("operation"), operation.get("operation"))
		pricing_basis = OPERATION_PRICING_BASIS.get(operation_name)
		if not pricing_basis:
			continue

		cost_rate = get_operation_cost_rate(operation_name, currency, pricing_basis)
		dims = input_dims.get(operation.get("sales_order_item"), {})
		unit_qty = _operation_unit_quantity(
			operation_name,
			pricing_basis,
			length_mm=dims.get("length_mm", 0),
			width_mm=dims.get("width_mm", 0),
			operation_count=flt(operation.get("operation_count") or 1),
		)
		piece_qty = flt(operation.get("qty") or 0)
		total += flt(cost_rate * unit_qty * piece_qty, 2)

	return flt(total, 2)


def validate_processing_cost_expense_account(settings, processing_cost: float) -> str:
	"""Validate capitalization settings and return the expense account."""
	if not cint(settings.get("capitalize_processing_costs")):
		return ""

	if flt(processing_cost) <= 0:
		return ""

	expense_account = settings.get("processing_cost_expense_account")
	if not expense_account:
		frappe.throw(
			"Processing Cost Expense Account is required in Glass Factory Settings when "
			"processing cost capitalization is enabled and the job has processing cost."
		)
	if not frappe.db.exists("Account", expense_account):
		frappe.throw(
			f"Processing Cost Expense Account <b>{expense_account}</b> does not exist. "
			"Update Glass Factory Settings before posting the final stock movement."
		)
	return expense_account
