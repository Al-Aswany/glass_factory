"""Batch helpers for glass stock tracking."""

from __future__ import annotations

import re

import frappe
from frappe.utils import cint, flt, getdate, nowdate, today

from glass_factory.glass_factory.item_resolver import _parse_raw_item_code, item_role

SOURCE_SHEET_ROLES = ("Raw Sheet", "Remnant")


def item_uses_batches(item_code: str) -> bool:
	return bool(frappe.db.get_value("Item", item_code, "has_batch_no"))


def validate_source_sheet_item_role(item_code: str, source_role: str | None = None) -> str:
	"""Ensure the source item is a batch-tracked Raw Sheet or Remnant."""
	role = item_role(item_code)
	if role not in SOURCE_SHEET_ROLES:
		frappe.throw(
			f"Item {item_code} must be a Raw Sheet or Remnant for source sheet consumption."
		)
	if not item_uses_batches(item_code):
		frappe.throw(f"Item {item_code} must have batch tracking enabled.")
	if source_role and source_role not in SOURCE_SHEET_ROLES:
		frappe.throw(f"Source role {source_role!r} must be Raw Sheet or Remnant.")
	if source_role and role != source_role:
		frappe.throw(
			f"Source sheet row: item role {role} does not match selected source role {source_role}."
		)
	return role


def validate_source_sheet_batch(
	batch_no: str,
	item_code: str,
	warehouse: str,
	source_role: str | None = None,
) -> None:
	"""Validate a selected batch before posting source sheet consumption."""
	if not batch_no:
		return

	validate_source_sheet_item_role(item_code, source_role)
	if not warehouse:
		frappe.throw("Warehouse is required when a Batch is selected on a source sheet row.")

	batch = frappe.db.get_value(
		"Batch",
		batch_no,
		["name", "item", "disabled", "expiry_date"],
		as_dict=True,
	)
	if not batch:
		frappe.throw(f"Batch {batch_no} does not exist.")
	if cint(batch.get("disabled")):
		frappe.throw(f"Batch {batch_no} is disabled.")
	if batch.get("item") != item_code:
		frappe.throw(f"Batch {batch_no} belongs to item {batch.get('item')}, not {item_code}.")

	expiry_date = batch.get("expiry_date")
	if expiry_date and getdate(expiry_date) < getdate(today()):
		frappe.throw(f"Batch {batch_no} expired on {expiry_date}.")

	from erpnext.stock.doctype.batch.batch import get_batch_qty

	qty = flt(get_batch_qty(batch_no=batch_no, warehouse=warehouse, item_code=item_code))
	if qty <= 0:
		frappe.throw(
			f"Batch {batch_no} has no available stock in warehouse {warehouse}."
		)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_source_sheet_batch_no(doctype, txt, searchfield, start, page_len, filters):
	"""Link query for source sheet batches with stock in the selected warehouse."""
	filters = frappe._dict(filters or {})
	item_code = filters.get("item_code")
	warehouse = filters.get("warehouse")
	source_role = filters.get("source_role")

	if not item_code or not warehouse:
		return []

	validate_source_sheet_item_role(item_code, source_role)

	from erpnext.controllers.queries import get_batch_no

	rows = get_batch_no(
		"Batch",
		txt,
		searchfield,
		start,
		page_len,
		{
			"item_code": item_code,
			"warehouse": warehouse,
			"posting_date": filters.get("posting_date") or nowdate(),
		},
	)
	return _enrich_source_sheet_batch_results(rows, item_code)


def batch_size_label(
	batch_no: str,
	item_code: str,
	dims_by_batch: dict[str, tuple[float, float]] | None = None,
) -> str:
	"""Human-readable size for batch dropdown descriptions."""
	length = width = 0.0
	if dims_by_batch and batch_no in dims_by_batch:
		length, width = dims_by_batch[batch_no]
	elif frappe.get_meta("Batch").has_field("gf_length_mm"):
		meta = frappe.db.get_value(
			"Batch",
			batch_no,
			["gf_length_mm", "gf_width_mm"],
			as_dict=True,
		)
		if meta:
			length = flt(meta.get("gf_length_mm"))
			width = flt(meta.get("gf_width_mm"))

	if not (length and width):
		parsed = _parse_raw_item_code(item_code) or {}
		length = flt(parsed.get("length_mm"))
		width = flt(parsed.get("width_mm"))

	if length and width:
		return f"{int(length)}×{int(width)} mm"
	return ""


def _enrich_source_sheet_batch_results(rows: list, item_code: str) -> list:
	"""Insert a glass size description column into ERPNext batch search rows."""
	if not rows:
		return []

	batch_names = [row[0] for row in rows if row and row[0]]
	dims_by_batch: dict[str, tuple[float, float]] = {}
	if batch_names and frappe.get_meta("Batch").has_field("gf_length_mm"):
		for batch in frappe.get_all(
			"Batch",
			filters={"name": ["in", batch_names]},
			fields=["name", "gf_length_mm", "gf_width_mm"],
		):
			length = flt(batch.get("gf_length_mm"))
			width = flt(batch.get("gf_width_mm"))
			if length and width:
				dims_by_batch[batch["name"]] = (length, width)

	enriched = []
	for row in rows:
		values = list(row)
		size_label = batch_size_label(values[0], item_code, dims_by_batch)
		if size_label:
			values.insert(1, size_label)
		enriched.append(tuple(values))
	return enriched


def ensure_remnant_batch(
	item_code: str,
	cutting_job: str,
	length_mm: float,
	width_mm: float,
	*,
	row_key: str = "",
) -> str:
	"""Create or reuse a Batch for a remnant output row."""
	if not item_uses_batches(item_code):
		return ""

	length = int(flt(length_mm))
	width = int(flt(width_mm))
	suffix = row_key or f"{length}x{width}"
	batch_id = f"{cutting_job}-{suffix}-REM"[:140]

	existing = frappe.db.get_value("Batch", {"batch_id": batch_id, "item": item_code}, "name")
	if existing:
		return existing

	batch = frappe.new_doc("Batch")
	batch.batch_id = batch_id
	batch.item = item_code
	if frappe.get_meta("Batch").has_field("gf_cutting_job"):
		batch.gf_cutting_job = cutting_job
	if frappe.get_meta("Batch").has_field("gf_length_mm"):
		batch.gf_length_mm = length
	if frappe.get_meta("Batch").has_field("gf_width_mm"):
		batch.gf_width_mm = width
	batch.insert(ignore_permissions=True)
	return batch.name


def ensure_output_batch(
	item_code: str,
	job_name: str,
	role: str,
	length_mm: float = 0,
	width_mm: float = 0,
	*,
	row_key: str = "",
	cutting_job: str | None = None,
) -> str:
	"""Create or reuse a Batch for Cut WIP and Final output rows."""
	if not item_uses_batches(item_code):
		return ""

	suffix = {"Cut WIP": "CUT", "Final": "FIN"}.get(role)
	if not suffix:
		frappe.throw(f"Cannot create output Batch for unsupported glass role {role}.")

	length, width = _batch_dimensions(item_code, length_mm, width_mm)
	batch_id = f"{job_name}-{_clean_batch_key(row_key)}-{suffix}"[:140]

	existing = frappe.db.get_value("Batch", {"batch_id": batch_id, "item": item_code}, "name")
	if existing:
		return existing

	batch = frappe.new_doc("Batch")
	batch.batch_id = batch_id
	batch.item = item_code
	if frappe.get_meta("Batch").has_field("gf_cutting_job") and cutting_job:
		batch.gf_cutting_job = cutting_job
	if frappe.get_meta("Batch").has_field("gf_length_mm"):
		batch.gf_length_mm = length
	if frappe.get_meta("Batch").has_field("gf_width_mm"):
		batch.gf_width_mm = width
	batch.insert(ignore_permissions=True)
	return batch.name


def batch_row_fields(item_code: str, batch_no: str | None) -> dict:
	"""Return Stock Entry Detail batch fields when the item is batch-tracked."""
	if not batch_no or not item_uses_batches(item_code):
		return {}
	return {"batch_no": batch_no, "use_serial_batch_fields": 1}


def _batch_dimensions(item_code: str, length_mm: float = 0, width_mm: float = 0) -> tuple[int, int]:
	length = flt(length_mm)
	width = flt(width_mm)
	if not (length and width):
		parsed = _parse_raw_item_code(item_code) or {}
		length = flt(parsed.get("length_mm"))
		width = flt(parsed.get("width_mm"))
	return int(length), int(width)


def _clean_batch_key(value: str | None) -> str:
	key = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
	return key or "ROW"
