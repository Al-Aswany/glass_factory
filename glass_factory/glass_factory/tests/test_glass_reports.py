"""Tests for Glass Factory management and operational script reports."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from glass_factory.glass_factory.report.report_utils import (
	derive_production_stage,
	pct,
	raw_sheet_area_m2,
)
from glass_factory.glass_factory.report.glass_costing_and_margin_report.glass_costing_and_margin_report import (
	_resolve_selling,
	execute as execute_costing,
)
from glass_factory.glass_factory.report.glass_cutting_yield_and_waste_report.glass_cutting_yield_and_waste_report import (
	execute as execute_yield,
)
from glass_factory.glass_factory.report.glass_processing_cost_report.glass_processing_cost_report import (
	execute as execute_processing_cost,
)
from glass_factory.glass_factory.report.glass_product_specification_summary.glass_product_specification_summary import (
	execute as execute_spec_summary,
)
from glass_factory.glass_factory.report.glass_production_progress_report.glass_production_progress_report import (
	execute as execute_progress,
)
from glass_factory.glass_factory.report.glass_stock_traceability_report.glass_stock_traceability_report import (
	execute as execute_traceability,
)

_PFX = "GF-RPT"
_ALL_REPORTS = (
	("Glass Product Specification Summary", execute_spec_summary),
	("Glass Costing and Margin Report", execute_costing),
	("Glass Production Progress Report", execute_progress),
	("Glass Cutting Yield and Waste Report", execute_yield),
	("Glass Processing Cost Report", execute_processing_cost),
	("Glass Stock Traceability Report", execute_traceability),
)


def _company() -> str:
	return frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")


def _run_report(execute_fn, filters=None):
	result = execute_fn(filters or {"company": _company()})
	columns = result[0]
	data = result[1]
	return columns, data


class TestGlassReportsLoad(IntegrationTestCase):
	"""Each report must load without error even when no data matches."""

	def test_all_reports_load_with_no_data(self):
		company = _company()
		self.assertTrue(company, "A default company is required for report tests")
		filters = {
			"company": company,
			"from_date": "2099-01-01",
			"to_date": "2099-12-31",
		}
		for title, execute_fn in _ALL_REPORTS:
			with self.subTest(report=title):
				columns, data = _run_report(execute_fn, filters)
				self.assertIsInstance(columns, list)
				self.assertGreater(len(columns), 0)
				self.assertIsInstance(data, list)
				self.assertEqual(len(data), 0)


class TestGlassReportsFilters(IntegrationTestCase):
	def test_company_and_date_filters_applied(self):
		company = _company()
		filters = {"company": company, "from_date": "2099-01-01", "to_date": "2099-12-31"}
		for title, execute_fn in _ALL_REPORTS:
			with self.subTest(report=title):
				_, data = _run_report(execute_fn, filters)
				self.assertEqual(data, [])


class TestProductionStageDerivation(unittest.TestCase):
	def test_spec_created(self):
		self.assertEqual(derive_production_stage(), "Spec Created")

	def test_quoted(self):
		self.assertEqual(derive_production_stage(quotation="QTN-001"), "Quoted")

	def test_sales_order_created(self):
		self.assertEqual(
			derive_production_stage(sales_order="SO-001"),
			"Sales Order Created",
		)

	def test_cutting_pending(self):
		self.assertEqual(
			derive_production_stage(
				sales_order="SO-001",
				has_cutting_job=True,
				cutting_job_status="Ready for Cutting",
			),
			"Cutting Pending",
		)

	def test_cut_wip(self):
		self.assertEqual(
			derive_production_stage(
				sales_order="SO-001",
				cut_qty=2,
				has_cutting_job=True,
				cutting_job_status="Cut Stock Posted",
			),
			"Cut WIP",
		)

	def test_processing_pending(self):
		self.assertEqual(
			derive_production_stage(
				sales_order="SO-001",
				cut_qty=2,
				has_cutting_job=True,
				has_processing_job=True,
				processing_job_status="Processing In Progress",
			),
			"Processing Pending",
		)

	def test_finished_goods(self):
		self.assertEqual(
			derive_production_stage(
				sales_order="SO-001",
				qty_ordered=2,
				processed_qty=2,
			),
			"Finished Goods",
		)

	def test_delivered(self):
		self.assertEqual(
			derive_production_stage(
				sales_order="SO-001",
				qty_ordered=2,
				processed_qty=2,
				delivered_qty=2,
			),
			"Delivered",
		)


class TestCuttingYieldWaste(unittest.TestCase):
	def test_waste_percent_calculation(self):
		raw = raw_sheet_area_m2(3200, 2800)
		finished = 5.0
		waste = raw - finished
		self.assertEqual(pct(waste, raw), flt(waste / raw * 100, 2))

	def test_waste_percent_zero_denominator(self):
		self.assertEqual(pct(1, 0), 0.0)


class TestCostingReportSellingPrice(unittest.TestCase):
	def test_selling_from_sales_order_not_stock(self):
		spec = {"name": "GF-SPEC-TEST", "qty": 2, "rate_per_piece": 50, "amount": 100, "currency": "USD"}
		so_row = {"amount": 240, "rate": 120, "qty": 2, "currency": "USD"}
		amount, _currency = _resolve_selling(spec, so_row, "")
		self.assertEqual(amount, 240)

	def test_selling_from_quotation_when_no_so(self):
		spec = {"name": "GF-SPEC-TEST", "qty": 1, "rate_per_piece": 50, "amount": 50, "currency": "USD"}
		with patch(
			"glass_factory.glass_factory.report.glass_costing_and_margin_report.glass_costing_and_margin_report._quotation_selling_amount",
			return_value=180,
		):
			amount, _ = _resolve_selling(spec, None, "QTN-001")
		self.assertEqual(amount, 180)

	def test_selling_falls_back_to_spec(self):
		spec = {"name": "GF-SPEC-TEST", "qty": 3, "rate_per_piece": 75, "amount": 225, "currency": "USD"}
		amount, currency = _resolve_selling(spec, None, "")
		self.assertEqual(amount, 225)
		self.assertEqual(currency, "USD")


class TestCostingReportCostBreakdown(unittest.TestCase):
	def test_material_and_processing_cost_separate(self):
		from glass_factory.glass_factory.report.report_utils import compute_spec_internal_processing_cost

		spec = {
			"currency": "USD",
			"raw_cost_per_finished_piece": 40,
			"operation_pricing": [
				{"operation": "Temper", "pricing_basis": "Per Square Meter", "quantity": 1.0},
			],
		}
		with patch(
			"glass_factory.glass_factory.report.report_utils.get_operation_cost_rate",
			return_value=15,
		):
			processing = compute_spec_internal_processing_cost(spec)
		self.assertEqual(processing, 15.0)
		material = flt(spec["raw_cost_per_finished_piece"])
		self.assertEqual(material + processing, 55.0)


class TestProcessingCostReportRates(unittest.TestCase):
	def test_cost_rate_and_selling_rate_are_separate(self):
		from glass_factory.glass_factory.report.glass_processing_cost_report.glass_processing_cost_report import (
			build_operation_cost_row,
		)

		row = {
			"specification": f"{_PFX}-PROC-COST",
			"customer": "Cust",
			"company": _company(),
			"currency": "USD",
			"operation": "Temper",
			"pricing_basis": "Per Square Meter",
			"quantity": 2.0,
			"rate": 25,
			"amount": 50,
		}
		with patch(
			"glass_factory.glass_factory.report.glass_processing_cost_report.glass_processing_cost_report.get_operation_cost_rate",
			return_value=10,
		):
			data = build_operation_cost_row(row)

		self.assertEqual(data["cost_rate"], 10)
		self.assertEqual(data["selling_rate"], 25)
		self.assertEqual(data["cost_amount"], 20)
		self.assertEqual(data["selling_amount"], 50)
		self.assertEqual(data["profit"], 30)


class TestStockTraceabilityLinks(unittest.TestCase):
	def test_links_cutting_and_processing_stock_entries(self):
		from glass_factory.glass_factory.report.glass_stock_traceability_report.glass_stock_traceability_report import (
			build_traceability_row,
		)

		spec = {
			"name": f"{_PFX}-TRACE",
			"customer": "Cust",
			"company": _company(),
			"raw_item_code": "RAW-001",
			"cut_wip_item_code": "WIP-001",
			"final_item_code": "FIN-001",
			"raw_sheet_item": "RAW-001",
		}
		so_row = {
			"name": "SOITEM-001",
			"sales_order": f"{_PFX}-SO",
			"qty": 1,
			"gf_cut_qty": 1,
			"gf_processed_qty": 1,
			"gf_delivered_qty": 0,
			"gf_cutting_job": f"{_PFX}-CJ",
			"gf_processing_job": f"{_PFX}-PJ",
			"customer": "Cust",
			"company": _company(),
		}
		settings = {
			"raw_warehouse": "RAW-WH",
			"cut_wip_warehouse": "WIP-WH",
			"final_goods_warehouse": "FG-WH",
		}

		def _get_value(doctype, name, field, *args, **kwargs):
			if doctype == "Cutting Job" and field == "status":
				return "Cut Stock Posted"
			if doctype == "Glass Processing Job" and field == "status":
				return "Final Stock Posted"
			if doctype == "Cutting Job" and field == "linked_stock_entry":
				return f"{_PFX}-SE1"
			if doctype == "Glass Processing Job" and field == "linked_stock_entry":
				return f"{_PFX}-SE2"
			return ""

		with patch(
			"glass_factory.glass_factory.report.glass_stock_traceability_report.glass_stock_traceability_report.frappe.db.get_value",
			side_effect=_get_value,
		), patch(
			"glass_factory.glass_factory.report.glass_stock_traceability_report.glass_stock_traceability_report.fetch_delivery_note_for_so_item",
			return_value="",
		):
			row = build_traceability_row(spec, so_row, settings)

		self.assertEqual(row["cutting_stock_entry"], f"{_PFX}-SE1")
		self.assertEqual(row["processing_stock_entry"], f"{_PFX}-SE2")
		self.assertEqual(row["raw_item"], "RAW-001")
		self.assertEqual(row["wip_item"], "WIP-001")
		self.assertEqual(row["finished_item"], "FIN-001")


class TestProductionProgressStageInReport(unittest.TestCase):
	def test_progress_report_derives_cut_wip_stage(self):
		from glass_factory.glass_factory.report.glass_production_progress_report.glass_production_progress_report import (
			build_progress_row,
		)

		spec = {"name": f"{_PFX}-PROG", "customer": "Cust", "company": _company()}
		so_row = {
			"name": "SOITEM-P",
			"sales_order": f"{_PFX}-SO-P",
			"qty": 2,
			"gf_cut_qty": 2,
			"gf_processed_qty": 0,
			"gf_delivered_qty": 0,
			"gf_cutting_job": f"{_PFX}-CJ-P",
			"gf_processing_job": "",
			"customer": "Cust",
			"company": _company(),
		}

		with patch(
			"glass_factory.glass_factory.report.glass_production_progress_report.glass_production_progress_report.fetch_linked_quotation",
			return_value="",
		), patch(
			"glass_factory.glass_factory.report.glass_production_progress_report.glass_production_progress_report.fetch_delivery_note_for_so_item",
			return_value="",
		), patch(
			"glass_factory.glass_factory.report.glass_production_progress_report.glass_production_progress_report._linked_stock_entry",
			return_value="",
		), patch(
			"glass_factory.glass_factory.report.glass_production_progress_report.glass_production_progress_report.frappe.db.get_value",
			return_value="Cut Stock Posted",
		):
			row = build_progress_row(spec, so_row)

		self.assertEqual(row["current_stage"], "Cut WIP")
