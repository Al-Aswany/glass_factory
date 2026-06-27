"""Glass Factory Settings validation for demo-safe setup and clear user errors."""

from __future__ import annotations

import frappe

from glass_factory.glass_factory.operation_rates import OPERATION_PRICING_BASIS

SETTINGS_DOCTYPE = "Glass Factory Settings"
SETTINGS_ROUTE = "Form/Glass Factory Settings/Glass Factory Settings"
SETTINGS_HELP = (
	f"Open <a href='/{SETTINGS_ROUTE}'>Glass Factory Settings</a> "
	"(Setup &gt; Glass Factory) and complete the required fields."
)

DEMO_ALLOWED_GLASS_TYPES = "CLEAR\nBRONZE\nTINTED"

WAREHOUSE_FIELDS = (
	("raw_warehouse", "Raw Warehouse"),
	("cut_wip_warehouse", "Cut WIP Warehouse"),
	("final_goods_warehouse", "Final Goods Warehouse"),
	("remnants_warehouse", "Remnants Warehouse"),
	("scrap_warehouse", "Scrap Warehouse"),
)

ITEM_GROUP_FIELDS = (
	("raw_item_group", "Raw Item Group"),
	("cut_wip_item_group", "Cut WIP Item Group"),
	("final_item_group", "Final Item Group"),
	("remnant_item_group", "Remnant Item Group"),
	("scrap_item_group", "Scrap Item Group"),
)

AREA_UOM_CANDIDATES = ("Square Meter", "Sq m", "Sqm")
AREA_UOM_ALIASES = frozenset({"sq m", "sqm", "square meter", "m2", "m²"})


def get_area_uom() -> str:
	"""Return an existing area UOM for glass scrap and sheet items."""
	for candidate in AREA_UOM_CANDIDATES:
		if frappe.db.exists("UOM", candidate):
			return candidate

	for uom in frappe.get_all("UOM", pluck="name", limit=500):
		if (uom or "").strip().lower() in AREA_UOM_ALIASES:
			return uom

	for fallback in (
		frappe.db.get_single_value(SETTINGS_DOCTYPE, "default_uom"),
		"Nos",
	):
		if fallback and frappe.db.exists("UOM", fallback):
			return fallback

	frappe.throw(
		"No suitable area Unit of Measure found. "
		"Complete ERPNext setup or create a Square Meter UOM before installing Glass Factory."
	)


def throw_missing_settings() -> None:
	frappe.throw(f"Glass Factory is not configured. {SETTINGS_HELP}")


def validate_settings_document(settings) -> None:
	"""Validate Glass Factory Settings on save."""
	errors = _collect_setup_errors(settings, scope="full")
	if errors:
		frappe.throw("<br>".join(errors), title="Glass Factory Settings")


def require_runtime_setup(scope: str = "items") -> None:
	"""Validate setup before glass item resolution or stock posting."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		throw_missing_settings()

	settings = frappe.get_single(SETTINGS_DOCTYPE)
	errors = _collect_setup_errors(settings, scope=scope)
	if errors:
		frappe.throw("<br>".join(errors), title="Glass Factory Setup Required")


def get_validated_stock_settings() -> frappe._dict:
	"""Return warehouse settings after validating stock posting prerequisites."""
	require_runtime_setup(scope="stock")
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	return frappe._dict({fieldname: settings.get(fieldname) for fieldname, _ in WAREHOUSE_FIELDS})


def get_raw_warehouse() -> str:
	settings = get_validated_stock_settings()
	return settings.raw_warehouse


def get_default_selling_warehouse() -> str | None:
	"""Default delivery warehouse for Sales Order glass item rows."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return None
	return frappe.db.get_single_value(SETTINGS_DOCTYPE, "final_goods_warehouse") or None


def _collect_setup_errors(settings, scope: str = "items") -> list[str]:
	errors: list[str] = []

	if scope in ("items", "full", "stock"):
		errors.extend(_validate_allowed_glass_types(settings))
		errors.extend(_validate_uom(settings))
		errors.extend(_validate_item_groups(settings, scope))
		errors.extend(_validate_scrap_item(settings))

	if scope in ("full",):
		errors.extend(_validate_operation_rates(settings))

	if scope in ("stock", "full"):
		errors.extend(_validate_warehouses(settings))

	return errors


def _validate_allowed_glass_types(settings) -> list[str]:
	raw_value = (settings.get("allowed_glass_types") or "").strip()
	if not raw_value:
		return [
			f"Allowed Glass Types is required. {SETTINGS_HELP}"
		]

	types = []
	for token in raw_value.replace(",", "\n").replace(";", "\n").split("\n"):
		code = token.strip().upper()
		if code and code not in types:
			types.append(code)
	if not types:
		return [f"Allowed Glass Types must list at least one glass type code. {SETTINGS_HELP}"]
	return []


def _validate_warehouses(settings) -> list[str]:
	errors: list[str] = []
	for fieldname, label in WAREHOUSE_FIELDS:
		warehouse = settings.get(fieldname)
		if not warehouse:
			errors.append(f"{label} is not set in Glass Factory Settings. {SETTINGS_HELP}")
			continue
		if not frappe.db.exists("Warehouse", warehouse):
			errors.append(
				f"{label} <b>{warehouse}</b> does not exist. "
				f"Create the warehouse or update Glass Factory Settings."
			)
	return errors


def _validate_item_groups(settings, scope: str) -> list[str]:
	errors: list[str] = []
	fields = ITEM_GROUP_FIELDS
	if scope == "items":
		fields = (
			("default_item_group", "Default Item Group"),
			*ITEM_GROUP_FIELDS,
		)

	for fieldname, label in fields:
		group = settings.get(fieldname)
		if not group:
			errors.append(f"{label} is not set in Glass Factory Settings. {SETTINGS_HELP}")
			continue
		if not frappe.db.exists("Item Group", group):
			errors.append(
				f"{label} <b>{group}</b> does not exist. "
				f"Create the Item Group or update Glass Factory Settings."
			)
	return errors


def _validate_uom(settings) -> list[str]:
	uom = settings.get("default_uom")
	if not uom:
		return [f"Default UOM is not set in Glass Factory Settings. {SETTINGS_HELP}"]
	if not frappe.db.exists("UOM", uom):
		return [
			f"Default UOM <b>{uom}</b> does not exist. "
			f"Create the UOM or choose another value in Glass Factory Settings."
		]
	return []


def _validate_operation_rates(settings) -> list[str]:
	errors: list[str] = []
	for row in settings.get("operation_rates") or []:
		if not row.get("operation"):
			continue
		expected = OPERATION_PRICING_BASIS.get(row.operation)
		if expected and row.pricing_basis != expected:
			errors.append(
				f"Operation <b>{row.operation}</b> must use pricing basis "
				f"<b>{expected}</b> (not <b>{row.pricing_basis}</b>)."
			)
	return errors


def _validate_scrap_item(settings) -> list[str]:
	scrap_item = settings.get("scrap_item")
	if not scrap_item:
		return [f"Scrap Item is not set in Glass Factory Settings. {SETTINGS_HELP}"]
	if not frappe.db.exists("Item", scrap_item):
		return [
			f"Scrap Item <b>{scrap_item}</b> does not exist. "
			f"Create the Item or update Glass Factory Settings."
		]
	return []
