import frappe

ORIGINAL_RATES = [
	{"operation": "Polish", "currency": "TZS", "pricing_basis": "Per Edge Meter", "rate": 60.0, "cost_rate": 40.0, "enabled": 1},
	{"operation": "Bevel", "currency": "TZS", "pricing_basis": "Per Edge Meter", "rate": 70.0, "cost_rate": 50.0, "enabled": 1},
	{"operation": "Hole", "currency": "TZS", "pricing_basis": "Per Unit", "rate": 15.0, "cost_rate": 10.0, "enabled": 1},
	{"operation": "Special Hole", "currency": "TZS", "pricing_basis": "Per Unit", "rate": 20.0, "cost_rate": 15.0, "enabled": 1},
	{"operation": "Slot", "currency": "TZS", "pricing_basis": "Per Unit", "rate": 17.0, "cost_rate": 13.0, "enabled": 1},
	{"operation": "Special Slot", "currency": "TZS", "pricing_basis": "Per Unit", "rate": 25.0, "cost_rate": 15.0, "enabled": 1},
	{"operation": "Temper", "currency": "TZS", "pricing_basis": "Per Square Meter", "rate": 80.0, "cost_rate": 60.0, "enabled": 1},
	{"operation": "Sandblast", "currency": "TZS", "pricing_basis": "Per Square Meter", "rate": 100.0, "cost_rate": 70.0, "enabled": 1},
	{"operation": "Laminate", "currency": "TZS", "pricing_basis": "Per Square Meter", "rate": 120.0, "cost_rate": 90.0, "enabled": 1},
]


def execute():
	settings = frappe.get_single("Glass Factory Settings")
	settings.set("operation_rates", [])
	for row in ORIGINAL_RATES:
		settings.append("operation_rates", row)
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	print("Restored", len(ORIGINAL_RATES), "operation rate rows")


def check():
	rows = frappe.get_all("Glass Operation Rate", filters={"parent": "Glass Factory Settings"}, fields=["operation"])
	print(f"ROW_COUNT={len(rows)}")
