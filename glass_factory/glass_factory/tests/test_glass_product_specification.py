import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from glass_factory.glass_factory.item_resolver import build_glass_item_code, build_glass_operation_code


def _base_spec_kwargs(**overrides):
	values = {
		"glass_type": "CLEAR",
		"thickness_mm": 8,
		"length_mm": 1200,
		"width_mm": 800,
		"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
	}
	values.update(overrides)
	return values


def _new_spec(**overrides):
	doc = frappe.new_doc("Glass Product Specification")
	doc.update(_base_spec_kwargs(**overrides))
	return doc


class TestBuildGlassItemCode(unittest.TestCase):
	def test_item_code_without_operations(self):
		self.assertEqual(
			build_glass_item_code("CLEAR", 8, 1200, 800),
			"GLS-CLEAR-8MM-1200X800",
		)

	def test_item_code_with_normal_operations(self):
		self.assertEqual(
			build_glass_item_code(
				"CLEAR",
				8,
				1200,
				800,
				polish=True,
				hole_count=2,
				slot_count=3,
				temper=True,
			),
			"GLS-CLEAR-8MM-1200X800-POL-HOL02-SLT03-TMP",
		)

	def test_item_code_with_special_operations(self):
		self.assertEqual(
			build_glass_item_code("CLEAR", 8, 1200, 800, special_hole_count=1, special_slot_count=2),
			"GLS-CLEAR-8MM-1200X800-SHOL01-SSLT02",
		)

	def test_operation_order_is_deterministic(self):
		self.assertEqual(
			build_glass_operation_code(
				polish=True,
				bevel=True,
				hole_count=2,
				special_hole_count=1,
				slot_count=3,
				special_slot_count=1,
				temper=True,
				sandblast=True,
				laminate=True,
			),
			"POL-BEV-HOL02-SHOL01-SLT03-SSLT01-TMP-SBL-LAM",
		)


class TestGlassProductSpecification(IntegrationTestCase):
	def test_creates_valid_specification(self):
		doc = _new_spec(specification_title="Test Spec")
		doc.insert()
		self.assertTrue(doc.name)
		self.assertTrue(doc.name.startswith("GF-SPEC-"))
		doc.delete()
		frappe.db.commit()

	def test_calculates_area_fields(self):
		doc = _new_spec()
		doc.insert()
		expected_area = flt((1200 * 800) / 1_000_000, 6)
		self.assertEqual(doc.area_m2, expected_area)
		self.assertEqual(doc.total_area_m2, expected_area)
		doc.delete()
		frappe.db.commit()

	def test_rejects_non_positive_dimensions(self):
		for fieldname, value in (
			("length_mm", 0),
			("width_mm", -1),
		):
			with self.subTest(fieldname=fieldname):
				doc = _new_spec(**{fieldname: value})
				with self.assertRaises(frappe.ValidationError):
					doc.insert()

	def test_rejects_negative_hole_and_slot_counts(self):
		for fieldname in ("hole_count", "special_hole_count", "slot_count", "special_slot_count"):
			with self.subTest(fieldname=fieldname):
				doc = _new_spec(**{fieldname: -1})
				with self.assertRaises(frappe.ValidationError):
					doc.insert()

	def test_item_code_preview_without_operations(self):
		doc = _new_spec()
		doc.insert()
		self.assertEqual(doc.item_code_preview, "GLS-CLEAR-8MM-1200X800")
		doc.delete()
		frappe.db.commit()

	def test_item_code_preview_with_normal_operations(self):
		doc = _new_spec(polish=1, hole_count=2, slot_count=3, temper=1)
		doc.insert()
		self.assertEqual(doc.item_code_preview, "GLS-CLEAR-8MM-1200X800-POL-HOL02-SLT03-TMP")
		doc.delete()
		frappe.db.commit()

	def test_item_code_preview_with_special_operations(self):
		doc = _new_spec(special_hole_count=1, special_slot_count=2)
		doc.insert()
		self.assertEqual(doc.item_code_preview, "GLS-CLEAR-8MM-1200X800-SHOL01-SSLT02")
		doc.delete()
		frappe.db.commit()

	def test_item_code_preview_operation_order(self):
		doc = _new_spec(
			polish=1,
			bevel=1,
			hole_count=2,
			special_hole_count=1,
			slot_count=3,
			special_slot_count=1,
			temper=1,
			sandblast=1,
			laminate=1,
		)
		doc.insert()
		self.assertEqual(
			doc.item_code_preview,
			"GLS-CLEAR-8MM-1200X800-POL-BEV-HOL02-SHOL01-SLT03-SSLT01-TMP-SBL-LAM",
		)
		doc.delete()
		frappe.db.commit()

	def test_allows_multiple_design_attachments(self):
		doc = _new_spec()
		doc.append("design_attachments", {"file_name": "front.dxf"})
		doc.append("design_attachments", {"file_name": "side.dxf"})
		doc.insert()
		self.assertEqual(len(doc.design_attachments), 2)
		doc.delete()
		frappe.db.commit()

	def test_rejects_multiple_primary_design_attachments(self):
		doc = _new_spec()
		doc.append("design_attachments", {"file_name": "front.dxf", "is_primary": 1})
		doc.append("design_attachments", {"file_name": "side.dxf", "is_primary": 1})
		with self.assertRaises(frappe.ValidationError):
			doc.insert()

	def test_rejects_missing_raw_sheet_item(self):
		doc = _new_spec(raw_sheet_item=None)
		with self.assertRaises(frappe.ValidationError):
			doc.insert()

	def test_pulls_glass_type_and_thickness_from_raw_sheet_item(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		doc = _new_spec(raw_sheet_item="GLS-CLEAR-8MM-3210X2250")
		doc.glass_type = ""
		doc.thickness_mm = 0
		doc.insert()
		self.assertEqual(doc.glass_type, "CLEAR")
		self.assertEqual(doc.thickness_mm, 8)
		doc.delete()
		frappe.db.commit()

	def test_pulls_raw_sheet_dimensions_from_item(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		doc = _new_spec(raw_sheet_item="GLS-CLEAR-8MM-3210X2250")
		doc.insert()
		self.assertEqual(doc.raw_sheet_length_mm, 3210)
		self.assertEqual(doc.raw_sheet_width_mm, 2250)
		self.assertEqual(doc.raw_sheet_area_m2, flt((3210 * 2250) / 1_000_000, 6))
		doc.delete()
		frappe.db.commit()

	def test_refresh_preview_without_finished_dimensions(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		doc = _new_spec(length_mm=0, width_mm=0)
		result = doc.refresh_preview()
		self.assertEqual(result["glass_type"], "CLEAR")
		self.assertEqual(result["thickness_mm"], 8)
		self.assertEqual(result["raw_sheet_length_mm"], 3210)
		self.assertEqual(result["raw_sheet_width_mm"], 2250)

	def test_refresh_preview_whitelisted_method(self):
		doc = _new_spec(polish=1)
		doc.insert()
		result = doc.refresh_preview()
		self.assertEqual(result["item_code_preview"], "GLS-CLEAR-8MM-1200X800-POL")
		self.assertIn("Operations: Polish", result["technical_summary"])
		doc.delete()
		frappe.db.commit()
