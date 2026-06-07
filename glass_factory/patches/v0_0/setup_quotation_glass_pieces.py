"""Add Quotation glass child table and hide auto-synced Quotation Item fields."""

import frappe

from glass_factory.install import create_glass_custom_fields


LEGACY_QUOTATION_FIELDS = ("cut_pieces", "glass_cut_pieces")


def execute():
	create_glass_custom_fields()
	_hide_quotation_item_glass_fields()
	_remove_legacy_quotation_fields()
	frappe.db.commit()


def _hide_quotation_item_glass_fields():
	for fieldname in (
		"gf_is_glass_item",
		"gf_glass_specification",
		"gf_raw_sheet_item",
		"gf_cut_wip_item",
		"gf_final_item",
		"gf_length_mm",
		"gf_width_mm",
		"gf_thickness_mm",
		"gf_processing_flags",
		"gf_area_m2",
		"gf_source_row_id",
	):
		if frappe.db.exists("Custom Field", {"dt": "Quotation Item", "fieldname": fieldname}):
			frappe.db.set_value(
				"Custom Field",
				{"dt": "Quotation Item", "fieldname": fieldname},
				{"hidden": 1, "read_only": 1, "allow_on_submit": 1},
				update_modified=True,
			)


def _remove_legacy_quotation_fields():
	for fieldname in LEGACY_QUOTATION_FIELDS:
		custom_field = frappe.db.get_value("Custom Field", {"dt": "Quotation", "fieldname": fieldname})
		if custom_field:
			frappe.delete_doc("Custom Field", custom_field, force=1, ignore_permissions=True)
