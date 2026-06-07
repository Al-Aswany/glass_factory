from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from glass_factory.glass_factory.stock_posting import (
	_allocate_cutting_repack_rates,
	_item_area_m2,
)


class TestStockPosting(IntegrationTestCase):
	def test_item_area_m2_from_glass_code(self):
		self.assertEqual(_item_area_m2("GLS-CLEAR-8MM-1200X800-CUT"), flt(1200 * 800 / 1_000_000, 6))
		self.assertEqual(_item_area_m2("GLS-CLEAR-8MM-1500X900-REM"), flt(1500 * 900 / 1_000_000, 6))

	def test_allocate_cutting_repack_rates_by_area(self):
		se = frappe.new_doc("Stock Entry")
		se.company = "_Test Company"
		se.posting_date = "2026-06-07"
		se.posting_time = "12:00:00"
		se.append(
			"items",
			{
				"item_code": "GLS-CLEAR-8MM-3210X2250",
				"s_warehouse": "Stores - _TC",
				"qty": 1,
				"transfer_qty": 1,
			},
		)
		se.append(
			"items",
			{
				"item_code": "GLS-CLEAR-8MM-1200X800-CUT",
				"t_warehouse": "Glass Cut WIP - _TC",
				"qty": 1,
				"transfer_qty": 1,
				"is_finished_item": 1,
				"gf_source_item_role": "Cut WIP",
			},
		)
		se.append(
			"items",
			{
				"item_code": "GLS-CLEAR-8MM-2000X1000-REM",
				"t_warehouse": "Glass Remnants - _TC",
				"qty": 1,
				"transfer_qty": 1,
				"is_finished_item": 1,
				"gf_source_item_role": "Remnant",
			},
		)
		cutting_job = frappe._dict({"pieces": []})

		with patch("glass_factory.glass_factory.stock_posting._source_row_rate", return_value=1000):
			_allocate_cutting_repack_rates(se, cutting_job)

		cut_row = se.items[1]
		remnant_row = se.items[2]
		self.assertEqual(len(se.items), 3)
		cut_area = _item_area_m2(cut_row.item_code)
		remnant_area = _item_area_m2(remnant_row.item_code)
		total_area = cut_area + remnant_area

		self.assertAlmostEqual(cut_row.basic_rate, flt(1000 * cut_area / total_area))
		self.assertAlmostEqual(remnant_row.basic_rate, flt(1000 * remnant_area / total_area))
		self.assertAlmostEqual(cut_row.basic_amount, flt(cut_row.basic_rate) * flt(cut_row.transfer_qty), places=2)
		self.assertAlmostEqual(remnant_row.basic_amount, flt(remnant_row.basic_rate) * flt(remnant_row.transfer_qty), places=2)
		self.assertTrue(cut_row.set_basic_rate_manually)
		self.assertTrue(remnant_row.set_basic_rate_manually)
