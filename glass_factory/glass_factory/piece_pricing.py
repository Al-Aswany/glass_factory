"""Auto pricing for Quotation Glass Piece rows."""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt

from glass_factory.glass_factory.item_resolver import get_item_glass_meta

PROCESS_RATE_FIELDS = (
	("process_polish", "polish_rate_per_m2"),
	("process_bevel", "bevel_rate_per_m2"),
	("process_holes", "holes_rate_per_m2"),
	("process_slots", "slots_rate_per_m2"),
	("process_temper", "temper_rate_per_m2"),
	("process_sandblast", "sandblast_rate_per_m2"),
	("process_laminate", "laminate_rate_per_m2"),
)

AREA_UOMS = frozenset({"sq m", "sqm", "square meter", "m2", "m²"})


def _piece_as_dict(piece) -> frappe._dict:
	if isinstance(piece, dict):
		return frappe._dict(piece)
	return frappe._dict(piece.as_dict())


def calculate_piece_rates(piece, price_list: str | None = None, company: str | None = None) -> dict:
	"""Return glass, processing, and total rates for one glass piece row."""
	piece = _piece_as_dict(piece)
	length_mm = flt(piece.length_mm)
	width_mm = flt(piece.width_mm)

	if length_mm <= 0 or width_mm <= 0:
		return _empty_rates()

	chargeable_area = chargeable_area_m2(length_mm, width_mm)
	glass_rate_per_m2 = get_glass_rate_per_m2(piece.raw_sheet_item, price_list, company)
	glass_rate = flt(glass_rate_per_m2 * chargeable_area, 2)

	processing_rates = calculate_processing_rates(piece, chargeable_area)
	processing_rate = flt(sum(processing_rates.values()), 2)

	return {
		"glass_rate": glass_rate,
		**processing_rates,
		"processing_rate": processing_rate,
		"rate": flt(glass_rate + processing_rate, 2),
	}


def chargeable_area_m2(length_mm: float, width_mm: float) -> float:
	area_m2 = flt((flt(length_mm) * flt(width_mm)) / 1_000_000, 6)
	min_area = flt(_settings_value("min_chargeable_area_m2")) or 0
	return max(area_m2, min_area)


def get_buying_price_list(company: str | None = None, currency: str | None = None) -> str | None:
	# 1. Company's configured buying price list
	if company:
		price_list = frappe.get_cached_value("Company", company, "buying_price_list")
		if price_list:
			return price_list

	# 2. Settings default buying price list
	if frappe.db.exists("DocType", "Glass Factory Settings"):
		settings_pl = frappe.db.get_single_value("Glass Factory Settings", "default_buying_price_list")
		if settings_pl:
			return settings_pl

	# 3. Any enabled buying price list matching the currency
	filters = {"buying": 1, "enabled": 1}
	if currency:
		filters["currency"] = currency
	return frappe.db.get_value("Price List", filters, "name", order_by="creation desc")


def get_glass_rate_per_m2(item_code: str | None, price_list: str | None = None, company: str | None = None) -> float:
	if not item_code:
		return 0

	buying_price_list = get_buying_price_list(company) or price_list
	unit_rate = get_item_buying_rate(item_code, buying_price_list, company)
	if unit_rate <= 0:
		return 0

	item = frappe.get_cached_doc("Item", item_code)
	stock_uom = (item.stock_uom or "").strip().lower()
	if stock_uom in AREA_UOMS:
		return unit_rate

	meta = get_item_glass_meta(item_code)
	sheet_area = flt(meta.get("gf_length_mm")) * flt(meta.get("gf_width_mm")) / 1_000_000
	if sheet_area > 0:
		return flt(unit_rate / sheet_area, 6)
	return unit_rate


def get_item_selling_rate(item_code: str, price_list: str | None = None, company: str | None = None) -> float:
	if price_list:
		filters = {"item_code": item_code, "price_list": price_list, "selling": 1}

		rate = frappe.db.get_value(
			"Item Price",
			filters,
			"price_list_rate",
			order_by="valid_from desc, creation desc",
		)
		if rate:
			return flt(rate)

	return flt(frappe.db.get_value("Item", item_code, "standard_rate"))


def get_item_buying_rate(
	item_code: str,
	price_list: str | None = None,
	company: str | None = None,
	*,
	currency: str | None = None,
) -> float:
	if not price_list and company:
		price_list = get_buying_price_list(company, currency)

	if price_list:
		rate = frappe.db.get_value(
			"Item Price",
			{"item_code": item_code, "price_list": price_list, "buying": 1},
			"price_list_rate",
			order_by="valid_from desc, creation desc",
		)
		if rate:
			return flt(rate)

	if currency:
		rate = frappe.db.get_value(
			"Item Price",
			{"item_code": item_code, "buying": 1, "currency": currency},
			"price_list_rate",
			order_by="valid_from desc, creation desc",
		)
		if rate:
			return flt(rate)

	last_purchase_rate = frappe.db.get_value("Item", item_code, "last_purchase_rate")
	if last_purchase_rate:
		return flt(last_purchase_rate)

	return 0


def calculate_processing_rates(piece, chargeable_area: float) -> dict[str, float]:
	settings = _get_processing_rate_settings()
	rates = {}
	for process_field, settings_field in PROCESS_RATE_FIELDS:
		rate_field = process_field.replace("process_", "") + "_rate"
		if cint(piece.get(process_field)):
			rates[rate_field] = flt(flt(settings.get(settings_field)) * chargeable_area, 2)
		else:
			rates[rate_field] = 0
	return rates


def apply_piece_rates(piece, price_list: str | None = None, company: str | None = None) -> dict:
	"""Calculate rates and merge them into the piece dict."""
	updated = _piece_as_dict(piece)
	updated.update(calculate_piece_rates(piece, price_list=price_list, company=company))
	return updated


def _get_processing_rate_settings() -> dict[str, float]:
	if not frappe.db.exists("DocType", "Glass Factory Settings"):
		return {}
	settings = frappe.get_single("Glass Factory Settings")
	return {settings_field: flt(settings.get(settings_field)) for _, settings_field in PROCESS_RATE_FIELDS}


def _settings_value(fieldname: str):
	if frappe.db.exists("DocType", "Glass Factory Settings"):
		return frappe.db.get_single_value("Glass Factory Settings", fieldname)
	return None


def _empty_rates() -> dict:
	rates = {
		"glass_rate": 0,
		"processing_rate": 0,
		"rate": 0,
	}
	for process_field, _ in PROCESS_RATE_FIELDS:
		rates[process_field.replace("process_", "") + "_rate"] = 0
	return rates
