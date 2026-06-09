import unittest
from unittest.mock import patch

import frappe
from frappe.utils import flt

from glass_factory.glass_factory.piece_pricing import (
	apply_piece_rates,
	calculate_piece_rates,
	chargeable_area_m2,
	get_glass_rate_per_m2,
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

	def test_calculate_piece_rates_combines_glass_and_processing(self):
		piece = {
			"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
			"length_mm": 1000,
			"width_mm": 800,
			"process_polish": 1,
			"process_temper": 1,
		}
		with patch(
			"glass_factory.glass_factory.piece_pricing.get_glass_rate_per_m2",
			return_value=100,
		), patch(
			"glass_factory.glass_factory.piece_pricing._get_processing_rate_settings",
			return_value={
				"polish_rate_per_m2": 20,
				"bevel_rate_per_m2": 0,
				"holes_rate_per_m2": 0,
				"slots_rate_per_m2": 0,
				"temper_rate_per_m2": 30,
				"sandblast_rate_per_m2": 0,
				"laminate_rate_per_m2": 0,
			},
		), patch(
			"glass_factory.glass_factory.piece_pricing.chargeable_area_m2",
			return_value=0.8,
		):
			rates = calculate_piece_rates(piece)

		self.assertEqual(rates["glass_rate"], 80)
		self.assertEqual(rates["polish_rate"], 16)
		self.assertEqual(rates["temper_rate"], 24)
		self.assertEqual(rates["processing_rate"], 40)
		self.assertEqual(rates["rate"], 120)

	def test_calculate_piece_rates_accepts_child_document(self):
		doc = frappe.new_doc("Quotation")
		doc.append(
			"glass_pieces",
			{
				"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
				"length_mm": 1000,
				"width_mm": 800,
				"qty": 1,
			},
		)
		piece = doc.glass_pieces[0]

		with patch(
			"glass_factory.glass_factory.piece_pricing.get_glass_rate_per_m2",
			return_value=0,
		):
			rates = calculate_piece_rates(piece)
			updated = apply_piece_rates(piece)

		self.assertIn("rate", rates)
		self.assertIn("rate", updated)

	def test_get_glass_rate_per_m2_prorates_full_sheet_price(self):
		with patch(
			"glass_factory.glass_factory.piece_pricing.get_item_selling_rate",
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
