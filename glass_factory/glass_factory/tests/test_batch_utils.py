from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from glass_factory.glass_factory.batch_utils import (
	SOURCE_SHEET_ROLES,
	batch_size_label,
	get_source_sheet_batch_no,
	validate_source_sheet_batch,
	validate_source_sheet_item_role,
	_enrich_source_sheet_batch_results,
)


class TestBatchUtils(IntegrationTestCase):
	def test_source_sheet_roles_constant(self):
		self.assertEqual(SOURCE_SHEET_ROLES, ("Raw Sheet", "Remnant"))

	def test_validate_source_sheet_item_role_rejects_final_item(self):
		with patch(
			"glass_factory.glass_factory.batch_utils.item_role",
			return_value="Final",
		):
			with self.assertRaises(frappe.ValidationError):
				validate_source_sheet_item_role("GLS-FINAL", "Final")

	def test_validate_source_sheet_item_role_rejects_role_mismatch(self):
		with patch(
			"glass_factory.glass_factory.batch_utils.item_role",
			return_value="Raw Sheet",
		), patch(
			"glass_factory.glass_factory.batch_utils.item_uses_batches",
			return_value=True,
		):
			with self.assertRaises(frappe.ValidationError):
				validate_source_sheet_item_role("GLS-RAW", "Remnant")

	def test_get_source_sheet_batch_no_requires_item_and_warehouse(self):
		self.assertEqual(get_source_sheet_batch_no("Batch", "", "name", 0, 20, {}), [])
		self.assertEqual(
			get_source_sheet_batch_no(
				"Batch",
				"",
				"name",
				0,
				20,
				{"item_code": "GLS-RAW"},
			),
			[],
		)

	def test_get_source_sheet_batch_no_delegates_to_erpnext_query(self):
		with patch(
			"glass_factory.glass_factory.batch_utils.validate_source_sheet_item_role",
			return_value="Raw Sheet",
		), patch(
			"erpnext.controllers.queries.get_batch_no",
			return_value=[["BATCH-001", 2.0]],
		) as mocked:
			result = get_source_sheet_batch_no(
				"Batch",
				"BATCH",
				"name",
				0,
				20,
				{
					"item_code": "GLS-RAW",
					"warehouse": "Stores - _TC",
					"source_role": "Raw Sheet",
				},
			)

		self.assertEqual(result[0][0], "BATCH-001")
		mocked.assert_called_once()
		self.assertEqual(mocked.call_args.args[5]["item_code"], "GLS-RAW")
		self.assertEqual(mocked.call_args.args[5]["warehouse"], "Stores - _TC")

	def test_batch_size_label_uses_batch_dims(self):
		label = batch_size_label(
			"BATCH-001",
			"GLS-CLEAR-8MM-3210X2250",
			{"BATCH-001": (1000, 600)},
		)
		self.assertEqual(label, "1000×600 mm")

	def test_batch_size_label_falls_back_to_item_code(self):
		label = batch_size_label("BATCH-001", "GLS-CLEAR-8MM-1200X800-CUT", {})
		self.assertEqual(label, "1200×800 mm")

	def test_enrich_source_sheet_batch_results_inserts_size_column(self):
		rows = _enrich_source_sheet_batch_results(
			[["BATCH-001", 2.0, "MFG-2026-01-01"]],
			"GLS-CLEAR-8MM-1000X600-REM",
		)
		self.assertEqual(rows[0][0], "BATCH-001")
		self.assertEqual(rows[0][1], "1000×600 mm")
		self.assertEqual(rows[0][2], 2.0)

	def test_validate_source_sheet_batch_checks_disabled_and_item(self):
		with patch(
			"glass_factory.glass_factory.batch_utils.validate_source_sheet_item_role",
			return_value="Raw Sheet",
		), patch(
			"frappe.db.get_value",
			return_value={
				"name": "BATCH-001",
				"item": "GLS-OTHER",
				"disabled": 0,
				"expiry_date": None,
			},
		):
			with self.assertRaises(frappe.ValidationError):
				validate_source_sheet_batch(
					"BATCH-001",
					"GLS-RAW",
					"Stores - _TC",
					"Raw Sheet",
				)

	def test_validate_source_sheet_batch_requires_stock_qty(self):
		with patch(
			"glass_factory.glass_factory.batch_utils.validate_source_sheet_item_role",
			return_value="Raw Sheet",
		), patch(
			"frappe.db.get_value",
			return_value={
				"name": "BATCH-001",
				"item": "GLS-RAW",
				"disabled": 0,
				"expiry_date": None,
			},
		), patch(
			"erpnext.stock.doctype.batch.batch.get_batch_qty",
			return_value=0,
		):
			with self.assertRaises(frappe.ValidationError):
				validate_source_sheet_batch(
					"BATCH-001",
					"GLS-RAW",
					"Stores - _TC",
					"Raw Sheet",
				)
