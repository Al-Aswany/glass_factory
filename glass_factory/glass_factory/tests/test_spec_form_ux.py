"""Tests for Glass Product Specification production UX layout and workflow."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt

from glass_factory.glass_factory.tests.test_glass_product_specification import _new_spec
from glass_factory.glass_factory.tests.test_glass_product_specification_items import (
	RAW_SHEET_ITEM,
	_insert_full_spec,
)
from glass_factory.glass_factory.tests.test_spec_pricing_ux import (
	_setup_per_m2_selling_price,
	_set_operation_rates,
	_teardown_per_m2_selling_price,
	_usd_rates,
)

APP_ROOT = Path(__file__).resolve().parents[2]
SPEC_JSON = (
	APP_ROOT / "glass_factory/doctype/glass_product_specification/glass_product_specification.json"
)
SPEC_JS = (
	APP_ROOT / "glass_factory/doctype/glass_product_specification/glass_product_specification.js"
)


class TestSpecFormMetadata(unittest.TestCase):
	def test_form_has_production_tabs(self):
		meta = json.loads(SPEC_JSON.read_text())
		field_order = meta.get("field_order") or []
		tab_labels = [
			f["label"]
			for f in meta.get("fields") or []
			if f.get("fieldtype") == "Tab Break"
		]
		for label in (
			"Specification",
			"Operations",
			"Operation Pricing",
			"Pricing Summary",
			"Raw Glass Pricing",
			"Design Files",
			"Generated Items / Transactions",
			"Advanced / Technical",
		):
			self.assertIn(label, tab_labels, f"Missing tab: {label}")

		for fieldname in (
			"tab_specification",
			"tab_operations",
			"tab_operation_pricing",
			"tab_pricing_summary",
			"tab_raw_glass_pricing",
			"tab_design_files",
			"tab_generated_items",
			"tab_advanced",
		):
			self.assertIn(fieldname, field_order)

	def test_deprecated_selling_per_sheet_not_in_main_pricing_sections(self):
		meta = json.loads(SPEC_JSON.read_text())
		fields = {f["fieldname"]: f for f in meta.get("fields") or []}
		field_order = meta.get("field_order") or []

		raw_tab_idx = field_order.index("tab_raw_glass_pricing")
		pricing_tab_idx = field_order.index("tab_pricing_summary")
		advanced_tab_idx = field_order.index("tab_advanced")
		deprecated_idx = field_order.index("raw_sheet_selling_rate_per_piece")

		self.assertGreater(deprecated_idx, advanced_tab_idx)
		self.assertNotIn(
			"raw_sheet_selling_rate_per_piece",
			field_order[pricing_tab_idx:raw_tab_idx],
		)
		self.assertNotIn(
			"raw_sheet_selling_rate_per_piece",
			field_order[raw_tab_idx:advanced_tab_idx],
		)
		self.assertIn("Deprecated", fields["raw_sheet_selling_rate_per_piece"]["label"])

	def test_pricing_summary_uses_production_labels(self):
		meta = json.loads(SPEC_JSON.read_text())
		labels = {f["fieldname"]: f.get("label") for f in meta.get("fields") or []}
		self.assertEqual(labels["raw_selling_amount_per_finished_piece"], "Raw Glass Charge")
		self.assertEqual(labels["processing_amount_per_piece"], "Processing Charge")
		self.assertEqual(labels["calculated_amount_per_piece"], "Calculated Price / Piece")
		self.assertEqual(labels["selling_rate_per_m2"], "Selling Rate / m²")
		self.assertEqual(labels["rate_per_piece"], "Final Price / Piece")
		self.assertEqual(labels["amount"], "Total Price")
		self.assertEqual(labels["gross_profit_per_piece"], "Profit / Piece")
		self.assertEqual(labels["gross_profit_percent"], "Profit %")

	def test_doctype_is_submittable(self):
		meta = json.loads(SPEC_JSON.read_text())
		self.assertEqual(cint(meta.get("is_submittable")), 1)

	def test_client_script_has_button_visibility_helpers(self):
		script = SPEC_JS.read_text()
		for fn in (
			"gf_spec_can_refresh_pricing",
			"gf_spec_can_generate_items",
			"gf_spec_pricing_is_valid",
			"gf_apply_spec_form_locks",
		):
			self.assertIn(fn, script)


class TestSpecWorkflowIntegration(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Item", RAW_SHEET_ITEM):
			raise unittest.SkipTest("Sample raw sheet item not installed.")
		if not frappe.db.exists("DocType", "Glass Factory Settings"):
			raise unittest.SkipTest("Glass Factory Settings not installed.")
		cls._previous_rates = frappe.get_all(
			"Glass Operation Rate",
			filters={"parent": "Glass Factory Settings"},
			fields=["*"],
		)
		_set_operation_rates(_usd_rates())
		cls._selling_price_state = _setup_per_m2_selling_price(rate=350)
		cls._buying_price_state = _setup_buying_price_for_tests()

	@classmethod
	def tearDownClass(cls):
		settings = frappe.get_single("Glass Factory Settings")
		settings.set("operation_rates", [])
		for row in cls._previous_rates:
			settings.append("operation_rates", row)
		settings.save(ignore_permissions=True)
		_teardown_per_m2_selling_price(cls._selling_price_state)
		_teardown_buying_price_for_tests(cls._buying_price_state)
		frappe.db.commit()
		super().tearDownClass()

	def _insert_priced_spec(self, **overrides):
		values = {
			"raw_sheet_item": RAW_SHEET_ITEM,
			"currency": "USD",
			"company": frappe.db.get_value("Company", {"is_group": 0}, "name"),
			"customer": frappe.db.get_value("Customer", {}, "name"),
			"price_list": cls_selling_pl(),
			"polish": 1,
			"bevel": 1,
			"sandblast": 1,
			"hole_count": 2,
			"slot_count": 1,
		}
		values.update(overrides)
		doc = _new_spec(**values)
		doc.insert()
		doc.refresh_pricing()
		doc.reload()
		return doc

	def test_refresh_pricing_sets_ready_status(self):
		doc = self._insert_priced_spec()
		self.assertIn(doc.status, ("Priced", "Ready"))
		self.assertGreater(flt(doc.calculated_amount_per_piece), 0)
		doc.delete()
		frappe.db.commit()

	def test_generate_items_blocked_without_pricing(self):
		doc = _new_spec(
			raw_sheet_item=RAW_SHEET_ITEM,
			length_mm=1200,
			width_mm=800,
		)
		doc.insert()
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.generate_items()
		self.assertIn("Refresh pricing", str(ctx.exception))
		doc.delete()
		frappe.db.commit()

	def test_missing_selling_item_price_still_raises_clear_error(self):
		missing_price_list = "GF-UX-Test-Missing-Selling"
		if not frappe.db.exists("Price List", missing_price_list):
			pl = frappe.new_doc("Price List")
			pl.price_list_name = missing_price_list
			pl.selling = 1
			pl.currency = "USD"
			pl.enabled = 1
			pl.insert(ignore_permissions=True)

		doc = _new_spec(
			raw_sheet_item=RAW_SHEET_ITEM,
			currency="USD",
			company=frappe.db.get_value("Company", {"is_group": 0}, "name"),
			customer=frappe.db.get_value("Customer", {}, "name"),
			price_list=missing_price_list,
		)
		doc.insert()
		with patch(
			"glass_factory.glass_factory.doctype.glass_product_specification.glass_product_specification.fetch_raw_selling_rate_per_m2",
			side_effect=lambda *args, **kwargs: (
				frappe.throw("Missing selling Item Price for raw sheet item with UOM Square Meter.")
				if kwargs.get("throw_if_missing")
				else 0
			),
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc.refresh_pricing()
		self.assertIn("Missing selling Item Price", str(ctx.exception))
		doc.delete()
		frappe.db.commit()

	def test_operation_override_behavior_still_works(self):
		doc = self._insert_priced_spec(polish=1)
		for row in doc.operation_pricing:
			if row.operation == "Polish":
				row.is_overridden = 1
				row.rate = 999.0
				row.source = "Manual"
		doc.save()
		doc.refresh_pricing()
		doc.reload()
		polish_row = next(r for r in doc.operation_pricing if r.operation == "Polish")
		self.assertAlmostEqual(flt(polish_row.rate), 999.0, places=1)
		self.assertEqual(cint(polish_row.is_overridden), 1)
		doc.delete()
		frappe.db.commit()

	def test_generate_items_sets_items_generated_status(self):
		doc = self._insert_priced_spec()
		doc.generate_items()
		doc.reload()
		self.assertEqual(doc.status, "Items Generated")
		self.assertEqual(doc.generation_status, "Generated")
		doc.delete()
		frappe.db.commit()

	def test_submitted_spec_cannot_edit_dimensions(self):
		doc = self._insert_priced_spec()
		doc.generate_items()
		doc.reload()
		doc.submit()
		doc.reload()
		self.assertEqual(doc.docstatus, 1)
		doc.length_mm = 1000
		with self.assertRaises(frappe.UpdateAfterSubmitError):
			doc.save()
		doc.reload()
		self.assertEqual(flt(doc.length_mm), 1200)
		doc.cancel()
		doc.delete()
		frappe.db.commit()

	def test_manual_verification_pricing_totals(self):
		"""800×1200 qty 1 with Polish, Bevel, Sandblast, 2 holes, 1 slot."""
		manual_rates = [
			{"operation": "Polish", "currency": "USD", "pricing_basis": "Per Edge Meter", "rate": 80, "cost_rate": 80, "enabled": 1},
			{"operation": "Bevel", "currency": "USD", "pricing_basis": "Per Edge Meter", "rate": 50, "cost_rate": 50, "enabled": 1},
			{"operation": "Hole", "currency": "USD", "pricing_basis": "Per Unit", "rate": 2, "cost_rate": 2, "enabled": 1},
			{"operation": "Slot", "currency": "USD", "pricing_basis": "Per Unit", "rate": 1.5, "cost_rate": 1.5, "enabled": 1},
			{"operation": "Sandblast", "currency": "USD", "pricing_basis": "Per Square Meter", "rate": 143.229167, "cost_rate": 143.229167, "enabled": 1},
		]
		_set_operation_rates(manual_rates)
		buying_rate = flt((999 * (1 - 0.204) - 663) / 0.96 * 7.2225, 2)
		frappe.db.set_value(
			"Item Price",
			self._buying_price_state["created_item_price"]
			or frappe.db.get_value(
				"Item Price",
				{"item_code": RAW_SHEET_ITEM, "price_list": self._buying_price_state["price_list"], "buying": 1},
				"name",
			),
			"price_list_rate",
			buying_rate,
		)
		frappe.db.commit()

		doc = self._insert_priced_spec(
			length_mm=800,
			width_mm=1200,
			qty=1,
			polish=1,
			bevel=1,
			sandblast=1,
			hole_count=2,
			slot_count=1,
			temper=0,
		)
		self.assertAlmostEqual(flt(doc.area_m2), 0.96, places=4)
		self.assertAlmostEqual(flt(doc.raw_selling_amount_per_finished_piece), 336, places=0)
		self.assertAlmostEqual(flt(doc.processing_amount_per_piece), 663, places=0)
		self.assertAlmostEqual(flt(doc.calculated_amount_per_piece), 999, places=0)
		self.assertAlmostEqual(flt(doc.selling_rate_per_m2), 1040.625, places=3)
		self.assertAlmostEqual(flt(doc.gross_profit_percent), 20.4, places=1)
		doc.delete()
		frappe.db.commit()
		_set_operation_rates(_usd_rates())

	def test_existing_pricing_calculations_unchanged_for_full_spec(self):
		doc = _insert_full_spec()
		doc.company = frappe.db.get_value("Company", {"is_group": 0}, "name")
		doc.customer = frappe.db.get_value("Customer", {}, "name")
		doc.currency = "USD"
		doc.price_list = cls_selling_pl()
		doc.save()
		before = {
			"processing_amount_per_piece": flt(doc.processing_amount_per_piece),
			"calculated_amount_per_piece": flt(doc.calculated_amount_per_piece),
			"rate_per_piece": flt(doc.rate_per_piece),
		}
		doc.refresh_pricing()
		doc.reload()
		for fieldname, value in before.items():
			if value > 0:
				self.assertAlmostEqual(flt(doc.get(fieldname)), value, places=2)
		doc.delete()
		frappe.db.commit()


def cls_selling_pl():
	from glass_factory.glass_factory.tests.test_spec_pricing_ux import PRICING_UX_SELLING_PRICE_LIST

	return PRICING_UX_SELLING_PRICE_LIST


def _setup_buying_price_for_tests(price_list="GF-UX-Buying", rate=100, currency="USD"):
	if not frappe.db.exists("Price List", price_list):
		pl = frappe.new_doc("Price List")
		pl.price_list_name = price_list
		pl.buying = 1
		pl.currency = currency
		pl.enabled = 1
		pl.insert(ignore_permissions=True)

	previous_default_pl = frappe.db.get_single_value(
		"Glass Factory Settings", "default_buying_price_list"
	)
	frappe.db.set_single_value("Glass Factory Settings", "default_buying_price_list", price_list)

	created_item_price = None
	existing = frappe.db.get_value(
		"Item Price",
		{"item_code": RAW_SHEET_ITEM, "price_list": price_list, "buying": 1},
		"name",
	)
	if not existing:
		ip = frappe.new_doc("Item Price")
		ip.item_code = RAW_SHEET_ITEM
		ip.price_list = price_list
		ip.buying = 1
		ip.currency = currency
		ip.price_list_rate = rate
		ip.insert(ignore_permissions=True)
		created_item_price = ip.name

	frappe.db.commit()
	return {
		"created_item_price": created_item_price,
		"previous_default_pl": previous_default_pl,
		"price_list": price_list,
	}


def _teardown_buying_price_for_tests(state):
	frappe.db.set_single_value(
		"Glass Factory Settings", "default_buying_price_list", state.get("previous_default_pl")
	)
	if state.get("created_item_price"):
		frappe.delete_doc(
			"Item Price", state["created_item_price"], force=True, ignore_permissions=True
		)
