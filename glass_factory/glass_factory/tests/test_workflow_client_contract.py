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
		self.assertIn('frm.call("get_valid_actions")', script)
		self.assertIn('frm.call("run_action"', script)
		self.assertNotIn('Create Repack', script)
		self.assertNotIn('Submit Repack', script)
