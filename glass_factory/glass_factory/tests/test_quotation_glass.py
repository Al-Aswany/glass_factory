import json
import unittest

import frappe

from glass_factory.glass_factory.quotation_glass import build_quotation_items_from_glass, processing_flags_from_piece


class TestQuotationGlass(unittest.TestCase):
	def test_processing_flags_from_piece_uses_fixed_order(self):
		piece = {
			"process_temper": 1,
			"process_holes": 1,
			"process_polish": 1,
		}
		self.assertEqual(processing_flags_from_piece(piece), "POL-HOL-TMP")

	def test_processing_flags_from_piece_empty(self):
		self.assertEqual(processing_flags_from_piece({}), "")

	def test_build_quotation_items_from_glass_returns_item_name_and_uom(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		result = build_quotation_items_from_glass(
			json.dumps([
				{
					"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
					"length_mm": 500,
					"width_mm": 300,
					"thickness_mm": 8,
					"qty": 3,
					"process_polish": 1,
				}
			])
		)
		self.assertEqual(len(result["items"]), 1)
		row = result["items"][0]
		self.assertTrue(row["item_code"])
		self.assertTrue(row["item_name"])
		self.assertTrue(row["uom"])
		self.assertEqual(row["qty"], 3)
