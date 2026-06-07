import unittest

import frappe

from glass_factory.glass_factory.item_glass_hooks import sync_glass_item_from_code, validate_glass_item


class TestItemGlassHooks(unittest.TestCase):
	def test_sync_fills_readonly_fields_from_item_code(self):
		doc = frappe._dict(
			doctype="Item",
			item_code="GLS-CLEAR-8MM-3210X2250",
			gf_glass_item_role="Raw Sheet",
		)
		sync_glass_item_from_code(doc)
		self.assertEqual(doc.gf_base_glass_type, "CLEAR")
		self.assertEqual(doc.gf_thickness_mm, 8)
		self.assertEqual(doc.gf_length_mm, 3210)
		self.assertEqual(doc.gf_width_mm, 2250)

	def test_sync_clears_fields_when_code_does_not_match(self):
		doc = frappe._dict(
			doctype="Item",
			item_code="RANDOM-ITEM",
			gf_glass_item_role="",
		)
		sync_glass_item_from_code(doc)
		self.assertEqual(doc.gf_base_glass_type, "")
		self.assertEqual(doc.gf_thickness_mm, 0)
		self.assertEqual(doc.gf_length_mm, 0)
		self.assertEqual(doc.gf_width_mm, 0)

	def test_validate_blocks_glass_role_with_invalid_code(self):
		doc = frappe._dict(
			doctype="Item",
			item_code="RANDOM-ITEM",
			gf_glass_item_role="Raw Sheet",
		)
		with self.assertRaises(frappe.ValidationError):
			validate_glass_item(doc)
