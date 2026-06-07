"""Add read-only glass dimension fields on Item and remove legacy duplicates."""

import frappe

from glass_factory.install import create_glass_custom_fields

LEGACY_ITEM_FIELDS = (
	"glass_type",
	"thickness_mm",
	"color_tint",
	"coating",
)


def execute():
	create_glass_custom_fields()
	_remove_legacy_item_fields()
	frappe.db.commit()


def _remove_legacy_item_fields():
	for fieldname in LEGACY_ITEM_FIELDS:
		custom_field = frappe.db.get_value("Custom Field", {"dt": "Item", "fieldname": fieldname})
		if custom_field:
			frappe.delete_doc("Custom Field", custom_field, force=1, ignore_permissions=True)
