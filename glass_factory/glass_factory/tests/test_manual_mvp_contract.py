"""Contract-level tests for the Phase 0 manual MVP helpers.

These tests avoid submitting ERPNext documents; the full submit/post flow is
covered by the manual acceptance path after bench services are available.
"""

import unittest

import frappe


class TestManualMVPContract(unittest.TestCase):
	def test_doc_events_are_rewired_to_phase0_services(self):
		from glass_factory import hooks

		self.assertIn(
			"glass_factory.glass_factory.quotation_glass.sync_glass_pieces_to_items",
			hooks.doc_events["Quotation"]["before_validate"],
		)
		self.assertIn(
			"glass_factory.glass_factory.selling_validations.resolve_glass_items",
			hooks.doc_events["Quotation"]["before_validate"],
		)
		self.assertIn(
			"glass_factory.glass_factory.quotation_glass.sync_glass_pieces_to_items",
			hooks.doc_events["Sales Order"]["before_validate"],
		)
		self.assertIn(
			"glass_factory.glass_factory.selling_validations.resolve_glass_items",
			hooks.doc_events["Sales Order"]["before_validate"],
		)
		self.assertEqual(
			hooks.doc_events["Sales Order"]["before_submit"],
			"glass_factory.glass_factory.selling_validations.validate_glass_selling_document",
		)
		self.assertEqual(
			hooks.doc_events["Delivery Note"]["validate"],
			"glass_factory.glass_factory.selling_validations.validate_delivery_note",
		)
		self.assertEqual(
			hooks.doc_events["Delivery Note"]["on_submit"],
			"glass_factory.glass_factory.selling_validations.on_delivery_note_submit",
		)
		self.assertEqual(
			hooks.doc_events["Stock Entry"]["before_validate"],
			"glass_factory.glass_factory.stock_entry_hooks.prepare_glass_stock_entry",
		)
		self.assertEqual(
			hooks.doc_events["Stock Entry"]["validate"],
			"glass_factory.glass_factory.selling_validations.validate_stock_entry",
		)

	def test_job_doctypes_use_glass_naming_series(self):
		for doctype, expected_series in (
			("Cutting Job", "GF-CUT-.YYYY.-"),
			("Glass Processing Job", "GF-PROC-.YYYY.-"),
		):
			meta = frappe.get_meta(doctype)
			self.assertEqual(meta.autoname, "naming_series:")
			series_field = meta.get_field("naming_series")
			self.assertIsNotNone(series_field, doctype)
			self.assertIn(expected_series, series_field.options.split("\n"))

	def test_phase0_doctypes_exist_after_migrate(self):
		for doctype in (
			"Glass Factory Settings",
			"Cutting Job",
			"Cutting Job Sales Order",
			"Cutting Job Piece",
			"Cutting Job Source Sheet",
			"Cutting Job COP File",
			"Cutting Job Optimization Used Sheet",
			"Cutting Job Optimization Placed Piece",
			"Cutting Job Optimization Remnant",
			"Glass Processing Job",
			"Glass Processing Job Input",
			"Glass Processing Job Output",
			"Glass Processing Operation",
			"Quotation Glass Piece",
		):
			self.assertTrue(frappe.db.exists("DocType", doctype), doctype)

	def test_item_glass_custom_fields(self):
		item_fields = frappe.get_all(
			"Custom Field",
			filters={"dt": "Item", "fieldname": ["like", "gf_%"]},
			pluck="fieldname",
		)
		self.assertEqual(
			sorted(item_fields),
			[
				"gf_base_glass_type",
				"gf_glass_item_role",
				"gf_glass_section",
				"gf_length_mm",
				"gf_thickness_mm",
				"gf_width_mm",
			],
		)

	def test_sales_order_and_stock_entry_trace_custom_fields(self):
		self.assertTrue(frappe.db.exists("Custom Field", {"dt": "Sales Order", "fieldname": "glass_pieces"}))
		self.assertTrue(frappe.db.exists("Custom Field", {"dt": "Stock Entry Detail", "fieldname": "gf_cutting_job"}))

	def test_glass_factory_settings_has_allowed_types(self):
		meta = frappe.get_meta("Glass Factory Settings")
		self.assertIsNotNone(meta.get_field("allowed_glass_types"))

	def test_legacy_doctypes_are_removed(self):
		for doctype in (
			"Glass Cut Piece",
			"Cutting Job Linked SO",
			"Cutting Job Tabular File",
			"Glass Cutting Settings",
		):
			self.assertFalse(frappe.db.exists("DocType", doctype), doctype)
