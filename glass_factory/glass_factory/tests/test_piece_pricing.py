import unittest
from unittest.mock import patch

import frappe
from frappe.utils import flt

from glass_factory.glass_factory.piece_pricing import (
	chargeable_area_m2,
	get_buying_price_list,
	get_glass_rate_per_m2,
	get_item_buying_rate,
	get_item_selling_rate,
	get_raw_glass_selling_rate_per_m2,
)


class TestPiecePricing(unittest.TestCase):
	def test_chargeable_area_uses_minimum_from_settings(self):
		with patch(
			"glass_factory.glass_factory.piece_pricing._settings_value",
			return_value=0.05,
		):
			self.assertEqual(chargeable_area_m2(100, 100), 0.05)

	def test_chargeable_area_uses_actual_area_when_larger(self):
		with patch(
			"glass_factory.glass_factory.piece_pricing._settings_value",
			return_value=0.05,
		):
			self.assertEqual(chargeable_area_m2(1000, 1000), flt(1.0, 6))

	def test_raw_selling_rate_reads_the_area_uom_price(self):
		with patch(
			"glass_factory.glass_factory.settings_validation.get_area_uom",
			return_value="Square Meter",
		), patch(
			"glass_factory.glass_factory.piece_pricing.frappe.db.get_value",
			return_value=80,
		) as get_value:
			rate = get_raw_glass_selling_rate_per_m2("GLS-CLEAR-8MM-3210X2250", "Standard Selling", currency="TZS")

		self.assertEqual(rate, 80)
		filters = get_value.call_args.args[1]
		self.assertEqual(filters["uom"], "Square Meter")
		self.assertEqual(filters["selling"], 1)
		self.assertEqual(filters["currency"], "TZS")

	def test_item_selling_rate_uses_the_selling_price_list(self):
		with patch(
			"glass_factory.glass_factory.piece_pricing.frappe.db.get_value",
			return_value=45,
		) as get_value:
			rate = get_item_selling_rate("GFV-ORDINARY", "Standard Selling")

		self.assertEqual(rate, 45)
		self.assertEqual(get_value.call_args.args[0], "Item Price")
		self.assertEqual(get_value.call_args.args[1]["selling"], 1)

	def test_buying_price_list_prefers_the_company_list(self):
		with patch(
			"glass_factory.glass_factory.piece_pricing.frappe.get_cached_value",
			return_value="Company Buying",
		):
			self.assertEqual(get_buying_price_list("Other Glass Co"), "Company Buying")

	def test_get_glass_rate_per_m2_prorates_full_sheet_price(self):
		with patch(
			"glass_factory.glass_factory.piece_pricing.get_item_buying_rate",
			return_value=1000,
		), patch(
			"glass_factory.glass_factory.piece_pricing.frappe.get_cached_doc"
		) as get_cached_doc, patch(
			"glass_factory.glass_factory.piece_pricing.get_item_glass_meta",
			return_value={"gf_length_mm": 3210, "gf_width_mm": 2250},
		):
			get_cached_doc.return_value.stock_uom = "Nos"
			rate_per_m2 = get_glass_rate_per_m2("GLS-CLEAR-8MM-3210X2250")

		sheet_area = 3210 * 2250 / 1_000_000
		self.assertAlmostEqual(rate_per_m2, flt(1000 / sheet_area, 6))

	def test_get_item_buying_rate_uses_buying_item_price(self):
		item_code = "GLS-CLEAR-8MM-3210X2250"
		price_list = "Standard Buying"
		if not frappe.db.exists("Item", item_code):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		with patch(
			"glass_factory.glass_factory.piece_pricing.frappe.db.get_value",
			side_effect=lambda doctype, filters, fieldname, **kwargs: (
				500
				if doctype == "Item Price"
				and filters.get("buying") == 1
				and filters.get("price_list") == price_list
				else None
			),
		):
			rate = get_item_buying_rate(item_code, price_list)
		self.assertEqual(rate, 500)
