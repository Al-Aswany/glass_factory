"""Shared operation rate definitions for Glass Factory pricing."""

from __future__ import annotations

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


def default_operation_rate_rows(currency: str) -> list[dict]:
	"""Return default flexible operation rate rows for a currency (rates 0 until configured)."""
	return [
		{
			"operation": operation,
			"currency": currency,
			"pricing_basis": pricing_basis,
			"rate": 0,
			"cost_rate": 0,
			"enabled": 1,
		}
		for operation, pricing_basis in OPERATION_PRICING_BASIS.items()
	]
