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
		self.assertIn('if (frm.doc.docstatus !== 1) return', script)
		self.assertIn('__("Cutting Job")', script)

	def test_quotation_item_grid_locks_generated_rows(self):
		quotation_script = (APP_ROOT / "public/js/quotation_glass.js").read_text()
		sync_script = (APP_ROOT / "public/js/gf_glass_sync.js").read_text()
		self.assertIn("GF_ITEM_LOCKED_FIELDS", quotation_script)
		self.assertIn("glass_factory.sync.sync_glass_items_to_form", quotation_script)
		self.assertIn("glass_factory.sync.item_locked_fields", sync_script)
		self.assertIn("items_grid.cannot_add_rows = has_glass", sync_script)
		self.assertIn("glass_factory.sync.set_grid_field_read_only", sync_script)

	def test_shared_glass_sync_does_not_depend_on_controller_globals(self):
		script = (APP_ROOT / "public/js/gf_glass_sync.js").read_text()
		self.assertIn("glass_factory.sync.remove_empty_item_rows(frm)", script)
		self.assertIn("glass_factory.sync.existing_glass_rates(frm)", script)
		self.assertIn("glass_factory.sync.toggle_items_grid(frm)", script)
		self.assertIn("glass_factory.sync.grid_has_field", script)
		self.assertNotIn("gf_remove_empty_item_rows(frm)", script)
		self.assertNotIn("gf_existing_glass_rates(frm)", script)
		self.assertNotIn("gf_toggle_items_grid(frm)", script)
		self.assertNotIn('"s_warehouse"', script)
		self.assertNotIn('"t_warehouse"', script)
