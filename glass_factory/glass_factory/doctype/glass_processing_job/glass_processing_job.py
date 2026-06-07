"""Manual Glass Processing Job controller for Phase 0."""

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt

from glass_factory.glass_factory.stock_posting import build_processing_repack


class GlassProcessingJob(Document):
	def validate(self):
		self._validate_inputs_outputs()

	@frappe.whitelist()
	def create_repack_stock_entry(self):
		if self.linked_stock_entry:
			return {"stock_entry": self.linked_stock_entry}
		self._mark_operations_completed_if_empty()
		se = build_processing_repack(self)
		se.insert(ignore_permissions=True)
		self.linked_stock_entry = se.name
		self.status = "Processing In Progress"
		self.save(ignore_permissions=True)
		return {"message": "Draft Repack #2 created.", "stock_entry": se.name}

	@frappe.whitelist()
	def submit_repack_stock_entry(self):
		if not self.linked_stock_entry:
			self.create_repack_stock_entry()
		se = frappe.get_doc("Stock Entry", self.linked_stock_entry)
		if se.docstatus == 0:
			se.submit()
		self.status = "Final Stock Posted"
		for row in self.outputs:
			self._update_so_item(row.sales_order_item, {
				"gf_processing_job": self.name,
				"gf_processed_qty": flt(row.qty),
			})
		self.save(ignore_permissions=True)
		return {"message": "Repack #2 submitted.", "stock_entry": se.name}

	@frappe.whitelist()
	def complete_job(self):
		if self.status != "Final Stock Posted":
			frappe.throw("Submit Repack #2 before completing the Processing Job.")
		self.status = "Completed"
		self.save(ignore_permissions=True)
		return {"message": "Glass Processing Job completed."}

	def _validate_inputs_outputs(self):
		output_by_so_item = {}
		for row in self.outputs:
			if flt(row.qty) <= 0:
				frappe.throw(f"Output row {row.idx}: quantity must be greater than zero.")
			so_item = frappe.db.get_value(
				"Sales Order Item",
				row.sales_order_item,
				["item_code", "gf_final_item", "qty"],
				as_dict=True,
			)
			if so_item and row.final_item != (so_item.gf_final_item or so_item.item_code):
				frappe.throw(f"Output row {row.idx}: final Item must match Sales Order Item.")
			output_by_so_item[row.sales_order_item] = output_by_so_item.get(row.sales_order_item, 0) + flt(row.qty)
			if so_item and output_by_so_item[row.sales_order_item] > flt(so_item.qty):
				frappe.throw(f"Output row {row.idx}: output quantity exceeds Sales Order quantity.")

		input_by_so_item = {}
		for row in self.inputs:
			if flt(row.qty) <= 0:
				frappe.throw(f"Input row {row.idx}: quantity must be greater than zero.")
			input_by_so_item[row.sales_order_item] = input_by_so_item.get(row.sales_order_item, 0) + flt(row.qty)

		for so_item, output_qty in output_by_so_item.items():
			if output_qty > input_by_so_item.get(so_item, 0):
				frappe.throw(f"Output quantity for Sales Order Item {so_item} exceeds Cut WIP input quantity.")

	def _mark_operations_completed_if_empty(self):
		for row in self.operations:
			if not row.status:
				row.status = "Completed"

	def _update_so_item(self, row_name, values):
		for fieldname, value in values.items():
			frappe.db.set_value("Sales Order Item", row_name, fieldname, value, update_modified=False)
