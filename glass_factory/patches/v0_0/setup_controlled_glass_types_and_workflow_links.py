"""Add controlled glass types, Sales Order glass entry, and workflow trace links."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	_create_custom_fields()
	_update_settings()
	_update_custom_field_properties()
	_update_doctype_properties()
	frappe.clear_cache()


def _create_custom_fields():
	create_custom_fields(
		{
			"Sales Order": [
				{"fieldname": "gf_glass_section", "fieldtype": "Section Break", "label": "Glass Pieces", "insert_after": "items"},
				{
					"fieldname": "glass_pieces",
					"fieldtype": "Table",
					"label": "Glass Pieces",
					"options": "Quotation Glass Piece",
					"insert_after": "gf_glass_section",
				},
			],
			"Stock Entry Detail": [
				{
					"fieldname": "gf_cutting_job",
					"label": "Cutting Job",
					"fieldtype": "Link",
					"options": "Cutting Job",
					"insert_after": "item_code",
					"read_only": 1,
				},
			],
		},
		ignore_validate=True,
	)


def _update_settings():
	if not frappe.db.exists("DocType", "Glass Factory Settings"):
		return
	if not frappe.get_meta("Glass Factory Settings").has_field("allowed_glass_types"):
		return
	settings = frappe.get_single("Glass Factory Settings")
	if not settings.allowed_glass_types:
		settings.allowed_glass_types = "CLEAR"
		settings.save(ignore_permissions=True)


def _update_custom_field_properties():
	for dt, fieldname in (("Stock Entry", "gf_cutting_job"), ("Stock Entry", "gf_processing_job")):
		name = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fieldname})
		if name:
			frappe.db.set_value("Custom Field", name, "allow_on_submit", 1, update_modified=False)
	name = frappe.db.get_value("Custom Field", {"dt": "Item", "fieldname": "gf_base_glass_type"})
	if name:
		frappe.db.set_value(
			"Custom Field",
			name,
			"description",
			"Allowed glass types are configured in Glass Factory Settings.",
			update_modified=False,
		)


def _update_doctype_properties():
	_update_docfield("Cutting Job", "linked_stock_entry", label="Cutting Stock Movement")
	_update_docfield("Glass Processing Job", "linked_stock_entry", label="Final Stock Movement")
	_update_docfield("Glass Processing Operation", "status", allow_on_submit=1)
	_update_docfield("Glass Processing Operation", "notes", allow_on_submit=1)

	status_name = frappe.db.get_value("DocField", {"parent": "Cutting Job", "fieldname": "status"})
	if status_name:
		options = frappe.db.get_value("DocField", status_name, "options") or ""
		if "Processing Started" not in options.split("\n"):
			parts = options.split("\n")
			insert_at = parts.index("Completed") if "Completed" in parts else len(parts)
			parts.insert(insert_at, "Processing Started")
			frappe.db.set_value("DocField", status_name, "options", "\n".join(parts), update_modified=False)

	if not frappe.db.exists("DocField", {"parent": "Cutting Job", "fieldname": "linked_processing_job"}):
		idx = (frappe.db.count("DocField", {"parent": "Cutting Job"}) or 0) + 1
		frappe.get_doc(
			{
				"doctype": "DocField",
				"parent": "Cutting Job",
				"parenttype": "DocType",
				"parentfield": "fields",
				"idx": idx,
				"allow_on_submit": 1,
				"fieldname": "linked_processing_job",
				"fieldtype": "Link",
				"label": "Glass Processing Job",
				"options": "Glass Processing Job",
				"read_only": 1,
			}
		).insert(ignore_permissions=True)



def _update_docfield(parent: str, fieldname: str, **values):
	name = frappe.db.get_value("DocField", {"parent": parent, "fieldname": fieldname})
	if name:
		frappe.db.set_value("DocField", name, values, update_modified=False)
