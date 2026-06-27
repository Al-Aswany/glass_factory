import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt

from glass_factory.glass_factory.quotation_glass import sync_glass_pieces_to_items
from glass_factory.glass_factory.spec_transaction import (
	add_spec_to_transaction,
	map_spec_to_transaction_row,
	mark_transaction_rate_overrides,
	validate_spec_ready_for_transaction,
)
from glass_factory.glass_factory.tests.test_glass_product_specification import _base_spec_kwargs, _new_spec
from glass_factory.glass_factory.tests.test_glass_product_specification_items import (
	RAW_SHEET_ITEM,
	_full_spec_kwargs,
	_insert_full_spec,
)


def _selling_price_list(currency=None):
	price_list = _price_list_for_currency(currency) if currency else None
	if price_list:
		return price_list
	return frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")


def _new_blank_quotation(company, customer, currency=None):
	quotation = frappe.new_doc("Quotation")
	quotation.quotation_to = "Customer"
	quotation.party_name = customer
	quotation.customer = customer
	quotation.company = company
	quotation.transaction_date = "2026-06-27"
	company_currency = frappe.db.get_value("Company", company, "default_currency")
	quotation.currency = currency or company_currency
	price_list = _price_list_for_currency(quotation.currency) or _selling_price_list(quotation.currency)
	if price_list and frappe.db.get_value("Price List", price_list, "currency") == quotation.currency:
		quotation.selling_price_list = price_list
	return quotation


def _new_blank_sales_order(company, customer, currency=None):
	so = frappe.new_doc("Sales Order")
	so.customer = customer
	so.company = company
	so.transaction_date = "2026-06-27"
	so.delivery_date = "2026-06-27"
	company_currency = frappe.db.get_value("Company", company, "default_currency")
	so.currency = currency or company_currency
	price_list = _price_list_for_currency(so.currency) or _selling_price_list(so.currency)
	if price_list and frappe.db.get_value("Price List", price_list, "currency") == so.currency:
		so.selling_price_list = price_list
	return so


def _company_and_customer():
	company = frappe.db.get_value("Company", {"name": "Frappe"}, "name") or frappe.db.get_value(
		"Company", {"is_group": 0}, "name"
	)
	customer = frappe.db.get_value("Customer", {}, "name")
	return company, customer


def _placeholder_item_code():
	item = frappe.db.get_value(
		"Item",
		{
			"is_sales_item": 1,
			"disabled": 0,
			"item_code": ("not like", "GLS-%"),
		},
		"name",
	)
	if item:
		return item
	return frappe.db.get_value("Item", {"is_sales_item": 1, "disabled": 0}, "name")


def _insert_blank_quotation(company, customer, currency=None):
	quotation = _new_blank_quotation(company, customer, currency)
	item_code = _placeholder_item_code()
	if not item_code:
		raise unittest.SkipTest("No sales item available to create a draft Quotation.")
	quotation.append("items", {"item_code": item_code, "qty": 1, "rate": 1})
	quotation.insert()
	return quotation


def _insert_blank_sales_order(company, customer, currency=None):
	so = _new_blank_sales_order(company, customer, currency)
	item_code = _placeholder_item_code()
	if not item_code:
		raise unittest.SkipTest("No sales item available to create a draft Sales Order.")
	so.append("items", {"item_code": item_code, "qty": 1, "rate": 1})
	so.insert()
	return so


def _price_list_for_currency(currency):
	return frappe.db.get_value(
		"Price List",
		{"selling": 1, "currency": currency, "enabled": 1},
		"name",
	)


def _ready_spec(*, currency=None, selling_rate_per_m2=None, **overrides):
	company, customer = _company_and_customer()
	if not currency:
		currency = frappe.db.get_value("Company", company, "default_currency")
	if selling_rate_per_m2 is None:
		selling_rate_per_m2 = 25000 if currency == "TZS" else 25
	kwargs = {
		"company": company,
		"customer": customer,
		"currency": currency,
		"price_list": _price_list_for_currency(currency),
	}
	kwargs.update(overrides)
	doc = _insert_full_spec(**kwargs)
	doc.generate_items()
	doc.reload()
	doc.manual_selling_rate_per_m2 = selling_rate_per_m2
	doc.save()
	doc.reload()
	return doc


class TestSpecTransactionValidation(unittest.TestCase):
	def test_validate_requires_generated_items(self):
		spec = frappe._dict(
			name="GF-SPEC-TEST",
			items_generated=0,
			generation_status="Not Generated",
			status="Draft",
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			validate_spec_ready_for_transaction(spec)
		self.assertIn("Generate items", str(ctx.exception))


class TestGlassProductSpecificationTransactions(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Item", RAW_SHEET_ITEM):
			raise unittest.SkipTest("Sample raw sheet Item is not installed on this site.")

	def tearDown(self):
		frappe.db.rollback()

	def _assert_row_matches_spec(self, row, spec):
		final_item = spec.final_item_code or spec.generated_item
		self.assertEqual(row.item_code, final_item)
		self.assertEqual(flt(row.qty), flt(spec.qty))
		self.assertEqual(flt(row.rate), flt(spec.rate_per_piece))
		self.assertEqual(flt(row.amount), flt(spec.rate_per_piece * spec.qty, 2))
		self.assertEqual(row.gf_glass_specification, spec.name)
		self.assertEqual(cint(row.gf_from_glass_specification), 1)
		self.assertEqual(flt(row.gf_area_m2), flt(spec.area_m2))
		self.assertEqual(flt(row.gf_total_area_m2), flt(spec.area_m2 * spec.qty, 6))
		self.assertEqual(flt(row.gf_selling_rate_per_m2), flt(spec.selling_rate_per_m2))
		self.assertEqual(flt(row.gf_calculated_rate_per_m2), flt(spec.calculated_rate_per_m2))
		self.assertEqual(flt(row.gf_rate_per_piece), flt(spec.rate_per_piece))
		self.assertEqual(row.gf_raw_sheet_item, spec.raw_item_code or spec.raw_sheet_item)
		self.assertEqual(row.gf_cut_wip_item, spec.cut_wip_item_code)
		self.assertEqual(row.gf_final_item, final_item)
		self.assertEqual(row.gf_technical_summary, spec.technical_summary)

	def test_add_generated_spec_to_new_quotation(self):
		spec = _ready_spec()
		result = add_spec_to_transaction(spec.name, "Quotation")
		quotation = frappe.get_doc("Quotation", result["name"])
		self.assertEqual(len(quotation.items), 1)
		self._assert_row_matches_spec(quotation.items[0], spec)
		quotation.delete()
		spec.delete()
		frappe.db.commit()

	def test_add_generated_spec_to_existing_quotation(self):
		spec = _ready_spec()
		company, customer = _company_and_customer()
		quotation = _insert_blank_quotation(company, customer)

		result = add_spec_to_transaction(spec.name, "Quotation", target_name=quotation.name)
		quotation.reload()
		self.assertEqual(result["name"], quotation.name)
		spec_rows = [row for row in quotation.items if cint(row.gf_from_glass_specification)]
		self.assertEqual(len(spec_rows), 1)
		self._assert_row_matches_spec(spec_rows[0], spec)

		quotation.delete()
		spec.delete()
		frappe.db.commit()

	def test_add_generated_spec_to_new_sales_order(self):
		spec = _ready_spec()
		result = add_spec_to_transaction(spec.name, "Sales Order")
		so = frappe.get_doc("Sales Order", result["name"])
		self.assertEqual(len(so.items), 1)
		self._assert_row_matches_spec(so.items[0], spec)
		so.delete()
		spec.delete()
		frappe.db.commit()

	def test_add_generated_spec_to_existing_sales_order(self):
		spec = _ready_spec()
		company, customer = _company_and_customer()
		so = _insert_blank_sales_order(company, customer)

		result = add_spec_to_transaction(spec.name, "Sales Order", target_name=so.name)
		so.reload()
		self.assertEqual(result["name"], so.name)
		spec_rows = [row for row in so.items if cint(row.gf_from_glass_specification)]
		self.assertEqual(len(spec_rows), 1)
		self._assert_row_matches_spec(spec_rows[0], spec)

		so.delete()
		spec.delete()
		frappe.db.commit()

	def test_spec_without_generated_items_cannot_be_added(self):
		doc = _new_spec(**_full_spec_kwargs())
		doc.insert()
		with self.assertRaises(frappe.ValidationError) as ctx:
			add_spec_to_transaction(doc.name, "Quotation")
		self.assertIn("Generate items", str(ctx.exception))
		doc.delete()
		frappe.db.commit()

	def test_regeneration_required_spec_cannot_be_added(self):
		spec = _ready_spec()
		spec.generation_status = "Regeneration Required"
		spec.save()
		with self.assertRaises(frappe.ValidationError) as ctx:
			add_spec_to_transaction(spec.name, "Quotation")
		self.assertIn("regeneration", str(ctx.exception).lower())
		spec.delete()
		frappe.db.commit()

	def test_zero_price_spec_cannot_be_added(self):
		spec = _ready_spec()
		frappe.db.set_value(
			"Glass Product Specification",
			spec.name,
			{"rate_per_piece": 0, "selling_rate_per_m2": 0},
			update_modified=False,
		)
		spec.reload()
		with self.assertRaises(frappe.ValidationError) as ctx:
			add_spec_to_transaction(spec.name, "Quotation")
		self.assertIn("Refresh pricing", str(ctx.exception))
		spec.delete()
		frappe.db.commit()

	def test_duplicate_spec_fails_without_update_existing(self):
		spec = _ready_spec()
		result = add_spec_to_transaction(spec.name, "Quotation")
		quotation = frappe.get_doc("Quotation", result["name"])
		with self.assertRaises(frappe.ValidationError) as ctx:
			add_spec_to_transaction(spec.name, "Quotation", target_name=quotation.name)
		self.assertIn("already exists", str(ctx.exception))
		quotation.delete()
		spec.delete()
		frappe.db.commit()

	def test_update_existing_preserves_manually_edited_rate(self):
		spec = _ready_spec(qty=10)
		result = add_spec_to_transaction(spec.name, "Quotation")
		quotation = frappe.get_doc("Quotation", result["name"])
		row = quotation.items[0]
		row.rate = 26
		row.amount = flt(row.rate * row.qty, 2)
		quotation.save()

		spec.qty = 12
		spec.save()
		add_spec_to_transaction(
			spec.name,
			"Quotation",
			target_name=quotation.name,
			update_existing=True,
		)
		quotation.reload()
		row = quotation.items[0]
		self.assertEqual(flt(row.rate), 26)
		self.assertEqual(flt(row.qty), 12)
		self.assertEqual(flt(row.amount), flt(26 * 12, 2))
		self.assertEqual(cint(row.gf_transaction_rate_overridden), 1)

		quotation.delete()
		spec.delete()
		frappe.db.commit()

	def test_manual_rate_sets_override_flag_on_validate(self):
		spec = _ready_spec()
		result = add_spec_to_transaction(spec.name, "Quotation")
		quotation = frappe.get_doc("Quotation", result["name"])
		quotation.items[0].rate = 26
		mark_transaction_rate_overrides(quotation)
		self.assertEqual(cint(quotation.items[0].gf_transaction_rate_overridden), 1)

		quotation.items[0].rate = quotation.items[0].gf_rate_per_piece
		mark_transaction_rate_overrides(quotation)
		self.assertEqual(cint(quotation.items[0].gf_transaction_rate_overridden), 0)

		quotation.delete()
		spec.delete()
		frappe.db.commit()

	def test_glass_pieces_sync_preserves_spec_generated_rows(self):
		spec = _ready_spec()
		spec_result = add_spec_to_transaction(spec.name, "Quotation")
		quotation = frappe.get_doc("Quotation", spec_result["name"])
		spec_row_name = quotation.items[0].name

		quotation.append("glass_pieces", {
			"raw_sheet_item": RAW_SHEET_ITEM,
			"length_mm": 600,
			"width_mm": 400,
			"thickness_mm": 8,
			"qty": 1,
		})
		sync_glass_pieces_to_items(quotation)
		spec_rows = [
			row for row in quotation.items if cint(row.gf_from_glass_specification)
		]
		self.assertEqual(len(spec_rows), 1)
		self.assertEqual(spec_rows[0].name, spec_row_name)
		self.assertTrue(any(cint(row.gf_is_glass_item) for row in quotation.items))

		quotation.delete()
		spec.delete()
		frappe.db.commit()

	def test_usd_spec_adds_usd_quotation_row(self):
		price_list = _price_list_for_currency("USD")
		if not price_list:
			self.skipTest("No USD selling price list on this site.")

		company, _customer = _company_and_customer()
		company_currency = frappe.db.get_value("Company", company, "default_currency")
		exchange_name = None
		if company_currency != "USD" and not frappe.db.exists(
			"Currency Exchange",
			{"from_currency": "USD", "to_currency": company_currency},
		):
			exchange = frappe.new_doc("Currency Exchange")
			exchange.from_currency = "USD"
			exchange.to_currency = company_currency
			exchange.exchange_rate = 2500
			exchange.insert(ignore_permissions=True)
			exchange_name = exchange.name
			frappe.db.commit()

		spec = _ready_spec(currency="USD", selling_rate_per_m2=25)
		try:
			result = add_spec_to_transaction(spec.name, "Quotation")
			quotation = frappe.get_doc("Quotation", result["name"])
			self.assertEqual(quotation.currency, "USD")
			self._assert_row_matches_spec(quotation.items[0], spec)
			quotation.delete()
		finally:
			spec.delete()
			if exchange_name:
				frappe.delete_doc("Currency Exchange", exchange_name, force=1)
			frappe.db.commit()

	def test_tzs_spec_adds_tzs_quotation_row(self):
		price_list = _price_list_for_currency("TZS")
		if not price_list:
			self.skipTest("No TZS selling price list on this site.")
		spec = _ready_spec(currency="TZS", selling_rate_per_m2=25000)
		result = add_spec_to_transaction(spec.name, "Quotation")
		quotation = frappe.get_doc("Quotation", result["name"])
		self.assertEqual(quotation.currency, "TZS")
		self._assert_row_matches_spec(quotation.items[0], spec)
		quotation.delete()
		spec.delete()
		frappe.db.commit()

	def test_map_spec_row_includes_design_attachment_summary(self):
		spec = _ready_spec()
		spec.append(
			"design_attachments",
			{"file_name": "drawing-v1.pdf", "is_primary": 1},
		)
		spec.append("design_attachments", {"file_name": "extra.pdf"})
		spec.save()
		row = map_spec_to_transaction_row(spec)
		self.assertIn("Primary: drawing-v1.pdf", row["gf_design_attachment_summary"])
		self.assertIn("Other files: 1", row["gf_design_attachment_summary"])
		spec.delete()
		frappe.db.commit()

	def test_existing_quotation_glass_tests_still_compatible(self):
		from glass_factory.glass_factory.quotation_glass import build_quotation_items_from_glass
		import json

		result = build_quotation_items_from_glass(
			json.dumps([
				{
					"raw_sheet_item": RAW_SHEET_ITEM,
					"length_mm": 500,
					"width_mm": 300,
					"thickness_mm": 8,
					"qty": 3,
				}
			])
		)
		self.assertEqual(len(result["items"]), 1)
		self.assertTrue(result["items"][0]["gf_is_glass_item"])
