"""GlassOptimizer Phase A file-based export/import for Cutting Job."""

from __future__ import annotations

import json

import frappe
from frappe.utils import flt, now_datetime

from glass_factory.glass_factory.item_resolver import _parse_raw_item_code, parse_processing_flags

PROCESS_FLAG_LABELS = {
	"POL": "POLISHED",
	"BEV": "BEVEL",
	"HOL": "HOLES",
	"SLT": "SLOTS",
	"TMP": "TEMPERED",
	"SBL": "SANDBLAST",
	"LAM": "LAMINATED",
}

EXPORT_TOP_LEVEL_KEYS = ("cutting_job", "material", "kerf_mm", "stock_sheets", "pieces")
STOCK_SHEET_KEYS = ("sheet_id", "length_mm", "width_mm", "qty")
PIECE_KEYS = ("piece_id", "item_code", "length_mm", "width_mm", "qty", "process")


def _get_kerf_mm() -> float:
	default = 3.0
	if not frappe.db.exists("DocType", "Glass Factory Settings"):
		return default

	settings = frappe.get_single("Glass Factory Settings")
	field_default = flt(settings.meta.get_field("kerf_mm").default or default)
	kerf = settings.get("kerf_mm")
	if kerf in (None, ""):
		return field_default

	kerf = flt(kerf)
	if kerf == 0 and field_default:
		return field_default
	return kerf


def _derive_material(raw_sheet_item: str) -> str:
	parsed = _parse_raw_item_code(raw_sheet_item)
	base_type = parsed.get("base_glass_type")
	thickness = flt(parsed.get("thickness_mm"))
	if not base_type or thickness <= 0:
		frappe.throw(
			f"Cannot derive material from raw sheet item {raw_sheet_item}. "
			"Expected format like GLS-CLEAR-8MM-3210X2250."
		)
	thickness_label = str(int(thickness)) if thickness == int(thickness) else str(thickness).rstrip("0").rstrip(".")
	return f"{base_type}-{thickness_label}MM"


def _process_label(processing_flags: str | None) -> str:
	flags = parse_processing_flags(processing_flags)
	if not flags:
		return ""
	labels = [PROCESS_FLAG_LABELS.get(flag, flag) for flag in flags]
	return "-".join(labels)


def build_export_payload(job) -> dict:
	"""Build job.json payload from a Cutting Job document."""
	payload = {
		"cutting_job": job.name,
		"material": _material_from_pieces(job),
		"kerf_mm": _get_kerf_mm(),
		"stock_sheets": _build_stock_sheets(job),
		"pieces": _build_pieces(job),
	}
	validate_export_payload(payload)
	return payload


def _material_from_pieces(job) -> str:
	pieces = [row for row in (job.get("pieces") or []) if row.get("raw_sheet_item")]
	if not pieces:
		frappe.throw("Cutting Job must have at least one piece with a raw sheet item to derive material.")

	materials: set[str] = set()
	for piece in pieces:
		materials.add(_derive_material(piece.raw_sheet_item))

	if len(materials) > 1:
		frappe.throw(
			"All pieces must share the same glass material (base type and thickness) for optimization export."
		)
	return materials.pop()


def _build_stock_sheets(job) -> list[dict]:
	sheets = []
	for idx, row in enumerate(job.get("source_sheets") or [], start=1):
		sheets.append({
			"sheet_id": f"SHEET-{idx:03d}",
			"length_mm": flt(row.get("length_mm")),
			"width_mm": flt(row.get("width_mm")),
			"qty": flt(row.get("qty_consumed") or 1),
		})
	return sheets


def _build_pieces(job) -> list[dict]:
	pieces = []
	for idx, row in enumerate(job.get("pieces") or [], start=1):
		qty = flt(row.get("qty_assigned") or row.get("qty_required"))
		pieces.append({
			"piece_id": f"P{idx}",
			"item_code": row.get("final_item") or "",
			"length_mm": flt(row.get("length_mm")),
			"width_mm": flt(row.get("width_mm")),
			"qty": qty,
			"process": _process_label(row.get("processing_flags")),
		})
	return pieces


def validate_export_payload(payload: dict) -> None:
	if not payload.get("cutting_job"):
		frappe.throw("Export payload requires cutting_job.")

	if not payload.get("material"):
		frappe.throw("Export payload requires material.")

	kerf_mm = flt(payload.get("kerf_mm"))
	if kerf_mm < 0:
		frappe.throw("kerf_mm must be greater than or equal to zero.")

	stock_sheets = payload.get("stock_sheets") or []
	if not stock_sheets:
		frappe.throw("Cutting Job must have at least one source sheet for optimization export.")

	for idx, row in enumerate(stock_sheets, start=1):
		length_mm = flt(row.get("length_mm"))
		width_mm = flt(row.get("width_mm"))
		qty = flt(row.get("qty"))
		if length_mm <= 0 or width_mm <= 0:
			frappe.throw(f"Source sheet row {idx}: length_mm and width_mm must be greater than zero.")
		if qty <= 0:
			frappe.throw(f"Source sheet row {idx}: qty must be greater than zero.")

	pieces = payload.get("pieces") or []
	if not pieces:
		frappe.throw("Cutting Job must have at least one piece for optimization export.")

	for idx, row in enumerate(pieces, start=1):
		if not row.get("item_code"):
			frappe.throw(f"Piece row {idx}: item_code is required.")
		length_mm = flt(row.get("length_mm"))
		width_mm = flt(row.get("width_mm"))
		qty = flt(row.get("qty"))
		if length_mm <= 0 or width_mm <= 0:
			frappe.throw(f"Piece row {idx}: length_mm and width_mm must be greater than zero.")
		if qty <= 0:
			frappe.throw(f"Piece row {idx}: qty must be greater than zero.")


def validate_import_payload(result: dict, cutting_job_name: str) -> None:
	if result.get("cutting_job") != cutting_job_name:
		frappe.throw(
			f"Result cutting_job {result.get('cutting_job')!r} does not match Cutting Job {cutting_job_name!r}."
		)

	if (result.get("status") or "").lower() != "completed":
		frappe.throw("Optimization result status must be 'completed' for import.")

	used_sheets = result.get("used_sheets") or []
	if not used_sheets:
		frappe.throw("Optimization result must include at least one used sheet.")

	used_sheet_ids: set[str] = set()
	for idx, row in enumerate(used_sheets, start=1):
		sheet_id = row.get("sheet_id")
		if not sheet_id:
			frappe.throw(f"Used sheet row {idx}: sheet_id is required.")
		used_qty = flt(row.get("used_qty"))
		if used_qty <= 0:
			frappe.throw(f"Used sheet row {idx}: used_qty must be greater than zero.")
		used_sheet_ids.add(sheet_id)

	placed_pieces = result.get("placed_pieces") or []
	if not placed_pieces:
		frappe.throw("Optimization result must include at least one placed piece.")

	for idx, row in enumerate(placed_pieces, start=1):
		if not row.get("piece_id"):
			frappe.throw(f"Placed piece row {idx}: piece_id is required.")
		if not row.get("item_code"):
			frappe.throw(f"Placed piece row {idx}: item_code is required.")
		length_mm = flt(row.get("length_mm"))
		width_mm = flt(row.get("width_mm"))
		qty = flt(row.get("qty"))
		if length_mm <= 0 or width_mm <= 0:
			frappe.throw(f"Placed piece row {idx}: length_mm and width_mm must be greater than zero.")
		if qty <= 0:
			frappe.throw(f"Placed piece row {idx}: qty must be greater than zero.")
		source_sheet_id = row.get("source_sheet_id")
		if not source_sheet_id:
			frappe.throw(f"Placed piece row {idx}: source_sheet_id is required.")
		if source_sheet_id not in used_sheet_ids:
			frappe.throw(
				f"Placed piece row {idx}: source_sheet_id {source_sheet_id!r} is not in used_sheets."
			)

	for idx, row in enumerate(result.get("remnants") or [], start=1):
		source_sheet_id = row.get("source_sheet_id")
		if not source_sheet_id:
			frappe.throw(f"Remnant row {idx}: source_sheet_id is required.")
		if source_sheet_id not in used_sheet_ids:
			frappe.throw(
				f"Remnant row {idx}: source_sheet_id {source_sheet_id!r} is not in used_sheets."
			)
		length_mm = flt(row.get("length_mm"))
		width_mm = flt(row.get("width_mm"))
		qty = flt(row.get("qty"))
		if length_mm <= 0 or width_mm <= 0:
			frappe.throw(f"Remnant row {idx}: length_mm and width_mm must be greater than zero.")
		if qty <= 0:
			frappe.throw(f"Remnant row {idx}: qty must be greater than zero.")

	if flt(result.get("waste_area_m2")) < 0:
		frappe.throw("waste_area_m2 must be greater than or equal to zero.")


def apply_import_result(job, result: dict, file_url: str | None = None) -> None:
	"""Populate optimization child tables on a mock Cutting Job object (used by tests)."""
	job.set("optimization_used_sheets", [])
	job.set("optimization_placed_pieces", [])
	job.set("optimization_remnants", [])

	for row in result.get("used_sheets") or []:
		job.append("optimization_used_sheets", {
			"sheet_id": row.get("sheet_id"),
			"used_qty": flt(row.get("used_qty")),
		})

	for row in result.get("placed_pieces") or []:
		job.append("optimization_placed_pieces", {
			"piece_id": row.get("piece_id"),
			"item_code": row.get("item_code"),
			"length_mm": flt(row.get("length_mm")),
			"width_mm": flt(row.get("width_mm")),
			"qty": flt(row.get("qty")),
			"source_sheet_id": row.get("source_sheet_id"),
		})

	for row in result.get("remnants") or []:
		job.append("optimization_remnants", {
			"source_sheet_id": row.get("source_sheet_id"),
			"length_mm": flt(row.get("length_mm")),
			"width_mm": flt(row.get("width_mm")),
			"qty": flt(row.get("qty")),
		})

	job.optimization_status = "Imported"
	job.optimization_message = result.get("message") or ""
	job.optimization_waste_area_m2 = flt(result.get("waste_area_m2"))
	job.optimization_imported_at = now_datetime()
	if file_url:
		job.optimization_result_file = file_url


def _persist_import_to_db(cutting_job_name: str, result: dict, file_url: str | None = None) -> None:
	"""Write import results directly to the database, bypassing all ORM submit restrictions."""
	now = now_datetime()
	user = frappe.session.user or "Administrator"

	# Wipe existing child rows
	frappe.db.delete("Cutting Job Optimization Used Sheet", {"parent": cutting_job_name})
	frappe.db.delete("Cutting Job Optimization Placed Piece", {"parent": cutting_job_name})
	frappe.db.delete("Cutting Job Optimization Remnant", {"parent": cutting_job_name})

	def _row_name():
		return frappe.generate_hash(length=10)

	for idx, row in enumerate(result.get("used_sheets") or [], start=1):
		frappe.db.sql(
			"""INSERT INTO `tabCutting Job Optimization Used Sheet`
			   (name, parent, parentfield, parenttype, idx,
			    sheet_id, used_qty,
			    owner, modified_by, creation, modified, docstatus)
			   VALUES (%s, %s, 'optimization_used_sheets', 'Cutting Job', %s,
			           %s, %s,
			           %s, %s, %s, %s, 0)""",
			(_row_name(), cutting_job_name, idx,
			 row.get("sheet_id"), flt(row.get("used_qty")),
			 user, user, now, now),
		)

	for idx, row in enumerate(result.get("placed_pieces") or [], start=1):
		frappe.db.sql(
			"""INSERT INTO `tabCutting Job Optimization Placed Piece`
			   (name, parent, parentfield, parenttype, idx,
			    piece_id, item_code, length_mm, width_mm, qty, source_sheet_id,
			    owner, modified_by, creation, modified, docstatus)
			   VALUES (%s, %s, 'optimization_placed_pieces', 'Cutting Job', %s,
			           %s, %s, %s, %s, %s, %s,
			           %s, %s, %s, %s, 0)""",
			(_row_name(), cutting_job_name, idx,
			 row.get("piece_id"), row.get("item_code"),
			 flt(row.get("length_mm")), flt(row.get("width_mm")),
			 flt(row.get("qty")), row.get("source_sheet_id"),
			 user, user, now, now),
		)

	for idx, row in enumerate(result.get("remnants") or [], start=1):
		frappe.db.sql(
			"""INSERT INTO `tabCutting Job Optimization Remnant`
			   (name, parent, parentfield, parenttype, idx,
			    source_sheet_id, length_mm, width_mm, qty,
			    owner, modified_by, creation, modified, docstatus)
			   VALUES (%s, %s, 'optimization_remnants', 'Cutting Job', %s,
			           %s, %s, %s, %s,
			           %s, %s, %s, %s, 0)""",
			(_row_name(), cutting_job_name, idx,
			 row.get("source_sheet_id"),
			 flt(row.get("length_mm")), flt(row.get("width_mm")), flt(row.get("qty")),
			 user, user, now, now),
		)

	# Update parent scalar fields directly — works for draft AND submitted docs
	parent_update = {
		"optimization_status": "Imported",
		"optimization_message": result.get("message") or "",
		"optimization_waste_area_m2": flt(result.get("waste_area_m2")),
		"optimization_imported_at": now,
	}
	if file_url:
		parent_update["optimization_result_file"] = file_url

	frappe.db.set_value("Cutting Job", cutting_job_name, parent_update, update_modified=True)


def _save_result_file(cutting_job_name: str, result: dict, json_text: str) -> str:
	"""Attach the result JSON as a file on the Cutting Job and return its file_url."""
	import json as _json
	# Pretty-print so the stored file is readable
	try:
		content = _json.dumps(result, indent=2)
	except Exception:
		content = json_text  # fallback to raw text if result is not serialisable
	file_name = f"{cutting_job_name}-optimization-result.json"
	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": file_name,
		"attached_to_doctype": "Cutting Job",
		"attached_to_name": cutting_job_name,
		"content": content,
		"is_private": 0,
	})
	file_doc.save(ignore_permissions=True)
	return file_doc.file_url


def _load_result_json(*, file_url: str | None = None, json_text: str | None = None) -> dict:
	if file_url:
		file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
		if not file_name:
			frappe.throw(f"File not found for URL {file_url}.")
		file_doc = frappe.get_doc("File", file_name)
		content = file_doc.get_content()
		if isinstance(content, bytes):
			content = content.decode("utf-8")
		return json.loads(content)

	if json_text:
		if isinstance(json_text, dict):
			return json_text
		return json.loads(json_text)

	frappe.throw("Provide either file_url or json_text for optimization result import.")


def _create_export_file(cutting_job_name: str, content: str) -> str:
	file_name = f"{cutting_job_name}-optimization-job.json"
	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": file_name,
		"attached_to_doctype": "Cutting Job",
		"attached_to_name": cutting_job_name,
		"content": content,
		"is_private": 0,
	})
	file_doc.save(ignore_permissions=True)
	return file_doc.file_url


def _save_optimization_job(job) -> None:
	"""Save non-import optimization fields (export timestamps, status) via ORM."""
	if job.docstatus == 1:
		job.flags.ignore_validate_update_after_submit = True
	job.save(ignore_permissions=True)


def _mark_import_failed(cutting_job_name: str, message: str) -> None:
	"""Record a failed import directly in the DB so it always persists."""
	frappe.db.set_value(
		"Cutting Job",
		cutting_job_name,
		{"optimization_status": "Failed", "optimization_message": message},
		update_modified=True,
	)


@frappe.whitelist()
def export_optimization_job(cutting_job_name: str) -> dict:
	"""Export Cutting Job as GlassOptimizer job.json and attach to the document."""
	job = frappe.get_doc("Cutting Job", cutting_job_name)
	payload = build_export_payload(job)
	content = json.dumps(payload, indent=2)
	file_url = _create_export_file(job.name, content)

	job.optimization_status = "Exported"
	job.optimization_message = "Optimization job exported."
	job.optimization_job_file = file_url
	job.optimization_exported_at = now_datetime()
	_save_optimization_job(job)

	return {
		"file_url": file_url,
		"message": "Optimization job exported.",
	}


@frappe.whitelist()
def import_optimization_result(
	cutting_job_name: str,
	file_url: str | None = None,
	json_text: str | None = None,
) -> dict:
	"""Import GlassOptimizer result.json into Cutting Job optimization tables."""
	try:
		result = _load_result_json(file_url=file_url, json_text=json_text)
		validate_import_payload(result, cutting_job_name)

		# When content is sent directly, save it as an attachment so it's visible
		if json_text and not file_url:
			file_url = _save_result_file(cutting_job_name, result, json_text)

		_persist_import_to_db(cutting_job_name, result, file_url=file_url)
		frappe.db.commit()
	except frappe.ValidationError as exc:
		_mark_import_failed(cutting_job_name, str(exc))
		frappe.db.commit()
		raise
	except Exception:
		frappe.log_error(frappe.get_traceback(), "GlassOptimizer Import Error")
		raise

	return {"message": "Optimization result imported."}


@frappe.whitelist()
def get_imported_optimization_result(cutting_job_name: str) -> dict:
	"""Return normalized imported optimization result from Cutting Job child tables."""
	job = frappe.get_doc("Cutting Job", cutting_job_name)
	return {
		"cutting_job": job.name,
		"status": job.get("optimization_status") or "Not Exported",
		"message": job.get("optimization_message") or "",
		"used_sheets": [
			{"sheet_id": row.sheet_id, "used_qty": flt(row.used_qty)}
			for row in (job.get("optimization_used_sheets") or [])
		],
		"placed_pieces": [
			{
				"piece_id": row.piece_id,
				"item_code": row.item_code,
				"length_mm": flt(row.length_mm),
				"width_mm": flt(row.width_mm),
				"qty": flt(row.qty),
				"source_sheet_id": row.source_sheet_id,
			}
			for row in (job.get("optimization_placed_pieces") or [])
		],
		"remnants": [
			{
				"source_sheet_id": row.source_sheet_id,
				"length_mm": flt(row.length_mm),
				"width_mm": flt(row.width_mm),
				"qty": flt(row.qty),
			}
			for row in (job.get("optimization_remnants") or [])
		],
		"waste_area_m2": flt(job.get("optimization_waste_area_m2")),
	}
