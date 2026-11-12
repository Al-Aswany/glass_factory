"""Source contracts for the pre-production corrections.

These checks read the application source. They do not connect to a site.
"""

import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]


def _source(*parts: str) -> str:
	return (APP_ROOT.joinpath(*parts)).read_text()


class TestPreproductionSourceContract(unittest.TestCase):
	def test_draft_stock_validation_does_not_write_bin(self):
		for name in ("stock_entry_hooks.py", "stock_valuation.py"):
			source = _source(name)
			self.assertNotIn('"Bin"', source, name)
			self.assertNotIn("_backfill_cut_wip_bin_from_repack1", source, name)

	def test_repack_rates_follow_every_erpnext_recalculation(self):
		"""Rates frozen at save time would miss a Landed Cost Voucher applied after stock has moved."""
		valuation = _source("stock_valuation.py")
		self.assertIn("class GlassStockEntryMixin", valuation)
		self.assertIn("def set_basic_rate", valuation)
		self.assertIn("def get_additional_cost_allocation", valuation)
		self.assertNotIn("frappe.db.set_value", valuation)
		self.assertNotIn("frappe.db.sql", valuation)
		hooks = _source("..", "hooks.py")
		self.assertIn("extend_doctype_class", hooks)
		self.assertIn("glass_factory.glass_factory.stock_valuation.GlassStockEntryMixin", hooks)
		self.assertIn("gf_processing_cost", _source("..", "install.py"))

	def test_sales_invoice_with_update_stock_obeys_delivery_limits(self):
		hooks = _source("..", "hooks.py")
		invoice = hooks.split('"Sales Invoice": {', 1)[1].split("\n\t},", 1)[0]
		self.assertIn("selling_validations.validate_delivery_note", invoice)
		self.assertIn("selling_validations.on_delivery_note_submit", invoice)
		selling = _source("selling_validations.py")
		self.assertIn("def _delivers_stock", selling)
		self.assertIn('doc.get("update_stock")', selling)
		invoice_guard = selling.split("def validate_delivery_note", 1)[1].split("if not _delivers_stock", 1)[0]
		self.assertIn('doc.doctype == "Sales Invoice"', invoice_guard)
		self.assertIn("_reject_non_commercial_item", invoice_guard)

	def test_capitalization_checks_the_valuation_account_and_a_specified_cost_rate(self):
		posting = _source("stock_posting.py")
		processing = _source("processing_cost.py")
		validation = _source("settings_validation.py")
		self.assertIn("missing_operation_cost_rates", posting)
		self.assertIn("seeded default", posting)
		self.assertIn("def cost_rate_is_specified", processing)
		self.assertIn("Expenses Included In Valuation", validation)
		self.assertIn("Deferred Income Tax", validation)
		child = _source("doctype", "glass_operation_rate", "glass_operation_rate.json")
		self.assertIn("cost_rate_set", child)

	def test_billing_area_precision_is_one_explicit_setting(self):
		uom = _source("selling_uom.py")
		self.assertIn("def set_billing_area_precision", uom)
		self.assertIn("BILLING_AREA_DECIMALS = (2, 3, 6)", uom)
		self.assertIn("_source_row_factor", uom)

	def test_install_checks_prerequisites_before_writing(self):
		source = _source("..", "install.py")
		foundation = source.split("def create_phase0_foundation", 1)[1].split("def create_roles", 1)[0]
		self.assertIn("validate_install_prerequisites(company)", foundation)
		self.assertLess(
			foundation.index("validate_install_prerequisites(company)"),
			foundation.index("create_roles()"),
		)
		self.assertIn("def ensure_glass_factory_setup", source)
		self.assertIn('"hidden": 1', source)
		self.assertIn("if first_seed:", source)
		self.assertIn("require_area_uom()", source)

	def test_prerequisites_run_before_the_app_is_registered(self):
		"""before_install runs ahead of add_to_installed_apps, so a failed check needs no --force."""
		hooks = _source("..", "hooks.py")
		self.assertIn('before_install = "glass_factory.install.before_install"', hooks)
		install = _source("..", "install.py")
		before = install.split("def before_install", 1)[1].split("def after_install", 1)[0]
		self.assertIn("validate_install_prerequisites()", before)

	def test_company_is_resolved_explicitly_and_warehouse_ownership_checked(self):
		install = _source("..", "install.py")
		self.assertNotIn("_get_install_company", install)
		self.assertIn("_assert_warehouse_company", install)
		self.assertIn("single-company", install)
		validation = _source("settings_validation.py")
		self.assertIn("glass_factory_company", validation)
		self.assertIn("Several companies exist", validation)

	def test_cancelled_movement_is_not_recreated(self):
		cutting = _source("doctype", "cutting_job", "cutting_job.py")
		processing = _source("doctype", "glass_processing_job", "glass_processing_job.py")
		movements = _source("stock_movements.py")
		for source in (cutting, processing):
			self.assertIn("recover_cancelled_stock_movement", source)
			self.assertNotIn("gf_cut_qty", source)
		self.assertNotIn('"gf_processed_qty": flt(row.qty)', processing)
		self.assertIn("reject_cancelled_movement", movements)
		self.assertIn("for_update=True", movements)

	def test_submitted_specification_is_sellable_and_margin_uses_cost_rate(self):
		transaction = _source("spec_transaction.py")
		pricing = _source("spec_pricing.py")
		self.assertIn('"Submitted"', transaction)
		self.assertIn("Cancelled specifications cannot be added", transaction)
		self.assertIn("processing_cost_per_piece", pricing)
		margin = pricing.split("def calculate_margin_fields", 1)[1].split("\ndef ", 1)[0]
		self.assertNotIn('spec.get("processing_amount_per_piece")', margin)
		self.assertIn("row.cost_amount = cost_amount", pricing)

	def test_selling_rows_use_area_uom_with_piece_stock_quantities(self):
		transaction = _source("spec_transaction.py")
		self.assertIn('"uom": area_uom', transaction)
		self.assertIn('"stock_qty": pieces', transaction)
		selling = _source("selling_validations.py")
		self.assertIn("delivered_pieces", selling)
		self.assertNotIn('flt(so_values.delivered_qty)', selling)
		cutting = _source("doctype", "cutting_job", "cutting_job.py")
		self.assertIn("sales_order_item_pieces", cutting)
		processing = _source("doctype", "glass_processing_job", "glass_processing_job.py")
		self.assertIn("sales_order_item_pieces", processing)

	def test_cost_fields_are_protected_and_kept_off_customer_documents(self):
		import json

		child = json.loads(_source("doctype", "glass_spec_operation_pricing", "glass_spec_operation_pricing.json"))
		fields = {field["fieldname"]: field for field in child["fields"]}
		for fieldname in ("cost_rate", "cost_amount"):
			self.assertEqual(fields[fieldname].get("permlevel"), 1, fieldname)
			self.assertEqual(fields[fieldname].get("read_only"), 1, fieldname)
			self.assertEqual(fields[fieldname].get("print_hide"), 1, fieldname)
		spec = json.loads(
			_source("doctype", "glass_product_specification", "glass_product_specification.json")
		)
		spec_fields = {field["fieldname"]: field for field in spec["fields"]}
		for fieldname in (
			"processing_cost_per_piece",
			"estimated_cost_per_piece",
			"gross_profit_per_piece",
			"gross_profit_per_m2",
			"gross_profit_percent",
			"total_gross_profit",
		):
			self.assertEqual(spec_fields[fieldname].get("permlevel"), 1, fieldname)
			self.assertEqual(spec_fields[fieldname].get("print_hide"), 1, fieldname)
		install = _source("..", "install.py")
		for fieldname in ("cost_rate", "cost_amount", "estimated_cost", "gross_profit"):
			self.assertNotIn(f'"fieldname": "gf_{fieldname}', install)
		transaction = _source("spec_transaction.py")
		self.assertNotIn("cost", transaction.split("def map_spec_to_transaction_row", 1)[1].split("def ", 1)[0].lower().replace("gf_", ""))

	def test_optimizer_is_gated_private_and_does_not_commit(self):
		optimizer = _source("glass_optimizer.py")
		self.assertNotIn("frappe.db.commit", optimizer)
		self.assertEqual(optimizer.count('"is_private": 1'), 2)
		self.assertNotIn('"is_private": 0', optimizer)
		self.assertIn("_require_cop_for_optimizer", optimizer)
