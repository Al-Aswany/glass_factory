"""Tests for design attachment propagation across Glass Factory documents."""

from __future__ import annotations

import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint

from glass_factory.glass_factory.design_attachments import (
	copy_file_to_document,
	get_design_files_from_item,
	get_design_files_from_spec,
	sync_item_design_attachments_to_doc,
)
from glass_factory.glass_factory.doctype.cutting_job.cutting_job import make_cutting_job
from glass_factory.glass_factory.spec_transaction import add_spec_to_transaction
from glass_factory.glass_factory.tests.test_glass_product_specification_items import (
	RAW_SHEET_ITEM,
	_insert_full_spec,
)


DESIGN_FILE_NAME = "gf-design-test.dxf"
DESIGN_FILE_CONTENT = b"GF design attachment propagation test content"


def _count_doc_files(doctype: str, name: str, *, file_url: str | None = None) -> int:
	filters = {"attached_to_doctype": doctype, "attached_to_name": name}
	if file_url:
		filters["file_url"] = file_url
	return frappe.db.count("File", filters)


def _attach_design_to_spec(spec) -> str:
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": DESIGN_FILE_NAME,
			"attached_to_doctype": "Glass Product Specification",
			"attached_to_name": spec.name,
			"content": DESIGN_FILE_CONTENT,
			"is_private": 0,
		}
	)
	file_doc.save(ignore_permissions=True)

	spec.append(
		"design_attachments",
		{
			"design_file": file_doc.file_url,
			"file_name": DESIGN_FILE_NAME,
			"is_primary": 1,
		},
	)
	spec.save(ignore_permissions=True)
	spec.reload()
	return file_doc.file_url


def _cancel_and_delete(doctype: str, name: str | None) -> None:
	if not name or not frappe.db.exists(doctype, name):
		return
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


class TestDesignAttachmentPropagation(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Item", RAW_SHEET_ITEM):
			raise unittest.SkipTest("Sample raw sheet Item is not installed on this site.")

	def setUp(self):
		self._cleanup: list[tuple[str, str]] = []

	def tearDown(self):
		for doctype, name in reversed(self._cleanup):
			_cancel_and_delete(doctype, name)
		frappe.db.commit()

	def _track(self, doctype: str, name: str) -> None:
		self._cleanup.append((doctype, name))

	def _prepare_spec_with_design(self):
		spec = _insert_full_spec()
		self._track("Glass Product Specification", spec.name)
		design_url = _attach_design_to_spec(spec)
		spec.generate_items()
		spec.reload()
		return spec, design_url

	def _ensure_spec_transaction_context(self, spec):
		company = spec.company or frappe.db.get_value("Company", {"is_group": 0}, "name")
		customer = spec.customer or frappe.db.get_value("Customer", {}, "name")
		if not company or not customer:
			self.skipTest("Company/Customer required for selling documents.")
		spec.company = company
		spec.customer = customer
		spec.currency = frappe.db.get_value("Company", company, "default_currency")
		spec.save(ignore_permissions=True)
		spec.reload()
		return company, customer

	def _create_spec_quotation(self, spec):
		self._ensure_spec_transaction_context(spec)
		result = add_spec_to_transaction(spec.name, "Quotation")
		quotation = frappe.get_doc("Quotation", result["name"])
		self._track("Quotation", quotation.name)
		return quotation

	def _create_spec_sales_order(self, spec):
		self._ensure_spec_transaction_context(spec)
		result = add_spec_to_transaction(spec.name, "Sales Order")
		so = frappe.get_doc("Sales Order", result["name"])
		self._track("Sales Order", so.name)
		return so

	def test_spec_design_files_are_discovered(self):
		spec, design_url = self._prepare_spec_with_design()
		files = get_design_files_from_spec(spec.name)
		urls = {row.file_url for row in files}
		self.assertIn(design_url, urls)

	def test_generate_items_copies_design_to_final_item(self):
		spec, design_url = self._prepare_spec_with_design()
		final_item = spec.final_item_code

		self.assertEqual(
			frappe.db.get_value("Item", final_item, "custom_glass_product_specification"),
			spec.name,
		)
		self.assertGreater(_count_doc_files("Item", final_item, file_url=design_url), 0)
		self.assertEqual(len(get_design_files_from_item(final_item)), 1)

	def test_quotation_receives_item_design_attachments(self):
		spec, design_url = self._prepare_spec_with_design()
		quotation = self._create_spec_quotation(spec)

		self.assertGreater(_count_doc_files("Quotation", quotation.name, file_url=design_url), 0)

		before = _count_doc_files("Quotation", quotation.name, file_url=design_url)
		quotation.save(ignore_permissions=True)
		after = _count_doc_files("Quotation", quotation.name, file_url=design_url)
		self.assertEqual(before, after, "Repeated Quotation save must not duplicate design files")

	def test_sales_order_receives_item_design_attachments(self):
		spec, design_url = self._prepare_spec_with_design()
		so = self._create_spec_sales_order(spec)

		self.assertGreater(_count_doc_files("Sales Order", so.name, file_url=design_url), 0)

		before = _count_doc_files("Sales Order", so.name, file_url=design_url)
		so.save(ignore_permissions=True)
		after = _count_doc_files("Sales Order", so.name, file_url=design_url)
		self.assertEqual(before, after, "Repeated Sales Order save must not duplicate design files")

	def test_cutting_job_receives_item_design_attachments(self):
		spec, design_url = self._prepare_spec_with_design()
		so = self._create_spec_sales_order(spec)
		so.submit()

		job = make_cutting_job(so.name)
		if not job.name:
			job.insert(ignore_permissions=True)
		self._track("Cutting Job", job.name)
		job.save(ignore_permissions=True)

		self.assertGreater(_count_doc_files("Cutting Job", job.name, file_url=design_url), 0)

		before = _count_doc_files("Cutting Job", job.name, file_url=design_url)
		job.save(ignore_permissions=True)
		after = _count_doc_files("Cutting Job", job.name, file_url=design_url)
		self.assertEqual(before, after, "Repeated Cutting Job save must not duplicate design files")

	def test_processing_job_receives_item_design_attachments(self):
		spec, design_url = self._prepare_spec_with_design()
		so = self._create_spec_sales_order(spec)
		so.submit()

		job = make_cutting_job(so.name)
		if not job.name:
			job.insert(ignore_permissions=True)
		self._track("Cutting Job", job.name)
		job.submit()
		job.db_set("status", "Cut Stock Posted")

		proc_result = job.make_processing_job()
		processing = frappe.get_doc("Glass Processing Job", proc_result["processing_job"])
		self._track("Glass Processing Job", processing.name)
		processing.save(ignore_permissions=True)

		self.assertGreater(
			_count_doc_files("Glass Processing Job", processing.name, file_url=design_url),
			0,
		)

		before = _count_doc_files("Glass Processing Job", processing.name, file_url=design_url)
		processing.save(ignore_permissions=True)
		after = _count_doc_files("Glass Processing Job", processing.name, file_url=design_url)
		self.assertEqual(before, after, "Repeated Processing Job save must not duplicate design files")

	def test_copy_file_to_document_deduplicates(self):
		spec, design_url = self._prepare_spec_with_design()
		source_files = get_design_files_from_spec(spec.name)
		self.assertTrue(source_files)
		final_item = spec.final_item_code

		first = copy_file_to_document(source_files[0], "Item", final_item)
		second = copy_file_to_document(source_files[0], "Item", final_item)
		self.assertIsNone(first, "File already exists on Item after item generation sync")
		self.assertIsNone(second)
		self.assertEqual(_count_doc_files("Item", final_item, file_url=design_url), 1)

	def test_sync_skips_items_without_spec_link(self):
		spec, design_url = self._prepare_spec_with_design()
		quotation = self._create_spec_quotation(spec)

		for file_name in frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Quotation", "attached_to_name": quotation.name},
			pluck="name",
		):
			frappe.delete_doc("File", file_name, ignore_permissions=True)

		for row in quotation.items:
			frappe.db.set_value(
				"Item",
				row.item_code,
				"custom_glass_product_specification",
				None,
				update_modified=False,
			)

		copied = sync_item_design_attachments_to_doc(quotation)
		self.assertEqual(copied, [])
		self.assertEqual(_count_doc_files("Quotation", quotation.name, file_url=design_url), 0)

	def test_original_spec_file_row_is_not_moved(self):
		spec, design_url = self._prepare_spec_with_design()
		original_name = frappe.db.get_value(
			"File",
			{
				"file_url": design_url,
				"attached_to_doctype": "Glass Product Specification",
				"attached_to_name": spec.name,
			},
			"name",
		)
		self.assertTrue(original_name)

		quotation = self._create_spec_quotation(spec)

		self.assertTrue(frappe.db.exists("File", original_name))
		still_on_spec = frappe.db.get_value("File", original_name, ["attached_to_doctype", "attached_to_name"], as_dict=True)
		self.assertEqual(still_on_spec.attached_to_doctype, "Glass Product Specification")
		self.assertEqual(still_on_spec.attached_to_name, spec.name)

		quotation_file = frappe.db.get_value(
			"File",
			{
				"file_url": design_url,
				"attached_to_doctype": "Quotation",
				"attached_to_name": quotation.name,
			},
			"name",
		)
		self.assertTrue(quotation_file)
		self.assertNotEqual(quotation_file, original_name)
