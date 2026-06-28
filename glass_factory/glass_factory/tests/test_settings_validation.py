from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from glass_factory.glass_factory.item_resolver import validate_glass_type
from glass_factory.glass_factory.settings_validation import (
	require_runtime_setup,
	throw_missing_settings,
	validate_settings_document,
)


class TestSettingsValidation(IntegrationTestCase):
	def test_throw_missing_settings_points_to_configuration(self):
		with patch("glass_factory.glass_factory.settings_validation.frappe.db.exists", return_value=False):
			with self.assertRaises(frappe.ValidationError) as ctx:
				throw_missing_settings()
		self.assertIn("Glass Factory Settings", str(ctx.exception))
		self.assertIn("not configured", str(ctx.exception))

	def test_missing_raw_warehouse_raises_clear_message(self):
		settings = frappe.get_single("Glass Factory Settings")
		original = settings.raw_warehouse
		settings.raw_warehouse = ""
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			message = str(ctx.exception)
			self.assertIn("Raw Warehouse", message)
			self.assertIn("Glass Factory Settings", message)
		finally:
			settings.raw_warehouse = original

	def test_missing_default_uom_raises_clear_message(self):
		settings = frappe.get_single("Glass Factory Settings")
		original = settings.default_uom
		settings.default_uom = ""
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			self.assertIn("Default UOM", str(ctx.exception))
		finally:
			settings.default_uom = original

	def test_missing_item_group_raises_clear_message(self):
		settings = frappe.get_single("Glass Factory Settings")
		original = settings.final_item_group
		settings.final_item_group = ""
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			self.assertIn("Final Item Group", str(ctx.exception))
		finally:
			settings.final_item_group = original

	def test_missing_allowed_glass_types_raises_clear_message(self):
		settings = frappe.get_single("Glass Factory Settings")
		original = settings.allowed_glass_types
		settings.allowed_glass_types = ""
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			self.assertIn("Allowed Glass Types", str(ctx.exception))
		finally:
			settings.allowed_glass_types = original

	def test_runtime_setup_passes_with_seeded_settings(self):
		require_runtime_setup(scope="stock")

	def test_invalid_operation_pricing_basis_raises_clear_message(self):
		settings = frappe.get_single("Glass Factory Settings")
		settings.append(
			"operation_rates",
			{
				"operation": "Polish",
				"currency": "USD",
				"pricing_basis": "Per Square Meter",
				"rate": 5,
				"enabled": 1,
			},
		)
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			message = str(ctx.exception)
			self.assertIn("Polish", message)
			self.assertIn("Per Edge Meter", message)
		finally:
			settings.reload()

	def test_invalid_glass_type_lists_allowed_types(self):
		with patch("glass_factory.glass_factory.item_resolver._settings_value", return_value="CLEAR\nBRONZE"):
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_glass_type("BLUE", context="Row 1")
		message = str(ctx.exception)
		self.assertIn("Allowed glass types: CLEAR, BRONZE", message)
		self.assertIn("Row 1", message)


class TestSettingsLayout(IntegrationTestCase):
	"""Verify that new fields exist and deprecated fields are properly hidden."""

	def test_default_buying_price_list_field_exists(self):
		meta = frappe.get_meta("Glass Factory Settings")
		field = meta.get_field("default_buying_price_list")
		self.assertIsNotNone(field, "default_buying_price_list field should exist")
		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "Price List")

	def test_default_selling_price_list_field_exists(self):
		meta = frappe.get_meta("Glass Factory Settings")
		field = meta.get_field("default_selling_price_list")
		self.assertIsNotNone(field, "default_selling_price_list field should exist")
		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "Price List")

	def test_glass_operation_rate_cost_rate_field_exists(self):
		meta = frappe.get_meta("Glass Operation Rate")
		field = meta.get_field("cost_rate")
		self.assertIsNotNone(field, "Glass Operation Rate should have a cost_rate field")
		self.assertEqual(field.fieldtype, "Currency")

	def test_deprecated_fixed_rate_fields_are_hidden(self):
		meta = frappe.get_meta("Glass Factory Settings")
		deprecated = [
			"polish_rate_per_m2",
			"bevel_rate_per_m2",
			"holes_rate_per_m2",
			"slots_rate_per_m2",
			"temper_rate_per_m2",
			"sandblast_rate_per_m2",
			"laminate_rate_per_m2",
		]
		for fieldname in deprecated:
			field = meta.get_field(fieldname)
			self.assertIsNotNone(field, f"Field {fieldname} must still exist for backward compat")
			self.assertTrue(field.hidden, f"Field {fieldname} should be hidden (deprecated)")

	def test_deprecated_fixed_cost_fields_are_hidden(self):
		meta = frappe.get_meta("Glass Factory Settings")
		deprecated = [
			"polish_cost_per_m2",
			"bevel_cost_per_m2",
			"holes_cost_per_m2",
			"slots_cost_per_m2",
			"temper_cost_per_m2",
			"sandblast_cost_per_m2",
			"laminate_cost_per_m2",
		]
		for fieldname in deprecated:
			field = meta.get_field(fieldname)
			self.assertIsNotNone(field, f"Field {fieldname} must still exist for backward compat")
			self.assertTrue(field.hidden, f"Field {fieldname} should be hidden (deprecated)")

	def test_operation_rates_section_exists_and_has_correct_label(self):
		meta = frappe.get_meta("Glass Factory Settings")
		field = meta.get_field("operation_rates_section")
		self.assertIsNotNone(field)
		self.assertEqual(field.label, "Operation Default Rates")

	def test_cop_section_has_enable_cop_field(self):
		meta = frappe.get_meta("Glass Factory Settings")
		self.assertIsNotNone(meta.get_field("cop_section"))
		self.assertIsNotNone(meta.get_field("enable_cop"))

	def test_price_lists_section_exists(self):
		meta = frappe.get_meta("Glass Factory Settings")
		self.assertIsNotNone(meta.get_field("price_lists_section"))


class TestOperationRateValidation(IntegrationTestCase):
	"""Validate improved operation rate rules."""

	def test_duplicate_enabled_operation_rate_fails(self):
		settings = frappe.get_single("Glass Factory Settings")
		settings.append("operation_rates", {
			"operation": "Temper",
			"currency": "USD",
			"pricing_basis": "Per Square Meter",
			"rate": 10,
			"cost_rate": 0,
			"enabled": 1,
		})
		settings.append("operation_rates", {
			"operation": "Temper",
			"currency": "USD",
			"pricing_basis": "Per Square Meter",
			"rate": 15,
			"cost_rate": 0,
			"enabled": 1,
		})
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			message = str(ctx.exception)
			self.assertIn("Duplicate", message)
			self.assertIn("Temper", message)
		finally:
			settings.reload()

	def test_negative_selling_rate_fails(self):
		settings = frappe.get_single("Glass Factory Settings")
		settings.append("operation_rates", {
			"operation": "Laminate",
			"currency": "USD",
			"pricing_basis": "Per Square Meter",
			"rate": -10,
			"cost_rate": 0,
			"enabled": 1,
		})
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			message = str(ctx.exception)
			self.assertIn("Laminate", message)
			self.assertIn(">= 0", message)
		finally:
			settings.reload()

	def test_negative_cost_rate_fails(self):
		settings = frappe.get_single("Glass Factory Settings")
		settings.append("operation_rates", {
			"operation": "Sandblast",
			"currency": "USD",
			"pricing_basis": "Per Square Meter",
			"rate": 8,
			"cost_rate": -5,
			"enabled": 1,
		})
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			message = str(ctx.exception)
			self.assertIn("Sandblast", message)
			self.assertIn(">= 0", message)
		finally:
			settings.reload()

	def test_zero_rate_is_valid(self):
		"""Zero rates are allowed (operation exists but not yet priced)."""
		settings = frappe.get_single("Glass Factory Settings")
		settings.append("operation_rates", {
			"operation": "Hole",
			"currency": "USD",
			"pricing_basis": "Per Unit",
			"rate": 0,
			"cost_rate": 0,
			"enabled": 1,
		})
		try:
			# Should not raise
			validate_settings_document(settings)
		finally:
			settings.reload()

	def test_disabled_duplicate_row_does_not_fail(self):
		"""Duplicate rows are only checked for enabled=1 rows."""
		settings = frappe.get_single("Glass Factory Settings")
		settings.append("operation_rates", {
			"operation": "Hole",
			"currency": "USD",
			"pricing_basis": "Per Unit",
			"rate": 5,
			"cost_rate": 0,
			"enabled": 0,
		})
		settings.append("operation_rates", {
			"operation": "Hole",
			"currency": "USD",
			"pricing_basis": "Per Unit",
			"rate": 7,
			"cost_rate": 0,
			"enabled": 0,
		})
		try:
			# Should not raise (both disabled)
			validate_settings_document(settings)
		finally:
			settings.reload()


class TestPriceListValidation(IntegrationTestCase):
	"""Validate price list field type checks."""

	def test_nonexistent_buying_price_list_fails(self):
		settings = frappe.get_single("Glass Factory Settings")
		settings.default_buying_price_list = "Nonexistent PL XYZ"
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			self.assertIn("Nonexistent PL XYZ", str(ctx.exception))
		finally:
			settings.reload()

	def test_nonexistent_selling_price_list_fails(self):
		settings = frappe.get_single("Glass Factory Settings")
		settings.default_selling_price_list = "Nonexistent PL ABC"
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			self.assertIn("Nonexistent PL ABC", str(ctx.exception))
		finally:
			settings.reload()

	def test_empty_price_lists_are_valid(self):
		"""Not setting price lists is allowed (they are optional)."""
		settings = frappe.get_single("Glass Factory Settings")
		original_buying = settings.default_buying_price_list
		original_selling = settings.default_selling_price_list
		settings.default_buying_price_list = ""
		settings.default_selling_price_list = ""
		try:
			# Should not raise
			validate_settings_document(settings)
		finally:
			settings.default_buying_price_list = original_buying
			settings.default_selling_price_list = original_selling

	def test_selling_price_list_used_as_buying_fails(self):
		"""A price list marked selling-only cannot be the buying price list."""
		# Find or create a selling-only price list
		selling_only = frappe.db.get_value("Price List", {"selling": 1, "buying": 0}, "name")
		if not selling_only:
			self.skipTest("No selling-only price list in test database")

		settings = frappe.get_single("Glass Factory Settings")
		settings.default_buying_price_list = selling_only
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			message = str(ctx.exception)
			self.assertIn("buying price list", message.lower())
		finally:
			settings.reload()

	def test_buying_price_list_used_as_selling_fails(self):
		"""A price list marked buying-only cannot be the selling price list."""
		buying_only = frappe.db.get_value("Price List", {"buying": 1, "selling": 0}, "name")
		if not buying_only:
			self.skipTest("No buying-only price list in test database")

		settings = frappe.get_single("Glass Factory Settings")
		settings.default_selling_price_list = buying_only
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_settings_document(settings)
			message = str(ctx.exception)
			self.assertIn("selling price list", message.lower())
		finally:
			settings.reload()


class TestPricingLookupFallback(IntegrationTestCase):
	"""Verify that price list fallback chain works correctly."""

	def test_buying_price_list_uses_company_first(self):
		"""Company buying price list takes priority over settings."""
		from glass_factory.glass_factory.piece_pricing import get_buying_price_list

		company = frappe.db.get_value("Company", {}, "name")
		company_pl = frappe.get_cached_value("Company", company, "buying_price_list") if company else None
		if not company_pl:
			self.skipTest("Company has no buying_price_list configured")

		result = get_buying_price_list(company=company)
		self.assertEqual(result, company_pl)

	def test_buying_price_list_falls_back_to_settings(self):
		"""When company has no price list, settings.default_buying_price_list is used."""
		from glass_factory.glass_factory.piece_pricing import get_buying_price_list

		# Find any existing buying price list
		buying_pl = frappe.db.get_value("Price List", {"buying": 1}, "name")
		if not buying_pl:
			self.skipTest("No buying price list in test database")

		settings = frappe.get_single("Glass Factory Settings")
		original = settings.default_buying_price_list
		settings.default_buying_price_list = buying_pl
		settings.save(ignore_permissions=True)

		try:
			with patch(
				"glass_factory.glass_factory.piece_pricing.frappe.get_cached_value",
				return_value=None,
			):
				result = get_buying_price_list(company="NonexistentCompany")
			self.assertEqual(result, buying_pl)
		finally:
			settings.default_buying_price_list = original
			settings.save(ignore_permissions=True)
			frappe.db.commit()

	def test_operation_cost_rate_returns_from_settings(self):
		"""get_operation_cost_rate reads cost_rate from operation_rates table."""
		from glass_factory.glass_factory.spec_pricing import get_operation_cost_rate

		settings = frappe.get_single("Glass Factory Settings")
		settings.append("operation_rates", {
			"operation": "Temper",
			"currency": "USD",
			"pricing_basis": "Per Square Meter",
			"rate": 10,
			"cost_rate": 7,
			"enabled": 1,
		})
		settings.save(ignore_permissions=True)

		try:
			result = get_operation_cost_rate("Temper", "USD", "Per Square Meter")
			self.assertEqual(flt(result), 7.0)
		finally:
			settings.reload()
			settings.save(ignore_permissions=True)
			frappe.db.commit()

	def test_operation_cost_rate_returns_zero_when_not_set(self):
		"""get_operation_cost_rate returns 0 when no matching row exists."""
		from glass_factory.glass_factory.spec_pricing import get_operation_cost_rate

		result = get_operation_cost_rate("Laminate", "ZZZ_NONEXISTENT", "Per Square Meter")
		self.assertEqual(result, 0)

	def test_get_operation_rate_not_affected_by_disabled_rows(self):
		"""Disabled rows in operation_rates are ignored."""
		from glass_factory.glass_factory.spec_pricing import get_operation_rate

		settings = frappe.get_single("Glass Factory Settings")
		settings.append("operation_rates", {
			"operation": "Sandblast",
			"currency": "USD",
			"pricing_basis": "Per Square Meter",
			"rate": 999,
			"cost_rate": 0,
			"enabled": 0,
		})
		settings.save(ignore_permissions=True)

		try:
			# Disabled row should not be returned; will fall back to legacy or 0
			result = get_operation_rate("Sandblast", "USD", "Per Square Meter")
			self.assertNotEqual(result, 999, "Disabled row rate should not be used")
		finally:
			settings.reload()
			settings.save(ignore_permissions=True)
			frappe.db.commit()
