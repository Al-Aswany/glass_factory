"""Remove redundant glass metadata custom fields from Item."""

import frappe

REMOVED_ITEM_FIELDS = (
	"gf_glass_specification",
	"gf_base_glass_type",
	"gf_thickness_mm",
	"gf_length_mm",
	"gf_width_mm",
	"gf_processing_flags",
	"gf_is_auto_created_glass_item",
)


def execute():
	for fieldname in REMOVED_ITEM_FIELDS:
		custom_field = frappe.db.get_value("Custom Field", {"dt": "Item", "fieldname": fieldname})
		if custom_field:
			frappe.delete_doc("Custom Field", custom_field, force=1, ignore_permissions=True)

	frappe.db.commit()
