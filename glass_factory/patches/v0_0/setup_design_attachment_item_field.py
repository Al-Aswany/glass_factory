"""Add Item link field for Glass Product Specification design propagation."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Item": [
				{
					"fieldname": "custom_glass_product_specification",
					"label": "Glass Product Specification",
					"fieldtype": "Link",
					"options": "Glass Product Specification",
					"insert_after": "gf_sheet_area_m2",
					"read_only": 1,
					"description": "Linked specification whose design files are carried on this item.",
				}
			]
		},
		update=True,
	)
	frappe.db.commit()
