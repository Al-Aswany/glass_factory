"""Phase 5: Production flow integration for spec-generated Sales Order Items."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	_reload_doctypes()
	_create_production_trace_custom_fields()
	frappe.db.commit()


def _reload_doctypes():
	for doctype in (
		"Cutting Job Piece",
		"Glass Processing Job",
		"Glass Processing Job Input",
		"Glass Processing Job Output",
		"Glass Processing Operation",
		"Cutting Job",
		"Cutting Job Source Sheet",
	):
		frappe.reload_doc("Glass Factory", "doctype", frappe.scrub(doctype))


def _create_production_trace_custom_fields():
	create_custom_fields(
		{
			"Stock Entry Detail": [
				{
					"fieldname": "gf_from_glass_specification",
					"label": "From Glass Specification",
					"fieldtype": "Check",
					"insert_after": "gf_glass_specification",
					"read_only": 1,
				},
				{
					"fieldname": "gf_technical_summary",
					"label": "Technical Summary",
					"fieldtype": "Small Text",
					"insert_after": "gf_from_glass_specification",
					"read_only": 1,
				},
			],
			"Delivery Note Item": [
				{
					"fieldname": "gf_from_glass_specification",
					"label": "From Glass Specification",
					"fieldtype": "Check",
					"insert_after": "gf_glass_specification",
					"read_only": 1,
				},
				{
					"fieldname": "gf_technical_summary",
					"label": "Technical Summary",
					"fieldtype": "Small Text",
					"insert_after": "gf_from_glass_specification",
					"read_only": 1,
				},
			],
		},
		update=True,
	)
