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
			hooks.doc_events["Stock Entry"]["validate"],
			"glass_factory.glass_factory.selling_validations.validate_stock_entry",
		)

	def test_phase0_doctypes_exist_after_migrate(self):
		for doctype in (
			"Glass Factory Settings",
			"Cutting Job",
			"Cutting Job Sales Order",
			"Cutting Job Piece",
			"Cutting Job Source Sheet",
			"Cutting Job COP File",
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

	def test_legacy_doctypes_are_removed(self):
		for doctype in (
			"Glass Cut Piece",
			"Cutting Job Linked SO",
			"Cutting Job Tabular File",
			"Glass Cutting Settings",
		):
			self.assertFalse(frappe.db.exists("DocType", doctype), doctype)
