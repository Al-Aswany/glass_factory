import json
import unittest

import frappe
from frappe.tests import IntegrationTestCase

from glass_factory.glass_factory.quotation_glass import (
	build_quotation_items_from_glass,
	item_table_editable_fields,
	processing_flags_from_piece,
)


class TestQuotationGlassSave(IntegrationTestCase):
	def test_quotation_save_syncs_glass_pieces_to_items(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		customer = frappe.db.get_value("Customer", {}, "name")
		company = frappe.db.get_value("Company", {"name": "Frappe"}, "name") or frappe.db.get_value(
			"Company", {"is_group": 0}, "name"
		)
		company_currency = frappe.db.get_value("Company", company, "default_currency")
		price_list = frappe.db.get_value(
			"Price List",
			{"selling": 1, "currency": company_currency},
			"name",
		)
		if not price_list:
			self.skipTest("No selling price list matches company currency on this site.")

		doc = frappe.new_doc("Quotation")
		doc.quotation_to = "Customer"
		doc.party_name = customer
		doc.customer = customer
		doc.company = company
		doc.transaction_date = "2026-06-09"
		doc.selling_price_list = price_list
		doc.append("glass_pieces", {
			"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
			"length_mm": 600,
			"width_mm": 400,
			"thickness_mm": 8,
			"qty": 1,
			"process_polish": 1,
		})
		doc.append("items", {"qty": 0})

		doc.save()
		self.assertEqual(len(doc.items), 1)
		self.assertTrue(doc.items[0].gf_is_glass_item)
		self.assertTrue(doc.items[0].item_code)
		self.assertEqual(doc.items[0].qty, 1)

		doc.delete()
		frappe.db.commit()


class TestQuotationGlass(unittest.TestCase):
	def test_processing_flags_from_piece_uses_fixed_order(self):
		piece = {
			"process_temper": 1,
			"process_holes": 1,
			"process_polish": 1,
		}
		self.assertEqual(processing_flags_from_piece(piece), "POL-HOL-TMP")

	def test_processing_flags_from_piece_empty(self):
		self.assertEqual(processing_flags_from_piece({}), "")

	def test_build_quotation_items_from_glass_returns_item_name_and_uom(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		result = build_quotation_items_from_glass(
			json.dumps([
				{
					"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
					"length_mm": 500,
					"width_mm": 300,
					"thickness_mm": 8,
					"qty": 3,
					"process_polish": 1,
				}
			])
		)
		self.assertEqual(len(result["items"]), 1)
		row = result["items"][0]
		self.assertTrue(row["item_code"])
		self.assertTrue(row["item_name"])
		self.assertTrue(row["uom"])
		self.assertEqual(row["qty"], 3)

	def test_generated_item_table_contract_allows_only_rate_edits(self):
		self.assertEqual(item_table_editable_fields(), ("rate",))

	def test_build_items_preserves_existing_generated_rate_only(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		result = build_quotation_items_from_glass(
			json.dumps([
				{
					"name": "piece-1",
					"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
					"length_mm": 500,
					"width_mm": 300,
					"thickness_mm": 8,
					"qty": 3,
					"rate": 10,
				}
			]),
			existing_glass_rates=json.dumps({"piece-1": 42}),
		)
		row = result["items"][0]
		self.assertEqual(row["rate"], 42)
		self.assertEqual(row["qty"], 3)
		self.assertEqual(row["uom"], frappe.db.get_value("Item", row["item_code"], "stock_uom") or "Nos")

	def test_build_items_sets_sales_order_delivery_date(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		result = build_quotation_items_from_glass(
			json.dumps([
				{
					"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
					"length_mm": 600,
					"width_mm": 400,
					"thickness_mm": 8,
					"qty": 1,
				}
			]),
			delivery_date="2026-06-10",
			parent_doctype="Sales Order",
		)
		self.assertEqual(result["items"][0]["delivery_date"], "2026-06-10")

	def test_build_items_sets_sales_order_warehouse_from_set_warehouse(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		result = build_quotation_items_from_glass(
			json.dumps([
				{
					"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
					"length_mm": 600,
					"width_mm": 400,
					"thickness_mm": 8,
					"qty": 1,
				}
			]),
			set_warehouse="Stores - _TC",
			parent_doctype="Sales Order",
		)
		self.assertEqual(result["items"][0]["warehouse"], "Stores - _TC")

	def test_build_items_defaults_sales_order_warehouse_from_settings(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		default_warehouse = frappe.db.get_single_value(
			"Glass Factory Settings", "final_goods_warehouse"
		)
		if not default_warehouse:
			self.skipTest("Final Goods Warehouse is not configured on this site.")

		result = build_quotation_items_from_glass(
			json.dumps([
				{
					"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
					"length_mm": 600,
					"width_mm": 400,
					"thickness_mm": 8,
					"qty": 1,
				}
			]),
			parent_doctype="Sales Order",
		)
		self.assertEqual(result["items"][0]["warehouse"], default_warehouse)

	def test_build_items_preserves_existing_glass_delivery_dates(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		result = build_quotation_items_from_glass(
			json.dumps([
				{
					"name": "piece-1",
					"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
					"length_mm": 600,
					"width_mm": 400,
					"thickness_mm": 8,
					"qty": 1,
				}
			]),
			existing_glass_delivery_dates=json.dumps({"piece-1": "2026-06-18"}),
		)
		self.assertEqual(result["items"][0]["delivery_date"], "2026-06-18")

	def test_sales_order_direct_cycle_uses_same_builder(self):
		if not frappe.db.exists("Item", "GLS-CLEAR-8MM-3210X2250"):
			self.skipTest("Sample raw sheet Item is not installed on this site.")

		result = build_quotation_items_from_glass(
			json.dumps([
				{
					"raw_sheet_item": "GLS-CLEAR-8MM-3210X2250",
					"length_mm": 600,
					"width_mm": 400,
					"thickness_mm": 8,
					"qty": 2,
					"process_temper": 1,
				}
			])
		)
		row = result["items"][0]
		self.assertTrue(row["gf_is_glass_item"])
		self.assertEqual(row["qty"], 2)
		self.assertEqual(row["gf_processing_flags"], "TMP")
