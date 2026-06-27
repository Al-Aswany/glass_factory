"""Pricing engine for Glass Product Specification."""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt

from glass_factory.glass_factory.piece_pricing import chargeable_area_m2, get_item_selling_rate

AREA_OPERATIONS = frozenset({"Temper", "Sandblast", "Laminate"})
EDGE_OPERATIONS = frozenset({"Polish", "Bevel"})
UNIT_OPERATIONS = {
	"Hole": "hole_count",
	"Special Hole": "special_hole_count",
	"Slot": "slot_count",
	"Special Slot": "special_slot_count",
}

OPERATION_PRICING_BASIS = {
	"Polish": "Per Edge Meter",
	"Bevel": "Per Edge Meter",
	"Hole": "Per Unit",
	"Special Hole": "Per Unit",
	"Slot": "Per Unit",
	"Special Slot": "Per Unit",
	"Temper": "Per Square Meter",
	"Sandblast": "Per Square Meter",
	"Laminate": "Per Square Meter",
}

BOOLEAN_OPERATION_FIELDS = {
	"Polish": "polish",
	"Bevel": "bevel",
	"Temper": "temper",
	"Sandblast": "sandblast",
	"Laminate": "laminate",
}

LEGACY_AREA_RATE_FIELDS = {
	"Temper": "temper_rate_per_m2",
	"Sandblast": "sandblast_rate_per_m2",
	"Laminate": "laminate_rate_per_m2",
}


def get_spec_currency(spec) -> str:
	if spec.get("currency"):
		return spec.currency
	if spec.get("company"):
		return frappe.get_cached_value("Company", spec.company, "default_currency") or "USD"
	return "USD"


def get_operation_rate(operation: str, currency: str, pricing_basis: str) -> float:
	"""Return configured rate for an operation, preferring the flexible child table."""
	if frappe.db.exists("DocType", "Glass Factory Settings"):
		settings = frappe.get_single("Glass Factory Settings")
		for row in settings.get("operation_rates") or []:
			if (
				row.operation == operation
				and row.currency == currency
				and row.pricing_basis == pricing_basis
				and cint(row.enabled)
			):
				return flt(row.rate)

	legacy_field = LEGACY_AREA_RATE_FIELDS.get(operation)
	if legacy_field and pricing_basis == "Per Square Meter":
		return flt(settings.get(legacy_field)) if settings else 0

	# Missing rates default to 0; callers may surface setup warnings in refresh flows.
	return 0


def fetch_raw_sheet_rate(spec, *, fetch_from_item_price: bool = False) -> float:
	if flt(spec.get("raw_sheet_rate_per_piece")) > 0:
		return flt(spec.raw_sheet_rate_per_piece)

	if not fetch_from_item_price or not spec.get("raw_sheet_item"):
		return 0

	currency = get_spec_currency(spec)
	rate = get_item_selling_rate(spec.raw_sheet_item, spec.get("price_list"), spec.get("company"))
	if rate and currency:
		item_price_currency = frappe.db.get_value(
			"Item Price",
			{"item_code": spec.raw_sheet_item, "price_list": spec.get("price_list"), "selling": 1},
			"currency",
			order_by="valid_from desc, creation desc",
		)
		if item_price_currency and item_price_currency != currency:
			# Phase 3 does not convert currencies; only use the price when currencies match.
			return 0
	return flt(rate)


def calculate_spec_pricing(spec) -> None:
	calculate_processing_quantities(spec)
	calculate_raw_cost(spec)
	calculate_processing_amounts(spec)
	calculate_final_pricing(spec)


def calculate_processing_quantities(spec) -> None:
	length = flt(spec.length_mm)
	width = flt(spec.width_mm)
	area_m2 = flt(spec.area_m2)
	qty = flt(spec.qty)

	spec.edge_meter = flt((2 * (length + width)) / 1000, 6) if length > 0 and width > 0 else 0
	spec.chargeable_area_m2 = chargeable_area_m2(length, width) if length > 0 and width > 0 else 0
	spec.total_chargeable_area_m2 = flt(spec.chargeable_area_m2 * qty, 6) if qty > 0 else 0

	if area_m2 <= 0 and length > 0 and width > 0:
		spec.chargeable_area_m2 = chargeable_area_m2(length, width)


def calculate_raw_cost(spec) -> None:
	raw_rate = flt(spec.get("raw_sheet_rate_per_piece"))
	raw_area = flt(spec.get("raw_sheet_area_m2"))
	area_m2 = flt(spec.get("area_m2"))

	if raw_rate > 0 and raw_area <= 0:
		frappe.throw("Raw sheet dimensions are required to calculate raw cost per m².")

	if raw_area > 0 and raw_rate > 0:
		spec.raw_cost_per_m2 = flt(raw_rate / raw_area, 6)
	else:
		spec.raw_cost_per_m2 = 0

	spec.raw_cost_per_finished_piece = flt(spec.raw_cost_per_m2 * area_m2, 6) if area_m2 > 0 else 0


def calculate_processing_amounts(spec) -> None:
	currency = get_spec_currency(spec)
	chargeable_area = flt(spec.chargeable_area_m2)
	edge_meter = flt(spec.edge_meter)

	area_rate_total = 0.0
	for operation in AREA_OPERATIONS:
		fieldname = BOOLEAN_OPERATION_FIELDS[operation]
		if cint(spec.get(fieldname)):
			area_rate_total += get_operation_rate(
				operation, currency, OPERATION_PRICING_BASIS[operation]
			)

	edge_rate_total = 0.0
	for operation in EDGE_OPERATIONS:
		fieldname = BOOLEAN_OPERATION_FIELDS[operation]
		if cint(spec.get(fieldname)):
			edge_rate_total += get_operation_rate(
				operation, currency, OPERATION_PRICING_BASIS[operation]
			)

	unit_amount = 0.0
	for operation, count_field in UNIT_OPERATIONS.items():
		count = cint(spec.get(count_field))
		if count > 0:
			rate = get_operation_rate(operation, currency, OPERATION_PRICING_BASIS[operation])
			unit_amount += count * rate

	spec.area_processing_amount_per_piece = flt(chargeable_area * area_rate_total, 2)
	spec.edge_processing_amount_per_piece = flt(edge_meter * edge_rate_total, 2)
	spec.unit_processing_amount_per_piece = flt(unit_amount, 2)
	spec.processing_amount_per_piece = flt(
		spec.area_processing_amount_per_piece
		+ spec.edge_processing_amount_per_piece
		+ spec.unit_processing_amount_per_piece,
		2,
	)


def calculate_final_pricing(spec) -> None:
	area_m2 = flt(spec.get("area_m2"))
	qty = flt(spec.get("qty"))

	if area_m2 <= 0:
		frappe.throw("Area (m²) must be greater than zero to calculate pricing.")

	spec.calculated_amount_per_piece = flt(
		flt(spec.raw_cost_per_finished_piece) + flt(spec.processing_amount_per_piece),
		2,
	)
	spec.calculated_rate_per_m2 = flt(spec.calculated_amount_per_piece / area_m2, 6)

	manual_rate = flt(spec.get("manual_selling_rate_per_m2"))
	if manual_rate > 0:
		spec.selling_rate_per_m2 = manual_rate
		spec.price_override = 1
	else:
		spec.selling_rate_per_m2 = spec.calculated_rate_per_m2
		spec.price_override = 0

	spec.price_difference_per_m2 = flt(spec.selling_rate_per_m2 - spec.calculated_rate_per_m2, 6)
	spec.rate_per_piece = flt(spec.selling_rate_per_m2 * area_m2, 2)
	spec.amount = flt(spec.rate_per_piece * qty, 2)


def pricing_result(spec) -> dict:
	return {
		"raw_cost_per_m2": spec.raw_cost_per_m2,
		"raw_cost_per_finished_piece": spec.raw_cost_per_finished_piece,
		"edge_meter": spec.edge_meter,
		"chargeable_area_m2": spec.chargeable_area_m2,
		"total_chargeable_area_m2": spec.total_chargeable_area_m2,
		"area_processing_amount_per_piece": spec.area_processing_amount_per_piece,
		"edge_processing_amount_per_piece": spec.edge_processing_amount_per_piece,
		"unit_processing_amount_per_piece": spec.unit_processing_amount_per_piece,
		"processing_amount_per_piece": spec.processing_amount_per_piece,
		"calculated_amount_per_piece": spec.calculated_amount_per_piece,
		"calculated_rate_per_m2": spec.calculated_rate_per_m2,
		"manual_selling_rate_per_m2": spec.manual_selling_rate_per_m2,
		"selling_rate_per_m2": spec.selling_rate_per_m2,
		"price_override": spec.price_override,
		"price_difference_per_m2": spec.price_difference_per_m2,
		"rate_per_piece": spec.rate_per_piece,
		"amount": spec.amount,
		"raw_sheet_rate_per_piece": spec.raw_sheet_rate_per_piece,
	}
