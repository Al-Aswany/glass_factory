// Copyright (c) 2026, Mahmoud Hussein and contributors
// For license information, please see license.txt

const OPERATION_PRICING_BASIS = {
	Polish: "Per Edge Meter",
	Bevel: "Per Edge Meter",
	Hole: "Per Unit",
	"Special Hole": "Per Unit",
	Slot: "Per Unit",
	"Special Slot": "Per Unit",
	Temper: "Per Square Meter",
	Sandblast: "Per Square Meter",
	Laminate: "Per Square Meter",
};

frappe.ui.form.on("Glass Operation Rate", {
	operation(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const pricing_basis = OPERATION_PRICING_BASIS[row.operation];
		if (pricing_basis) {
			frappe.model.set_value(cdt, cdn, "pricing_basis", pricing_basis);
		}
	},
});
