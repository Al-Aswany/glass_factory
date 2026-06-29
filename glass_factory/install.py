import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from glass_factory.glass_factory.settings_validation import DEMO_ALLOWED_GLASS_TYPES, get_area_uom
from glass_factory.glass_factory.operation_rates import default_operation_rate_rows


GLASS_ROLES = [
	"Glass Sales User",
	"Glass Production Planner",
	"Glass Cutting Operator",
	"Glass Processing Operator",
	"Glass Stock User",
	"Glass Manager",
]


def after_install():
	"""Set up the Phase 0 manual glass MVP foundation."""
	create_phase0_foundation()


def create_phase0_foundation():
	create_roles()
	create_glass_custom_fields()
	seed_standard_permissions()
	create_item_groups()
	abbr = create_warehouses()
	seed_glass_factory_settings(abbr)


def create_roles():
	for role_name in GLASS_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 1
		role.insert(ignore_permissions=True)
	frappe.db.commit()


def seed_standard_permissions():
	permission_matrix = {
		"Quotation": {"Glass Sales User": ["read", "write", "create", "submit", "print", "email"], "Glass Manager": ["read", "write", "create", "submit", "cancel", "amend", "print", "email"]},
		"Sales Order": {"Glass Sales User": ["read", "write", "create", "submit", "print", "email"], "Glass Manager": ["read", "write", "create", "submit", "cancel", "amend", "print", "email"]},
		"Delivery Note": {"Glass Sales User": ["read", "write", "create", "submit", "print", "email"], "Glass Manager": ["read", "write", "create", "submit", "cancel", "amend", "print", "email"]},
		"Sales Invoice": {"Glass Sales User": ["read", "write", "create", "submit", "print", "email"], "Glass Manager": ["read", "write", "create", "submit", "cancel", "amend", "print", "email"]},
		"Stock Entry": {"Glass Stock User": ["read", "write", "create", "submit", "cancel", "amend", "print", "email"], "Glass Manager": ["read", "write", "create", "submit", "cancel", "amend", "print", "email"]},
	}
	for doctype, role_map in permission_matrix.items():
		for role, permissions in role_map.items():
			if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
				frappe.permissions.add_permission(doctype, role, 0)
			for permission in permissions:
				frappe.permissions.update_permission_property(doctype, role, 0, permission, 1)
	frappe.db.commit()


def create_glass_custom_fields():
	hidden_glass_meta = {"hidden": 1, "read_only": 1, "allow_on_submit": 1}
	quotation_item_glass_fields = [
		{"fieldname": "gf_is_glass_item", "label": "Is Glass Item", "fieldtype": "Check", "insert_after": "item_code", **hidden_glass_meta},
		{"fieldname": "gf_glass_specification", "label": "Glass Specification", "fieldtype": "Data", "insert_after": "gf_is_glass_item", **hidden_glass_meta},
		{"fieldname": "gf_from_glass_specification", "label": "From Glass Specification", "fieldtype": "Check", "insert_after": "gf_glass_specification", **hidden_glass_meta},
		{"fieldname": "gf_raw_sheet_item", "label": "Raw Sheet Item", "fieldtype": "Link", "options": "Item", "insert_after": "gf_from_glass_specification", **hidden_glass_meta},
		{"fieldname": "gf_cut_wip_item", "label": "Cut WIP Item", "fieldtype": "Link", "options": "Item", "insert_after": "gf_raw_sheet_item", **hidden_glass_meta},
		{"fieldname": "gf_final_item", "label": "Final Item", "fieldtype": "Link", "options": "Item", "insert_after": "gf_cut_wip_item", **hidden_glass_meta},
		{"fieldname": "gf_length_mm", "label": "Length (mm)", "fieldtype": "Float", "insert_after": "gf_final_item", **hidden_glass_meta},
		{"fieldname": "gf_width_mm", "label": "Width (mm)", "fieldtype": "Float", "insert_after": "gf_length_mm", **hidden_glass_meta},
		{"fieldname": "gf_thickness_mm", "label": "Thickness (mm)", "fieldtype": "Float", "insert_after": "gf_width_mm", **hidden_glass_meta},
		{"fieldname": "gf_processing_flags", "label": "Processing Flags", "fieldtype": "Data", "insert_after": "gf_thickness_mm", **hidden_glass_meta},
		{"fieldname": "gf_area_m2", "label": "Area (m²)", "fieldtype": "Float", "insert_after": "gf_processing_flags", **hidden_glass_meta},
		{"fieldname": "gf_total_area_m2", "label": "Total Area (m²)", "fieldtype": "Float", "insert_after": "gf_area_m2", **hidden_glass_meta},
		{"fieldname": "gf_selling_rate_per_m2", "label": "Selling Rate per m²", "fieldtype": "Currency", "insert_after": "gf_total_area_m2", **hidden_glass_meta},
		{"fieldname": "gf_calculated_rate_per_m2", "label": "Calculated Rate per m²", "fieldtype": "Currency", "insert_after": "gf_selling_rate_per_m2", **hidden_glass_meta},
		{"fieldname": "gf_manual_selling_rate_per_m2", "label": "Manual Selling Rate per m²", "fieldtype": "Currency", "insert_after": "gf_calculated_rate_per_m2", **hidden_glass_meta},
		{"fieldname": "gf_price_override", "label": "Price Override", "fieldtype": "Check", "insert_after": "gf_manual_selling_rate_per_m2", **hidden_glass_meta},
		{"fieldname": "gf_price_difference_per_m2", "label": "Price Difference per m²", "fieldtype": "Currency", "insert_after": "gf_price_override", **hidden_glass_meta},
		{"fieldname": "gf_rate_per_piece", "label": "Spec Rate per Piece", "fieldtype": "Currency", "insert_after": "gf_price_difference_per_m2", **hidden_glass_meta},
		{"fieldname": "gf_source_row_id", "label": "Source Row ID", "fieldtype": "Data", "insert_after": "gf_rate_per_piece", **hidden_glass_meta},
		{"fieldname": "gf_technical_summary", "label": "Technical Summary", "fieldtype": "Small Text", "insert_after": "gf_source_row_id", **hidden_glass_meta},
		{"fieldname": "gf_design_attachment_summary", "label": "Design Attachment Summary", "fieldtype": "Small Text", "insert_after": "gf_technical_summary", **hidden_glass_meta},
		{"fieldname": "gf_transaction_rate_overridden", "label": "Transaction Rate Overridden", "fieldtype": "Check", "insert_after": "gf_design_attachment_summary", **hidden_glass_meta},
	]
	common_selling_fields = [
		{"fieldname": "gf_is_glass_item", "label": "Is Glass Item", "fieldtype": "Check", "insert_after": "item_code", "read_only": 1},
		{"fieldname": "gf_glass_specification", "label": "Glass Specification", "fieldtype": "Data", "insert_after": "gf_is_glass_item", "read_only": 1},
		{"fieldname": "gf_from_glass_specification", "label": "From Glass Specification", "fieldtype": "Check", "insert_after": "gf_glass_specification", "read_only": 1},
		{"fieldname": "gf_raw_sheet_item", "label": "Raw Sheet Item", "fieldtype": "Link", "options": "Item", "insert_after": "gf_from_glass_specification", "read_only": 1},
		{"fieldname": "gf_cut_wip_item", "label": "Cut WIP Item", "fieldtype": "Link", "options": "Item", "read_only": 1, "insert_after": "gf_raw_sheet_item"},
		{"fieldname": "gf_final_item", "label": "Final Item", "fieldtype": "Link", "options": "Item", "read_only": 1, "insert_after": "gf_cut_wip_item"},
		{"fieldname": "gf_length_mm", "label": "Length (mm)", "fieldtype": "Float", "read_only": 1, "insert_after": "gf_final_item"},
		{"fieldname": "gf_width_mm", "label": "Width (mm)", "fieldtype": "Float", "read_only": 1, "insert_after": "gf_length_mm"},
		{"fieldname": "gf_thickness_mm", "label": "Thickness (mm)", "fieldtype": "Float", "read_only": 1, "insert_after": "gf_width_mm"},
		{"fieldname": "gf_processing_flags", "label": "Processing Flags", "fieldtype": "Data", "read_only": 1, "insert_after": "gf_thickness_mm"},
		{"fieldname": "gf_area_m2", "label": "Area (m²)", "fieldtype": "Float", "read_only": 1, "insert_after": "gf_processing_flags"},
		{"fieldname": "gf_total_area_m2", "label": "Total Area (m²)", "fieldtype": "Float", "read_only": 1, "insert_after": "gf_area_m2"},
		{"fieldname": "gf_selling_rate_per_m2", "label": "Selling Rate per m²", "fieldtype": "Currency", "read_only": 1, "insert_after": "gf_total_area_m2"},
		{"fieldname": "gf_calculated_rate_per_m2", "label": "Calculated Rate per m²", "fieldtype": "Currency", "read_only": 1, "insert_after": "gf_selling_rate_per_m2"},
		{"fieldname": "gf_manual_selling_rate_per_m2", "label": "Manual Selling Rate per m²", "fieldtype": "Currency", "read_only": 1, "insert_after": "gf_calculated_rate_per_m2"},
		{"fieldname": "gf_price_override", "label": "Price Override", "fieldtype": "Check", "read_only": 1, "insert_after": "gf_manual_selling_rate_per_m2"},
		{"fieldname": "gf_price_difference_per_m2", "label": "Price Difference per m²", "fieldtype": "Currency", "read_only": 1, "insert_after": "gf_price_override"},
		{"fieldname": "gf_rate_per_piece", "label": "Spec Rate per Piece", "fieldtype": "Currency", "read_only": 1, "insert_after": "gf_price_difference_per_m2"},
		{"fieldname": "gf_source_row_id", "label": "Source Row ID", "fieldtype": "Data", "read_only": 1, "insert_after": "gf_rate_per_piece"},
		{"fieldname": "gf_technical_summary", "label": "Technical Summary", "fieldtype": "Small Text", "read_only": 1, "insert_after": "gf_source_row_id"},
		{"fieldname": "gf_design_attachment_summary", "label": "Design Attachment Summary", "fieldtype": "Small Text", "read_only": 1, "insert_after": "gf_technical_summary"},
		{"fieldname": "gf_transaction_rate_overridden", "label": "Transaction Rate Overridden", "fieldtype": "Check", "read_only": 1, "insert_after": "gf_design_attachment_summary"},
	]

	custom_fields = {
		"Item": [
			{
				"fieldname": "gf_glass_section",
				"fieldtype": "Section Break",
				"label": "Glass",
				"insert_after": "item_group",
				"collapsible": 1,
			},
			{
				"fieldname": "gf_glass_item_role",
				"label": "Glass Item Role",
				"fieldtype": "Select",
				"options": "\nRaw Sheet\nCut WIP\nFinal\nRemnant\nScrap",
				"insert_after": "gf_glass_section",
				"description": "Set for glass stock items. Dimensions are auto-filled from the GLS-* item code.",
			},
			{
				"fieldname": "gf_base_glass_type",
				"label": "Glass Type",
				"fieldtype": "Data",
				"insert_after": "gf_glass_item_role",
				"read_only": 1,
				"description": "Allowed glass types are configured in Glass Factory Settings.",
			},
			{
				"fieldname": "gf_thickness_mm",
				"label": "Thickness (mm)",
				"fieldtype": "Float",
				"insert_after": "gf_base_glass_type",
				"read_only": 1,
			},
			{
				"fieldname": "gf_length_mm",
				"label": "Length (mm)",
				"fieldtype": "Float",
				"insert_after": "gf_thickness_mm",
				"read_only": 1,
			},
			{
				"fieldname": "gf_width_mm",
				"label": "Width (mm)",
				"fieldtype": "Float",
				"insert_after": "gf_length_mm",
				"read_only": 1,
			},
		],
		"Quotation": _glass_piece_parent_fields(),
		"Sales Order": _glass_piece_parent_fields(),
		"Quotation Item": quotation_item_glass_fields,
		"Sales Order Item": common_selling_fields + [
			{"fieldname": "gf_cutting_job", "label": "Cutting Job", "fieldtype": "Link", "options": "Cutting Job", "read_only": 1, "insert_after": "gf_transaction_rate_overridden"},
			{"fieldname": "gf_processing_job", "label": "Glass Processing Job", "fieldtype": "Link", "options": "Glass Processing Job", "read_only": 1, "insert_after": "gf_cutting_job"},
			{"fieldname": "gf_cut_qty", "label": "Cut Qty", "fieldtype": "Float", "read_only": 1, "insert_after": "gf_processing_job"},
			{"fieldname": "gf_processed_qty", "label": "Processed Qty", "fieldtype": "Float", "read_only": 1, "insert_after": "gf_cut_qty"},
			{"fieldname": "gf_delivered_qty", "label": "Glass Delivered Qty", "fieldtype": "Float", "read_only": 1, "insert_after": "gf_processed_qty"},
		],
		"Delivery Note Item": [
			{"fieldname": "gf_is_glass_item", "label": "Is Glass Item", "fieldtype": "Check", "insert_after": "item_code"},
			{"fieldname": "gf_sales_order_item", "label": "Sales Order Item Row", "fieldtype": "Data", "read_only": 1, "insert_after": "gf_is_glass_item"},
			{"fieldname": "gf_glass_specification", "label": "Glass Specification", "fieldtype": "Data", "read_only": 1, "insert_after": "gf_sales_order_item"},
			{"fieldname": "gf_from_glass_specification", "label": "From Glass Specification", "fieldtype": "Check", "read_only": 1, "insert_after": "gf_glass_specification"},
			{"fieldname": "gf_cutting_job", "label": "Cutting Job", "fieldtype": "Link", "options": "Cutting Job", "read_only": 1, "insert_after": "gf_from_glass_specification"},
			{"fieldname": "gf_processing_job", "label": "Glass Processing Job", "fieldtype": "Link", "options": "Glass Processing Job", "read_only": 1, "insert_after": "gf_cutting_job"},
			{"fieldname": "gf_technical_summary", "label": "Technical Summary", "fieldtype": "Small Text", "read_only": 1, "insert_after": "gf_processing_job"},
		],
		"Stock Entry": [
			{"fieldname": "gf_cutting_job", "label": "Cutting Job", "fieldtype": "Link", "options": "Cutting Job", "insert_after": "remarks", "allow_on_submit": 1},
			{"fieldname": "gf_processing_job", "label": "Glass Processing Job", "fieldtype": "Link", "options": "Glass Processing Job", "insert_after": "gf_cutting_job", "allow_on_submit": 1},
			{"fieldname": "gf_glass_stock_flow", "label": "Glass Stock Flow", "fieldtype": "Select", "options": "\nRaw to Cut WIP\nCut WIP to Final\nRemnant/Scrap", "insert_after": "gf_processing_job"},
			{"fieldname": "gf_created_by_glass_factory", "label": "Created by Glass Factory", "fieldtype": "Check", "insert_after": "gf_glass_stock_flow"},
		],
		"Stock Entry Detail": [
			{"fieldname": "gf_cutting_job", "label": "Cutting Job", "fieldtype": "Link", "options": "Cutting Job", "insert_after": "item_code", "read_only": 1},
			{"fieldname": "gf_sales_order", "label": "Sales Order", "fieldtype": "Link", "options": "Sales Order", "insert_after": "gf_cutting_job"},
			{"fieldname": "gf_sales_order_item", "label": "Sales Order Item Row", "fieldtype": "Data", "insert_after": "gf_sales_order"},
			{"fieldname": "gf_glass_specification", "label": "Glass Specification", "fieldtype": "Data", "insert_after": "gf_sales_order_item"},
			{"fieldname": "gf_from_glass_specification", "label": "From Glass Specification", "fieldtype": "Check", "read_only": 1, "insert_after": "gf_glass_specification"},
			{"fieldname": "gf_technical_summary", "label": "Technical Summary", "fieldtype": "Small Text", "read_only": 1, "insert_after": "gf_from_glass_specification"},
			{"fieldname": "gf_source_item_role", "label": "Source Item Role", "fieldtype": "Select", "options": "\nRaw Sheet\nCut WIP\nFinal\nRemnant\nScrap", "insert_after": "gf_technical_summary"},
		],
		"Batch": [
			{"fieldname": "gf_glass_section", "fieldtype": "Section Break", "label": "Glass", "insert_after": "item", "collapsible": 1},
			{"fieldname": "gf_cutting_job", "label": "Source Cutting Job", "fieldtype": "Link", "options": "Cutting Job", "insert_after": "gf_glass_section"},
			{"fieldname": "gf_length_mm", "label": "Length (mm)", "fieldtype": "Float", "insert_after": "gf_cutting_job"},
			{"fieldname": "gf_width_mm", "label": "Width (mm)", "fieldtype": "Float", "insert_after": "gf_length_mm"},
			{"fieldname": "gf_area_m2", "label": "Area (m²)", "fieldtype": "Float", "insert_after": "gf_width_mm", "read_only": 1},
		],
	}

	create_custom_fields(custom_fields, ignore_validate=True)
	frappe.db.commit()


def _glass_piece_parent_fields():
	return [
		{"fieldname": "gf_glass_section", "fieldtype": "Section Break", "label": "Glass Pieces", "insert_after": "items"},
		{
			"fieldname": "glass_pieces",
			"fieldtype": "Table",
			"label": "Glass Pieces",
			"options": "Quotation Glass Piece",
			"insert_after": "gf_glass_section",
		},
	]


def create_item_groups():
	for group_name in ("Glass Raw", "Glass Cut WIP", "Glass Final", "Glass Remnants", "Glass Scrap"):
		if frappe.db.exists("Item Group", group_name):
			continue
		group = frappe.new_doc("Item Group")
		group.item_group_name = group_name
		group.parent_item_group = "All Item Groups"
		group.is_group = 0
		group.insert(ignore_permissions=True)
	frappe.db.commit()


def _get_install_company():
	default_company = frappe.defaults.get_defaults().company
	if default_company and frappe.db.exists("Company", default_company):
		return default_company

	company = frappe.db.get_value("Company", {"is_group": 0}, "name")
	if company:
		return company

	return frappe.db.get_value("Company", {}, "name")


def create_warehouses():
	company = _get_install_company()
	if not company:
		frappe.throw(
			_(
				"Glass Factory requires at least one Company. "
				"Complete the ERPNext Setup Wizard or create a Company before installing this app."
			)
		)

	abbr = frappe.get_value("Company", company, "abbr") or company[:4].upper()
	for wh_name in ("Glass Raw Stock", "Glass Cut WIP", "Glass Final Goods", "Glass Remnants", "Glass Scrap"):
		full_name = f"{wh_name} - {abbr}"
		if frappe.db.exists("Warehouse", full_name):
			continue
		wh = frappe.new_doc("Warehouse")
		wh.warehouse_name = wh_name
		wh.company = company
		wh.insert(ignore_permissions=True)
	frappe.db.commit()
	return abbr


def seed_glass_factory_settings(abbr):
	settings = frappe.get_single("Glass Factory Settings")
	settings.raw_warehouse = settings.raw_warehouse or f"Glass Raw Stock - {abbr}"
	settings.cut_wip_warehouse = settings.cut_wip_warehouse or f"Glass Cut WIP - {abbr}"
	settings.final_goods_warehouse = settings.final_goods_warehouse or f"Glass Final Goods - {abbr}"
	settings.remnants_warehouse = settings.remnants_warehouse or f"Glass Remnants - {abbr}"
	settings.scrap_warehouse = settings.scrap_warehouse or f"Glass Scrap - {abbr}"
	settings.default_uom = settings.default_uom or "Nos"
	settings.default_item_group = settings.default_item_group or "All Item Groups"
	settings.raw_item_group = settings.raw_item_group or "Glass Raw"
	settings.cut_wip_item_group = settings.cut_wip_item_group or "Glass Cut WIP"
	settings.final_item_group = settings.final_item_group or "Glass Final"
	settings.remnant_item_group = settings.remnant_item_group or "Glass Remnants"
	settings.scrap_item_group = settings.scrap_item_group or "Glass Scrap"
	settings.scrap_item = settings.scrap_item or _ensure_scrap_item()
	if hasattr(settings, "allowed_glass_types") and not settings.allowed_glass_types:
		settings.allowed_glass_types = DEMO_ALLOWED_GLASS_TYPES
	settings.min_remnant_area_m2 = settings.min_remnant_area_m2 or 0.1
	settings.min_remnant_side_mm = settings.min_remnant_side_mm or 100
	settings.min_chargeable_area_m2 = settings.min_chargeable_area_m2 or 0.05
	settings.enable_cop = 0
	ensure_default_operation_rates(settings)
	settings.save(ignore_permissions=True)
	frappe.db.commit()


def ensure_default_operation_rates(settings):
	"""Seed flexible operation rates when the table is empty."""
	if settings.get("operation_rates"):
		return

	currency = _get_default_company_currency()
	for row in default_operation_rate_rows(currency):
		settings.append("operation_rates", row)


def _get_default_company_currency() -> str:
	company = frappe.defaults.get_global_default("company")
	if company:
		return frappe.get_cached_value("Company", company, "default_currency") or "USD"
	return frappe.db.get_value("Company", {"is_group": 0}, "default_currency") or "USD"


def _ensure_scrap_item():
	item_code = "Glass Scrap"
	if frappe.db.exists("Item", item_code):
		return item_code
	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = item_code
	item.item_group = "Glass Scrap" if frappe.db.exists("Item Group", "Glass Scrap") else "All Item Groups"
	item.stock_uom = get_area_uom()
	item.is_stock_item = 1
	item.is_sales_item = 0
	item.is_purchase_item = 0
	item.gf_glass_item_role = "Scrap"
	item.insert(ignore_permissions=True)
	return item_code
