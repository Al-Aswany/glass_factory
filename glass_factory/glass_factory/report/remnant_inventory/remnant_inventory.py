"""Remnant Inventory query report."""
import frappe
from frappe import _


def execute(filters=None):
	"""Generate Remnant Inventory report."""
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	"""Define report columns."""
	return [
		{
			"fieldname": "item_code",
			"label": _("Item Code"),
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{
			"fieldname": "item_name",
			"label": _("Item Name"),
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"fieldname": "length_mm",
			"label": _("Length (mm)"),
			"fieldtype": "Float",
			"width": 100,
		},
		{
			"fieldname": "width_mm",
			"label": _("Width (mm)"),
			"fieldtype": "Float",
			"width": 100,
		},
		{
			"fieldname": "area_m2",
			"label": _("Area (m²)"),
			"fieldtype": "Float",
			"width": 100,
		},
		{
			"fieldname": "qty_on_hand",
			"label": _("Qty on Hand"),
			"fieldtype": "Float",
			"width": 100,
		},
		{
			"fieldname": "valuation_rate",
			"label": _("Valuation Rate"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "total_value",
			"label": _("Total Value"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "cutting_job",
			"label": _("Cutting Job"),
			"fieldtype": "Link",
			"options": "Cutting Job",
			"width": 120,
		},
		{
			"fieldname": "parent_item",
			"label": _("Parent Item"),
			"fieldtype": "Link",
			"options": "Item",
			"width": 120,
		},
	]


def get_data(filters):
	"""Fetch and prepare data."""
	# Get all remnant items
	remnant_items = frappe.db.get_list(
		"Item",
		filters=[["item_code", "like", "%-REM-%"]],
		fields=["name", "item_name", "length_mm", "width_mm"],
	)

	data = []

	for item in remnant_items:
		item_code = item["name"]

		# Get stock balance
		stock_balance = frappe.db.get_value(
			"Bin",
			filters={"item_code": item_code},
			fieldname=["actual_qty", "valuation_rate"],
		) or (0, 0)

		qty = stock_balance[0] if stock_balance else 0
		valuation_rate = stock_balance[1] if stock_balance else 0

		if qty <= 0:
			continue  # Skip items with no stock

		# Compute dimensions
		length = item.get("length_mm", 0)
		width = item.get("width_mm", 0)
		area_m2 = (length * width) / 1e6 if length and width else 0

		# Get parent item code (extract from item code pattern)
		parent_code = _extract_parent_code(item_code)

		# Get cutting job that produced this remnant (from latest serial)
		cutting_job = _get_cutting_job_for_item(item_code)

		total_value = qty * area_m2 * valuation_rate

		data.append({
			"item_code": item_code,
			"item_name": item.get("item_name", item_code),
			"length_mm": length,
			"width_mm": width,
			"area_m2": area_m2,
			"qty_on_hand": qty,
			"valuation_rate": valuation_rate,
			"total_value": total_value,
			"cutting_job": cutting_job,
			"parent_item": parent_code,
		})

	# Sort by area descending
	data.sort(key=lambda x: x["area_m2"], reverse=True)

	return data


def _extract_parent_code(item_code: str) -> str:
	"""Extract parent item code from remnant item code pattern."""
	# Pattern: {parent}-REM-{length}x{width}
	if "-REM-" in item_code:
		return item_code.split("-REM-")[0]
	return item_code


def _get_cutting_job_for_item(item_code: str) -> str:
	"""Get cutting job that produced this item."""
	# Find the most recent serial with this item code and a cutting_job link
	serial = frappe.db.get_value(
		"Serial No",
		filters={"item_code": item_code, "cutting_job": ["!=", ""]},
		fieldname="cutting_job",
		order_by="creation desc",
	)
	return serial or ""
