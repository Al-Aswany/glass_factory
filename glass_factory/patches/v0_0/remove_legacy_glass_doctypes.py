"""Delete legacy DocTypes superseded by the manual MVP model."""

import frappe

LEGACY_DOCTYPES = (
	"Glass Cut Piece",
	"Cutting Job Linked SO",
	"Cutting Job Tabular File",
	"Glass Cutting Settings",
)


def execute():
	_remove_workspace_links()
	_delete_legacy_doctypes()
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


def _delete_legacy_doctypes():
	for doctype in LEGACY_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		frappe.delete_doc("DocType", doctype, force=1, ignore_permissions=True)
