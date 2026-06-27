"""Manual Glass Processing Job controller for Phase 0."""

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt

from glass_factory.glass_factory.stock_posting import build_processing_repack

OPERATION_LABELS = {
	"POL": "Polishing",
	"BEV": "Beveling",
	"HOL": "Drilling",
	"SLT": "Slotting",
	"TMP": "Tempering",
	"SBL": "Sandblasting",
	"LAM": "Laminating",
}


class GlassProcessingJob(Document):
	def validate(self):
		self._validate_inputs_outputs()

	@frappe.whitelist()
	def create_repack_stock_entry(self):
		if self.docstatus != 1:
			frappe.throw("Submit the Processing Job before creating a final stock movement.")
		self._validate_operations_completed()
		if self.linked_stock_entry:
			return {"stock_entry": self.linked_stock_entry}
		se = build_processing_repack(self)
		se.insert(ignore_permissions=True)
		self.linked_stock_entry = se.name
		self.status = "Processing In Progress"
		self.save(ignore_permissions=True)
		return {"message": "Draft final stock movement created.", "stock_entry": se.name}

	@frappe.whitelist()
	def submit_repack_stock_entry(self):
		self._validate_operations_completed()
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
		return {"message": "Final stock movement submitted.", "stock_entry": se.name}

	@frappe.whitelist()
	def complete_job(self):
		if self.status != "Final Stock Posted":
			frappe.throw("Submit the final stock movement before completing the Processing Job.")
		self.status = "Completed"
		self.save(ignore_permissions=True)
		return {"message": "Glass Processing Job completed."}

	@frappe.whitelist()
	def get_valid_actions(self):
		return get_valid_actions_for_doc(self)

	@frappe.whitelist()
	def run_action(self, action: str):
		valid_actions = {row["action"]: row for row in get_valid_actions_for_doc(self)}
		if action not in valid_actions:
			frappe.throw("This action is not valid for the current Processing Job status.")

		if action.startswith("start_operation::"):
			return self._set_operation_status(action.split("::", 1)[1], "In Progress")
		if action.startswith("complete_operation::"):
			return self._set_operation_status(action.split("::", 1)[1], "Completed")
		if action == "create_final_stock_movement":
			return self.create_repack_stock_entry()
		if action == "submit_final_stock_movement":
			return self.submit_repack_stock_entry()
		if action == "complete_job":
			return self.complete_job()

		frappe.throw("Unsupported Processing Job action.")

	def _set_operation_status(self, row_name: str, status: str):
		for row in self.operations:
			if row.name != row_name:
				continue
			row.status = status
			self.status = "Processing In Progress"
			self.save(ignore_permissions=True)
			return {"message": f"{_operation_label(row.operation)} marked {status}."}
		frappe.throw("Processing operation was not found.")

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

	def _validate_operations_completed(self):
		for row in self.operations:
			if row.status != "Completed":
				frappe.throw("Complete all processing operations before creating the final stock movement.")

	def _update_so_item(self, row_name, values):
		for fieldname, value in values.items():
			frappe.db.set_value("Sales Order Item", row_name, fieldname, value, update_modified=False)


def get_valid_actions_for_doc(doc) -> list[dict[str, str]]:
	"""Return the operation-driven next actions for a Processing Job."""
	if doc.docstatus != 1 or doc.status in ("Completed", "Cancelled"):
		return []

	for row in doc.get("operations") or []:
		if row.status == "In Progress":
			return [{"action": f"complete_operation::{row.name}", "label": f"Complete {_operation_label(row.operation)}"}]

	for row in doc.get("operations") or []:
		if row.status in ("", "Pending"):
			return [{"action": f"start_operation::{row.name}", "label": f"Start {_operation_label(row.operation)}"}]

	if not doc.get("linked_stock_entry"):
		return [{"action": "create_final_stock_movement", "label": "Create Final Stock Movement"}]
	if doc.status != "Final Stock Posted":
		return [{"action": "submit_final_stock_movement", "label": "Submit Final Stock Movement"}]
	return [{"action": "complete_job", "label": "Complete Job"}]


def _operation_label(operation: str) -> str:
	return OPERATION_LABELS.get(operation, operation or "Operation")
