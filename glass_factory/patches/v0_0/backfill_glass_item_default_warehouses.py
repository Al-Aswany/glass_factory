"""Backfill Item Default.default_warehouse on existing GLS-* Items."""

import frappe

from glass_factory.glass_factory.item_resolver import backfill_glass_item_default_warehouse


def execute():
	item_codes = frappe.get_all(
		"Item",
		filters={"item_code": ["like", "GLS-%"]},
		pluck="name",
		limit=5000,
	)
	updated = 0
	for item_code in item_codes:
		if backfill_glass_item_default_warehouse(item_code):
			updated += 1

	frappe.db.commit()
	frappe.logger("glass_factory").info(f"Backfilled default warehouse on {updated} glass Item(s).")
