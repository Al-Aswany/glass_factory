"""Retire legacy Phase 0 DocTypes that conflict with the manual MVP model."""

import frappe

LEGACY_DOCTYPES = (
	"Glass Cut Piece",
	"Cutting Job Linked SO",
	"Cutting Job Tabular File",
)


def execute():
	_remove_workspace_links()
	for doctype in LEGACY_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		frappe.db.set_value(
			"DocType",
			doctype,
			{
				"read_only": 1,
				"show_name_in_global_search": 0,
			},
			update_modified=False,
		)

	frappe.db.commit()


def _remove_workspace_links():
	for workspace_name in frappe.get_all("Workspace", filters={"module": "Glass Factory"}, pluck="name"):
		workspace = frappe.get_doc("Workspace", workspace_name)
		original_count = len(workspace.links or [])
		workspace.links = [
			link for link in (workspace.links or []) if link.link_to not in LEGACY_DOCTYPES
		]
		if len(workspace.links) != original_count:
			workspace.save(ignore_permissions=True)
