"""Batch helpers for glass stock tracking."""

from __future__ import annotations

import frappe
from frappe.utils import flt


def item_uses_batches(item_code: str) -> bool:
	return bool(frappe.db.get_value("Item", item_code, "has_batch_no"))


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


def batch_row_fields(item_code: str, batch_no: str | None) -> dict:
	"""Return Stock Entry Detail batch fields when the item is batch-tracked."""
	if not batch_no or not item_uses_batches(item_code):
		return {}
	return {"batch_no": batch_no, "use_serial_batch_fields": 1}
