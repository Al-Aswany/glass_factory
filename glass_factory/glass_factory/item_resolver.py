"""Deterministic glass Item resolution for the Phase 0 manual MVP."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import frappe
from frappe.utils import cint, flt

PROCESS_ORDER = ("POL", "BEV", "HOL", "SLT", "TMP", "SBL", "LAM")
VALID_ROLES = ("Raw Sheet", "Cut WIP", "Final", "Remnant", "Scrap")
BATCH_TRACKED_ROLES = ("Raw Sheet", "Remnant", "Cut WIP", "Final")
DEFAULT_GLASS_TYPES = ("CLEAR",)
ROLE_WAREHOUSE_FIELDS = {
	"Raw Sheet": "raw_warehouse",
	"Cut WIP": "cut_wip_warehouse",
	"Final": "final_goods_warehouse",
	"Remnant": "remnants_warehouse",
	"Scrap": "scrap_warehouse",
}


@dataclass(frozen=True)
class GlassSpec:
	base_glass_type: str
	thickness_mm: float
	length_mm: float
	width_mm: float
	processing_flags: tuple[str, ...]

	@property
	def area_m2(self) -> float:
		return flt((self.length_mm * self.width_mm) / 1_000_000, 6)

	@property
	def spec_key(self) -> str:
		flags = "-".join(self.processing_flags) if self.processing_flags else "CUT"
		return f"{self.base_glass_type}|{_fmt_num(self.thickness_mm)}|{_fmt_num(self.length_mm)}|{_fmt_num(self.width_mm)}|{flags}"


def resolve_row_items(row) -> dict[str, str | float]:
	"""Resolve/create raw, cut WIP, and final Items for a selling document row."""
	if not cint(row.get("gf_is_glass_item")):
		return {}

	from glass_factory.glass_factory.settings_validation import require_runtime_setup

	require_runtime_setup(scope="items")

	raw_item = row.get("gf_raw_sheet_item") or row.get("item_code")
	if not raw_item:
		frappe.throw(f"Row {row.idx}: Raw Sheet Item is required for glass items.")

	raw_doc = frappe.get_doc("Item", raw_item)
	_validate_role(raw_doc, ("Raw Sheet", "Remnant", ""))
	spec = spec_from_row(row, raw_doc)
	cut_item = ensure_cut_wip_item(raw_doc, spec)
	final_item = ensure_final_item(raw_doc, spec)

	row.gf_raw_sheet_item = raw_doc.name
	row.gf_cut_wip_item = cut_item
	row.gf_final_item = final_item
	row.gf_glass_specification = spec.spec_key
	row.gf_area_m2 = spec.area_m2
	row.gf_processing_flags = "-".join(spec.processing_flags)
	row.item_code = final_item

	return {
		"raw_item": raw_doc.name,
		"cut_wip_item": cut_item,
		"final_item": final_item,
		"area_m2": spec.area_m2,
	}


def spec_from_row(row, raw_doc=None) -> GlassSpec:
	raw_doc = raw_doc or frappe.get_doc("Item", row.get("gf_raw_sheet_item") or row.get("item_code"))
	raw_parsed = _parse_raw_item_code(raw_doc.name)
	base_type = row.get("gf_base_glass_type") or raw_parsed.get("base_glass_type")
	thickness = (
		flt(row.get("gf_thickness_mm"))
		or flt(raw_parsed.get("thickness_mm"))
	)
	length = flt(row.get("gf_length_mm"))
	width = flt(row.get("gf_width_mm"))

	if not base_type:
		frappe.throw(
			f"Row {row.idx}: Base glass type must be encoded in the raw sheet Item code "
			f"(for example GLS-CLEAR-8MM-3210X2250)."
		)
	validate_glass_type(base_type, context=f"Row {row.idx}")
	if thickness <= 0:
		frappe.throw(f"Row {row.idx}: Thickness must be greater than zero.")
	if length <= 0 or width <= 0:
		frappe.throw(f"Row {row.idx}: Length and width must be greater than zero.")

	return GlassSpec(
		base_glass_type=_code_part(base_type),
		thickness_mm=thickness,
		length_mm=length,
		width_mm=width,
		processing_flags=parse_processing_flags(row.get("gf_processing_flags")),
	)


def parse_processing_flags(value) -> tuple[str, ...]:
	"""Normalize process flags into the required fixed abbreviation order."""
	if not value:
		return ()

	tokens: set[str] = set()
	if isinstance(value, str):
		text = value.strip()
		if text.startswith("{") or text.startswith("["):
			try:
				decoded = json.loads(text)
			except Exception:
				decoded = text
			else:
				return parse_processing_flags(decoded)
		for token in re.split(r"[^A-Za-z0-9]+", text):
			if token:
				tokens.add(_process_alias(token))
	elif isinstance(value, dict):
		for key, enabled in value.items():
			if enabled:
				tokens.add(_process_alias(key))
	elif isinstance(value, (list, tuple, set)):
		for item in value:
			if item:
				tokens.add(_process_alias(str(item)))

	return tuple(flag for flag in PROCESS_ORDER if flag in tokens)


def ensure_raw_sheet_item(base_glass_type: str, thickness_mm: float, length_mm: float, width_mm: float) -> str:
	validate_glass_type(base_glass_type, context="Raw sheet Item")
	spec = GlassSpec(_code_part(base_glass_type), thickness_mm, length_mm, width_mm, ())
	item_code = _raw_item_code(spec)
	return _ensure_item(
		item_code=item_code,
		role="Raw Sheet",
		spec=spec,
		item_group=_settings_value("raw_item_group") or _settings_value("default_item_group") or "All Item Groups",
		stock_uom=_settings_value("default_uom") or "Nos",
	)


def ensure_cut_wip_item(raw_doc, spec: GlassSpec) -> str:
	item_code = _cut_wip_item_code(spec)
	return _ensure_item(
		item_code=item_code,
		role="Cut WIP",
		spec=spec,
		item_group=_settings_value("cut_wip_item_group") or _settings_value("default_item_group") or raw_doc.item_group,
		stock_uom=raw_doc.stock_uom or _settings_value("default_uom") or "Nos",
	)


def ensure_final_item(raw_doc, spec: GlassSpec) -> str:
	item_code = _final_item_code(spec)
	return _ensure_item(
		item_code=item_code,
		role="Final",
		spec=spec,
		item_group=_settings_value("final_item_group") or _settings_value("default_item_group") or raw_doc.item_group,
		stock_uom=raw_doc.stock_uom or _settings_value("default_uom") or "Nos",
	)


def ensure_remnant_item(base_item, length_mm: float, width_mm: float) -> str:
	base_doc = frappe.get_doc("Item", base_item)
	base_parsed = _parse_raw_item_code(base_item)
	spec = GlassSpec(
		_code_part(base_parsed.get("base_glass_type") or "GLASS"),
		flt(base_parsed.get("thickness_mm")),
		flt(length_mm),
		flt(width_mm),
		(),
	)
	if spec.thickness_mm <= 0:
		frappe.throw(f"Cannot create remnant Item from {base_item}: missing thickness.")
	item_code = f"GLS-{spec.base_glass_type}-{_fmt_num(spec.thickness_mm)}MM-{_fmt_num(spec.length_mm)}X{_fmt_num(spec.width_mm)}-REM"
	return _ensure_item(
		item_code=item_code,
		role="Remnant",
		spec=spec,
		item_group=_settings_value("remnant_item_group") or _settings_value("default_item_group") or base_doc.item_group,
		stock_uom=base_doc.stock_uom or _settings_value("default_uom") or "Nos",
	)


def get_scrap_item() -> str:
	code = _settings_value("scrap_item") or _settings_value("scrap_item_code") or "Glass Scrap"
	if frappe.db.exists("Item", code):
		return code

	item = frappe.new_doc("Item")
	item.item_code = code
	item.item_name = code
	item.item_group = _settings_value("scrap_item_group") or _settings_value("default_item_group") or "All Item Groups"
	item.stock_uom = "Sq m"
	item.is_stock_item = 1
	item.gf_glass_item_role = "Scrap"
	_ensure_item_default_warehouse(item, "Scrap")
	item.insert(ignore_permissions=True)
	return item.name


def item_role(item_code: str) -> str:
	role = frappe.db.get_value("Item", item_code, "gf_glass_item_role") or ""
	return role or infer_glass_role_from_item_code(item_code)


@frappe.whitelist()
def get_item_glass_meta(item_code: str) -> dict:
	"""Return role and dimensions parsed from a glass Item code."""
	parsed = _parse_raw_item_code(item_code) or {}
	return {
		"parsed": bool(parsed),
		"gf_glass_item_role": item_role(item_code),
		"gf_base_glass_type": parsed.get("base_glass_type", ""),
		"gf_thickness_mm": flt(parsed.get("thickness_mm")),
		"gf_length_mm": flt(parsed.get("length_mm")),
		"gf_width_mm": flt(parsed.get("width_mm")),
	}


def validate_final_item_matches_row(row) -> None:
	if not cint(row.get("gf_is_glass_item")):
		return
	final_item = row.get("gf_final_item")
	if not final_item or not frappe.db.exists("Item", final_item):
		frappe.throw(f"Row {row.idx}: Final glass Item must exist before submission.")
	if row.get("item_code") != final_item:
		frappe.throw(f"Row {row.idx}: Item Code must be the exact final glass Item {final_item}.")
	if item_role(final_item) != "Final":
		frappe.throw(f"Row {row.idx}: Final Item {final_item} must have glass role Final.")

	item_spec = spec_from_item_code(final_item)
	row_spec = spec_from_row(row)
	if item_spec.spec_key != row_spec.spec_key:
		frappe.throw(f"Row {row.idx}: Final Item specification does not match the row.")


def _ensure_item(item_code: str, role: str, spec: GlassSpec, item_group: str, stock_uom: str) -> str:
	if frappe.db.exists("Item", item_code):
		item = frappe.get_doc("Item", item_code)
		_validate_role(item, (role,))
		_update_glass_item_fields(item, role, spec)
		return item.name

	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = item_group or "All Item Groups"
	item.stock_uom = stock_uom or "Nos"
	item.is_stock_item = 1
	item.include_item_in_manufacturing = 0
	item.is_sales_item = 1 if role == "Final" else 0
	item.is_purchase_item = 1 if role in ("Raw Sheet", "Remnant") else 0
	if role in BATCH_TRACKED_ROLES:
		item.has_batch_no = 1
		item.has_serial_no = 0
	_update_glass_item_fields(item, role, spec)
	_ensure_item_default_warehouse(item, role)
	try:
		item.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		if not frappe.db.exists("Item", item_code):
			raise
	return item.name


def _update_glass_item_fields(item, role: str, spec: GlassSpec) -> None:
	changed = False
	if item.get("gf_glass_item_role") != role:
		item.gf_glass_item_role = role
		changed = True
	if role in BATCH_TRACKED_ROLES:
		if not cint(item.get("has_batch_no")):
			item.has_batch_no = 1
			changed = True
		if cint(item.get("has_serial_no")):
			item.has_serial_no = 0
			changed = True
	if _ensure_item_default_warehouse(item, role):
		changed = True
	if changed and not item.is_new():
		item.save(ignore_permissions=True)


def _default_warehouse_for_role(role: str) -> str | None:
	fieldname = ROLE_WAREHOUSE_FIELDS.get(role)
	if not fieldname:
		return None
	return _settings_value(fieldname)


def _ensure_item_default_warehouse(item, role: str) -> bool:
	"""Set Item Default.default_warehouse from Glass Factory Settings for the item role."""
	warehouse = _default_warehouse_for_role(role)
	if not warehouse:
		return False

	company = frappe.db.get_value("Warehouse", warehouse, "company")
	if not company:
		company = frappe.defaults.get_defaults().company
	if not company:
		return False

	for row in item.get("item_defaults") or []:
		if row.company == company:
			if row.default_warehouse:
				return False
			row.default_warehouse = warehouse
			return True

	item.append("item_defaults", {"company": company, "default_warehouse": warehouse})
	return True


def backfill_glass_item_default_warehouse(item_code: str) -> bool:
	"""Populate missing default warehouse on an existing glass Item."""
	if not item_code or not frappe.db.exists("Item", item_code):
		return False

	item = frappe.get_doc("Item", item_code)
	role = item.get("gf_glass_item_role") or infer_glass_role_from_item_code(item_code)
	if not role:
		return False

	if not _ensure_item_default_warehouse(item, role):
		return False

	item.save(ignore_permissions=True)
	return True


def _raw_item_code(spec: GlassSpec) -> str:
	return f"GLS-{spec.base_glass_type}-{_fmt_num(spec.thickness_mm)}MM-{_fmt_num(spec.length_mm)}X{_fmt_num(spec.width_mm)}"


def _cut_wip_item_code(spec: GlassSpec) -> str:
	return f"GLS-{spec.base_glass_type}-{_fmt_num(spec.thickness_mm)}MM-{_fmt_num(spec.length_mm)}X{_fmt_num(spec.width_mm)}-CUT"


def _final_item_code(spec: GlassSpec) -> str:
	base = f"GLS-{spec.base_glass_type}-{_fmt_num(spec.thickness_mm)}MM-{_fmt_num(spec.length_mm)}X{_fmt_num(spec.width_mm)}"
	return f"{base}-{'-'.join(spec.processing_flags)}" if spec.processing_flags else base


def _parse_raw_item_code(item_code: str) -> dict[str, str | float]:
	match = re.match(r"^GLS-([A-Z0-9]+)-([0-9.]+)MM-([0-9.]+)X([0-9.]+)", item_code or "", re.IGNORECASE)
	if not match:
		return {}
	return {
		"base_glass_type": match.group(1).upper(),
		"thickness_mm": flt(match.group(2)),
		"length_mm": flt(match.group(3)),
		"width_mm": flt(match.group(4)),
	}


def processing_flags_from_item_code(item_code: str) -> tuple[str, ...]:
	parsed = _parse_raw_item_code(item_code)
	if not parsed or infer_glass_role_from_item_code(item_code) != "Final":
		return ()

	suffix = re.sub(
		rf"^GLS-{parsed['base_glass_type']}-{_fmt_num(parsed['thickness_mm'])}MM-"
		rf"{_fmt_num(parsed['length_mm'])}X{_fmt_num(parsed['width_mm'])}-?",
		"",
		item_code.upper(),
	)
	return parse_processing_flags(suffix) if suffix else ()


def spec_from_item_code(item_code: str) -> GlassSpec:
	parsed = _parse_raw_item_code(item_code)
	if not parsed:
		frappe.throw(f"Item {item_code}: cannot parse glass specification from item code.")
	validate_glass_type(parsed["base_glass_type"], context=f"Item {item_code}")
	return GlassSpec(
		_code_part(parsed["base_glass_type"]),
		flt(parsed["thickness_mm"]),
		flt(parsed["length_mm"]),
		flt(parsed["width_mm"]),
		processing_flags_from_item_code(item_code),
	)


def infer_glass_role_from_item_code(item_code: str) -> str:
	"""Infer glass Item role from deterministic GLS-* naming."""
	code = (item_code or "").upper()
	if not code.startswith("GLS-"):
		return ""

	scrap_item = _settings_value("scrap_item") or _settings_value("scrap_item_code") or "Glass Scrap"
	if code == scrap_item.upper():
		return "Scrap"
	if code.endswith("-REM"):
		return "Remnant"
	if code.endswith("-CUT"):
		return "Cut WIP"

	base_match = re.match(r"^GLS-([A-Z0-9]+)-([0-9.]+)MM-([0-9.]+)X([0-9.]+)(?:-(.*))?$", code)
	if not base_match:
		return ""
	suffix = base_match.group(5) or ""
	if not suffix:
		return "Raw Sheet"
	if suffix in PROCESS_ORDER or any(flag in suffix.split("-") for flag in PROCESS_ORDER):
		return "Final"
	return ""


def backfill_glass_item_fields(item_code: str) -> bool:
	"""Populate missing glass role on an existing glass Item from its code."""
	if not item_code or not frappe.db.exists("Item", item_code):
		return False

	item = frappe.get_doc("Item", item_code)
	if item.get("gf_glass_item_role"):
		return False

	role = infer_glass_role_from_item_code(item_code)
	if not role:
		return False

	item.gf_glass_item_role = role
	item.save(ignore_permissions=True)
	return True


def _validate_role(item, allowed: tuple[str, ...]) -> None:
	role = item.get("gf_glass_item_role") or infer_glass_role_from_item_code(item.name)
	if role not in allowed:
		frappe.throw(f"Item {item.name} has glass role {role or 'blank'}, expected one of {', '.join(allowed)}.")


@frappe.whitelist()
def get_allowed_glass_types() -> list[str]:
	"""Return setup-controlled glass type codes in display order."""
	raw_value = _settings_value("allowed_glass_types")
	values = []
	for token in re.split(r"[\n,;]+", raw_value or ""):
		code = _code_part(token)
		if code and code not in values:
			values.append(code)
	return values or list(DEFAULT_GLASS_TYPES)


def validate_glass_type(value: str, context: str = "Glass type") -> None:
	code = _code_part(value)
	allowed = get_allowed_glass_types()
	if code in allowed:
		return
	frappe.throw(
		f"{context}: glass type {code or 'blank'} is not allowed. "
		f"Allowed glass types: {', '.join(allowed)}."
	)


def _process_alias(value: str) -> str:
	text = _code_part(value)
	aliases = {
		"POLISH": "POL",
		"POLISHED": "POL",
		"BEVEL": "BEV",
		"BEVELED": "BEV",
		"HOLE": "HOL",
		"HOLES": "HOL",
		"DRILL": "HOL",
		"DRILLED": "HOL",
		"SLOT": "SLT",
		"SLOTS": "SLT",
		"TEMP": "TMP",
		"TEMPER": "TMP",
		"TEMPERED": "TMP",
		"SANDBLAST": "SBL",
		"SANDBLASTED": "SBL",
		"LAMINATE": "LAM",
		"LAMINATED": "LAM",
	}
	return aliases.get(text, text)


def _code_part(value) -> str:
	return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _fmt_num(value: float) -> str:
	value = flt(value)
	return str(int(value)) if value == int(value) else str(value).rstrip("0").rstrip(".")


def _settings_value(fieldname: str):
	if frappe.db.exists("DocType", "Glass Factory Settings"):
		meta = frappe.get_meta("Glass Factory Settings")
		if meta.has_field(fieldname):
			return frappe.db.get_single_value("Glass Factory Settings", fieldname)
	return None
