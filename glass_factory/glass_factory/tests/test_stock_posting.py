from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from glass_factory.glass_factory.stock_posting import (
	_allocate_cutting_repack_rates,
	_item_area_m2,
	_optimization_active,
	_validate_optimization_sheets,
	build_cutting_repack,
	build_processing_repack,
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
		cutting_job = frappe._dict(
			name="CJ-TRACE-001",
			source_sheets=[
				frappe._dict(idx=1, item_code="RAW-GLASS", warehouse="Stores - _TC", qty_consumed=1, source_role="Raw Sheet", batch_no="RAW-BATCH"),
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
			patch("glass_factory.glass_factory.stock_posting.ensure_output_batch", return_value="CUT-BATCH"), \
			patch("glass_factory.glass_factory.stock_posting.batch_row_fields", side_effect=lambda item, batch: {"batch_no": batch, "use_serial_batch_fields": 1} if batch else {}), \
			patch("glass_factory.glass_factory.stock_posting._allocate_cutting_repack_rates"):
			se = build_cutting_repack(cutting_job)

		self.assertTrue(se.items)
		self.assertTrue(all(row.gf_cutting_job == cutting_job.name for row in se.items))
		self.assertEqual(se.items[0].batch_no, "RAW-BATCH")
		self.assertEqual(se.items[1].batch_no, "CUT-BATCH")

	def test_cutting_repack_requires_source_sheet_batch(self):
		cutting_job = frappe._dict(
			name="CJ-NO-BATCH",
			source_sheets=[frappe._dict(idx=1, item_code="RAW-GLASS")],
			pieces=[frappe._dict(idx=1, cut_wip_item="CUT-GLASS", qty_required=1)],
		)

		with self.assertRaises(frappe.ValidationError):
			build_cutting_repack(cutting_job)

	def test_processing_repack_requires_completed_operations(self):
		processing_job = frappe._dict(
			name="GPJ-TRACE-001",
			inputs=[frappe._dict(idx=1, cut_wip_item="CUT-GLASS", qty=1)],
			outputs=[frappe._dict(idx=1, final_item="FINAL-GLASS", qty=1)],
			operations=[frappe._dict(idx=1, operation="POL", status="Pending")],
		)

		with self.assertRaises(frappe.ValidationError):
			build_processing_repack(processing_job)


def _optimized_job(**overrides):
	job = frappe._dict({
		"name": "CJ-OPT-1",
		"optimization_status": "Imported",
		"optimization_waste_area_m2": 1.5,
		"sales_orders": [],
		"source_sheets": [
			frappe._dict({
				"idx": 1,
				"item_code": "GLS-CLEAR-8MM-3210X2250",
				"warehouse": "Glass Raw Stock - _TC",
				"source_role": "Raw Sheet",
				"batch_no": "RAW-BATCH",
				"qty_consumed": 2,
			}),
		],
		"pieces": [
			frappe._dict({
				"idx": 1,
				"cut_wip_item": "GLS-CLEAR-8MM-1200X800-CUT",
				"target_warehouse": "Glass Cut WIP - _TC",
				"qty_cut": 3,
				"qty_required": 3,
				"sales_order": None,
				"sales_order_item": None,
				"glass_specification": None,
			}),
		],
		"optimization_used_sheets": [
			frappe._dict({"sheet_id": "SHEET-001", "used_qty": 1}),
		],
		"optimization_remnants": [
			frappe._dict({"source_sheet_id": "SHEET-001", "length_mm": 1000, "width_mm": 600, "qty": 1}),
			frappe._dict({"source_sheet_id": "SHEET-001", "length_mm": 500, "width_mm": 400, "qty": 2}),
		],
	})
	job.update(overrides)
	return job


def _patched_build(job):
	"""Run build_cutting_repack with all DB-touching helpers stubbed out."""
	def fake_role(item_code):
		return "Cut WIP" if item_code.endswith("-CUT") else "Raw Sheet"

	settings = frappe._dict({
		"raw_warehouse": "Glass Raw Stock - _TC",
		"cut_wip_warehouse": "Glass Cut WIP - _TC",
		"final_goods_warehouse": "Glass Final Goods - _TC",
		"remnants_warehouse": "Glass Remnants - _TC",
		"scrap_warehouse": "Glass Scrap - _TC",
	})
	base = "glass_factory.glass_factory.stock_posting."
	with patch(base + "_settings", return_value=settings), \
		patch(base + "_company_from_job", return_value="_Test Company"), \
		patch(base + "_stock_uom", return_value="Nos"), \
		patch(base + "item_role", side_effect=fake_role), \
		patch(base + "ensure_output_batch", side_effect=lambda item, job_name, role, *args, **kwargs: f"{item}-{role}-BATCH"), \
		patch(base + "batch_row_fields", side_effect=lambda item, batch: {"batch_no": batch, "use_serial_batch_fields": 1} if batch else {}), \
		patch(base + "ensure_remnant_item", side_effect=lambda item, length, width: f"{item}-{int(length)}X{int(width)}-REM"), \
		patch(base + "get_scrap_item", return_value="Glass Scrap"), \
		patch(base + "_allocate_cutting_repack_rates"):
		return build_cutting_repack(job)


class TestCuttingRepackOptimization(IntegrationTestCase):
	def test_optimization_active_detection(self):
		self.assertTrue(_optimization_active(_optimized_job()))
		self.assertFalse(_optimization_active(_optimized_job(optimization_status="Exported")))
		self.assertFalse(_optimization_active(_optimized_job(optimization_used_sheets=[])))

	def test_validate_optimization_sheets_rejects_unknown_id(self):
		job = _optimized_job()
		with self.assertRaises(frappe.ValidationError):
			_validate_optimization_sheets(job, {"SHEET-002": 1})

	def test_used_qty_overrides_manual_consumption(self):
		se = _patched_build(_optimized_job())
		raw_row = next(r for r in se.items if r.item_code == "GLS-CLEAR-8MM-3210X2250")
		# manual qty_consumed is 2, but used_sheets says 1
		self.assertEqual(flt(raw_row.qty), 1)
		self.assertEqual(flt(raw_row.transfer_qty), 1)

	def test_one_stock_row_per_optimization_remnant(self):
		se = _patched_build(_optimized_job())
		remnant_rows = [r for r in se.items if r.gf_source_item_role == "Remnant"]
		self.assertEqual(len(remnant_rows), 2)
		codes = {r.item_code for r in remnant_rows}
		self.assertIn("GLS-CLEAR-8MM-3210X2250-1000X600-REM", codes)
		self.assertIn("GLS-CLEAR-8MM-3210X2250-500X400-REM", codes)
		qty_by_code = {r.item_code: flt(r.qty) for r in remnant_rows}
		self.assertEqual(qty_by_code["GLS-CLEAR-8MM-3210X2250-500X400-REM"], 2)

	def test_scrap_row_sized_from_waste_area(self):
		se = _patched_build(_optimized_job())
		scrap_rows = [r for r in se.items if r.gf_source_item_role == "Scrap"]
		self.assertEqual(len(scrap_rows), 1)
		self.assertEqual(flt(scrap_rows[0].qty), 1.5)

	def test_no_scrap_row_when_waste_is_zero(self):
		se = _patched_build(_optimized_job(optimization_waste_area_m2=0))
		self.assertFalse([r for r in se.items if r.gf_source_item_role == "Scrap"])

	def test_without_optimization_uses_manual_fields(self):
		job = _optimized_job(
			optimization_status="Exported",
			optimization_remnants=[],
			optimization_waste_area_m2=0,
		)
		se = _patched_build(job)
		raw_row = next(r for r in se.items if r.item_code == "GLS-CLEAR-8MM-3210X2250")
		# falls back to manual qty_consumed of 2
		self.assertEqual(flt(raw_row.qty), 2)
		self.assertFalse([r for r in se.items if r.gf_source_item_role in ("Remnant", "Scrap")])
