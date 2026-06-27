"""Switch glass stock traceability from Serial No to Batch."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from glass_factory.glass_factory.item_resolver import BATCH_TRACKED_ROLES


def execute():
	_create_batch_custom_fields()
	_enable_batch_tracking_on_glass_items()
	_reload_source_sheet_doctype()
	frappe.clear_cache()


def _create_batch_custom_fields():
	create_custom_fields(
		{
			"Batch": [
				{"fieldname": "gf_glass_section", "fieldtype": "Section Break", "label": "Glass", "insert_after": "item", "collapsible": 1},
				{"fieldname": "gf_cutting_job", "label": "Source Cutting Job", "fieldtype": "Link", "options": "Cutting Job", "insert_after": "gf_glass_section"},
				{"fieldname": "gf_length_mm", "label": "Length (mm)", "fieldtype": "Float", "insert_after": "gf_cutting_job"},
				{"fieldname": "gf_width_mm", "label": "Width (mm)", "fieldtype": "Float", "insert_after": "gf_length_mm"},
				{"fieldname": "gf_area_m2", "label": "Area (m²)", "fieldtype": "Float", "insert_after": "gf_width_mm", "read_only": 1},
			],
		},
		ignore_validate=True,
	)
	frappe.db.commit()


def _enable_batch_tracking_on_glass_items():
	roles = list(BATCH_TRACKED_ROLES)
	items = frappe.get_all(
		"Item",
		filters={"gf_glass_item_role": ["in", roles]},
		pluck="name",
	)
	for item_code in items:
		frappe.db.set_value(
			"Item",
			item_code,
			{"has_batch_no": 1, "has_serial_no": 0},
			update_modified=False,
		)
	frappe.db.commit()


def _reload_source_sheet_doctype():
	frappe.reload_doc("glass_factory", "doctype", "cutting_job_source_sheet")
