"""Ensure raw sheet and remnant Items are purchase-only (not for sale)."""

import frappe
from frappe.utils import cint


def execute():
	roles = ("Raw Sheet", "Remnant")
	item_codes = frappe.get_all(
		"Item",
		filters={"gf_glass_item_role": ["in", roles]},
		pluck="name",
		limit=5000,
	)
	updated = 0
	for item_code in item_codes:
		values = {}
		if cint(frappe.db.get_value("Item", item_code, "is_sales_item")):
			values["is_sales_item"] = 0
		if not cint(frappe.db.get_value("Item", item_code, "is_purchase_item")):
			values["is_purchase_item"] = 1
		if values:
			frappe.db.set_value("Item", item_code, values, update_modified=False)
			updated += 1

	frappe.db.commit()
	frappe.logger("glass_factory").info(
		f"Updated purchase flags on {updated} raw sheet / remnant Item(s)."
	)
