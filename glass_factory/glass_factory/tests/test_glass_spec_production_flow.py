import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, cint, flt, today

from glass_factory.glass_factory.doctype.cutting_job.cutting_job import make_cutting_job
from glass_factory.glass_factory.selling_validations import validate_delivery_note
from glass_factory.glass_factory.spec_production import (
	build_cutting_piece_from_so_item,
	build_processing_operations_from_piece,
	is_glass_production_row,
	is_spec_production_row,
	resolve_processing_job_customer,
	validate_spec_so_item_for_production,
)
from glass_factory.glass_factory.spec_transaction import add_spec_to_transaction, map_spec_to_transaction_row
from glass_factory.glass_factory.stock_posting import build_cutting_repack, build_processing_repack
from glass_factory.glass_factory.tests.test_glass_product_specification_items import (
	RAW_SHEET_ITEM,
	_insert_full_spec,
)


def _spec_so_item(**overrides):
	row = frappe._dict(
		{
			"name": "SOI-SPEC-001",
			"idx": 1,
			"item_code": "GLS-CLEAR-8MM-1200X800-POL-HOL02-SHOL01-SLT03-SSLT01-TMP",
			"qty": 10,
			"gf_from_glass_specification": 1,
			"gf_glass_specification": "GF-SPEC-TEST",
			"gf_raw_sheet_item": RAW_SHEET_ITEM,
			"gf_cut_wip_item": "GLS-CLEAR-8MM-1200X800-CUT",
			"gf_final_item": "GLS-CLEAR-8MM-1200X800-POL-HOL02-SHOL01-SLT03-SSLT01-TMP",
			"gf_length_mm": 1200,
			"gf_width_mm": 800,
			"gf_thickness_mm": 8,
			"gf_area_m2": flt(0.96, 6),
			"gf_technical_summary": "CLEAR 8mm test",
			"gf_design_attachment_summary": "Primary: front.dxf",
		}
	)
	row.update(overrides)
	return row


def _cancel_and_delete(doctype, name):
	if not name or not frappe.db.exists(doctype, name):
		return
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.delete_doc(doctype, name, force=1)


class TestSpecProductionHelpers(unittest.TestCase):
	def test_is_glass_production_row_detects_spec_rows_without_gf_is_glass_item(self):
		row = frappe._dict(gf_from_glass_specification=1, gf_is_glass_item=0)
		self.assertTrue(is_glass_production_row(row))
		self.assertTrue(is_spec_production_row(row))

	def test_build_cutting_piece_from_spec_so_item(self):
		so = frappe._dict(name="SO-001", customer="CUST-001", customer_name="Test Customer")
		item = _spec_so_item()
		piece = build_cutting_piece_from_so_item(so, item, 10)
		self.assertEqual(piece["from_glass_specification"], 1)
		self.assertEqual(piece["glass_specification"], "GF-SPEC-TEST")
		self.assertEqual(piece["cut_wip_item"], "GLS-CLEAR-8MM-1200X800-CUT")
		self.assertEqual(piece["final_item"], item.gf_final_item)
		self.assertEqual(piece["technical_summary"], "CLEAR 8mm test")

	def test_build_processing_operations_from_spec_piece(self):
		piece = frappe._dict(
			from_glass_specification=1,
			polish=1,
			hole_count=2,
			special_hole_count=1,
			slot_count=3,
			special_slot_count=1,
			temper=1,
			sales_order="SO-001",
			sales_order_item="SOI-001",
			glass_specification="GF-SPEC-TEST",
		)
		operations = build_processing_operations_from_piece(piece, 10)
		labels = [row["operation_label"] for row in operations]
		self.assertIn("Polish", labels)
		self.assertIn("Hole × 2", labels)
		self.assertIn("Special Hole", labels)
		self.assertIn("Slot × 3", labels)
		self.assertIn("Special Slot", labels)
		self.assertIn("Temper", labels)

	def test_resolve_processing_job_customer_single(self):
		rows = [frappe._dict(customer="CUST-1", customer_name="Alpha")]
		customer, _name, display = resolve_processing_job_customer(rows)
		self.assertEqual(customer, "CUST-1")
		self.assertEqual(display, "Alpha")

	def test_resolve_processing_job_customer_multiple(self):
		rows = [
			frappe._dict(customer="CUST-1", customer_name="Alpha"),
			frappe._dict(customer="CUST-2", customer_name="Beta"),
		]
		customer, _name, display = resolve_processing_job_customer(rows)
		self.assertIsNone(customer)
		self.assertEqual(display, "Multiple Customers")

	def test_validate_spec_so_item_missing_final_item(self):
		item = _spec_so_item(gf_final_item=None, item_code=None)
		with self.assertRaises(frappe.ValidationError) as ctx:
			validate_spec_so_item_for_production(item)
		self.assertIn("Final Item", str(ctx.exception))

	def test_validate_spec_so_item_missing_cut_wip(self):
		item = _spec_so_item(gf_cut_wip_item=None)
		with self.assertRaises(frappe.ValidationError) as ctx:
			validate_spec_so_item_for_production(item)
		self.assertIn("Cut WIP Item", str(ctx.exception))

	def test_old_glass_piece_operations_use_processing_flags(self):
		piece = frappe._dict(processing_flags="POL-HOL02-TMP", from_glass_specification=0)
		operations = build_processing_operations_from_piece(piece, 1)
		codes = [row["operation"] for row in operations]
		self.assertEqual(codes, ["POL", "HOL", "TMP"])
		self.assertEqual(operations[1]["operation_count"], 2)

	def test_start_processing_returns_route_payload(self):
		job = frappe.get_doc({"doctype": "Cutting Job", "name": "CJ-ROUTE-1"})
		job.docstatus = 1
		job.status = "Cut Stock Posted"
		job.linked_stock_entry = "STE-001"
		job.append(
			"pieces",
			{
				"processing_flags": "POL",
				"qty_required": 1,
				"qty_cut": 1,
				"cut_wip_item": "CUT",
				"final_item": "FINAL",
				"sales_order": "SO-1",
				"sales_order_item": "SOI-1",
			},
		)

		with patch.object(job, "make_processing_job", return_value={"processing_job": "GPJ-001"}), \
			patch("glass_factory.glass_factory.doctype.cutting_job.cutting_job.frappe.db.get_value", return_value=1), \
			patch.object(job, "save"):
			result = job.start_processing()
		self.assertEqual(result["doctype"], "Glass Processing Job")
		self.assertEqual(result["name"], "GPJ-001")


class TestSpecProductionRepack(unittest.TestCase):
	def test_repack1_uses_spec_cut_wip_item_and_trace_fields(self):
		cutting_job = frappe._dict(
			name="CJ-SPEC-001",
			company="_Test Company",
			source_sheets=[
				frappe._dict(
					idx=1,
					item_code=RAW_SHEET_ITEM,
					warehouse="Stores - _TC",
					qty_consumed=1,
					source_role="Raw Sheet",
					batch_no="RAW-BATCH",
				),
			],
			pieces=[
				frappe._dict(
					idx=1,
					cut_wip_item="GLS-CLEAR-8MM-1200X800-CUT",
					final_item="GLS-CLEAR-8MM-1200X800-POL-HOL02-SHOL01-SLT03-SSLT01-TMP",
					sales_order="SO-SPEC-001",
					sales_order_item="SOI-SPEC-001",
					glass_specification="GF-SPEC-001",
					from_glass_specification=1,
					technical_summary="CLEAR 8mm",
					length_mm=1200,
					width_mm=800,
					qty_required=2,
					qty_cut=2,
				),
			],
		)

		with patch("glass_factory.glass_factory.stock_posting._settings", return_value=frappe._dict({"raw_warehouse": "Stores - _TC", "cut_wip_warehouse": "WIP - _TC"})), \
			patch("glass_factory.glass_factory.stock_posting._company_from_job", return_value="_Test Company"), \
			patch("glass_factory.glass_factory.stock_posting.item_role", side_effect=lambda item: "Raw Sheet" if item == RAW_SHEET_ITEM else "Cut WIP"), \
			patch("glass_factory.glass_factory.stock_posting._stock_uom", return_value="Nos"), \
			patch("glass_factory.glass_factory.stock_posting.ensure_output_batch", return_value="CUT-BATCH"), \
			patch("glass_factory.glass_factory.stock_posting.batch_row_fields", side_effect=lambda item, batch: {"batch_no": batch, "use_serial_batch_fields": 1} if batch else {}), \
			patch("glass_factory.glass_factory.stock_posting._allocate_cutting_repack_rates"):
			se = build_cutting_repack(cutting_job)

		cut_rows = [row for row in se.items if row.item_code == "GLS-CLEAR-8MM-1200X800-CUT"]
		self.assertEqual(len(cut_rows), 1)
		self.assertEqual(cut_rows[0].batch_no, "CUT-BATCH")
		self.assertEqual(cint(cut_rows[0].gf_from_glass_specification), 1)
		self.assertEqual(cut_rows[0].gf_glass_specification, "GF-SPEC-001")
		self.assertEqual(cut_rows[0].gf_technical_summary, "CLEAR 8mm")

	def test_repack1_requires_source_batch_for_spec_rows(self):
		cutting_job = frappe._dict(
			name="CJ-NO-BATCH",
			source_sheets=[frappe._dict(idx=1, item_code=RAW_SHEET_ITEM)],
			pieces=[
				frappe._dict(
					idx=1,
					cut_wip_item="GLS-CLEAR-8MM-1200X800-CUT",
					from_glass_specification=1,
					qty_required=1,
				),
			],
		)
		with self.assertRaises(frappe.ValidationError):
			build_cutting_repack(cutting_job)

	def test_repack2_uses_spec_final_item_and_trace_fields(self):
		processing_job = frappe._dict(
			name="GPJ-SPEC-001",
			cutting_job="CJ-SPEC-001",
			company="_Test Company",
			inputs=[
				frappe._dict(
					idx=1,
					cut_wip_item="GLS-CLEAR-8MM-1200X800-CUT",
					sales_order="SO-SPEC-001",
					sales_order_item="SOI-SPEC-001",
					glass_specification="GF-SPEC-001",
					from_glass_specification=1,
					technical_summary="CLEAR 8mm",
					length_mm=1200,
					width_mm=800,
					qty=2,
				),
			],
			outputs=[
				frappe._dict(
					idx=1,
					final_item="GLS-CLEAR-8MM-1200X800-POL-HOL02-SHOL01-SLT03-SSLT01-TMP",
					sales_order="SO-SPEC-001",
					sales_order_item="SOI-SPEC-001",
					glass_specification="GF-SPEC-001",
					from_glass_specification=1,
					technical_summary="CLEAR 8mm",
					length_mm=1200,
					width_mm=800,
					qty=2,
				),
			],
			operations=[
				frappe._dict(idx=1, operation="POL", operation_label="Polish", status="Completed"),
				frappe._dict(idx=2, operation="SHOL", operation_label="Special Hole", status="Completed"),
				frappe._dict(idx=3, operation="TMP", operation_label="Temper", status="Completed"),
			],
		)

		with patch("glass_factory.glass_factory.stock_posting._settings", return_value=frappe._dict({"cut_wip_warehouse": "WIP - _TC", "final_goods_warehouse": "Finished - _TC"})), \
			patch("glass_factory.glass_factory.stock_posting._company_from_processing_job", return_value="_Test Company"), \
			patch("glass_factory.glass_factory.stock_posting.item_role", side_effect=lambda item: "Cut WIP" if "CUT" in item else "Final"), \
			patch("glass_factory.glass_factory.stock_posting._stock_uom", return_value="Nos"), \
			patch("glass_factory.glass_factory.stock_posting.ensure_output_batch", side_effect=lambda item, *args, **kwargs: "CUT-BATCH" if "CUT" in item else "FINAL-BATCH"), \
			patch("glass_factory.glass_factory.stock_posting.batch_row_fields", side_effect=lambda item, batch: {"batch_no": batch, "use_serial_batch_fields": 1} if batch else {}), \
			patch("frappe.db.get_value", return_value=frappe._dict(item_code="GLS-CLEAR-8MM-1200X800-POL-HOL02-SHOL01-SLT03-SSLT01-TMP", gf_final_item="GLS-CLEAR-8MM-1200X800-POL-HOL02-SHOL01-SLT03-SSLT01-TMP")):
			se = build_processing_repack(processing_job)

		final_rows = [row for row in se.items if row.item_code.endswith("-TMP")]
		self.assertEqual(len(final_rows), 1)
		self.assertEqual(final_rows[0].batch_no, "FINAL-BATCH")
		self.assertEqual(cint(final_rows[0].gf_from_glass_specification), 1)
		self.assertEqual(final_rows[0].gf_glass_specification, "GF-SPEC-001")

	def test_pending_special_operations_block_repack2(self):
		processing_job = frappe._dict(
			name="GPJ-PENDING",
			inputs=[frappe._dict(idx=1, cut_wip_item="GLS-CLEAR-8MM-1200X800-CUT", qty=1)],
			outputs=[frappe._dict(idx=1, final_item="GLS-CLEAR-8MM-1200X800-POL-TMP", qty=1, sales_order_item="SOI-1")],
			operations=[
				frappe._dict(idx=1, operation="POL", status="Completed"),
				frappe._dict(idx=2, operation="SHOL", operation_label="Special Hole", status="Pending"),
			],
		)
		with self.assertRaises(frappe.ValidationError):
			build_processing_repack(processing_job)


def _fake_delivery_note(items):
	class _FakeDoc:
		doctype = "Delivery Note"
		docstatus = 0

		def __init__(self, rows):
			self.items = rows

		def get(self, key, default=None):
			return getattr(self, key, default)

	return _FakeDoc(items)


class TestSpecDeliveryValidation(unittest.TestCase):
	def test_delivery_blocks_before_processing_for_spec_row(self):
		row = frappe._dict(
			idx=1,
			item_code="GLS-CLEAR-8MM-1200X800-POL-TMP",
			gf_is_glass_item=0,
			against_sales_order="SO-001",
			so_detail="SOI-001",
			qty=1,
		)
		doc = _fake_delivery_note([row])

		with patch(
			"glass_factory.glass_factory.selling_validations.frappe.db.get_value",
			side_effect=[
				frappe._dict(gf_is_glass_item=0, gf_from_glass_specification=1),
				frappe._dict(
					item_code="GLS-CLEAR-8MM-1200X800-POL-TMP",
					gf_final_item="GLS-CLEAR-8MM-1200X800-POL-TMP",
					gf_cutting_job="CJ-001",
					gf_processing_job=None,
					gf_glass_specification="GF-SPEC-001",
					gf_from_glass_specification=1,
					gf_processed_qty=0,
					gf_technical_summary="CLEAR 8mm",
					delivered_qty=0,
				),
			],
		), patch(
			"glass_factory.glass_factory.selling_validations.item_role",
			return_value="Final",
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_delivery_note(doc)
			self.assertIn("processed", str(ctx.exception).lower())

	def test_delivery_allows_spec_row_after_processing(self):
		row = frappe._dict(
			idx=1,
			item_code="GLS-CLEAR-8MM-1200X800-POL-TMP",
			gf_is_glass_item=0,
			against_sales_order="SO-001",
			so_detail="SOI-001",
			qty=2,
		)
		doc = _fake_delivery_note([row])

		with patch(
			"glass_factory.glass_factory.selling_validations.frappe.db.get_value",
			side_effect=[
				frappe._dict(gf_is_glass_item=0, gf_from_glass_specification=1),
				frappe._dict(
					item_code="GLS-CLEAR-8MM-1200X800-POL-TMP",
					gf_final_item="GLS-CLEAR-8MM-1200X800-POL-TMP",
					gf_cutting_job="CJ-001",
					gf_processing_job="GPJ-001",
					gf_glass_specification="GF-SPEC-001",
					gf_from_glass_specification=1,
					gf_processed_qty=5,
					gf_technical_summary="CLEAR 8mm",
					delivered_qty=0,
				),
			],
		), patch(
			"glass_factory.glass_factory.selling_validations.item_role",
			return_value="Final",
		):
			validate_delivery_note(doc)
			self.assertEqual(cint(row.gf_from_glass_specification), 1)
			self.assertEqual(row.gf_glass_specification, "GF-SPEC-001")
			self.assertEqual(row.gf_technical_summary, "CLEAR 8mm")


class TestSpecProductionIntegration(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Item", RAW_SHEET_ITEM):
			raise unittest.SkipTest("Sample raw sheet Item is not installed on this site.")

	def test_map_spec_to_transaction_row_includes_production_fields(self):
		spec = _insert_full_spec()
		spec.generate_items()
		spec.reload()
		row = map_spec_to_transaction_row(spec)
		self.assertEqual(cint(row["gf_is_glass_item"]), 1)
		self.assertEqual(flt(row["gf_length_mm"]), 1200)
		self.assertTrue(row["gf_processing_flags"])
		spec.delete()
		frappe.db.commit()

	def _create_spec_sales_order(self, spec):
		company = frappe.db.get_value("Company", {"is_group": 0}, "name")
		customer = frappe.db.get_value("Customer", {}, "name")
		if not company or not customer:
			self.skipTest("Company/Customer required for Sales Order.")
		spec.company = company
		spec.customer = customer
		spec.currency = frappe.db.get_value("Company", company, "default_currency")
		spec.save()
		spec.reload()
		return add_spec_to_transaction(spec.name, "Sales Order")

	def test_cutting_job_pulls_spec_generated_sales_order_item(self):
		spec = _insert_full_spec()
		spec.generate_items()
		spec.reload()
		result = self._create_spec_sales_order(spec)
		so = frappe.get_doc("Sales Order", result["name"])
		so_item = so.items[0]
		self.assertEqual(cint(so_item.gf_from_glass_specification), 1)
		self.assertEqual(cint(so_item.gf_is_glass_item), 1)
		so.submit()

		job = make_cutting_job(so.name)
		self.assertEqual(len(job.pieces), 1)
		piece = job.pieces[0]
		self.assertEqual(cint(piece.from_glass_specification), 1)
		self.assertEqual(piece.glass_specification, spec.name)
		self.assertEqual(piece.cut_wip_item, spec.cut_wip_item_code)
		self.assertEqual(piece.final_item, spec.final_item_code)
		self.assertEqual(piece.raw_sheet_item, RAW_SHEET_ITEM)
		self.assertGreaterEqual(cint(piece.hole_count), 0)

		_cancel_and_delete("Cutting Job", job.name)
		so.cancel()
		spec.delete()
		frappe.db.commit()

	def test_cutting_job_pulls_spec_row_without_gf_is_glass_item(self):
		spec = _insert_full_spec()
		spec.generate_items()
		spec.reload()
		result = self._create_spec_sales_order(spec)
		so = frappe.get_doc("Sales Order", result["name"])
		frappe.db.set_value("Sales Order Item", so.items[0].name, "gf_is_glass_item", 0, update_modified=False)
		so.reload()
		self.assertEqual(cint(so.items[0].gf_from_glass_specification), 1)
		self.assertEqual(cint(so.items[0].gf_is_glass_item), 0)
		so.submit()

		job = make_cutting_job(so.name)
		self.assertEqual(len(job.pieces), 1)
		self.assertEqual(cint(job.pieces[0].from_glass_specification), 1)

		_cancel_and_delete("Cutting Job", job.name)
		so.cancel()
		spec.delete()
		frappe.db.commit()

	def test_make_processing_job_from_spec_pieces(self):
		spec = _insert_full_spec()
		spec.generate_items()
		spec.reload()
		result = self._create_spec_sales_order(spec)
		so = frappe.get_doc("Sales Order", result["name"])
		so.submit()
		job = make_cutting_job(so.name)
		if not job.name:
			job.insert(ignore_permissions=True)
		job.submit()
		job.db_set("status", "Cut Stock Posted")

		proc_result = job.make_processing_job()
		processing = frappe.get_doc("Glass Processing Job", proc_result["processing_job"])
		self.assertEqual(processing.inputs[0].cut_wip_item, spec.cut_wip_item_code)
		self.assertEqual(processing.outputs[0].final_item, spec.final_item_code)
		self.assertTrue(processing.customer_display)
		labels = [row.operation_label for row in processing.operations]
		self.assertIn("Polish", labels)
		self.assertIn("Hole × 2", labels)
		self.assertIn("Special Hole", labels)
		self.assertIn("Temper", labels)

		_cancel_and_delete("Glass Processing Job", processing.name)
		_cancel_and_delete("Cutting Job", job.name)
		so.cancel()
		spec.delete()
		frappe.db.commit()

	def test_old_glass_pieces_cutting_job_still_works(self):
		customer = frappe.db.get_value("Customer", {}, "name")
		company = frappe.db.get_value("Company", {"is_group": 0}, "name")
		if not customer or not company:
			self.skipTest("Company/Customer required for Sales Order.")

		company_currency = frappe.db.get_value("Company", company, "default_currency")
		price_list = frappe.db.get_value("Price List", {"selling": 1, "currency": company_currency}, "name")
		if not price_list:
			self.skipTest("No selling price list matches company currency on this site.")

		quotation = frappe.new_doc("Quotation")
		quotation.quotation_to = "Customer"
		quotation.party_name = customer
		quotation.customer = customer
		quotation.company = company
		quotation.transaction_date = today()
		quotation.selling_price_list = price_list
		quotation.currency = company_currency
		quotation.valid_till = add_days(today(), 30)
		quotation.append("glass_pieces", {
			"raw_sheet_item": RAW_SHEET_ITEM,
			"length_mm": 600,
			"width_mm": 400,
			"thickness_mm": 8,
			"qty": 1,
			"process_polish": 1,
		})
		quotation.append("items", {"qty": 0})
		quotation.save()
		self.assertTrue(quotation.items[0].gf_is_glass_item)
		self.assertEqual(cint(quotation.items[0].gf_from_glass_specification), 0)
		quotation.submit()

		from erpnext.selling.doctype.quotation.quotation import make_sales_order

		so = frappe.get_doc(make_sales_order(quotation.name))
		if not so.delivery_date or so.delivery_date <= so.transaction_date:
			so.delivery_date = add_days(so.transaction_date, 7)
		so.insert(ignore_permissions=True)
		so.submit()

		job = make_cutting_job(so.name)
		self.assertEqual(len(job.pieces), 1)
		self.assertEqual(cint(job.pieces[0].from_glass_specification), 0)
		self.assertTrue(job.pieces[0].processing_flags)

		_cancel_and_delete("Cutting Job", job.name)
		so.cancel()
		_cancel_and_delete("Quotation", quotation.name)
		frappe.db.commit()
