import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt

from glass_factory.glass_factory.item_resolver import (
	BATCH_TRACKED_ROLES,
	build_glass_item_code,
	item_role,
	spec_is_used_in_transaction,
)
from glass_factory.glass_factory.tests.test_glass_product_specification import _base_spec_kwargs, _new_spec


RAW_SHEET_ITEM = "GLS-CLEAR-8MM-3210X2250"


def _full_spec_kwargs(**overrides):
	qty = overrides.pop("qty", 10)
	values = _base_spec_kwargs(
		qty=qty,
		polish=1,
		hole_count=2,
		special_hole_count=1,
		slot_count=3,
		special_slot_count=1,
		temper=1,
		raw_sheet_item=RAW_SHEET_ITEM,
		**overrides,
	)
	return values


def _insert_full_spec(**overrides):
	doc = _new_spec(**_full_spec_kwargs(**overrides))
	doc.insert()
	return doc


def _expected_final_code(**overrides):
	return build_glass_item_code(
		"CLEAR",
		8,
		1200,
		800,
		polish=True,
		hole_count=2,
		special_hole_count=1,
		slot_count=3,
		special_slot_count=1,
		temper=True,
		**overrides,
	)


class TestGlassProductSpecificationItems(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Item", RAW_SHEET_ITEM):
			raise unittest.SkipTest("Sample raw sheet Item is not installed on this site.")

	def test_generate_items_from_valid_specification(self):
		doc = _insert_full_spec()
		expected_final = _expected_final_code()
		self.assertEqual(doc.item_code_preview, expected_final)

		result = doc.generate_items()
		doc.reload()

		self.assertEqual(result["raw_item_code"], RAW_SHEET_ITEM)
		self.assertEqual(result["cut_wip_item_code"], "GLS-CLEAR-8MM-1200X800-CUT")
		self.assertEqual(result["final_item_code"], expected_final)
		self.assertEqual(doc.raw_item_code, RAW_SHEET_ITEM)
		self.assertEqual(doc.cut_wip_item_code, "GLS-CLEAR-8MM-1200X800-CUT")
		self.assertEqual(doc.final_item_code, expected_final)
		self.assertEqual(doc.generated_item, expected_final)
		self.assertEqual(cint(doc.items_generated), 1)
		self.assertEqual(doc.generation_status, "Generated")
		self.assertEqual(doc.status, "Ready")
		self.assertTrue(doc.generated_on)
		self.assertEqual(doc.generated_by, frappe.session.user)

		doc.delete()
		frappe.db.commit()

	def test_reuses_selected_raw_sheet_item(self):
		doc = _insert_full_spec()
		doc.generate_items()
		doc.reload()
		self.assertEqual(doc.raw_item_code, RAW_SHEET_ITEM)
		raw_doc = frappe.get_doc("Item", doc.raw_item_code)
		self.assertEqual(raw_doc.gf_glass_item_role, "Raw Sheet")

		doc.delete()
		frappe.db.commit()

	def test_creates_or_reuses_cut_wip_item(self):
		doc = _insert_full_spec()
		doc.generate_items()
		cut_code = "GLS-CLEAR-8MM-1200X800-CUT"
		self.assertTrue(frappe.db.exists("Item", cut_code))
		self.assertEqual(frappe.db.get_value("Item", cut_code, "gf_glass_item_role"), "Cut WIP")

		doc.delete()
		frappe.db.commit()

	def test_creates_or_reuses_final_item_from_preview(self):
		doc = _insert_full_spec()
		expected_final = _expected_final_code()
		doc.generate_items()
		self.assertTrue(frappe.db.exists("Item", expected_final))
		self.assertEqual(frappe.db.get_value("Item", expected_final, "gf_glass_item_role"), "Final")

		doc.delete()
		frappe.db.commit()

	def test_generate_twice_does_not_create_duplicate_items(self):
		doc = _insert_full_spec()
		doc.generate_items()
		first_final = doc.final_item_code

		doc.generation_status = "Regeneration Required"
		doc.save()
		doc.generate_items()
		doc.reload()

		self.assertEqual(doc.final_item_code, first_final)
		self.assertEqual(
			frappe.db.count("Item", {"item_code": first_final}),
			1,
		)

		doc.delete()
		frappe.db.commit()

	def test_missing_raw_sheet_item_fails(self):
		doc = _new_spec()
		doc.insert()
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.generate_items()
		self.assertIn("Raw Sheet Item is required before generating items.", str(ctx.exception))
		doc.delete()
		frappe.db.commit()

	def test_invalid_raw_sheet_role_fails(self):
		final_code = "GLS-CLEAR-8MM-1200X800-POL"
		if not frappe.db.exists("Item", final_code):
			self.skipTest("Final sample Item is not installed on this site.")

		doc = _new_spec(raw_sheet_item=final_code)
		doc.insert()
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.generate_items()
		self.assertIn("Raw Sheet Item must be a Glass Raw Sheet item.", str(ctx.exception))
		doc.delete()
		frappe.db.commit()

	def test_final_item_uses_counted_operation_codes(self):
		doc = _insert_full_spec()
		expected_final = "GLS-CLEAR-8MM-1200X800-POL-HOL02-SHOL01-SLT03-SSLT01-TMP"
		self.assertEqual(doc.item_code_preview, expected_final)
		doc.generate_items()
		self.assertEqual(doc.final_item_code, expected_final)

		doc.delete()
		frappe.db.commit()

	def test_cut_wip_item_uses_cut_suffix(self):
		doc = _insert_full_spec()
		doc.generate_items()
		self.assertTrue(doc.cut_wip_item_code.endswith("-CUT"))

		doc.delete()
		frappe.db.commit()

	def test_generated_items_have_correct_dimensions(self):
		doc = _insert_full_spec()
		doc.generate_items()

		for item_code, length, width in (
			(doc.cut_wip_item_code, 1200, 800),
			(doc.final_item_code, 1200, 800),
		):
			item = frappe.get_doc("Item", item_code)
			self.assertEqual(flt(item.gf_length_mm), length)
			self.assertEqual(flt(item.gf_width_mm), width)
			self.assertEqual(flt(item.gf_thickness_mm), 8)
			self.assertEqual(item.gf_base_glass_type, "CLEAR")

		doc.delete()
		frappe.db.commit()

	def test_generated_items_have_correct_roles(self):
		doc = _insert_full_spec()
		doc.generate_items()

		self.assertEqual(item_role(doc.raw_item_code), "Raw Sheet")
		self.assertEqual(item_role(doc.cut_wip_item_code), "Cut WIP")
		self.assertEqual(item_role(doc.final_item_code), "Final")

		doc.delete()
		frappe.db.commit()

	def test_generated_items_are_batch_tracked(self):
		doc = _insert_full_spec()
		doc.generate_items()

		for item_code in (doc.raw_item_code, doc.cut_wip_item_code, doc.final_item_code):
			role = item_role(item_code)
			item = frappe.get_doc("Item", item_code)
			if role in BATCH_TRACKED_ROLES:
				self.assertEqual(cint(item.has_batch_no), 1)
				self.assertEqual(cint(item.has_serial_no), 0)

		doc.delete()
		frappe.db.commit()

	def test_reset_clears_links_without_deleting_items(self):
		doc = _insert_full_spec()
		doc.generate_items()
		raw_item = doc.raw_item_code
		cut_item = doc.cut_wip_item_code
		final_item = doc.final_item_code

		doc.reset_generated_items()
		doc.reload()

		self.assertEqual(cint(doc.items_generated), 0)
		self.assertIsNone(doc.raw_item_code)
		self.assertIsNone(doc.cut_wip_item_code)
		self.assertIsNone(doc.final_item_code)
		self.assertIsNone(doc.generated_item)
		self.assertEqual(doc.generation_status, "Not Generated")
		self.assertEqual(doc.status, "Draft")

		self.assertTrue(frappe.db.exists("Item", raw_item))
		self.assertTrue(frappe.db.exists("Item", cut_item))
		self.assertTrue(frappe.db.exists("Item", final_item))

		doc.delete()
		frappe.db.commit()

	def test_technical_field_change_marks_regeneration_required(self):
		doc = _insert_full_spec()
		doc.generate_items()
		doc.reload()
		self.assertEqual(doc.generation_status, "Generated")

		doc.hole_count = 3
		doc.save()
		doc.reload()
		self.assertEqual(doc.generation_status, "Regeneration Required")

		doc.delete()
		frappe.db.commit()

	def test_regeneration_reuses_existing_items_when_preview_unchanged(self):
		doc = _insert_full_spec()
		doc.generate_items()
		original_codes = {
			"raw": doc.raw_item_code,
			"cut": doc.cut_wip_item_code,
			"final": doc.final_item_code,
		}

		doc.hole_count = 3
		doc.save()
		self.assertEqual(doc.generation_status, "Regeneration Required")

		doc.hole_count = 2
		doc.save()
		doc.generation_status = "Regeneration Required"
		doc.save()

		doc.generate_items()
		doc.reload()

		self.assertEqual(doc.raw_item_code, original_codes["raw"])
		self.assertEqual(doc.cut_wip_item_code, original_codes["cut"])
		self.assertEqual(doc.final_item_code, original_codes["final"])
		self.assertEqual(doc.generation_status, "Generated")

		doc.delete()
		frappe.db.commit()

	def test_generate_blocks_when_already_generated(self):
		doc = _insert_full_spec()
		doc.generate_items()
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.generate_items()
		self.assertIn("Items already generated.", str(ctx.exception))

		doc.delete()
		frappe.db.commit()

	def test_spec_is_not_used_in_transaction_in_phase_2(self):
		self.assertFalse(spec_is_used_in_transaction("GF-SPEC-00001"))
