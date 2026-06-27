import unittest

import frappe

from glass_factory.glass_factory.glass_optimizer import (
	EXPORT_TOP_LEVEL_KEYS,
	PIECE_KEYS,
	STOCK_SHEET_KEYS,
	apply_import_result,
	build_export_payload,
	validate_import_payload,
)


def _sample_job(**overrides):
	job = frappe._dict({
		"name": "CJ-0001",
		"pieces": [
			frappe._dict({
				"raw_sheet_item": "GLS-CLEAR-8MM-3660X2440",
				"final_item": "GLS-CLEAR-8MM-1200X800-POL",
				"length_mm": 1200,
				"width_mm": 800,
				"qty_required": 5,
				"qty_assigned": 5,
				"processing_flags": "POL",
			}),
		],
		"source_sheets": [
			frappe._dict({
				"length_mm": 3660,
				"width_mm": 2440,
				"qty_consumed": 2,
			}),
		],
	})
	job.update(overrides)
	return job


def _sample_result(**overrides):
	result = {
		"cutting_job": "CJ-0001",
		"status": "completed",
		"message": "Optimization completed",
		"used_sheets": [{"sheet_id": "SHEET-001", "used_qty": 2}],
		"placed_pieces": [{
			"piece_id": "P1",
			"item_code": "GLS-CLEAR-8MM-1200X800-POL",
			"length_mm": 1200,
			"width_mm": 800,
			"qty": 5,
			"source_sheet_id": "SHEET-001",
		}],
		"remnants": [],
		"waste_area_m2": 1.25,
	}
	result.update(overrides)
	return result


class TestGlassOptimizerExport(unittest.TestCase):
	def test_valid_cutting_job_exports_valid_json(self):
		payload = build_export_payload(_sample_job())
		self.assertEqual(payload["cutting_job"], "CJ-0001")
		self.assertEqual(payload["material"], "CLEAR-8MM")
		self.assertEqual(payload["kerf_mm"], 3)
		self.assertEqual(len(payload["stock_sheets"]), 1)
		self.assertEqual(payload["stock_sheets"][0]["sheet_id"], "SHEET-001")
		self.assertEqual(payload["pieces"][0]["piece_id"], "P1")
		self.assertEqual(payload["pieces"][0]["item_code"], "GLS-CLEAR-8MM-1200X800-POL")
		self.assertEqual(payload["pieces"][0]["process"], "POLISHED")

	def test_export_uses_exact_snake_case_keys(self):
		payload = build_export_payload(_sample_job())
		self.assertEqual(tuple(payload.keys()), EXPORT_TOP_LEVEL_KEYS)
		self.assertEqual(tuple(payload["stock_sheets"][0].keys()), STOCK_SHEET_KEYS)
		self.assertEqual(tuple(payload["pieces"][0].keys()), PIECE_KEYS)

	def test_missing_stock_sheets_fails(self):
		job = _sample_job(source_sheets=[])
		with self.assertRaises(frappe.ValidationError):
			build_export_payload(job)

	def test_missing_pieces_fails(self):
		job = _sample_job(pieces=[])
		with self.assertRaises(frappe.ValidationError):
			build_export_payload(job)

	def test_invalid_dimensions_fail(self):
		job = _sample_job(pieces=[
			frappe._dict({
				"raw_sheet_item": "GLS-CLEAR-8MM-3660X2440",
				"final_item": "GLS-CLEAR-8MM-1200X800-POL",
				"length_mm": 0,
				"width_mm": 800,
				"qty_required": 5,
				"qty_assigned": 5,
				"processing_flags": "",
			}),
		])
		with self.assertRaises(frappe.ValidationError):
			build_export_payload(job)

	def test_invalid_qty_fails(self):
		payload = _sample_result()
		payload["placed_pieces"][0]["qty"] = 0
		with self.assertRaises(frappe.ValidationError):
			validate_import_payload(payload, "CJ-0001")


class TestGlassOptimizerImport(unittest.TestCase):
	def test_valid_result_passes_validation(self):
		validate_import_payload(_sample_result(), "CJ-0001")

	def test_cutting_job_mismatch_fails(self):
		with self.assertRaises(frappe.ValidationError):
			validate_import_payload(_sample_result(cutting_job="CJ-9999"), "CJ-0001")

	def test_status_not_completed_fails(self):
		with self.assertRaises(frappe.ValidationError):
			validate_import_payload(_sample_result(status="failed"), "CJ-0001")

	def test_empty_used_sheets_fails(self):
		with self.assertRaises(frappe.ValidationError):
			validate_import_payload(_sample_result(used_sheets=[]), "CJ-0001")

	def test_empty_placed_pieces_fails(self):
		with self.assertRaises(frappe.ValidationError):
			validate_import_payload(_sample_result(placed_pieces=[]), "CJ-0001")

	def test_invalid_dimensions_fail(self):
		result = _sample_result()
		result["placed_pieces"][0]["length_mm"] = -1
		with self.assertRaises(frappe.ValidationError):
			validate_import_payload(result, "CJ-0001")

	def test_apply_import_clears_and_replaces_rows(self):
		job = frappe._dict({
			"name": "CJ-0001",
			"optimization_used_sheets": [frappe._dict({"sheet_id": "OLD", "used_qty": 1})],
			"optimization_placed_pieces": [frappe._dict({"piece_id": "OLD"})],
			"optimization_remnants": [frappe._dict({"source_sheet_id": "OLD"})],
			"append": lambda field, row: job.setdefault(field, []).append(frappe._dict(row)),
			"set": lambda field, value: job.update({field: value}),
		})

		apply_import_result(job, _sample_result(), file_url="/files/result.json")

		self.assertEqual(job.optimization_status, "Imported")
		self.assertEqual(job.optimization_message, "Optimization completed")
		self.assertEqual(job.optimization_waste_area_m2, 1.25)
		self.assertEqual(job.optimization_result_file, "/files/result.json")
		self.assertEqual(len(job.optimization_used_sheets), 1)
		self.assertEqual(job.optimization_used_sheets[0].sheet_id, "SHEET-001")
		self.assertEqual(len(job.optimization_placed_pieces), 1)
		self.assertEqual(job.optimization_placed_pieces[0].piece_id, "P1")
		self.assertEqual(job.optimization_remnants, [])

		apply_import_result(job, _sample_result(
			used_sheets=[{"sheet_id": "SHEET-002", "used_qty": 1}],
			placed_pieces=[{
				"piece_id": "P2",
				"item_code": "GLS-CLEAR-8MM-900X600-POL",
				"length_mm": 900,
				"width_mm": 600,
				"qty": 2,
				"source_sheet_id": "SHEET-002",
			}],
		))

		self.assertEqual(len(job.optimization_used_sheets), 1)
		self.assertEqual(job.optimization_used_sheets[0].sheet_id, "SHEET-002")
		self.assertEqual(job.optimization_placed_pieces[0].piece_id, "P2")
