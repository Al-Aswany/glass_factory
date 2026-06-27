import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt

from glass_factory.glass_factory.spec_pricing import (
	calculate_spec_pricing,
	fetch_raw_sheet_rate,
	get_operation_rate,
	get_spec_currency,
)
from glass_factory.glass_factory.tests.test_glass_product_specification import _base_spec_kwargs, _new_spec
from glass_factory.glass_factory.tests.test_glass_product_specification_items import (
	RAW_SHEET_ITEM,
	_full_spec_kwargs,
	_insert_full_spec,
)


def _pricing_spec(**overrides):
	values = {
		"glass_type": "CLEAR",
		"thickness_mm": 8,
		"length_mm": 1200,
		"width_mm": 800,
		"raw_sheet_item": RAW_SHEET_ITEM,
		"raw_sheet_rate_per_piece": 100,
		"currency": "USD",
	}
	values.update(overrides)
	doc = frappe._dict(values)
	doc.area_m2 = flt((doc.length_mm * doc.width_mm) / 1_000_000, 6)
	doc.total_area_m2 = doc.area_m2

	if any(
		key in overrides
		for key in ("raw_sheet_length_mm", "raw_sheet_width_mm", "raw_sheet_area_m2")
	):
		doc.raw_sheet_length_mm = flt(overrides.get("raw_sheet_length_mm", 0))
		doc.raw_sheet_width_mm = flt(overrides.get("raw_sheet_width_mm", 0))
		doc.raw_sheet_area_m2 = flt(overrides.get("raw_sheet_area_m2", 0))
	else:
		doc.raw_sheet_length_mm = 3210
		doc.raw_sheet_width_mm = 2250
		doc.raw_sheet_area_m2 = flt((3210 * 2250) / 1_000_000, 6)
	return doc


def _set_operation_rates(rows):
	settings = frappe.get_single("Glass Factory Settings")
	settings.set("operation_rates", [])
	for row in rows:
		settings.append("operation_rates", row)
	settings.save(ignore_permissions=True)
	frappe.db.commit()


def _usd_rates():
	return [
		{"operation": "Polish", "currency": "USD", "pricing_basis": "Per Edge Meter", "rate": 5, "enabled": 1},
		{"operation": "Bevel", "currency": "USD", "pricing_basis": "Per Edge Meter", "rate": 3, "enabled": 1},
		{"operation": "Hole", "currency": "USD", "pricing_basis": "Per Unit", "rate": 2, "enabled": 1},
		{"operation": "Special Hole", "currency": "USD", "pricing_basis": "Per Unit", "rate": 4, "enabled": 1},
		{"operation": "Slot", "currency": "USD", "pricing_basis": "Per Unit", "rate": 1.5, "enabled": 1},
		{"operation": "Special Slot", "currency": "USD", "pricing_basis": "Per Unit", "rate": 3, "enabled": 1},
		{"operation": "Temper", "currency": "USD", "pricing_basis": "Per Square Meter", "rate": 10, "enabled": 1},
		{"operation": "Sandblast", "currency": "USD", "pricing_basis": "Per Square Meter", "rate": 8, "enabled": 1},
		{"operation": "Laminate", "currency": "USD", "pricing_basis": "Per Square Meter", "rate": 12, "enabled": 1},
		{"operation": "Polish", "currency": "TZS", "pricing_basis": "Per Edge Meter", "rate": 12000, "enabled": 1},
		{"operation": "Temper", "currency": "TZS", "pricing_basis": "Per Square Meter", "rate": 25000, "enabled": 1},
	]


class TestGlassProductSpecificationPricingCalculations(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("DocType", "Glass Factory Settings"):
			raise unittest.SkipTest("Glass Factory Settings is not installed.")
		cls._previous_rates = frappe.get_all(
			"Glass Operation Rate",
			filters={"parent": "Glass Factory Settings"},
			fields=["*"],
		)
		_set_operation_rates(_usd_rates())

	@classmethod
	def tearDownClass(cls):
		settings = frappe.get_single("Glass Factory Settings")
		settings.set("operation_rates", [])
		for row in cls._previous_rates:
			settings.append("operation_rates", row)
		settings.save(ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def test_raw_sheet_bought_by_piece_converts_to_raw_cost_per_m2(self):
		spec = _pricing_spec()
		calculate_spec_pricing(spec)
		expected = flt(100 / 7.2225, 5)
		self.assertAlmostEqual(spec.raw_cost_per_m2, expected, places=4)

	def test_raw_cost_per_finished_piece(self):
		spec = _pricing_spec()
		calculate_spec_pricing(spec)
		expected_raw_m2 = flt(100 / 7.2225, 6)
		self.assertAlmostEqual(spec.raw_cost_per_finished_piece, flt(expected_raw_m2 * 0.96, 6), places=4)

	def test_edge_meter_is_calculated_correctly(self):
		spec = _pricing_spec()
		calculate_spec_pricing(spec)
		self.assertEqual(spec.edge_meter, 4.0)

	def test_polish_and_bevel_use_edge_meter_pricing(self):
		spec = _pricing_spec(polish=1, bevel=1)
		calculate_spec_pricing(spec)
		self.assertEqual(spec.edge_processing_amount_per_piece, 32.0)

	def test_temper_sandblast_laminate_use_per_m2_pricing(self):
		spec = _pricing_spec(temper=1, sandblast=1, laminate=1)
		calculate_spec_pricing(spec)
		self.assertEqual(spec.area_processing_amount_per_piece, flt(0.96 * (10 + 8 + 12), 2))

	def test_holes_and_slots_use_per_unit_pricing(self):
		spec = _pricing_spec(
			hole_count=2,
			special_hole_count=1,
			slot_count=3,
			special_slot_count=1,
		)
		calculate_spec_pricing(spec)
		expected = (2 * 2) + (1 * 4) + (3 * 1.5) + (1 * 3)
		self.assertEqual(spec.unit_processing_amount_per_piece, expected)

	def test_calculated_amount_per_piece_includes_raw_and_processing(self):
		spec = _pricing_spec(polish=1, temper=1, hole_count=2)
		calculate_spec_pricing(spec)
		expected = flt(spec.raw_cost_per_finished_piece + spec.processing_amount_per_piece, 2)
		self.assertEqual(spec.calculated_amount_per_piece, expected)

	def test_calculated_rate_per_m2_equals_amount_per_piece_divided_by_area(self):
		spec = _pricing_spec(polish=1, temper=1)
		calculate_spec_pricing(spec)
		self.assertAlmostEqual(
			spec.calculated_rate_per_m2,
			flt(spec.calculated_amount_per_piece / spec.area_m2, 6),
			places=6,
		)

	def test_manual_selling_rate_overrides_calculated_rate(self):
		spec = _pricing_spec(manual_selling_rate_per_m2=25)
		calculate_spec_pricing(spec)
		self.assertEqual(spec.selling_rate_per_m2, 25)

	def test_price_override_is_set_when_manual_selling_rate_is_used(self):
		spec = _pricing_spec(manual_selling_rate_per_m2=25)
		calculate_spec_pricing(spec)
		self.assertEqual(cint(spec.price_override), 1)

	def test_rate_per_piece_equals_selling_rate_per_m2_times_area(self):
		spec = _pricing_spec(manual_selling_rate_per_m2=25)
		calculate_spec_pricing(spec)
		self.assertEqual(spec.rate_per_piece, 24.0)

	def test_amount_equals_rate_per_piece(self):
		spec = _pricing_spec(manual_selling_rate_per_m2=25)
		calculate_spec_pricing(spec)
		self.assertEqual(spec.amount, 24.0)

	def test_operation_rates_are_selected_by_currency(self):
		self.assertEqual(get_operation_rate("Polish", "USD", "Per Edge Meter"), 5)
		self.assertEqual(get_operation_rate("Polish", "TZS", "Per Edge Meter"), 12000)
		self.assertEqual(get_operation_rate("Temper", "TZS", "Per Square Meter"), 25000)

	def test_missing_raw_sheet_rate_uses_zero_raw_cost(self):
		spec = _pricing_spec(raw_sheet_rate_per_piece=0)
		calculate_spec_pricing(spec)
		self.assertEqual(spec.raw_cost_per_m2, 0)
		self.assertEqual(spec.raw_cost_per_finished_piece, 0)

	def test_missing_raw_sheet_dimensions_with_raw_rate_fails(self):
		spec = _pricing_spec(raw_sheet_length_mm=0, raw_sheet_width_mm=0, raw_sheet_area_m2=0)
		with self.assertRaises(frappe.ValidationError) as ctx:
			calculate_spec_pricing(spec)
		self.assertIn("Raw sheet dimensions are required", str(ctx.exception))


class TestGlassProductSpecificationPricingIntegration(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Item", RAW_SHEET_ITEM):
			raise unittest.SkipTest("Sample raw sheet Item is not installed on this site.")
		if not frappe.db.exists("DocType", "Glass Factory Settings"):
			raise unittest.SkipTest("Glass Factory Settings is not installed.")
		cls._previous_rates = frappe.get_all(
			"Glass Operation Rate",
			filters={"parent": "Glass Factory Settings"},
			fields=["*"],
		)
		_set_operation_rates(_usd_rates())

	@classmethod
	def tearDownClass(cls):
		settings = frappe.get_single("Glass Factory Settings")
		settings.set("operation_rates", [])
		for row in cls._previous_rates:
			settings.append("operation_rates", row)
		settings.save(ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def test_validate_calculates_pricing_on_save(self):
		doc = _new_spec(
			raw_sheet_item=RAW_SHEET_ITEM,
			raw_sheet_rate_per_piece=100,
			currency="USD",
			polish=1,
			temper=1,
		)
		doc.insert()
		self.assertGreater(flt(doc.raw_cost_per_m2), 0)
		self.assertGreater(flt(doc.calculated_rate_per_m2), 0)
		doc.delete()
		frappe.db.commit()

	def test_refresh_pricing_fetches_and_saves(self):
		doc = _new_spec(
			raw_sheet_item=RAW_SHEET_ITEM,
			currency="USD",
			polish=1,
		)
		doc.insert()
		result = doc.refresh_pricing()
		self.assertIn("raw_cost_per_m2", result)
		self.assertIn("selling_rate_per_m2", result)
		doc.reload()
		self.assertGreaterEqual(flt(doc.edge_meter), 0)
		doc.delete()
		frappe.db.commit()

	def test_pricing_changes_do_not_mark_regeneration_required(self):
		doc = _insert_full_spec()
		doc.generate_items()
		doc.reload()
		self.assertEqual(doc.generation_status, "Generated")

		doc.manual_selling_rate_per_m2 = 25
		doc.raw_sheet_rate_per_piece = 120
		doc.currency = "USD"
		doc.save()
		doc.reload()
		self.assertEqual(doc.generation_status, "Generated")
		self.assertEqual(doc.rate_per_piece, 24.0)
		self.assertEqual(doc.amount, 24.0)

		doc.delete()
		frappe.db.commit()

	def test_technical_changes_still_mark_regeneration_required(self):
		doc = _insert_full_spec()
		doc.generate_items()
		doc.reload()

		doc.hole_count = 3
		doc.save()
		doc.reload()
		self.assertEqual(doc.generation_status, "Regeneration Required")

		doc.delete()
		frappe.db.commit()

	def test_fetch_raw_sheet_rate_uses_manual_value_first(self):
		doc = _new_spec(raw_sheet_rate_per_piece=150, raw_sheet_item=RAW_SHEET_ITEM)
		rate = fetch_raw_sheet_rate(doc, fetch_from_item_price=True)
		self.assertEqual(rate, 150)

	def test_get_spec_currency_defaults_to_usd(self):
		spec = frappe._dict({})
		self.assertEqual(get_spec_currency(spec), "USD")


class TestGlassProductSpecificationPricingLegacyFallback(unittest.TestCase):
	def test_legacy_area_rates_used_when_child_table_missing(self):
		with patch(
			"glass_factory.glass_factory.spec_pricing.frappe.get_single"
		) as mock_get_single:
			settings = frappe._dict(
				operation_rates=[],
				temper_rate_per_m2=15,
				sandblast_rate_per_m2=0,
				laminate_rate_per_m2=0,
			)
			mock_get_single.return_value = settings
			with patch("glass_factory.glass_factory.spec_pricing.frappe.db.exists", return_value=True):
				self.assertEqual(get_operation_rate("Temper", "USD", "Per Square Meter"), 15)
