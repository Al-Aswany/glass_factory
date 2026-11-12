"""Manual Glass Processing Job controller for Phase 0."""

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt

from glass_factory.glass_factory.authorization import require_doc_permission, require_stock_movement_authority
from glass_factory.glass_factory.selling_uom import sales_order_item_pieces
from glass_factory.glass_factory.stock_movements import (
	PROCESSING_FLOW,
	assert_job_cancel_allowed,
	clear_cancelled_link,
	guard_linked_movement,
	lock_job,
	mark_posted_after_submit,
)
from glass_factory.glass_factory.stock_posting import build_processing_repack

OPERATION_LABELS = {
	"POL": "Polishing",
	"BEV": "Beveling",
	"HOL": "Drilling",
	"SHOL": "Special Hole",
	"SLT": "Slotting",
	"SSLT": "Special Slot",
	"TMP": "Tempering",
	"SBL": "Sandblasting",
	"LAM": "Laminating",
}


class GlassProcessingJob(Document):
	def validate(self):
		self._validate_inputs_outputs()

	def before_cancel(self):
		assert_job_cancel_allowed(self)

	@frappe.whitelist()
	def create_repack_stock_entry(self):
		if self.docstatus != 1:
			frappe.throw("Submit the Processing Job before creating a final stock movement.")
		self._validate_operations_completed()
		require_stock_movement_authority(self, PROCESSING_FLOW)
		locked = lock_job(self.doctype, self.name)
		linked = guard_linked_movement(locked, flow=PROCESSING_FLOW)
		if linked:
			return {"stock_entry": linked, "message": "Existing final stock movement kept."}
		se = build_processing_repack(locked)
		se.insert(ignore_permissions=True)
		locked.db_set("linked_stock_entry", se.name)
		if locked.status != "Final Stock Posted":
			locked.db_set("status", "Processing In Progress")
		self.reload()  # the job was changed through the locked copy; hand the caller the committed state
		return {"message": "Draft final stock movement created.", "stock_entry": se.name}

	@frappe.whitelist()
	def submit_repack_stock_entry(self):
		if self.docstatus != 1:
			frappe.throw("Submit the Processing Job before posting a final stock movement.")
		self._validate_operations_completed()
		require_stock_movement_authority(self, PROCESSING_FLOW)
		locked = lock_job(self.doctype, self.name)
		if not locked.linked_stock_entry:
			self.create_repack_stock_entry()
			locked = lock_job(self.doctype, self.name)
		linked = guard_linked_movement(locked, flow=PROCESSING_FLOW)
		se = frappe.get_doc("Stock Entry", linked)
		if se.docstatus == 0:
			se.submit()
			se.reload()
		if se.docstatus != 1:
			frappe.throw(
				f"Stock Entry {se.name} is not submitted, so the Processing Job was not marked as posted."
			)
		for row in locked.get("outputs") or []:
			if row.sales_order_item:
				self._update_so_item(row.sales_order_item, {"gf_processing_job": locked.name})
		mark_posted_after_submit(locked, se.name)
		self.reload()  # the job was changed through the locked copy; hand the caller the committed state
		return {"message": "Final stock movement submitted.", "stock_entry": se.name}

	@frappe.whitelist()
	def recover_cancelled_stock_movement(self):
		"""Clear a cancelled movement link. Does not create a replacement Stock Entry."""
		cancelled_name = clear_cancelled_link(self, flow=PROCESSING_FLOW)
		return {
			"message": (
				f"Cancelled Stock Entry {cancelled_name} was unlinked. "
				"Create a new final stock movement when you are ready to post again."
			),
			"stock_entry": cancelled_name,
		}

	@frappe.whitelist()
	def complete_job(self):
		if self.status != "Final Stock Posted":
			frappe.throw("Submit the final stock movement before completing the Processing Job.")
		require_doc_permission(self, "write")
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
		if action == "recover_cancelled_movement":
			return self.recover_cancelled_stock_movement()
		if action == "complete_job":
			return self.complete_job()

		frappe.throw("Unsupported Processing Job action.")

	def _set_operation_status(self, row_name: str, status: str):
		require_doc_permission(self, "write")
		for row in self.operations:
			if row.name != row_name:
				continue
			row.status = status
			self.status = "Processing In Progress"
			self.save(ignore_permissions=True)
			return {"message": f"{_operation_label(row)} marked {status}."}
		frappe.throw("Processing operation was not found.")

	def _validate_inputs_outputs(self):
		output_by_so_item = {}
		for row in self.outputs:
			if flt(row.qty) <= 0:
				frappe.throw(f"Output row {row.idx}: quantity must be greater than zero.")
			so_item = frappe.db.get_value(
				"Sales Order Item",
				row.sales_order_item,
				["item_code", "gf_final_item"],
				as_dict=True,
			)
			if so_item and row.final_item != (so_item.gf_final_item or so_item.item_code):
				frappe.throw(f"Output row {row.idx}: final Item must match Sales Order Item.")
			output_by_so_item[row.sales_order_item] = output_by_so_item.get(row.sales_order_item, 0) + flt(row.qty)
			# Output is a piece count; the Sales Order row may be sold in m², so compare in pieces.
			if so_item and flt(output_by_so_item[row.sales_order_item], 3) > flt(
				sales_order_item_pieces(row.sales_order_item), 3
			):
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
			return [{"action": f"complete_operation::{row.name}", "label": f"Complete {_operation_label(row)}"}]

	for row in doc.get("operations") or []:
		if row.status in ("", "Pending"):
			return [{"action": f"start_operation::{row.name}", "label": f"Start {_operation_label(row)}"}]

	if not doc.get("linked_stock_entry"):
		return [{"action": "create_final_stock_movement", "label": "Create Final Stock Movement"}]
	if _movement_state(doc) == 2:
		return [{"action": "recover_cancelled_movement", "label": "Recover Cancelled Movement"}]
	if doc.status != "Final Stock Posted":
		return [{"action": "submit_final_stock_movement", "label": "Submit Final Stock Movement"}]
	return [{"action": "complete_job", "label": "Complete Job"}]


def _movement_state(doc) -> int | None:
	"""Docstatus of the linked movement. Unsaved test docs are treated as drafts."""
	explicit = doc.get("_movement_docstatus") if hasattr(doc, "get") else None
	if explicit not in (None, ""):
		return cint(explicit)
	linked = doc.get("linked_stock_entry") if hasattr(doc, "get") else None
	if not linked:
		return None
	if doc.get("doctype") != "Glass Processing Job" or not doc.get("name"):
		return 0
	value = frappe.db.get_value("Stock Entry", linked, "docstatus")
	if value is None:
		return 0
	return cint(value)


def _operation_label(operation_row) -> str:
	if hasattr(operation_row, "get"):
		label = operation_row.get("operation_label")
		if label:
			return label
		operation = operation_row.get("operation")
	else:
		label = getattr(operation_row, "operation_label", None)
		if label:
			return label
		operation = getattr(operation_row, "operation", None)
	return OPERATION_LABELS.get(operation, operation or "Operation")
