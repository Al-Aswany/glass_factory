"""Ensure Glass Factory appears on the Desk with a standard desktop icon."""

import frappe
from frappe.desk.doctype.desktop_icon.desktop_icon import clear_desktop_icons_cache


def execute():
	_ensure_desktop_icon()
	clear_desktop_icons_cache()


def _ensure_desktop_icon():
	fields = {
		"app": "glass_factory",
		"bg_color": "blue",
		"hidden": 0,
		"icon": "cutting",
		"icon_type": "Link",
		"idx": 50,
		"label": "Glass Factory",
		"link_to": "Glass Factory",
		"link_type": "Workspace Sidebar",
		"parent_icon": "",
		"standard": 1,
	}

	if frappe.db.exists("Desktop Icon", "Glass Factory"):
		frappe.db.set_value("Desktop Icon", "Glass Factory", fields, update_modified=True)
		return

	icon = frappe.new_doc("Desktop Icon")
	icon.name = "Glass Factory"
	icon.update(fields)
	icon.insert(ignore_permissions=True)
