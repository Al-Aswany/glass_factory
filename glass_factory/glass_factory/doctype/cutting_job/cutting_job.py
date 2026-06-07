"""Manual Cutting Job controller for Phase 0 glass MVP."""

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt

from glass_factory.glass_factory.item_resolver import get_item_glass_meta, item_role
from glass_factory.glass_factory.stock_posting import build_cutting_repack


class CuttingJob(Document):
	def validate(self):
		self._validate_sales_orders()
		self._validate_pieces()
		self._validate_assignment_limits()
		self._validate_source_sheets()

	@frappe.whitelist()
	def pull_from_sales_orders(self):
		"""Populate pieces and source sheets from linked submitted Sales Orders."""
		self._populate_from_sales_orders()
		self.save(ignore_permissions=True)
		return {
			"message": (
				f"Pulled {len(self.pieces)} glass piece row(s) "
				f"and {len(self.source_sheets)} source sheet row(s)."
			)
		}

	def _populate_from_sales_orders(self):
		self.set("pieces", [])
		for link in self.get("sales_orders") or []:
			if not link.sales_order:
				continue
			so = frappe.get_doc("Sales Order", link.sales_order)
			if so.docstatus != 1:
				frappe.throw(f"Sales Order {so.name} must be submitted before cutting.")
			for item in so.items:
				if not item.get("gf_is_glass_item"):
					continue
				remaining = flt(item.qty) - self._assigned_qty(item.name)
				if remaining <= 0:
					continue
				self.append("pieces", {
					"sales_order": so.name,
					"sales_order_item": item.name,
					"customer": so.customer,
					"raw_sheet_item": item.gf_raw_sheet_item,
					"cut_wip_item": item.gf_cut_wip_item,
					"final_item": item.gf_final_item or item.item_code,
					"length_mm": item.gf_length_mm,
					"width_mm": item.gf_width_mm,
					"thickness_mm": item.gf_thickness_mm,
					"processing_flags": item.gf_processing_flags,
					"glass_specification": item.gf_glass_specification,
					"qty_required": remaining,
					"qty_assigned": remaining,
					"qty_cut": remaining,
				})
		self._populate_source_sheets_from_pieces()
		self.status = "Planned"

	def _populate_source_sheets_from_pieces(self):
		self.set("source_sheets", [])
		raw_warehouse = _raw_warehouse()
		seen: set[str] = set()
		for piece in self.get("pieces") or []:
			raw_item = piece.get("raw_sheet_item")
			if not raw_item or raw_item in seen:
				continue
			seen.add(raw_item)
			meta = get_item_glass_meta(raw_item)
			self.append("source_sheets", {
				"item_code": raw_item,
				"source_role": meta.get("gf_glass_item_role") or "Raw Sheet",
				"warehouse": raw_warehouse,
				"length_mm": meta.get("gf_length_mm") or 0,
				"width_mm": meta.get("gf_width_mm") or 0,
				"qty_consumed": 1,
			})

	# Backward-compatible old button method name.
	def pull_pieces_from_sales_orders(self):
		return self.pull_from_sales_orders()

	@frappe.whitelist()
	def create_repack_stock_entry(self):
		if self.linked_stock_entry:
			return {"stock_entry": self.linked_stock_entry}
		se = build_cutting_repack(self)
		se.insert(ignore_permissions=True)
		self.linked_stock_entry = se.name
		self.status = "Ready for Cutting"
		self.save(ignore_permissions=True)
		return {"message": "Draft Repack #1 created.", "stock_entry": se.name}

	@frappe.whitelist()
	def submit_repack_stock_entry(self):
		if not self.linked_stock_entry:
			self.create_repack_stock_entry()
		se = frappe.get_doc("Stock Entry", self.linked_stock_entry)
		if se.docstatus == 0:
			se.submit()
		self.status = "Cut Stock Posted"
		for piece in self.pieces:
			self._update_so_item(piece.sales_order_item, {
				"gf_cutting_job": self.name,
				"gf_cut_qty": flt(piece.get("qty_cut") or piece.get("qty_required")),
			})
		self.save(ignore_permissions=True)
		return {"message": "Repack #1 submitted.", "stock_entry": se.name}

	@frappe.whitelist()
	def make_processing_job(self):
		if self.status not in ("Cut Stock Posted", "Completed"):
			frappe.throw("Submit Repack #1 before creating a Glass Processing Job.")
		job = frappe.new_doc("Glass Processing Job")
		job.cutting_job = self.name
		job.status = "Ready for Processing"
		for piece in self.pieces:
			qty = flt(piece.get("qty_cut") or piece.get("qty_required"))
			if qty <= 0:
				continue
			job.append("inputs", {
				"cut_wip_item": piece.cut_wip_item,
				"sales_order": piece.sales_order,
				"sales_order_item": piece.sales_order_item,
				"glass_specification": piece.glass_specification,
				"qty": qty,
			})
			job.append("outputs", {
				"final_item": piece.final_item,
				"sales_order": piece.sales_order,
				"sales_order_item": piece.sales_order_item,
				"glass_specification": piece.glass_specification,
				"qty": qty,
			})
			for flag in (piece.get("processing_flags") or "").split("-"):
				if flag:
					job.append("operations", {"operation": flag, "sales_order": piece.sales_order, "sales_order_item": piece.sales_order_item, "qty": qty, "status": "Pending"})
		job.insert(ignore_permissions=True)
		return {"message": "Glass Processing Job created.", "processing_job": job.name}

	@frappe.whitelist()
	def complete_job(self):
		if self.status != "Cut Stock Posted":
			frappe.throw("Submit Repack #1 before completing the Cutting Job.")
		self.status = "Completed"
		self.save(ignore_permissions=True)
		return {"message": "Cutting Job completed."}

	def _validate_sales_orders(self):
		seen = set()
		for row in self.get("sales_orders") or []:
			if not row.sales_order:
				continue
			if row.sales_order in seen:
				frappe.throw(f"Sales Order {row.sales_order} is duplicated.")
			seen.add(row.sales_order)
			if frappe.db.get_value("Sales Order", row.sales_order, "docstatus") != 1:
				frappe.throw(f"Sales Order {row.sales_order} must be submitted.")

	def _validate_pieces(self):
		for row in self.get("pieces") or []:
			if not row.sales_order or not row.sales_order_item:
				frappe.throw(f"Piece row {row.idx}: Sales Order and Sales Order Item are required.")
			if not row.cut_wip_item or not row.final_item:
				frappe.throw(f"Piece row {row.idx}: Cut WIP Item and Final Item are required.")
			if flt(row.get("qty_required")) <= 0:
				frappe.throw(f"Piece row {row.idx}: required quantity must be greater than zero.")
			assigned = flt(row.get("qty_assigned") or row.get("qty_required"))
			if assigned <= 0:
				frappe.throw(f"Piece row {row.idx}: assigned quantity must be greater than zero.")
			if assigned > flt(row.get("qty_required")):
				frappe.throw(f"Piece row {row.idx}: assigned quantity cannot exceed required quantity.")
			if flt(row.get("qty_cut") or assigned) > assigned:
				frappe.throw(f"Piece row {row.idx}: cut quantity cannot exceed assigned quantity.")
			so_item = frappe.db.get_value("Sales Order Item", row.sales_order_item, ["item_code", "gf_final_item"], as_dict=True)
			if so_item and row.final_item != (so_item.gf_final_item or so_item.item_code):
				frappe.throw(f"Piece row {row.idx}: final Item must match Sales Order Item.")

	def _validate_assignment_limits(self):
		totals: dict[str, float] = {}
		for row in self.get("pieces") or []:
			if not row.sales_order_item:
				continue
			assigned = flt(row.get("qty_assigned") or row.get("qty_required"))
			totals[row.sales_order_item] = totals.get(row.sales_order_item, 0) + assigned

		for so_item_name, assigned_total in totals.items():
			ordered_qty = flt(frappe.db.get_value("Sales Order Item", so_item_name, "qty"))
			other_jobs_qty = self._assigned_qty(so_item_name)
			if other_jobs_qty + assigned_total > ordered_qty:
				frappe.throw(
					f"Sales Order Item {so_item_name}: assigned quantity "
					f"{other_jobs_qty + assigned_total} exceeds ordered quantity {ordered_qty}."
				)

	def _validate_source_sheets(self):
		for row in self.get("source_sheets") or []:
			if not row.item_code:
				continue
			role = row.get("source_role") or item_role(row.item_code)
			if role not in ("Raw Sheet", "Remnant"):
				frappe.throw(f"Source sheet row {row.idx}: source Item must be Raw Sheet or Remnant.")

	def _assigned_qty(self, so_item_name):
		filters = {
			"sales_order_item": so_item_name,
			"parenttype": "Cutting Job",
		}
		if self.name:
			filters["parent"] = ["!=", self.name]
		rows = frappe.get_all(
			"Cutting Job Piece",
			filters=filters,
			fields=["qty_assigned", "qty_required"],
		)
		return sum(flt(row.qty_assigned or row.qty_required) for row in rows)

	def _update_so_item(self, row_name, values):
		for fieldname, value in values.items():
			frappe.db.set_value("Sales Order Item", row_name, fieldname, value, update_modified=False)

	@frappe.whitelist()
	def generate_cop_files(self):
		if not _cop_enabled():
			frappe.throw("COP is disabled for Phase 0 manual flow.")
		frappe.throw("COP generation is dormant in Phase 0.")

	@frappe.whitelist()
	def process_result(self):
		if not _cop_enabled():
			frappe.throw("COP is disabled for Phase 0 manual flow.")
		frappe.throw("COP result processing is dormant in Phase 0.")

	@frappe.whitelist()
	def confirm_and_post(self, parsed_payload=None):
		return self.submit_repack_stock_entry()


def _cop_enabled():
	if frappe.db.exists("DocType", "Glass Factory Settings"):
		return bool(frappe.db.get_single_value("Glass Factory Settings", "enable_cop"))
	return False


@frappe.whitelist()
def make_cutting_job(source_name, target_doc=None):
	"""Create a Cutting Job draft linked to a submitted Sales Order."""
	source = frappe.get_doc("Sales Order", source_name)
	if source.docstatus != 1:
		frappe.throw("Sales Order must be submitted before creating a Cutting Job.")
	if not any(item.get("gf_is_glass_item") for item in source.items):
		frappe.throw("Sales Order has no glass items.")

	if target_doc:
		job = frappe.get_doc(frappe.parse_json(target_doc))
	else:
		job = frappe.new_doc("Cutting Job")

	job.company = job.company or source.company
	job.status = job.status or "Draft"

	if not any(link.sales_order == source.name for link in job.get("sales_orders") or []):
		job.append("sales_orders", {
			"sales_order": source.name,
			"customer": source.customer,
			"delivery_date": source.delivery_date,
		})

	job._populate_from_sales_orders()
	if not job.name:
		job.insert(ignore_permissions=True)

	return job


def _raw_warehouse():
	if frappe.db.exists("DocType", "Glass Factory Settings"):
		return frappe.db.get_single_value("Glass Factory Settings", "raw_warehouse")
	return None
