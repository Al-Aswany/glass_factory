"""Desk app visibility for Glass Factory."""

from __future__ import annotations

import frappe

GLASS_APP_ROLES = (
	"Glass Sales User",
	"Glass Production Planner",
	"Glass Cutting Operator",
	"Glass Processing Operator",
	"Glass Stock User",
	"Glass Manager",
)


def has_app_permission() -> bool:
	if frappe.session.user == "Administrator":
		return True

	if frappe.session.data.user_type == "Website User":
		return False

	roles = set(frappe.get_roles())
	if "System Manager" in roles:
		return True

	return bool(roles.intersection(GLASS_APP_ROLES))
