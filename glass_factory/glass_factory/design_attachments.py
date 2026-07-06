"""Propagate Glass Product Specification design files to Items and downstream documents."""

from __future__ import annotations

import frappe
from frappe.utils import cint

ITEM_CODE_FIELD_NAMES = (
	"item_code",
	"finished_item",
	"output_item",
	"source_item",
	"raw_sheet_item",
	"cut_wip_item",
	"final_item",
)

CHILD_TABLE_ITEM_FIELDS: dict[str, list[tuple[str, str]]] = {
	"Quotation": [("items", "item_code")],
	"Sales Order": [("items", "item_code")],
	"Cutting Job": [
		("pieces", "raw_sheet_item"),
		("pieces", "cut_wip_item"),
		("pieces", "final_item"),
		("source_sheets", "item_code"),
		("optimization_placed_pieces", "item_code"),
	],
	"Glass Processing Job": [
		("inputs", "cut_wip_item"),
		("outputs", "final_item"),
	],
}


def get_design_files_from_spec(spec_name: str | frappe.model.document.Document) -> list[frappe.model.document.Document]:
	"""Return File documents for a specification's design attachments."""
	spec = (
		frappe.get_doc("Glass Product Specification", spec_name)
		if isinstance(spec_name, str)
		else spec_name
	)
	seen_urls: set[str] = set()
	files: list[frappe.model.document.Document] = []

	for row in spec.get("design_attachments") or []:
		file_url = (row.get("design_file") or "").strip()
		if not file_url or file_url in seen_urls:
			continue
		file_doc = _resolve_file_from_url(file_url)
		if file_doc:
			seen_urls.add(file_url)
			files.append(file_doc)

	for file_name in frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Glass Product Specification",
			"attached_to_name": spec.name,
		},
		pluck="name",
	):
		file_doc = frappe.get_doc("File", file_name)
		file_url = (file_doc.file_url or "").strip()
		if file_url and file_url not in seen_urls:
			seen_urls.add(file_url)
			files.append(file_doc)

	return files


def get_design_files_from_item(item_code: str) -> list[frappe.model.document.Document]:
	"""Return File documents attached to an Item."""
	if not item_code:
		return []

	return [
		frappe.get_doc("File", name)
		for name in frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "Item",
				"attached_to_name": item_code,
			},
			pluck="name",
			order_by="creation asc",
		)
	]


def copy_file_to_document(
	source_file: str | frappe.model.document.Document,
	target_doctype: str,
	target_name: str,
) -> str | None:
	"""Create a new File row on target document referencing the same file_url."""
	if not target_doctype or not target_name:
		return None

	if isinstance(source_file, str):
		if not frappe.db.exists("File", source_file):
			return None
		source_file = frappe.get_doc("File", source_file)

	file_url = (source_file.file_url or "").strip()
	if not file_url:
		return None

	if frappe.db.exists(
		"File",
		{
			"file_url": file_url,
			"attached_to_doctype": target_doctype,
			"attached_to_name": target_name,
		},
	):
		return None

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": source_file.file_name or file_url.rsplit("/", 1)[-1],
			"file_url": file_url,
			"is_private": cint(source_file.is_private),
			"content_hash": source_file.get("content_hash"),
			"attached_to_doctype": target_doctype,
			"attached_to_name": target_name,
		}
	)
	file_doc.save(ignore_permissions=True)
	return file_doc.name


def sync_design_attachments_to_item(spec_doc: str | frappe.model.document.Document) -> list[str]:
	"""Copy specification design files to generated Items and link the spec on Item."""
	spec = (
		frappe.get_doc("Glass Product Specification", spec_doc)
		if isinstance(spec_doc, str)
		else spec_doc
	)

	item_codes = _resolve_spec_item_codes(spec)
	if not item_codes:
		return []

	design_files = get_design_files_from_spec(spec)
	if not design_files:
		return []

	copied: list[str] = []
	for item_code in item_codes:
		_set_item_spec_link(item_code, spec.name)
		for source_file in design_files:
			new_name = copy_file_to_document(source_file, "Item", item_code)
			if new_name:
				copied.append(new_name)

	return copied


def sync_item_design_attachments_to_doc(doc: frappe.model.document.Document) -> list[str]:
	"""Copy Item design attachments onto a saved downstream document."""
	if not doc or not doc.name:
		return []

	item_codes = _collect_item_codes_from_doc(doc)
	if not item_codes:
		return []

	copied: list[str] = []
	for item_code in item_codes:
		spec_name = frappe.db.get_value("Item", item_code, "custom_glass_product_specification")
		if not spec_name:
			continue

		for source_file in get_design_files_from_item(item_code):
			new_name = copy_file_to_document(source_file, doc.doctype, doc.name)
			if new_name:
				copied.append(new_name)

	return copied


def on_glass_product_specification_save(doc: frappe.model.document.Document, method=None) -> None:
	if not doc.name:
		return
	if not _resolve_spec_item_codes(doc):
		return
	sync_design_attachments_to_item(doc)


def on_document_update_sync_design_attachments(doc: frappe.model.document.Document, method=None) -> None:
	if doc.name:
		sync_item_design_attachments_to_doc(doc)


def _resolve_spec_item_codes(spec) -> list[str]:
	"""Return spec-specific generated items (exclude shared raw sheet items)."""
	codes: list[str] = []
	for fieldname in ("final_item_code", "generated_item", "cut_wip_item_code"):
		value = (spec.get(fieldname) or "").strip()
		if value and value not in codes:
			codes.append(value)
	return codes


def _set_item_spec_link(item_code: str, spec_name: str) -> None:
	if not item_code or not spec_name:
		return

	current = frappe.db.get_value("Item", item_code, "custom_glass_product_specification")
	if current:
		return

	frappe.db.set_value(
		"Item",
		item_code,
		"custom_glass_product_specification",
		spec_name,
		update_modified=False,
	)


def _collect_item_codes_from_doc(doc) -> list[str]:
	codes: list[str] = []
	seen: set[str] = set()

	for child_table, fieldname in CHILD_TABLE_ITEM_FIELDS.get(doc.doctype, []):
		for row in doc.get(child_table) or []:
			_add_item_code(codes, seen, row.get(fieldname))

	for table_field in doc.meta.get_table_fields():
		for row in doc.get(table_field.fieldname) or []:
			for fieldname in ITEM_CODE_FIELD_NAMES:
				if row.meta.has_field(fieldname):
					_add_item_code(codes, seen, row.get(fieldname))

	return codes


def _add_item_code(codes: list[str], seen: set[str], value) -> None:
	item_code = (value or "").strip()
	if item_code and item_code not in seen:
		seen.add(item_code)
		codes.append(item_code)


def _resolve_file_from_url(file_url: str) -> frappe.model.document.Document | None:
	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name", order_by="creation asc")
	if file_name:
		return frappe.get_doc("File", file_name)

	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_url.rsplit("/", 1)[-1],
			"file_url": file_url,
			"is_private": 0,
		}
	)
