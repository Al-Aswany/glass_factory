"""Tests for the two-stage Glass Factory production costing model."""

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from glass_factory.glass_factory.spec_transaction import map_spec_to_transaction_row
from glass_factory.glass_factory.stock_posting import (
	build_cutting_repack,
	build_processing_repack,
	create_cutting_repack_stock_entry,
	create_finished_goods_repack_stock_entry,
)


def _stock_settings(**overrides):
	settings = frappe._dict(
		{
			"raw_warehouse": "Glass Raw Stock - _TC",
			"cut_wip_warehouse": "Glass Cut WIP - _TC",
			"final_goods_warehouse": "Glass Final Goods - _TC",
			"remnants_warehouse": "Glass Remnants - _TC",
			"scrap_warehouse": "Glass Scrap - _TC",
			"capitalize_processing_costs": 0,
			"processing_cost_expense_account": None,
			"processing_cost_cost_center": None,
		}
	)
	settings.update(overrides)
	return settings


def _cutting_job_three_wip_pieces():
	return frappe._dict(
		name="CJ-COST-001",
		source_sheets=[
			frappe._dict(
				idx=1,
				item_code="GLS-CLEAR-8MM-3210X2250",
				warehouse="Glass Raw Stock - _TC",
				qty_consumed=1,
				source_role="Raw Sheet",
				batch_no="RAW-BATCH-COST",
			),
		],
		pieces=[
			frappe._dict(
				idx=1,
				cut_wip_item="GLS-CLEAR-8MM-1200X800-CUT",
				target_warehouse="Glass Cut WIP - _TC",
				qty_required=1,
				qty_cut=1,
				length_mm=1200,
				width_mm=800,
			),
			frappe._dict(
				idx=2,
				cut_wip_item="GLS-CLEAR-8MM-1200X800-CUT",
				target_warehouse="Glass Cut WIP - _TC",
				qty_required=1,
				qty_cut=1,
				length_mm=1200,
				width_mm=800,
			),
			frappe._dict(
				idx=3,
				cut_wip_item="GLS-CLEAR-8MM-1200X800-CUT",
				target_warehouse="Glass Cut WIP - _TC",
				qty_required=1,
				qty_cut=1,
				length_mm=1200,
				width_mm=800,
			),
		],
	)


def _processing_job_three_final_pieces():
	return frappe._dict(
		name="GPJ-COST-001",
		cutting_job="CJ-COST-001",
		inputs=[
			frappe._dict(
				idx=1,
				cut_wip_item="GLS-CLEAR-8MM-1200X800-CUT",
				warehouse="Glass Cut WIP - _TC",
				qty=3,
				sales_order="SO-COST-001",
				sales_order_item="SOI-COST-001",
				length_mm=1200,
				width_mm=800,
			),
		],
		outputs=[
			frappe._dict(
				idx=1,
				final_item="GLS-CLEAR-8MM-1200X800-POL-TMP-FINAL",
				warehouse="Glass Final Goods - _TC",
				qty=3,
				sales_order="SO-COST-001",
				sales_order_item="SOI-COST-001",
				length_mm=1200,
				width_mm=800,
			),
		],
		operations=[
			frappe._dict(
				idx=1,
				operation="POL",
				status="Completed",
				qty=3,
				operation_count=1,
				sales_order_item="SOI-COST-001",
			),
			frappe._dict(
				idx=2,
				operation="TMP",
				status="Completed",
				qty=3,
				operation_count=1,
				sales_order_item="SOI-COST-001",
			),
		],
	)


def _patched_cutting_build(job):
	base = "glass_factory.glass_factory.stock_posting."

	def fake_role(item_code):
		return "Cut WIP" if item_code.endswith("-CUT") else "Raw Sheet"

	with patch(base + "_settings", return_value=_stock_settings()), \
		patch(base + "_company_from_job", return_value="_Test Company"), \
		patch(base + "_stock_uom", return_value="Nos"), \
		patch(base + "item_role", side_effect=fake_role), \
		patch(base + "ensure_output_batch", return_value="CUT-BATCH-COST"), \
		patch(base + "batch_row_fields", side_effect=lambda item, batch: {"batch_no": batch, "use_serial_batch_fields": 1} if batch else {}), \
		patch(base + "_source_row_rate", return_value=500):
		return build_cutting_repack(job)


def _patched_processing_build(job, *, settings=None):
	base = "glass_factory.glass_factory.stock_posting."
	settings = settings or _stock_settings(capitalize_processing_costs=1, processing_cost_expense_account="Expenses - _TC")
	real_get_value = frappe.db.get_value
	real_exists = frappe.db.exists

	def fake_role(item_code):
		if item_code.endswith("-CUT"):
			return "Cut WIP"
		return "Final"

	def _fake_get_value(doctype, *args, **kwargs):
		if doctype == "Sales Order Item":
			return frappe._dict(
				{
					"item_code": "GLS-CLEAR-8MM-1200X800-POL-TMP-FINAL",
					"gf_final_item": "GLS-CLEAR-8MM-1200X800-POL-TMP-FINAL",
				}
			)
		return real_get_value(doctype, *args, **kwargs)

	def _fake_exists(doctype, name):
		if doctype == "Account" and name == "Expenses - _TC":
			return True
		return real_exists(doctype, name)

	with patch(base + "_settings", return_value=_stock_settings()), \
		patch(base + "_glass_factory_settings", return_value=settings), \
		patch(base + "_company_from_processing_job", return_value="_Test Company"), \
		patch(base + "_stock_uom", return_value="Nos"), \
		patch(base + "item_role", side_effect=fake_role), \
		patch(base + "ensure_output_batch", return_value="FINAL-BATCH-COST"), \
		patch(base + "batch_row_fields", side_effect=lambda item, batch: {"batch_no": batch, "use_serial_batch_fields": 1} if batch else {}), \
		patch("frappe.db.get_value", side_effect=_fake_get_value), \
		patch("frappe.db.exists", side_effect=_fake_exists), \
		patch(base + "compute_processing_job_internal_cost", return_value=210):
		return build_processing_repack(job)


def _set_repack_totals(se, *, outgoing_total: float, incoming_rate: float) -> None:
	outgoing = _outgoing_rows(se)
	if outgoing:
		outgoing[0].basic_rate = flt(outgoing_total / flt(outgoing[0].transfer_qty), 6)
		outgoing[0].basic_amount = outgoing_total
		outgoing[0].amount = outgoing_total

	for row in _incoming_rows(se):
		row.basic_rate = incoming_rate
		row.basic_amount = flt(incoming_rate * flt(row.transfer_qty), 2)
		row.set_basic_rate_manually = 1

	se.distribute_additional_costs()
	se.update_valuation_rate()
	se.set_total_incoming_outgoing_value()


def _incoming_rows(se):
	return [row for row in se.items if row.t_warehouse]


def _outgoing_rows(se):
	return [row for row in se.items if row.s_warehouse]


class TestProductionCosting(IntegrationTestCase):
	def test_repack1_material_only_allocation(self):
		job = _cutting_job_three_wip_pieces()
		se = _patched_cutting_build(job)
		_set_repack_totals(se, outgoing_total=500, incoming_rate=166.67)

		self.assertFalse(se.get("additional_costs"))
		self.assertAlmostEqual(flt(se.total_outgoing_value), 500, places=1)
		self.assertAlmostEqual(flt(se.total_incoming_value), 500, places=1)

		wip_rows = [row for row in _incoming_rows(se) if row.gf_source_item_role == "Cut WIP"]
		self.assertEqual(len(wip_rows), 3)
		for row in wip_rows:
			self.assertAlmostEqual(flt(row.valuation_rate), 166.67, places=2)

	def test_repack2_capitalizes_processing_cost(self):
		job = _processing_job_three_final_pieces()
		se = _patched_processing_build(job)
		_set_repack_totals(se, outgoing_total=500, incoming_rate=flt(500 / 3, 6))

		self.assertEqual(len(se.get("additional_costs") or []), 1)
		self.assertAlmostEqual(flt(se.additional_costs[0].base_amount), 210, places=2)
		self.assertAlmostEqual(flt(se.total_outgoing_value), 500, places=2)
		self.assertAlmostEqual(flt(se.total_incoming_value), 710, places=2)

		final_row = _incoming_rows(se)[0]
		self.assertAlmostEqual(flt(final_row.valuation_rate), 236.67, places=2)

	def test_transaction_selling_rate_uses_spec_not_stock_valuation(self):
		spec = frappe._dict(
			name="GPS-COST-SELL",
			final_item_code="GLS-CLEAR-8MM-1200X800-POL-TMP-FINAL",
			generated_item="GLS-CLEAR-8MM-1200X800-POL-TMP-FINAL",
			qty=3,
			area_m2=0.96,
			selling_rate_per_m2=1000,
			calculated_rate_per_m2=900,
			manual_selling_rate_per_m2=0,
			price_override=0,
			price_difference_per_m2=100,
			rate_per_piece=960,
			amount=2880,
			raw_item_code="GLS-CLEAR-8MM-3210X2250",
			cut_wip_item_code="GLS-CLEAR-8MM-1200X800-CUT",
			technical_summary="CLEAR 8mm 1200x800",
			length_mm=1200,
			width_mm=800,
			thickness_mm=8,
			polish=1,
			temper=1,
		)

		with patch("frappe.get_doc") as get_doc:
			get_doc.return_value = frappe._dict(
				item_name=spec.final_item_code,
				stock_uom="Nos",
			)
			row = map_spec_to_transaction_row(spec)

		self.assertAlmostEqual(flt(row["rate"]), 960, places=2)
		self.assertAlmostEqual(flt(row["gf_selling_rate_per_m2"]), 1000, places=2)
		self.assertNotAlmostEqual(flt(row["rate"]), 236.67, places=2)

	def test_repack2_skips_additional_cost_when_capitalization_disabled(self):
		job = _processing_job_three_final_pieces()
		settings = _stock_settings(capitalize_processing_costs=0)
		se = _patched_processing_build(job, settings=settings)

		self.assertFalse(se.get("additional_costs"))

	def test_repack2_requires_expense_account_when_capitalization_enabled(self):
		job = _processing_job_three_final_pieces()
		settings = _stock_settings(capitalize_processing_costs=1, processing_cost_expense_account=None)

		with self.assertRaises(frappe.ValidationError) as ctx:
			_patched_processing_build(job, settings=settings)

		self.assertIn("Processing Cost Expense Account", str(ctx.exception))

	def test_stage_entrypoint_aliases(self):
		self.assertIs(create_cutting_repack_stock_entry, build_cutting_repack)
		self.assertIs(create_finished_goods_repack_stock_entry, build_processing_repack)
