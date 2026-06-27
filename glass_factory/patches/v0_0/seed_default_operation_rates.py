"""Seed default flexible operation rates on existing Glass Factory Settings."""

import frappe

from glass_factory.install import ensure_default_operation_rates


def execute():
	if not frappe.db.exists("DocType", "Glass Factory Settings"):
		return

	settings = frappe.get_single("Glass Factory Settings")
	ensure_default_operation_rates(settings)
	settings.save(ignore_permissions=True)
	frappe.db.commit()
