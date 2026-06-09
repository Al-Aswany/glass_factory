"""Add auto-pricing fields to Glass Factory Settings and Quotation Glass Piece."""

import frappe


def execute():
	frappe.reload_doc("Glass Factory", "doctype", "glass_factory_settings")
	frappe.reload_doc("Glass Factory", "doctype", "quotation_glass_piece")
	frappe.db.commit()
