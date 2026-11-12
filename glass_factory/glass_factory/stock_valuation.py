"""Valuation of glass repack movements.

ERPNext re-prices a Stock Entry every time it recalculates it: on save, right after the ledger
is posted, and again whenever an earlier receipt is re-valued (for example by a Landed Cost
Voucher). Rates frozen at save time would miss that last case, so the rates of glass repack
rows are derived from the consumed rows inside ``set_basic_rate`` itself.

* Raw to Cut WIP: the consumed sheet value is spread over the finished rows by glass area,
  scrap carries nothing. Rates set on Cutting Job pieces stay as the user chose them.
* Cut WIP to Final: each finished row takes the value of the Cut WIP consumed for its own
  Sales Order row, so two lines with different Cut WIP batch costs stay different.
* Processing cost: the cost of the operations on a Sales Order row is added to that row only,
  instead of being shared over every finished row in proportion to material value.

Nothing here writes Bin, Stock Ledger or GL; it only sets rates on the document being saved.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.query_builder import Order
from frappe.utils import cint, flt

from glass_factory.glass_factory.item_resolver import _parse_raw_item_code, item_role
from glass_factory.glass_factory.stock_movements import CUTTING_FLOW, PROCESSING_FLOW

PROCESSING_COST_FIELD = "gf_processing_cost"


def glass_flow(doc) -> str | None:
	"""The glass stock flow of a Stock Entry created by Glass Factory, else None."""
	if doc.get("doctype") != "Stock Entry" or not cint(doc.get("gf_created_by_glass_factory")):
		return None
	if doc.get("purpose") != "Repack":
		return None
	flow = doc.get("gf_glass_stock_flow")
	return flow if flow in (CUTTING_FLOW, PROCESSING_FLOW) else None


def finished_rows(doc) -> list:
	return [row for row in doc.get("items") or [] if row.t_warehouse and not row.s_warehouse and row.is_finished_item]


def outgoing_rows(doc) -> list:
	return [row for row in doc.get("items") or [] if row.s_warehouse and not row.t_warehouse]


def item_area_m2(item_code: str) -> float:
	parsed = _parse_raw_item_code(item_code)
	if not parsed:
		return 0
	return flt(parsed["length_mm"]) * flt(parsed["width_mm"]) / 1_000_000


def row_weight(row) -> float:
	"""Quantity times the glass area of the Item; plain quantity when the Item carries no dimensions."""
	area_m2 = item_area_m2(row.item_code)
	return flt(row.transfer_qty) * (area_m2 if area_m2 > 0 else 1)


def existing_scrap_item() -> str | None:
	"""The scrap Item if it exists. Valuation must never create master data, least of all during a repost."""
	code = frappe.db.get_single_value("Glass Factory Settings", "scrap_item") or "Glass Scrap"
	return code if frappe.db.exists("Item", code) else None


def is_scrap_row(row, scrap_item: str | None = None) -> bool:
	if row.get("gf_source_item_role") == "Scrap":
		return True
	return bool(scrap_item) and row.item_code == scrap_item


def set_row_amount(row, amount: float) -> None:
	"""Give a finished row a total value; the rate follows from it so the row always adds up."""
	qty = flt(row.transfer_qty)
	row.basic_rate = flt(amount) / qty if qty else 0
	row.basic_amount = flt(amount, row.precision("basic_amount"))
	row.set_basic_rate_manually = 1


def split_value(total: float, rows: list, weights: list[float]) -> list[float]:
	"""Share ``total`` over ``rows`` by weight. The heaviest row takes what rounding leaves over."""
	total_weight = sum(weights)
	if not rows:
		return []
	if total_weight <= 0:
		weights = [flt(row.transfer_qty) for row in rows]
		total_weight = sum(weights)
	if total_weight <= 0:
		return [0.0 for _row in rows]
	precision = rows[0].precision("basic_amount")
	amounts = [flt(total * weight / total_weight, precision) for weight in weights]
	heaviest = max(range(len(rows)), key=lambda index: weights[index])
	amounts[heaviest] = flt(amounts[heaviest] + (total - sum(amounts)), precision)
	return amounts


def _strict(doc, raise_error_if_no_rate: bool) -> bool:
	"""Missing valuation is an error on save, never while ERPNext re-prices a posted entry."""
	return bool(raise_error_if_no_rate) and not doc.flags.get("via_repost")


# --------------------------------------------------------------------------- Raw to Cut WIP


def piece_rate_overrides(doc) -> dict[str, float]:
	"""Cut WIP rates the user typed on the Cutting Job pieces."""
	job = doc.get("gf_cutting_job")
	if not job or not frappe.db.exists("Cutting Job", job):
		return {}
	rows = frappe.get_all(
		"Cutting Job Piece",
		filters={"parent": job, "parenttype": "Cutting Job", "basic_rate": [">", 0]},
		fields=["cut_wip_item", "basic_rate"],
	)
	return {row.cut_wip_item: flt(row.basic_rate) for row in rows if row.cut_wip_item}


def value_cutting_repack(doc, *, strict: bool, overrides: dict[str, float] | None = None) -> None:
	"""Spread the consumed sheet value over the finished rows by glass area."""
	from glass_factory.glass_factory.stock_posting import _source_row_rate

	total_source_cost = 0.0
	for row in outgoing_rows(doc):
		if flt(row.basic_rate) <= 0:
			rate = _source_row_rate(doc, row)
			if rate <= 0:
				if strict:
					frappe.throw(
						_(
							"Stock Entry row {0}: valuation rate is missing for Item {1} in warehouse {2}. "
							"Receive stock or set valuation before posting this glass stock movement."
						).format(row.idx, row.item_code, row.s_warehouse)
					)
				continue
			row.basic_rate = rate
			row.basic_amount = flt(flt(row.transfer_qty) * rate, row.precision("basic_amount"))
		total_source_cost += flt(row.basic_amount)

	allocate_cutting_rates(doc, total_source_cost, piece_rate_overrides(doc) if overrides is None else overrides, strict=strict)


def allocate_cutting_rates(doc, total_source_cost: float, overrides: dict[str, float], *, strict: bool = True) -> None:
	scrap_item = existing_scrap_item()
	finished = finished_rows(doc)
	scrap_rows = [row for row in finished if is_scrap_row(row, scrap_item)]
	allocatable = [row for row in finished if row not in scrap_rows]
	if not allocatable:
		if strict:
			frappe.throw(_("Cutting stock movement has no finished rows to receive source material value."))
		return

	fixed_cost = 0.0
	auto_rows = []
	for row in allocatable:
		override_rate = flt(overrides.get(row.item_code))
		if override_rate > 0:
			set_row_amount(row, flt(row.transfer_qty) * override_rate)
			row.basic_rate = override_rate
			fixed_cost += flt(row.basic_amount)
		else:
			auto_rows.append(row)

	remaining_cost = flt(total_source_cost) - fixed_cost
	if remaining_cost < -0.005 and strict:
		frappe.throw(_("Cut WIP basic rates exceed consumed source material value."))
	remaining_cost = max(remaining_cost, 0.0)

	if auto_rows:
		amounts = split_value(remaining_cost, auto_rows, [row_weight(row) for row in auto_rows])
		for row, amount in zip(auto_rows, amounts, strict=True):
			if amount <= 0 and strict:
				frappe.throw(
					_(
						"Stock Entry row {0}: could not derive a valuation rate for {1}. "
						"Check source stock valuation and finished quantities."
					).format(row.idx, row.item_code)
				)
			set_row_amount(row, amount)

	for row in scrap_rows:
		row.basic_rate = 0
		row.basic_amount = 0
		row.allow_zero_valuation_rate = 1
		row.set_basic_rate_manually = 1


# --------------------------------------------------------------------------- Cut WIP to Final


def posted_cut_wip_material_rate(doc, row) -> float:
	"""Rate the Cut WIP batch was posted at, when ERPNext could not price the outgoing row itself."""
	ledger_rate = _stock_ledger_incoming_rate(row)
	if ledger_rate > 0:
		return ledger_rate
	return _submitted_cutting_detail_rate(doc, row)


def _stock_ledger_incoming_rate(row) -> float:
	filters = {
		"item_code": row.item_code,
		"warehouse": row.s_warehouse,
		"is_cancelled": 0,
		"actual_qty": [">", 0],
	}
	if row.get("batch_no"):
		filters["batch_no"] = row.batch_no
	entry = frappe.db.get_value(
		"Stock Ledger Entry",
		filters,
		["valuation_rate", "stock_value_difference", "actual_qty"],
		as_dict=True,
		order_by="posting_datetime desc, creation desc",
	)
	if not entry:
		return 0
	if flt(entry.valuation_rate) > 0:
		return flt(entry.valuation_rate)
	if flt(entry.actual_qty) and flt(entry.stock_value_difference) > 0:
		return flt(entry.stock_value_difference) / flt(entry.actual_qty)
	return 0


def _submitted_cutting_detail_rate(doc, row) -> float:
	parent = frappe.qb.DocType("Stock Entry")
	child = frappe.qb.DocType("Stock Entry Detail")
	query = (
		frappe.qb.from_(child)
		.join(parent)
		.on(parent.name == child.parent)
		.select(child.valuation_rate, child.basic_rate)
		.where(
			(parent.docstatus == 1)
			& (parent.gf_created_by_glass_factory == 1)
			& (parent.gf_glass_stock_flow == CUTTING_FLOW)
			& (child.item_code == row.item_code)
			& (child.t_warehouse == row.s_warehouse)
		)
		.orderby(parent.posting_date, order=Order.desc)
		.orderby(parent.name, order=Order.desc)
		.limit(1)
	)
	if doc.get("gf_cutting_job"):
		query = query.where(parent.gf_cutting_job == doc.gf_cutting_job)
	if row.get("batch_no"):
		query = query.where(child.batch_no == row.batch_no)
	rows = query.run(as_dict=True)
	if not rows:
		return 0
	return flt(rows[0].valuation_rate) or flt(rows[0].basic_rate)


def restore_missing_cut_wip_rates(doc, *, strict: bool) -> None:
	"""ERPNext rates a batch outgoing row from the ledger. When that gives zero, use the posted rate."""
	for row in outgoing_rows(doc):
		if flt(row.basic_rate) > 0:
			continue
		if (row.get("gf_source_item_role") or item_role(row.item_code)) != "Cut WIP":
			continue
		rate = posted_cut_wip_material_rate(doc, row)
		if rate <= 0:
			if strict:
				frappe.throw(
					_(
						"Cut WIP item {0} has no posted material valuation in {1}. "
						"Submit the cutting stock movement before saving the processing movement."
					).format(row.item_code, row.s_warehouse)
				)
			continue
		row.basic_rate = rate
		row.basic_amount = flt(flt(row.transfer_qty) * rate, row.precision("basic_amount"))


def output_rate_overrides(doc) -> dict[tuple[str, str], float]:
	"""Final rates the user typed on the Processing Job outputs, by (Final Item, Sales Order row)."""
	job = doc.get("gf_processing_job")
	if not job or not frappe.db.exists("Glass Processing Job", job):
		return {}
	rows = frappe.get_all(
		"Glass Processing Job Output",
		filters={"parent": job, "parenttype": "Glass Processing Job", "basic_rate": [">", 0]},
		fields=["final_item", "sales_order_item", "basic_rate"],
	)
	return {(row.final_item, row.sales_order_item or ""): flt(row.basic_rate) for row in rows}


def value_processing_repack(doc, *, strict: bool) -> None:
	"""Each finished row takes the value of the Cut WIP consumed for its own Sales Order row."""
	restore_missing_cut_wip_rates(doc, strict=strict)
	finished = finished_rows(doc)
	if not finished:
		return

	groups: dict[str, dict[str, list]] = {}
	for row in outgoing_rows(doc):
		groups.setdefault(row.get("gf_sales_order_item") or "", {"in": [], "out": []})["in"].append(row)
	for row in finished:
		groups.setdefault(row.get("gf_sales_order_item") or "", {"in": [], "out": []})["out"].append(row)

	overrides = output_rate_overrides(doc)
	for key, group in groups.items():
		label = key or _("rows without a Sales Order row")
		if not group["out"]:
			if strict:
				frappe.throw(_("Cut WIP is consumed for {0} but the movement has no finished row for it.").format(label))
			continue
		if not group["in"]:
			if strict:
				frappe.throw(_("The movement has a finished row for {0} but consumes no Cut WIP for it.").format(label))
			continue

		pool = sum(flt(row.basic_amount) for row in group["in"])
		fixed_cost = 0.0
		auto_rows = []
		for row in group["out"]:
			override_rate = flt(overrides.get((row.item_code, key)))
			if override_rate > 0:
				set_row_amount(row, flt(row.transfer_qty) * override_rate)
				row.basic_rate = override_rate
				fixed_cost += flt(row.basic_amount)
			else:
				auto_rows.append(row)

		remaining = pool - fixed_cost
		if not auto_rows:
			if strict and abs(remaining) > 0.01:
				frappe.throw(
					_("Rates set on the finished rows for {0} do not add up to the consumed Cut WIP value {1}.").format(
						label, flt(pool, 2)
					)
				)
			continue
		if remaining < -0.005 and strict:
			frappe.throw(_("Finished rates for {0} exceed the consumed Cut WIP value.").format(label))
		amounts = split_value(max(remaining, 0.0), auto_rows, [row_weight(row) for row in auto_rows])
		for row, amount in zip(auto_rows, amounts, strict=True):
			if amount <= 0 and strict:
				frappe.throw(
					_(
						"Stock Entry row {0}: could not derive a valuation rate for {1}. "
						"Check the Cut WIP stock valuation."
					).format(row.idx, row.item_code)
				)
			set_row_amount(row, amount)


# --------------------------------------------------------------------------- ERPNext extension


class GlassStockEntryMixin:
	"""Mixed into ERPNext's Stock Entry through the ``extend_doctype_class`` hook."""

	def set_basic_rate(self, reset_outgoing_rate=True, raise_error_if_no_rate=True):
		super().set_basic_rate(reset_outgoing_rate, raise_error_if_no_rate)
		flow = glass_flow(self)
		if not flow:
			return
		strict = _strict(self, raise_error_if_no_rate)
		if flow == CUTTING_FLOW:
			value_cutting_repack(self, strict=strict)
		else:
			value_processing_repack(self, strict=strict)

	def get_additional_cost_allocation(self):
		"""Processing cost goes to the row it was computed for, not in proportion to material value."""
		incoming_items, basis, total_basis = super().get_additional_cost_allocation()
		if glass_flow(self) == PROCESSING_FLOW:
			weights = sum(flt(row.get(PROCESSING_COST_FIELD)) for row in incoming_items)
			if weights > 0:
				return incoming_items, PROCESSING_COST_FIELD, weights
		return incoming_items, basis, total_basis
