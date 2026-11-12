"""Glass Factory Settings validation for demo-safe setup and clear user errors."""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt

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
	("raw_item_group", "Raw Sheet Item Group"),
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


def find_area_uom_name() -> str | None:
	"""Return the site's area UOM, or None. Never raises and never falls back to Nos."""
	for candidate in AREA_UOM_CANDIDATES:
		if frappe.db.exists("UOM", candidate):
			return candidate

	for uom in frappe.get_all("UOM", pluck="name", limit=500):
		if (uom or "").strip().lower() in AREA_UOM_ALIASES:
			return uom
	return None


def require_area_uom() -> str:
	"""Require a real area UOM that allows fractions. Installation must not fall back to Nos."""
	area_uom = find_area_uom_name()
	if not area_uom:
		frappe.throw(
			"Glass Factory requires an area Unit of Measure such as Square Meter. "
			"Complete ERPNext setup before installing this app."
		)
	if cint(frappe.db.get_value("UOM", area_uom, "must_be_whole_number")):
		frappe.throw(
			f"UOM {area_uom} is set to whole numbers only. Glass is sold by area, "
			"so allow fractional quantities on that UOM before installing Glass Factory."
		)
	return area_uom


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
		errors.extend(_validate_item_groups(settings))
		errors.extend(_validate_scrap_item(settings))

	if scope in ("full",):
		errors.extend(_validate_operation_rates(settings))
		errors.extend(_validate_price_lists(settings))
		errors.extend(_validate_processing_cost_capitalization(settings))

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
	company = settings.get("company")
	owners: dict[str, str] = {}
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
			continue
		owners[label] = frappe.db.get_value("Warehouse", warehouse, "company")

	if company:
		for label, owner in owners.items():
			if owner != company:
				errors.append(
					f"{label} belongs to company <b>{owner}</b>, but the Operating Company is "
					f"<b>{company}</b>. Use a warehouse of {company}."
				)
	elif len(set(owners.values())) > 1:
		errors.append(
			"Glass warehouses belong to different companies "
			f"({', '.join(sorted(set(owners.values())))}). This deployment is single-company: "
			"choose one Operating Company and use its warehouses."
		)
	return errors


def get_operating_company() -> str | None:
	"""The one company this deployment posts glass stock for, if it can be determined."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return None
	company = frappe.db.get_single_value(SETTINGS_DOCTYPE, "company")
	if company:
		return company
	raw_warehouse = frappe.db.get_single_value(SETTINGS_DOCTYPE, "raw_warehouse")
	if raw_warehouse:
		return frappe.db.get_value("Warehouse", raw_warehouse, "company")
	return None


def assert_operating_company(company: str | None, *, context: str = "document") -> str:
	"""Reject glass work for a company other than the operating company."""
	operating = get_operating_company()
	if operating and company and company != operating:
		frappe.throw(
			f"{context} belongs to company {company}, but Glass Factory stock is set up for "
			f"{operating}. This deployment is single-company."
		)
	return company or operating or ""


def _non_group_companies() -> list[str]:
	return frappe.get_all("Company", filters={"is_group": 0}, pluck="name", order_by="name asc")


def resolve_setup_company(explicit: str | None = None) -> str:
	"""Intended operating company for setup.

	Order: explicit argument, site config ``glass_factory_company``, the company already saved
	in Glass Factory Settings, the only company, then ERPNext's Global Defaults company. With
	several companies and none of those, setup stops and asks for an explicit choice.
	"""
	companies = _non_group_companies()
	if not companies:
		frappe.throw(
			"Glass Factory requires at least one Company. "
			"Complete the ERPNext Setup Wizard or create a Company before installing this app."
		)

	saved = None
	if frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		saved = frappe.db.get_single_value(SETTINGS_DOCTYPE, "company")

	chosen = explicit or frappe.conf.get("glass_factory_company") or saved
	if saved and chosen != saved:
		# Checked here, before any setup write: one deployment, one operating company.
		frappe.throw(
			f"Glass Factory is already set up for company {saved}. This deployment is single-company, "
			f"so it cannot also be set up for {chosen}."
		)
	if chosen:
		if chosen not in companies:
			frappe.throw(f"Company <b>{chosen}</b> does not exist or is a group company.")
		return chosen

	if len(companies) == 1:
		return companies[0]

	default = frappe.defaults.get_global_default("company")
	if default in companies:
		return default

	frappe.throw(
		"Several companies exist ("
		+ ", ".join(companies)
		+ ") and Glass Factory cannot tell which one operates glass. Choose one explicitly: "
		'bench --site <site> set-config glass_factory_company "<Company>", then install again, '
		"or call glass_factory.install.ensure_glass_factory_setup with company=<Company>."
	)


def _validate_item_groups(settings) -> list[str]:
	"""Validate that all required role-specific item groups are set."""
	errors: list[str] = []
	for fieldname, label in ITEM_GROUP_FIELDS:
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
	seen_enabled: set[tuple] = set()

	for row in settings.get("operation_rates") or []:
		if not row.get("operation"):
			continue

		# Pricing basis must match the canonical basis for the operation
		expected = OPERATION_PRICING_BASIS.get(row.operation)
		if expected and row.pricing_basis != expected:
			errors.append(
				f"Operation <b>{row.operation}</b> must use pricing basis "
				f"<b>{expected}</b> (not <b>{row.pricing_basis}</b>)."
			)

		if not cint(row.enabled):
			continue

		# Selling rate must not be negative
		rate = flt(row.get("rate") or 0)
		if rate < 0:
			errors.append(
				f"Default Selling Rate for <b>{row.operation}</b> / {row.currency} must be >= 0."
			)

		# Cost rate must not be negative. A positive rate was entered on purpose.
		cost_rate = flt(row.get("cost_rate") or 0)
		if cost_rate < 0:
			errors.append(
				f"Default Cost Rate for <b>{row.operation}</b> / {row.currency} must be >= 0."
			)
		elif cost_rate > 0:
			row.cost_rate_set = 1

		# No duplicate enabled rows for the same operation + currency + pricing_basis
		key = (row.operation, row.currency or "", row.pricing_basis or "")
		if key in seen_enabled:
			errors.append(
				f"Duplicate enabled operation rate found for "
				f"<b>{row.operation}</b> / {row.currency} / {row.pricing_basis}."
			)
		else:
			seen_enabled.add(key)

	return errors


def _validate_price_lists(settings) -> list[str]:
	"""Validate that the optional default price lists are of the correct type."""
	errors: list[str] = []

	buying_pl = settings.get("default_buying_price_list")
	if buying_pl:
		if not frappe.db.exists("Price List", buying_pl):
			errors.append(
				f"Default Raw Buying Price List <b>{buying_pl}</b> does not exist. "
				f"Create the Price List or update Glass Factory Settings."
			)
		else:
			is_buying = frappe.db.get_value("Price List", buying_pl, "buying")
			if not cint(is_buying):
				errors.append(
					f"Default Raw Buying Price List <b>{buying_pl}</b> is not marked as a buying price list."
				)

	selling_pl = settings.get("default_selling_price_list")
	if selling_pl:
		if not frappe.db.exists("Price List", selling_pl):
			errors.append(
				f"Default Finished Selling Price List <b>{selling_pl}</b> does not exist. "
				f"Create the Price List or update Glass Factory Settings."
			)
		else:
			is_selling = frappe.db.get_value("Price List", selling_pl, "selling")
			if not cint(is_selling):
				errors.append(
					f"Default Finished Selling Price List <b>{selling_pl}</b> is not marked as a selling price list."
				)

	return errors


VALUATION_ACCOUNT_TYPE = "Expenses Included In Valuation"


def valuation_account_errors(account: str | None, company: str | None) -> list[str]:
	"""Why this account cannot carry processing cost that ERPNext includes in stock value."""
	if not account:
		return [
			"Processing Cost Expense Account is required when processing cost capitalization is enabled. "
			f"{SETTINGS_HELP}"
		]
	if not frappe.db.exists("Account", account):
		return [
			f"Processing Cost Expense Account <b>{account}</b> does not exist. "
			"Create the account or update Glass Factory Settings."
		]

	row = frappe.db.get_value(
		"Account",
		account,
		["disabled", "is_group", "company", "account_type", "account_name", "report_type"],
		as_dict=True,
	) or {}
	errors: list[str] = []
	if cint(row.get("disabled")):
		errors.append(
			f"Processing Cost Expense Account <b>{account}</b> is disabled. Choose an active account."
		)
	owner = row.get("company")
	if not company:
		errors.append(
			"Operating Company is not set, so the processing cost account cannot be checked. "
			f"{SETTINGS_HELP}"
		)
	elif owner and owner != company:
		errors.append(
			f"Processing Cost Expense Account <b>{account}</b> belongs to company <b>{owner}</b>, "
			f"but glass stock is posted for <b>{company}</b>. Use an account of {company}."
		)
	if cint(row.get("is_group")):
		errors.append(
			f"Processing Cost Expense Account <b>{account}</b> is a group account, not a posting account. "
			"Choose a ledger account."
		)

	account_type = row.get("account_type") or ""
	account_name = row.get("account_name") or account
	if account_type != VALUATION_ACCOUNT_TYPE:
		tax_note = ""
		lowered = (account_name or "").lower()
		if account_type == "Tax" or "deferred income tax" in lowered or "tax expense" in lowered:
			tax_note = " Deferred Income Tax Expense and other tax accounts cannot carry stock valuation."
		errors.append(
			f"Processing Cost Expense Account <b>{account}</b> is not suitable for ERPNext valuation costs. "
			f"It must be an active posting account of type <b>{VALUATION_ACCOUNT_TYPE}</b> "
			f"(this account is {account_type or 'untyped'}, {account_name}). "
			"That is the account ERPNext credits when a cost is included in stock value."
			+ tax_note
		)
	elif (row.get("report_type") or "") != "Profit and Loss":
		errors.append(
			f"Processing Cost Expense Account <b>{account}</b> must be a Profit and Loss account."
		)
	return errors


def _validate_processing_cost_capitalization(settings) -> list[str]:
	"""Validate processing cost capitalization settings on save."""
	if not cint(settings.get("capitalize_processing_costs")):
		return []

	company = settings.get("company") or get_operating_company()
	errors = valuation_account_errors(settings.get("processing_cost_expense_account"), company)
	errors.extend(_processing_cost_center_errors(settings, company))
	return errors


def _processing_cost_center_errors(settings, company: str | None) -> list[str]:
	cost_center = settings.get("processing_cost_cost_center")
	if not cost_center:
		return []
	if not frappe.db.exists("Cost Center", cost_center):
		return [
			f"Processing Cost Cost Center <b>{cost_center}</b> does not exist. "
			"Create the cost center or update Glass Factory Settings."
		]
	row = frappe.db.get_value(
		"Cost Center", cost_center, ["disabled", "is_group", "company"], as_dict=True
	) or {}
	errors: list[str] = []
	if cint(row.get("disabled")):
		errors.append(f"Processing Cost Cost Center <b>{cost_center}</b> is disabled.")
	if cint(row.get("is_group")):
		errors.append(
			f"Processing Cost Cost Center <b>{cost_center}</b> is a group. Choose a posting cost center."
		)
	owner = row.get("company")
	if company and owner and owner != company:
		errors.append(
			f"Processing Cost Cost Center <b>{cost_center}</b> belongs to <b>{owner}</b>, "
			f"not the operating company <b>{company}</b>."
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
