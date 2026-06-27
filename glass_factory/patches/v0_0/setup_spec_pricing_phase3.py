"""Reload doctypes for Glass Product Specification Phase 3 pricing."""

import frappe


def execute():
	frappe.reload_doc("Glass Factory", "doctype", "glass_operation_rate")
	frappe.reload_doc("Glass Factory", "doctype", "glass_factory_settings")
	frappe.reload_doc("Glass Factory", "doctype", "glass_product_specification")
	frappe.db.commit()
