import unittest

import frappe

from glass_factory.glass_factory.doctype.glass_processing_job.glass_processing_job import get_valid_actions_for_doc


class TestProcessingJobActions(unittest.TestCase):
	def test_actions_hidden_before_submit(self):
		doc = frappe._dict(docstatus=0, status="Ready for Processing", operations=[])
		self.assertEqual(get_valid_actions_for_doc(doc), [])

	def test_actions_follow_current_operation(self):
		doc = frappe._dict(
			docstatus=1,
			status="Ready for Processing",
			linked_stock_entry=None,
			operations=[
				frappe._dict(name="op-pol", operation="POL", status="Pending"),
				frappe._dict(name="op-tmp", operation="TMP", status="Pending"),
			],
		)
		self.assertEqual(get_valid_actions_for_doc(doc), [{"action": "start_operation::op-pol", "label": "Start Polishing"}])

		doc.operations[0].status = "In Progress"
		self.assertEqual(get_valid_actions_for_doc(doc), [{"action": "complete_operation::op-pol", "label": "Complete Polishing"}])

		doc.operations[0].status = "Completed"
		self.assertEqual(get_valid_actions_for_doc(doc), [{"action": "start_operation::op-tmp", "label": "Start Tempering"}])

	def test_invalid_status_has_no_actions(self):
		doc = frappe._dict(docstatus=1, status="Completed", operations=[])
		self.assertEqual(get_valid_actions_for_doc(doc), [])

	def test_stock_actions_after_operations_are_single_next_action(self):
		doc = frappe._dict(docstatus=1, status="Processing In Progress", linked_stock_entry=None, operations=[])
		self.assertEqual(
			get_valid_actions_for_doc(doc),
			[{"action": "create_final_stock_movement", "label": "Create Final Stock Movement"}],
		)

		doc.linked_stock_entry = "STE-001"
		self.assertEqual(
			get_valid_actions_for_doc(doc),
			[{"action": "submit_final_stock_movement", "label": "Submit Final Stock Movement"}],
		)

		doc.status = "Final Stock Posted"
		self.assertEqual(get_valid_actions_for_doc(doc), [{"action": "complete_job", "label": "Complete Job"}])

	def test_direct_final_stock_creation_blocks_pending_operations(self):
		doc = frappe.get_doc({"doctype": "Glass Processing Job", "status": "Ready for Processing"})
		doc.docstatus = 1
		doc.append("operations", {"operation": "POL", "status": "Pending"})

		with self.assertRaises(frappe.ValidationError):
			doc.create_repack_stock_entry()

	def test_cutting_job_completion_blocks_required_processing(self):
		doc = frappe.get_doc({"doctype": "Cutting Job", "status": "Cut Stock Posted"})
		doc.docstatus = 1
		doc.append("pieces", {"processing_flags": "POL"})

		with self.assertRaises(frappe.ValidationError):
			doc.complete_job()
