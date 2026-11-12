"""Permission checks for Glass Factory mutations.

Stock Entry inserts still use ignore_permissions because cutting and processing
operators are not given Stock Entry DocPerms. That bypass runs only after the
job write permission and an explicit role check succeed.
"""

from __future__ import annotations

import frappe
from frappe import _

from glass_factory.glass_factory.permissions import GLASS_APP_ROLES

CUTTING_STOCK_ROLES = (
	"System Manager",
	"Glass Manager",
	"Glass Stock User",
	"Glass Cutting Operator",
)
PROCESSING_STOCK_ROLES = (
	"System Manager",
	"Glass Manager",
	"Glass Stock User",
	"Glass Processing Operator",
)


COST_VIEW_ROLES = ("System Manager", "Glass Manager", "Glass Production Planner")


def can_view_costs() -> bool:
	"""Internal costs and margins are for authorized internal users only."""
	if frappe.session.user == "Administrator":
		return True
	return bool(set(frappe.get_roles()).intersection(COST_VIEW_ROLES))


def require_cost_access() -> None:
	if not can_view_costs():
		frappe.throw(
			_("You do not have permission to view internal glass costs."),
			frappe.PermissionError,
		)


def require_doc_permission(doc, ptype: str, message: str | None = None) -> None:
	"""Require ptype on this document, including company user permissions."""
	if frappe.has_permission(doc.doctype, ptype, doc=doc):
		return
	frappe.throw(
		message or _("You do not have permission to {0} {1} {2}.").format(ptype, doc.doctype, doc.name),
		frappe.PermissionError,
	)


def require_doctype_permission(doctype: str, ptype: str, *, company: str | None = None, message: str | None = None) -> None:
	probe = frappe.new_doc(doctype)
	if company and probe.meta.has_field("company"):
		probe.company = company
	if frappe.has_permission(doctype, ptype, doc=probe):
		return
	frappe.throw(
		message or _("You do not have permission to {0} {1}.").format(ptype, doctype),
		frappe.PermissionError,
	)


def require_stock_movement_authority(job, flow: str) -> None:
	"""Authorize a Stock Entry bypass for a job the user can already write."""
	require_doc_permission(job, "write")
	allowed = set(CUTTING_STOCK_ROLES if flow == "Raw to Cut WIP" else PROCESSING_STOCK_ROLES)
	if set(frappe.get_roles()).intersection(allowed):
		return
	frappe.throw(
		_("You do not have permission to post stock for {0} {1}.").format(job.doctype, job.name),
		frappe.PermissionError,
	)


def require_glass_document_write(doctype: str, name: str):
	doc = frappe.get_doc(doctype, name)
	if cint_docstatus_cancelled(doc):
		frappe.throw(_("{0} {1} is cancelled.").format(doctype, name))
	require_doc_permission(doc, "write")
	return doc


def cint_docstatus_cancelled(doc) -> bool:
	return int(doc.docstatus or 0) == 2


def require_logged_in_glass_user() -> None:
	if frappe.session.user == "Administrator":
		return
	if frappe.session.user in (None, "Guest"):
		frappe.throw(_("Login required."), frappe.PermissionError)
	roles = set(frappe.get_roles())
	if "System Manager" in roles or roles.intersection(GLASS_APP_ROLES):
		return
	frappe.throw(_("You do not have permission to use Glass Factory."), frappe.PermissionError)
