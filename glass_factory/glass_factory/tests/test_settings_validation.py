from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

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
