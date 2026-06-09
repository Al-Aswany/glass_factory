"""Item form hooks for read-only glass metadata derived from item codes."""

from __future__ import annotations

import frappe
from frappe.utils import flt

from glass_factory.glass_factory.item_resolver import (
	_parse_raw_item_code,
	infer_glass_role_from_item_code,
	get_allowed_glass_types,
	validate_glass_type,
)

GLASS_ROLES_REQUIRING_CODE = ("Raw Sheet", "Cut WIP", "Final", "Remnant")
GLASS_ITEM_CODE_HELP = (
	"Glass items must follow GLS-{TYPE}-{THICKNESS}MM-{LENGTH}X{WIDTH} "
	"(example: GLS-CLEAR-8MM-3210X2250)."
)


def sync_glass_item_from_code(doc, method=None):
	"""Populate read-only glass fields from the deterministic item code."""
	if doc.doctype != "Item":
		return

	item_code = doc.item_code or doc.name
	parsed = _parse_raw_item_code(item_code)

	if parsed:
		doc.gf_base_glass_type = parsed["base_glass_type"]
		doc.gf_thickness_mm = flt(parsed["thickness_mm"])
		doc.gf_length_mm = flt(parsed["length_mm"])
		doc.gf_width_mm = flt(parsed["width_mm"])
		if not doc.gf_glass_item_role:
			doc.gf_glass_item_role = infer_glass_role_from_item_code(item_code)
		return

	doc.gf_base_glass_type = ""
	doc.gf_thickness_mm = 0
	doc.gf_length_mm = 0
	doc.gf_width_mm = 0


def validate_glass_item(doc, method=None):
	"""Block glass-role Items that do not follow the required naming pattern."""
	if doc.doctype != "Item":
		return

	sync_glass_item_from_code(doc)

	role = doc.gf_glass_item_role
	if not role or role == "Scrap":
		return

	item_code = doc.item_code or doc.name
	if role in GLASS_ROLES_REQUIRING_CODE and not _parse_raw_item_code(item_code):
		frappe.throw(
			f"{GLASS_ITEM_CODE_HELP} Allowed glass types: {', '.join(get_allowed_glass_types())}. "
			f"Item <b>{frappe.utils.escape_html(item_code)}</b> cannot be used as {role}."
		)

	if role in GLASS_ROLES_REQUIRING_CODE:
		validate_glass_type(doc.gf_base_glass_type, context=f"Item {item_code}")
