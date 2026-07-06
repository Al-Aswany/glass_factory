import frappe


def execute():
	rows = frappe.get_all(
		"Glass Operation Rate",
		filters={"parent": "Glass Factory Settings"},
		fields=["idx", "operation", "currency", "pricing_basis", "rate", "cost_rate", "enabled"],
		order_by="idx",
	)
	print(f"ROW_COUNT={len(rows)}")
	for r in rows:
		print(r)
