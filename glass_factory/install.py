import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
	"""Set up custom fields, warehouses, and initial settings on app install."""
	create_glass_custom_fields()
	abbr = create_warehouses()
	seed_glass_cutting_settings(abbr)


def create_glass_custom_fields():
	"""Create all custom fields for the glass factory workflow."""
	custom_fields = {
		"Item": [
			{
				"fieldname": "glass_type",
				"label": "Glass Type",
				"fieldtype": "Select",
				"options": "\nFloat\nTempered\nLaminated\nLow-E",
				"insert_after": "item_group",
			},
			{
				"fieldname": "thickness_mm",
				"label": "Thickness (mm)",
				"fieldtype": "Float",
				"insert_after": "glass_type",
			},
			{
				"fieldname": "color_tint",
				"label": "Color Tint",
				"fieldtype": "Data",
				"insert_after": "thickness_mm",
			},
			{
				"fieldname": "coating",
				"label": "Coating",
				"fieldtype": "Data",
				"insert_after": "color_tint",
			},
		],
		"Serial No": [
			{
				"fieldname": "length_mm",
				"label": "Length (mm)",
				"fieldtype": "Int",
				"insert_after": "serial_no",
			},
			{
				"fieldname": "width_mm",
				"label": "Width (mm)",
				"fieldtype": "Int",
				"insert_after": "length_mm",
			},
			{
				"fieldname": "area_m2",
				"label": "Area (m²)",
				"fieldtype": "Float",
				"insert_after": "width_mm",
				"read_only": 1,
			},
			{
				"fieldname": "cutting_job",
				"label": "Cutting Job",
				"fieldtype": "Link",
				"options": "Cutting Job",
				"insert_after": "area_m2",
			},
			{
				"fieldname": "source_purchase_receipt",
				"label": "Source Purchase Receipt",
				"fieldtype": "Link",
				"options": "Purchase Receipt",
				"insert_after": "cutting_job",
			},
		],
		"Quotation": [
			{
				"fieldname": "cut_pieces",
				"label": "Cut Pieces",
				"fieldtype": "Table",
				"options": "Glass Cut Piece",
				"insert_after": "items",
			},
		],
		"Sales Order": [
			{
				"fieldname": "cut_pieces",
				"label": "Cut Pieces",
				"fieldtype": "Table",
				"options": "Glass Cut Piece",
				"insert_after": "items",
			},
		],
		"Stock Entry": [
			{
				"fieldname": "cutting_job",
				"label": "Cutting Job",
				"fieldtype": "Link",
				"options": "Cutting Job",
				"insert_after": "remarks",
			},
		],
	}

	create_custom_fields(custom_fields, ignore_validate=True)
	frappe.db.commit()


def create_warehouses():
	"""
	Create glass factory warehouses if missing.
	Returns the company abbreviation used in warehouse names.
	"""
	company = frappe.get_value("Company", {"is_group": 0}, "name") or "Default Company"
	abbr = frappe.get_value("Company", company, "abbr") or company[:4].upper()

	warehouses = [
		"Glass Raw Stock",
		"Glass Cut Pieces",
		"Glass Remnants",
		"Glass Scrap",
	]

	for wh_name in warehouses:
		full_name = f"{wh_name} - {abbr}"
		if not frappe.db.exists("Warehouse", full_name):
			wh = frappe.new_doc("Warehouse")
			wh.warehouse_name = wh_name
			wh.company = company
			wh.insert(ignore_permissions=True)
			frappe.db.commit()

	return abbr


def seed_glass_cutting_settings(abbr):
	"""
	Populate Glass Cutting Settings with default values and the newly created
	warehouse links so the very first Stock Entry can find them.
	"""
	settings = frappe.get_doc("Glass Cutting Settings")
	settings.raw_warehouse = f"Glass Raw Stock - {abbr}"
	settings.cut_pieces_warehouse = f"Glass Cut Pieces - {abbr}"
	settings.remnants_warehouse = f"Glass Remnants - {abbr}"
	settings.scrap_warehouse = f"Glass Scrap - {abbr}"

	# Sensible defaults — operator can override in the UI
	if not settings.min_remnant_area_m2:
		settings.min_remnant_area_m2 = 0.1
	if not settings.min_remnant_side_mm:
		settings.min_remnant_side_mm = 100
	if not settings.min_chargeable_area_m2:
		settings.min_chargeable_area_m2 = 0.05

	# Ensure a scrap item exists and is linked
	scrap_item_code = "Glass Scrap"
	if not frappe.db.exists("Item", scrap_item_code):
		_create_scrap_item(scrap_item_code)
	settings.scrap_item_code = scrap_item_code

	settings.save(ignore_permissions=True)
	frappe.db.commit()


def _create_scrap_item(item_code):
	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = "Glass Scrap"
	item.uom = "Sq m"
	item.is_stock_item = 1
	# item_group "All Item Groups" always exists
	item.item_group = "All Item Groups"
	item.insert(ignore_permissions=True)
