"""Phase 5: Production integration for spec-generated Sales Order Items."""

from __future__ import annotations

import re

import frappe
from frappe.utils import cint, flt

from glass_factory.glass_factory.item_resolver import (
	build_glass_operation_code,
	get_item_glass_meta,
	parse_processing_flags,
	processing_flags_from_item_code,
)

OPERATION_DISPLAY = {
	"POL": "Polish",
	"BEV": "Bevel",
	"HOL": "Hole",
	"SHOL": "Special Hole",
	"SLT": "Slot",
	"SSLT": "Special Slot",
	"TMP": "Temper",
	"SBL": "Sandblast",
	"LAM": "Laminate",
}

COUNTED_OPERATIONS = frozenset({"HOL", "SHOL", "SLT", "SSLT"})
BOOLEAN_OPERATIONS = frozenset({"POL", "BEV", "TMP", "SBL", "LAM"})

_FLAG_PATTERN = re.compile(r"^(POL|BEV|TMP|SBL|LAM|HOL|SHOL|SLT|SSLT)(\d{2})?$")


def is_spec_production_row(row) -> bool:
	return cint(row.get("gf_from_glass_specification")) == 1


def is_glass_production_row(row) -> bool:
	"""True for Phase 0 glass_pieces rows and Phase 4 spec transaction rows."""
	return cint(row.get("gf_is_glass_item")) or is_spec_production_row(row)


def sales_order_has_glass_production_items(so) -> bool:
	return any(is_glass_production_row(item) for item in so.get("items") or [])


def _customer_name(customer: str | None) -> str:
	if not customer:
		return ""
	return frappe.db.get_value("Customer", customer, "customer_name") or customer


def _parse_operation_token(token: str) -> tuple[str, int]:
	match = _FLAG_PATTERN.match((token or "").strip().upper())
	if not match:
		return token or "", 1
	code = match.group(1)
	count = int(match.group(2)) if match.group(2) else 1
	return code, count


def processing_flags_from_spec_fields(row) -> str:
	"""Build processing_flags string from explicit spec operation fields."""
	return build_glass_operation_code(
		polish=cint(row.get("polish")),
		bevel=cint(row.get("bevel")),
		hole_count=cint(row.get("hole_count")),
		special_hole_count=cint(row.get("special_hole_count")),
		slot_count=cint(row.get("slot_count")),
		special_slot_count=cint(row.get("special_slot_count")),
		temper=cint(row.get("temper")),
		sandblast=cint(row.get("sandblast")),
		laminate=cint(row.get("laminate")),
	)


def _spec_doc_fields(spec_name: str) -> dict:
	spec = frappe.get_doc("Glass Product Specification", spec_name)
	return {
		"glass_type": spec.glass_type,
		"thickness_mm": flt(spec.thickness_mm),
		"length_mm": flt(spec.length_mm),
		"width_mm": flt(spec.width_mm),
		"area_m2": flt(spec.area_m2),
		"total_area_m2": flt(spec.total_area_m2 or spec.area_m2),
		"polish": cint(spec.polish),
		"bevel": cint(spec.bevel),
		"temper": cint(spec.temper),
		"sandblast": cint(spec.sandblast),
		"laminate": cint(spec.laminate),
		"hole_count": cint(spec.hole_count),
		"special_hole_count": cint(spec.special_hole_count),
		"slot_count": cint(spec.slot_count),
		"special_slot_count": cint(spec.special_slot_count),
		"technical_summary": spec.technical_summary or "",
		"design_attachment_summary": spec.get("design_attachment_summary") or "",
		"selling_rate_per_m2": flt(spec.selling_rate_per_m2),
		"rate_per_piece": flt(spec.rate_per_piece),
		"raw_sheet_item": spec.raw_item_code or spec.raw_sheet_item,
		"cut_wip_item": spec.cut_wip_item_code,
		"final_item": spec.final_item_code or spec.generated_item,
		"processing_flags": processing_flags_from_spec_fields(spec),
	}


def _dimensions_from_final_item(final_item: str) -> dict:
	meta = get_item_glass_meta(final_item)
	return {
		"length_mm": flt(meta.get("gf_length_mm")),
		"width_mm": flt(meta.get("gf_width_mm")),
		"thickness_mm": flt(meta.get("gf_thickness_mm")),
		"glass_type": meta.get("gf_base_glass_type") or "",
		"processing_flags": "-".join(processing_flags_from_item_code(final_item)),
	}


def validate_spec_so_item_for_production(item, *, context: str = "Sales Order Item") -> None:
	"""Ensure a spec-generated SO row has everything production needs."""
	if not is_spec_production_row(item):
		return

	label = f"{context} {item.get('name') or item.idx}"
	final_item = item.get("gf_final_item") or item.get("item_code")
	if not final_item:
		frappe.throw(f"{label} is from Glass Product Specification but has no generated Final Item.")
	if not item.get("gf_cut_wip_item"):
		frappe.throw(f"{label} is from Glass Product Specification but has no Cut WIP Item.")
	if not item.get("gf_raw_sheet_item"):
		frappe.throw(f"{label} is from Glass Product Specification but has no Raw Sheet Item.")

	length = flt(item.get("gf_length_mm"))
	width = flt(item.get("gf_width_mm"))
	thickness = flt(item.get("gf_thickness_mm"))
	if length <= 0 or width <= 0 or thickness <= 0:
		dims = _dimensions_from_final_item(final_item)
		length = length or dims["length_mm"]
		width = width or dims["width_mm"]
		thickness = thickness or dims["thickness_mm"]
	if length <= 0 or width <= 0 or thickness <= 0:
		frappe.throw(f"{label} is from Glass Product Specification but has no valid dimensions.")


def enrich_spec_transaction_row(spec) -> dict:
	"""Production metadata copied onto Quotation/Sales Order items from a spec."""
	flags = processing_flags_from_spec_fields(spec)
	return {
		"gf_is_glass_item": 1,
		"gf_length_mm": flt(spec.length_mm),
		"gf_width_mm": flt(spec.width_mm),
		"gf_thickness_mm": flt(spec.thickness_mm),
		"gf_processing_flags": flags,
	}


def build_cutting_piece_from_so_item(so, item, remaining: float) -> dict:
	"""Build a Cutting Job Piece row dict from a Sales Order Item."""
	is_spec = is_spec_production_row(item)
	if is_spec:
		validate_spec_so_item_for_production(item, context=f"Sales Order Item row {item.idx}")

	final_item = item.get("gf_final_item") or item.item_code
	raw_item = item.get("gf_raw_sheet_item")
	cut_item = item.get("gf_cut_wip_item")
	length = flt(item.get("gf_length_mm"))
	width = flt(item.get("gf_width_mm"))
	thickness = flt(item.get("gf_thickness_mm"))
	flags = item.get("gf_processing_flags") or ""
	glass_spec = item.get("gf_glass_specification") or ""
	if not is_spec:
		glass_spec = ""
	technical_summary = item.get("gf_technical_summary") or ""
	design_summary = item.get("gf_design_attachment_summary") or ""
	area_m2 = flt(item.get("gf_area_m2"))
	total_area_m2 = flt(item.get("gf_total_area_m2") or area_m2)
	glass_type = ""
	polish = bevel = temper = sandblast = laminate = 0
	hole_count = special_hole_count = slot_count = special_slot_count = 0
	selling_rate = flt(item.get("gf_selling_rate_per_m2"))
	rate_per_piece = flt(item.get("gf_rate_per_piece") or item.get("rate"))

	if is_spec and glass_spec and frappe.db.exists("Glass Product Specification", glass_spec):
		spec_fields = _spec_doc_fields(glass_spec)
		glass_type = spec_fields["glass_type"]
		length = length or spec_fields["length_mm"]
		width = width or spec_fields["width_mm"]
		thickness = thickness or spec_fields["thickness_mm"]
		flags = flags or spec_fields["processing_flags"]
		technical_summary = technical_summary or spec_fields["technical_summary"]
		design_summary = design_summary or spec_fields["design_attachment_summary"]
		area_m2 = area_m2 or spec_fields["area_m2"]
		total_area_m2 = total_area_m2 or spec_fields["total_area_m2"]
		polish = spec_fields["polish"]
		bevel = spec_fields["bevel"]
		temper = spec_fields["temper"]
		sandblast = spec_fields["sandblast"]
		laminate = spec_fields["laminate"]
		hole_count = spec_fields["hole_count"]
		special_hole_count = spec_fields["special_hole_count"]
		slot_count = spec_fields["slot_count"]
		special_slot_count = spec_fields["special_slot_count"]
		selling_rate = selling_rate or spec_fields["selling_rate_per_m2"]
		rate_per_piece = rate_per_piece or spec_fields["rate_per_piece"]
		raw_item = raw_item or spec_fields["raw_sheet_item"]
		cut_item = cut_item or spec_fields["cut_wip_item"]
		final_item = final_item or spec_fields["final_item"]

	if not length or not width or not thickness:
		dims = _dimensions_from_final_item(final_item)
		length = length or dims["length_mm"]
		width = width or dims["width_mm"]
		thickness = thickness or dims["thickness_mm"]
		glass_type = glass_type or dims["glass_type"]
		flags = flags or dims["processing_flags"]

	if not flags and any(
		(polish, bevel, temper, sandblast, laminate, hole_count, special_hole_count, slot_count, special_slot_count)
	):
		flags = processing_flags_from_spec_fields(
			frappe._dict(
				polish=polish,
				bevel=bevel,
				temper=temper,
				sandblast=sandblast,
				laminate=laminate,
				hole_count=hole_count,
				special_hole_count=special_hole_count,
				slot_count=slot_count,
				special_slot_count=special_slot_count,
			)
		)

	return {
		"sales_order": so.name,
		"sales_order_item": item.name,
		"customer": so.customer,
		"customer_name": so.customer_name or _customer_name(so.customer),
		"raw_sheet_item": raw_item,
		"cut_wip_item": cut_item,
		"final_item": final_item,
		"length_mm": length,
		"width_mm": width,
		"thickness_mm": thickness,
		"glass_type": glass_type,
		"processing_flags": flags,
		"glass_specification": glass_spec,
		"from_glass_specification": 1 if is_spec else 0,
		"area_m2": area_m2,
		"total_area_m2": total_area_m2 or flt(area_m2 * remaining, 6),
		"polish": polish,
		"bevel": bevel,
		"temper": temper,
		"sandblast": sandblast,
		"laminate": laminate,
		"hole_count": hole_count,
		"special_hole_count": special_hole_count,
		"slot_count": slot_count,
		"special_slot_count": special_slot_count,
		"technical_summary": technical_summary,
		"design_attachment_summary": design_summary,
		"selling_rate_per_m2": selling_rate,
		"rate_per_piece": rate_per_piece,
		"qty_required": remaining,
		"qty_assigned": remaining,
		"qty_cut": remaining,
	}


def build_processing_operations_from_piece(piece, qty: float) -> list[dict]:
	"""Create Glass Processing Operation row dicts with counts preserved."""
	base = {
		"sales_order": piece.get("sales_order"),
		"sales_order_item": piece.get("sales_order_item"),
		"qty": qty,
		"status": "Pending",
		"glass_specification": piece.get("glass_specification"),
		"from_glass_specification": cint(piece.get("from_glass_specification")),
	}

	if cint(piece.get("from_glass_specification")):
		return _spec_operations(piece, base)

	operations: list[dict] = []
	for token in (piece.get("processing_flags") or "").split("-"):
		if not token:
			continue
		code, count = _parse_operation_token(token)
		label = OPERATION_DISPLAY.get(code, code)
		if count > 1:
			label = f"{label} × {count}"
		operations.append(
			{
				**base,
				"operation": code,
				"operation_label": label,
				"operation_count": count,
			}
		)
	return operations


def _spec_operations(piece, base: dict) -> list[dict]:
	operations: list[dict] = []

	def append_bool(field: str, code: str) -> None:
		if cint(piece.get(field)):
			operations.append(
				{
					**base,
					"operation": code,
					"operation_label": OPERATION_DISPLAY[code],
					"operation_count": 1,
				}
			)

	append_bool("polish", "POL")
	append_bool("bevel", "BEV")

	for field, code in (
		("hole_count", "HOL"),
		("special_hole_count", "SHOL"),
		("slot_count", "SLT"),
		("special_slot_count", "SSLT"),
	):
		count = cint(piece.get(field))
		if count > 0:
			label = OPERATION_DISPLAY[code]
			if count > 1:
				label = f"{label} × {count}"
			operations.append(
				{
					**base,
					"operation": code,
					"operation_label": label,
					"operation_count": count,
				}
			)

	append_bool("temper", "TMP")
	append_bool("sandblast", "SBL")
	append_bool("laminate", "LAM")
	return operations


def piece_has_processing(piece) -> bool:
	if cint(piece.get("from_glass_specification")):
		return bool(build_processing_operations_from_piece(piece, 1))
	return bool(parse_processing_flags(piece.get("processing_flags")))


def build_processing_input_row(piece, qty: float) -> dict:
	return {
		"cut_wip_item": piece.cut_wip_item,
		"sales_order": piece.sales_order,
		"sales_order_item": piece.sales_order_item,
		"glass_specification": piece.get("glass_specification"),
		"from_glass_specification": cint(piece.get("from_glass_specification")),
		"customer": piece.get("customer"),
		"customer_name": piece.get("customer_name"),
		"length_mm": flt(piece.get("length_mm")),
		"width_mm": flt(piece.get("width_mm")),
		"qty": qty,
		"area_m2": flt(piece.get("area_m2")),
		"technical_summary": piece.get("technical_summary") or "",
		"design_attachment_summary": piece.get("design_attachment_summary") or "",
	}


def build_processing_output_row(piece, qty: float) -> dict:
	return {
		"final_item": piece.final_item,
		"sales_order": piece.sales_order,
		"sales_order_item": piece.sales_order_item,
		"glass_specification": piece.get("glass_specification"),
		"from_glass_specification": cint(piece.get("from_glass_specification")),
		"customer": piece.get("customer"),
		"customer_name": piece.get("customer_name"),
		"length_mm": flt(piece.get("length_mm")),
		"width_mm": flt(piece.get("width_mm")),
		"qty": qty,
		"area_m2": flt(piece.get("area_m2")),
		"technical_summary": piece.get("technical_summary") or "",
		"design_attachment_summary": piece.get("design_attachment_summary") or "",
	}


def resolve_processing_job_customer(rows: list) -> tuple[str | None, str | None, str]:
	customers = {row.get("customer") for row in rows if row.get("customer")}
	if len(customers) == 1:
		customer = next(iter(customers))
		name = next((row.get("customer_name") for row in rows if row.get("customer") == customer), "")
		if not name:
			name = _customer_name(customer)
		return customer, name, name
	if len(customers) > 1:
		return None, None, "Multiple Customers"
	return None, None, ""


def stock_entry_trace_fields(piece) -> dict:
	return {
		"gf_sales_order": piece.get("sales_order"),
		"gf_sales_order_item": piece.get("sales_order_item"),
		"gf_glass_specification": piece.get("glass_specification"),
		"gf_from_glass_specification": cint(piece.get("from_glass_specification")),
		"gf_technical_summary": piece.get("technical_summary") or "",
	}
