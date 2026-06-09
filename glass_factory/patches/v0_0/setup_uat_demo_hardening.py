"""Ensure demo-safe Glass Factory setup data for UAT and client demos."""

import frappe

from glass_factory.glass_factory.settings_validation import DEMO_ALLOWED_GLASS_TYPES
from glass_factory.install import (
	create_item_groups,
	create_warehouses,
	seed_glass_factory_settings,
)


def execute():
	create_item_groups()
	abbr = create_warehouses()
	seed_glass_factory_settings(abbr)
	_ensure_demo_glass_types()
	frappe.db.commit()
	frappe.clear_cache()


def _ensure_demo_glass_types():
	if not frappe.db.exists("DocType", "Glass Factory Settings"):
		return
	if not frappe.get_meta("Glass Factory Settings").has_field("allowed_glass_types"):
		return

	settings = frappe.get_single("Glass Factory Settings")
	current = (settings.allowed_glass_types or "").strip()
	if not current or current.upper() == "CLEAR":
		settings.allowed_glass_types = DEMO_ALLOWED_GLASS_TYPES
		settings.save(ignore_permissions=True)
