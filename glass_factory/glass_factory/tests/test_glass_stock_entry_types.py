"""Tests for glass-specific Stock Entry Types and repack builders."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from glass_factory.glass_factory.glass_stock_entry_types import (
	GLASS_CUTTING_REPACK_STOCK_ENTRY_TYPE,
	GLASS_PROCESSING_REPACK_STOCK_ENTRY_TYPE,
	ensure_glass_stock_entry_types,
)
from glass_factory.glass_factory.selling_validations import validate_stock_entry
from glass_factory.glass_factory.tests.test_production_costing import (
	_cutting_job_three_wip_pieces,
	_patched_cutting_build,
	_patched_processing_build,
	_processing_job_three_final_pieces,
	_stock_settings,
)


class TestGlassStockEntryTypes(IntegrationTestCase):
	def test_ensure_glass_stock_entry_types_creates_both_records(self):
		ensure_glass_stock_entry_types()

		for name in (GLASS_CUTTING_REPACK_STOCK_ENTRY_TYPE, GLASS_PROCESSING_REPACK_STOCK_ENTRY_TYPE):
			self.assertTrue(frappe.db.exists("Stock Entry Type", name))
			self.assertEqual(frappe.db.get_value("Stock Entry Type", name, "purpose"), "Repack")
			self.assertEqual(frappe.db.get_value("Stock Entry Type", name, "is_standard"), 0)

	def test_ensure_glass_stock_entry_types_is_idempotent(self):
		ensure_glass_stock_entry_types()
		first = {
			name: frappe.db.get_value("Stock Entry Type", name, ["purpose", "is_standard"], as_dict=True)
			for name in (GLASS_CUTTING_REPACK_STOCK_ENTRY_TYPE, GLASS_PROCESSING_REPACK_STOCK_ENTRY_TYPE)
		}

		ensure_glass_stock_entry_types()
		second = {
			name: frappe.db.get_value("Stock Entry Type", name, ["purpose", "is_standard"], as_dict=True)
			for name in (GLASS_CUTTING_REPACK_STOCK_ENTRY_TYPE, GLASS_PROCESSING_REPACK_STOCK_ENTRY_TYPE)
		}

		self.assertEqual(first, second)

	def test_cutting_repack_uses_glass_cutting_type_without_additional_costs(self):
		se = _patched_cutting_build(_cutting_job_three_wip_pieces())

		self.assertEqual(se.purpose, "Repack")
		self.assertEqual(se.stock_entry_type, GLASS_CUTTING_REPACK_STOCK_ENTRY_TYPE)
		self.assertFalse(se.get("additional_costs"))

	def test_processing_repack_uses_glass_processing_type_with_capitalized_costs(self):
		se = _patched_processing_build(_processing_job_three_final_pieces())

		self.assertEqual(se.purpose, "Repack")
		self.assertEqual(se.stock_entry_type, GLASS_PROCESSING_REPACK_STOCK_ENTRY_TYPE)
		self.assertEqual(len(se.get("additional_costs") or []), 1)

	def test_processing_repack_skips_additional_costs_when_capitalization_disabled(self):
		job = _processing_job_three_final_pieces()
		settings = _stock_settings(capitalize_processing_costs=0)
		se = _patched_processing_build(job, settings=settings)

		self.assertEqual(se.stock_entry_type, GLASS_PROCESSING_REPACK_STOCK_ENTRY_TYPE)
		self.assertFalse(se.get("additional_costs"))

	def test_validate_stock_entry_accepts_legacy_repack_type(self):
		doc = frappe._dict(
			doctype="Stock Entry",
			gf_created_by_glass_factory=1,
			gf_cutting_job="CJ-0001",
			gf_processing_job=None,
			stock_entry_type="Repack",
			purpose="Repack",
			gf_glass_stock_flow="Raw to Cut WIP",
			items=[],
		)
		validate_stock_entry(doc)

	def test_validate_stock_entry_accepts_glass_cutting_repack_type(self):
		doc = frappe._dict(
			doctype="Stock Entry",
			gf_created_by_glass_factory=1,
			gf_cutting_job="CJ-0001",
			gf_processing_job=None,
			stock_entry_type=GLASS_CUTTING_REPACK_STOCK_ENTRY_TYPE,
			purpose="Repack",
			gf_glass_stock_flow="Raw to Cut WIP",
			items=[],
		)
		validate_stock_entry(doc)

	def test_validate_stock_entry_accepts_glass_processing_repack_type(self):
		doc = frappe._dict(
			doctype="Stock Entry",
			gf_created_by_glass_factory=1,
			gf_cutting_job=None,
			gf_processing_job="GPJ-0001",
			stock_entry_type=GLASS_PROCESSING_REPACK_STOCK_ENTRY_TYPE,
			purpose="Repack",
			gf_glass_stock_flow="Cut WIP to Final",
			items=[],
		)
		validate_stock_entry(doc)
