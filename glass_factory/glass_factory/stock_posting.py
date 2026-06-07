"""Manual Stock Entry builders for Phase 0 glass processing."""

from __future__ import annotations

import frappe
from frappe.utils import flt, nowdate, nowtime

from erpnext.stock.utils import get_incoming_rate

from glass_factory.glass_factory.item_resolver import (
	_parse_raw_item_code,
	ensure_remnant_item,
	get_scrap_item,
	item_role,
)


def build_cutting_repack(cutting_job):
	"""Build Repack #1: Raw Sheet/Remnant -> Cut WIP + Remnant + Scrap."""
	_validate_cutting_job(cutting_job)
	settings = _settings()
	company = _company_from_job(cutting_job)

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Repack"
	se.purpose = "Repack"
	se.company = company
	se.posting_date = nowdate()
	se.posting_time = nowtime()
	se.gf_cutting_job = cutting_job.name
	se.gf_glass_stock_flow = "Raw to Cut WIP"
	se.gf_created_by_glass_factory = 1

	for source in cutting_job.get("source_sheets") or []:
		qty = flt(source.get("qty_consumed") or source.get("qty") or 1)
		if qty <= 0:
			continue
		role = source.get("source_role") or item_role(source.item_code)
		if role not in ("Raw Sheet", "Remnant"):
			frappe.throw(f"Source sheet row {source.idx}: Item must be Raw Sheet or Remnant.")
		se.append("items", {
			"item_code": source.item_code,
			"s_warehouse": source.warehouse or settings.raw_warehouse,
			"qty": qty,
			"transfer_qty": qty,
			"uom": _stock_uom(source.item_code),
			"stock_uom": _stock_uom(source.item_code),
			"conversion_factor": 1,
			"serial_no": source.get("serial_no"),
			"gf_source_item_role": role,
		})

	for piece in cutting_job.get("pieces") or []:
		qty = flt(piece.get("qty_cut") or piece.get("qty_required") or piece.get("qty") or 0)
		if qty <= 0:
			continue
		if not piece.get("cut_wip_item"):
			frappe.throw(f"Piece row {piece.idx}: Cut WIP Item is required.")
		if item_role(piece.cut_wip_item) != "Cut WIP":
			frappe.throw(f"Piece row {piece.idx}: Output item must be Cut WIP.")
		se.append("items", {
			"item_code": piece.cut_wip_item,
			"t_warehouse": piece.get("target_warehouse") or settings.cut_wip_warehouse,
			"qty": qty,
			"transfer_qty": qty,
			"uom": _stock_uom(piece.cut_wip_item),
			"stock_uom": _stock_uom(piece.cut_wip_item),
			"conversion_factor": 1,
			"is_finished_item": 1,
			"gf_sales_order": piece.sales_order,
			"gf_sales_order_item": piece.sales_order_item,
			"gf_glass_specification": piece.get("glass_specification"),
			"gf_source_item_role": "Cut WIP",
		})

	for source in cutting_job.get("source_sheets") or []:
		if flt(source.get("remnant_qty")) > 0:
			remnant_item = source.get("remnant_item") or ensure_remnant_item(source.item_code, source.remnant_length_mm, source.remnant_width_mm)
			se.append("items", {
				"item_code": remnant_item,
				"t_warehouse": settings.remnants_warehouse,
				"qty": flt(source.remnant_qty),
				"transfer_qty": flt(source.remnant_qty),
				"uom": _stock_uom(remnant_item),
				"stock_uom": _stock_uom(remnant_item),
				"conversion_factor": 1,
				"is_finished_item": 1,
				"gf_source_item_role": "Remnant",
			})
		if flt(source.get("scrap_qty")) > 0:
			scrap_item = get_scrap_item()
			se.append("items", {
				"item_code": scrap_item,
				"t_warehouse": settings.scrap_warehouse,
				"qty": flt(source.scrap_qty),
				"transfer_qty": flt(source.scrap_qty),
				"uom": _stock_uom(scrap_item),
				"stock_uom": _stock_uom(scrap_item),
				"conversion_factor": 1,
				"is_finished_item": 1,
				"gf_source_item_role": "Scrap",
			})

	if not se.items:
		frappe.throw("Cutting Repack has no rows to post.")

	_allocate_cutting_repack_rates(se, cutting_job)
	return se


def build_processing_repack(processing_job):
	"""Build Repack #2: Cut WIP -> exact final Sales Order Item."""
	_validate_processing_job(processing_job)
	settings = _settings()
	company = _company_from_processing_job(processing_job)

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Repack"
	se.purpose = "Repack"
	se.company = company
	se.posting_date = nowdate()
	se.posting_time = nowtime()
	se.gf_processing_job = processing_job.name
	se.gf_glass_stock_flow = "Cut WIP to Final"
	se.gf_created_by_glass_factory = 1

	for row in processing_job.get("inputs") or []:
		qty = flt(row.get("qty") or 0)
		if qty <= 0:
			continue
		if item_role(row.cut_wip_item) != "Cut WIP":
			frappe.throw(f"Input row {row.idx}: source must be Cut WIP.")
		se.append("items", {
			"item_code": row.cut_wip_item,
			"s_warehouse": row.get("warehouse") or settings.cut_wip_warehouse,
			"qty": qty,
			"transfer_qty": qty,
			"uom": _stock_uom(row.cut_wip_item),
			"stock_uom": _stock_uom(row.cut_wip_item),
			"conversion_factor": 1,
			"gf_sales_order": row.sales_order,
			"gf_sales_order_item": row.sales_order_item,
			"gf_glass_specification": row.get("glass_specification"),
			"gf_source_item_role": "Cut WIP",
		})

	for row in processing_job.get("outputs") or []:
		qty = flt(row.get("qty") or 0)
		if qty <= 0:
			continue
		if item_role(row.final_item) != "Final":
			frappe.throw(f"Output row {row.idx}: target must be Final.")
		_so_item = frappe.db.get_value("Sales Order Item", row.sales_order_item, ["item_code", "gf_final_item"], as_dict=True)
		if _so_item and row.final_item != (_so_item.gf_final_item or _so_item.item_code):
			frappe.throw(f"Output row {row.idx}: final Item must match the Sales Order Item.")
		row_data = {
			"item_code": row.final_item,
			"t_warehouse": row.get("warehouse") or settings.final_goods_warehouse,
			"qty": qty,
			"transfer_qty": qty,
			"uom": _stock_uom(row.final_item),
			"stock_uom": _stock_uom(row.final_item),
			"conversion_factor": 1,
			"is_finished_item": 1,
			"gf_sales_order": row.sales_order,
			"gf_sales_order_item": row.sales_order_item,
			"gf_glass_specification": row.get("glass_specification"),
			"gf_source_item_role": "Final",
		}
		override_rate = flt(row.get("basic_rate"))
		if override_rate > 0:
			row_data["set_basic_rate_manually"] = 1
			row_data["basic_rate"] = override_rate
		se.append("items", row_data)

	if not se.items:
		frappe.throw("Processing Repack has no rows to post.")
	return se


def _validate_cutting_job(cutting_job):
	if not cutting_job.get("source_sheets"):
		frappe.throw("Add at least one source sheet before creating Repack #1.")
	if not cutting_job.get("pieces"):
		frappe.throw("Add at least one cutting piece before creating Repack #1.")


def _validate_processing_job(processing_job):
	if not processing_job.get("inputs"):
		frappe.throw("Add at least one Cut WIP input before creating Repack #2.")
	if not processing_job.get("outputs"):
		frappe.throw("Add at least one Final output before creating Repack #2.")


def _settings():
	if not frappe.db.exists("DocType", "Glass Factory Settings"):
		frappe.throw("Configure Glass Factory Settings before posting glass stock entries.")
	settings = frappe.get_single("Glass Factory Settings")
	return frappe._dict({
		"raw_warehouse": settings.raw_warehouse,
		"cut_wip_warehouse": settings.cut_wip_warehouse,
		"final_goods_warehouse": settings.final_goods_warehouse,
		"remnants_warehouse": settings.remnants_warehouse,
		"scrap_warehouse": settings.scrap_warehouse,
	})


def _company_from_job(cutting_job):
	for row in cutting_job.get("sales_orders") or []:
		if row.sales_order:
			company = frappe.db.get_value("Sales Order", row.sales_order, "company")
			if company:
				return company
	return frappe.defaults.get_user_default("Company") or frappe.db.get_value("Company", {"is_group": 0}, "name")


def _company_from_processing_job(processing_job):
	for row in processing_job.get("outputs") or []:
		if row.sales_order:
			company = frappe.db.get_value("Sales Order", row.sales_order, "company")
			if company:
				return company
	return frappe.defaults.get_user_default("Company") or frappe.db.get_value("Company", {"is_group": 0}, "name")


def _stock_uom(item_code):
	return frappe.db.get_value("Item", item_code, "stock_uom") or "Nos"


def _item_area_m2(item_code: str) -> float:
	parsed = _parse_raw_item_code(item_code)
	if not parsed:
		return 0
	return flt(parsed["length_mm"]) * flt(parsed["width_mm"]) / 1_000_000


def _source_row_rate(se, row) -> float:
	args = frappe._dict(
		{
			"item_code": row.item_code,
			"warehouse": row.s_warehouse,
			"posting_date": se.posting_date,
			"posting_time": se.posting_time,
			"qty": -flt(row.transfer_qty),
			"voucher_type": se.doctype,
			"voucher_no": se.name,
			"company": se.company,
			"serial_no": row.get("serial_no"),
		}
	)
	rate = flt(get_incoming_rate(args, raise_error_if_no_rate=False))
	if rate > 0:
		return rate

	return flt(
		frappe.db.get_value(
			"Bin",
			{"item_code": row.item_code, "warehouse": row.s_warehouse},
			"valuation_rate",
		)
	)


def _sync_manual_row_amounts(se) -> None:
	"""ERPNext skips amount calculation for rows with set_basic_rate_manually."""
	for row in se.items:
		if not row.set_basic_rate_manually:
			continue
		row.basic_amount = flt(flt(row.transfer_qty) * flt(row.basic_rate), row.precision("basic_amount"))
		row.valuation_rate = flt(row.basic_rate)


def _total_source_cost(se) -> float:
	total_source_cost = 0.0
	for row in se.items:
		if not row.s_warehouse:
			continue
		rate = _source_row_rate(se, row)
		if rate <= 0:
			frappe.throw(
				f"Stock Entry row {row.idx}: valuation rate is missing for Item {row.item_code} "
				f"in warehouse {row.s_warehouse}. Receive raw stock or set valuation before Repack #1."
			)
		total_source_cost += flt(row.transfer_qty) * rate
	return total_source_cost


def _allocate_cutting_repack_rates(se, cutting_job) -> None:
	"""Spread consumed source material value across finished rows by glass area."""
	scrap_item = get_scrap_item()
	piece_rate_overrides = {
		piece.cut_wip_item: flt(piece.get("basic_rate"))
		for piece in cutting_job.get("pieces") or []
		if piece.cut_wip_item and flt(piece.get("basic_rate")) > 0
	}
	total_source_cost = _total_source_cost(se)

	finished_rows = [row for row in se.items if row.t_warehouse and not row.s_warehouse and row.is_finished_item]
	scrap_rows = [row for row in finished_rows if row.item_code == scrap_item]
	allocatable = [row for row in finished_rows if row.item_code != scrap_item]

	if not allocatable:
		frappe.throw("Cutting Repack has no finished rows to receive source material value.")

	unique_allocatable = {row.item_code for row in allocatable}
	if len(unique_allocatable) == 1 and not scrap_rows and not piece_rate_overrides:
		# ERPNext repack can auto-derive the finished rate from outgoing material cost.
		return

	override_cost = 0.0
	auto_rows = []
	for row in allocatable:
		override_rate = flt(piece_rate_overrides.get(row.item_code))
		if override_rate > 0:
			row.basic_rate = override_rate
			row.set_basic_rate_manually = 1
			override_cost += flt(row.transfer_qty) * override_rate
			continue
		auto_rows.append(row)

	remaining_cost = total_source_cost - override_cost
	if remaining_cost < 0:
		frappe.throw("Cut WIP basic rates exceed consumed source material value.")

	weights = [_finished_row_weight(row) for row in auto_rows]
	total_weight = sum(weights)

	for row in auto_rows:
		qty = flt(row.transfer_qty)
		if qty <= 0:
			continue

		if total_weight > 0:
			weight = _finished_row_weight(row)
			share = weight / total_weight
		else:
			total_qty = sum(flt(item.transfer_qty) for item in auto_rows)
			share = qty / total_qty if total_qty else 0

		row.basic_rate = flt((remaining_cost * share) / qty)
		if row.basic_rate <= 0:
			frappe.throw(
				f"Stock Entry row {row.idx}: could not derive a valuation rate for {row.item_code}. "
				"Check source stock valuation and finished quantities."
			)
		row.set_basic_rate_manually = 1

	for row in scrap_rows:
		row.basic_rate = 0
		row.allow_zero_valuation_rate = 1
		row.set_basic_rate_manually = 1

	_sync_manual_row_amounts(se)


def _finished_row_weight(row) -> float:
	area_m2 = _item_area_m2(row.item_code)
	return flt(row.transfer_qty) * (area_m2 if area_m2 > 0 else 1)
