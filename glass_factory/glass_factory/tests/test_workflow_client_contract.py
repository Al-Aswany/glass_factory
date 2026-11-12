import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]


class TestWorkflowClientContract(unittest.TestCase):
	def test_cutting_job_post_submit_has_single_start_processing_step(self):
		script = (APP_ROOT / "glass_factory/doctype/cutting_job/cutting_job.js").read_text()
		self.assertIn('frm.doc.docstatus !== 1', script)
		self.assertIn('__("Start Processing")', script)
		self.assertNotIn('__("Complete Cutting Job")', script)
		self.assertNotIn('__("Create Repack', script)
		self.assertNotIn('__("Submit Repack', script)

	def test_stock_entry_start_processing_redirects_to_processing_job(self):
		script = (APP_ROOT / "public/js/stock_entry_glass.js").read_text()
		self.assertIn('frm.doc.docstatus !== 1', script)
		self.assertIn('__("Start Processing")', script)
		self.assertIn('frappe.set_route("Form", "Glass Processing Job", processing_job)', script)

	def test_processing_job_actions_are_loaded_dynamically(self):
		script = (APP_ROOT / "glass_factory/doctype/glass_processing_job/glass_processing_job.js").read_text()
		self.assertIn('frm.doc.docstatus !== 1', script)
		self.assertIn('frm.call("get_valid_actions")', script)
		self.assertIn('frm.call("run_action"', script)
		self.assertNotIn('Create Repack', script)
		self.assertNotIn('Submit Repack', script)

	def test_sales_order_cutting_job_hidden_before_submit(self):
		script = (APP_ROOT / "public/js/sales_order_glass.js").read_text()
		self.assertIn("frm.doc.docstatus === 1", script)
		self.assertIn('__("Cutting Job")', script)
		self.assertIn("frappe.model.can_create(\"Cutting Job\")", script)

	def test_quotation_item_grid_locks_generated_rows(self):
		quotation_script = (APP_ROOT / "public/js/quotation_glass.js").read_text()
		sync_script = (APP_ROOT / "public/js/gf_glass_sync.js").read_text()
		self.assertIn("Add Glass Specification", quotation_script)
		self.assertIn("glass_factory.sync.show_add_spec_to_transaction_dialog", quotation_script)
		self.assertIn("glass_factory.sync.spec_ready_filters", sync_script)
		self.assertIn('"Submitted"', sync_script)
		self.assertNotIn('"Cancelled"', sync_script.split("spec_ready_filters", 1)[1].split("};", 1)[0])

	def test_shared_glass_sync_does_not_depend_on_controller_globals(self):
		script = (APP_ROOT / "public/js/gf_glass_sync.js").read_text()
		self.assertIn("glass_factory.sync.add_spec_to_transaction", script)
		self.assertIn("glass_factory.sync.spec_ready_filters", script)
		self.assertNotIn("gf_remove_empty_item_rows(frm)", script)
		self.assertNotIn("gf_existing_glass_rates(frm)", script)
		self.assertNotIn("gf_toggle_items_grid(frm)", script)
