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


def compute_processing_job_line_costs(processing_job, company: str) -> dict[str, float]:
	"""Internal processing cost of a Processing Job, by Sales Order row."""
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

	costs: dict[str, float] = {}
	for operation in processing_job.get("operations") or []:
		operation_name = OPERATION_CODE_TO_NAME.get(operation.get("operation"), operation.get("operation"))
		pricing_basis = OPERATION_PRICING_BASIS.get(operation_name)
		if not pricing_basis:
			continue

		cost_rate = get_operation_cost_rate(operation_name, currency, pricing_basis)
		line = operation.get("sales_order_item") or ""
		dims = input_dims.get(line, {})
		unit_qty = _operation_unit_quantity(
			operation_name,
			pricing_basis,
			length_mm=dims.get("length_mm", 0),
			width_mm=dims.get("width_mm", 0),
			operation_count=flt(operation.get("operation_count") or 1),
		)
		piece_qty = flt(operation.get("qty") or 0)
		costs[line] = flt(costs.get(line, 0) + flt(cost_rate * unit_qty * piece_qty, 2), 2)

	return costs


def compute_processing_job_internal_cost(processing_job, company: str) -> float:
	"""Return total internal processing cost for a Processing Job using cost rates."""
	return flt(sum(compute_processing_job_line_costs(processing_job, company).values()), 2)


def cost_rate_is_specified(row) -> bool:
	"""True when the cost rate was entered on purpose, including an intentional zero.

	A positive rate is specified even if the tick was never saved, because it cannot be the
	seeded default. A zero is specified only when Cost Rate Set is ticked.
	"""
	if cint(row.get("cost_rate_set")):
		return True
	return flt(row.get("cost_rate")) > 0


def missing_operation_cost_rates(processing_job, company: str) -> list[str]:
	"""Operations on this job whose cost rate is still the seeded default."""
	currency = frappe.get_cached_value("Company", company, "default_currency") or "USD"
	settings = frappe.get_single("Glass Factory Settings")
	index = {}
	for row in settings.get("operation_rates") or []:
		if row.get("operation") and cint(row.get("enabled")):
			index[(row.operation, row.currency, row.pricing_basis)] = row

	missing: list[str] = []
	seen: set[str] = set()
	for operation in processing_job.get("operations") or []:
		name = OPERATION_CODE_TO_NAME.get(operation.get("operation"), operation.get("operation"))
		basis = OPERATION_PRICING_BASIS.get(name)
		if not name or not basis or name in seen:
			continue
		seen.add(name)
		row = index.get((name, currency, basis))
		if row is None or not cost_rate_is_specified(row):
			missing.append(f"{name} ({basis}, {currency})")
	return missing


def validate_processing_cost_expense_account(settings, processing_cost: float, company: str | None = None) -> str:
	"""Validate the capitalization account and return it.

	Called when a final stock movement is about to be posted. A zero processing cost
	has nothing to post, so the account is not required in that case.
	"""
	if not cint(settings.get("capitalize_processing_costs")):
		return ""

	if flt(processing_cost) <= 0:
		return ""

	from glass_factory.glass_factory.settings_validation import (
		get_operating_company,
		valuation_account_errors,
	)

	posting_company = company or settings.get("company") or get_operating_company()
	errors = valuation_account_errors(settings.get("processing_cost_expense_account"), posting_company)
	if errors:
		frappe.throw("<br>".join(errors), title="Processing Cost Expense Account")
	return settings.get("processing_cost_expense_account")
