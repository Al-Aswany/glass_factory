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

	def test_cutting_job_is_copied_to_stock_entry_detail_rows(self):
		from glass_factory.glass_factory.stock_posting import build_cutting_repack

		cutting_job = frappe._dict(
			name="CJ-TRACE-001",
			source_sheets=[
				frappe._dict(idx=1, item_code="RAW-GLASS", warehouse="Stores - _TC", qty_consumed=1, source_role="Raw Sheet"),
			],
			pieces=[
				frappe._dict(
					idx=1,
					cut_wip_item="CUT-GLASS",
					sales_order="SO-TRACE-001",
					sales_order_item="SOI-TRACE-001",
					glass_specification="CLEAR|8|500|300|CUT",
					qty_required=1,
					qty_cut=1,
				),
			],
		)

		with patch("glass_factory.glass_factory.stock_posting._settings", return_value=frappe._dict({"raw_warehouse": "Stores - _TC", "cut_wip_warehouse": "WIP - _TC"})), \
			patch("glass_factory.glass_factory.stock_posting._company_from_job", return_value="_Test Company"), \
			patch("glass_factory.glass_factory.stock_posting.item_role", side_effect=lambda item: "Raw Sheet" if item == "RAW-GLASS" else "Cut WIP"), \
			patch("glass_factory.glass_factory.stock_posting._stock_uom", return_value="Nos"), \
			patch("glass_factory.glass_factory.stock_posting._allocate_cutting_repack_rates"):
			se = build_cutting_repack(cutting_job)

		self.assertTrue(se.items)
		self.assertTrue(all(row.gf_cutting_job == cutting_job.name for row in se.items))
