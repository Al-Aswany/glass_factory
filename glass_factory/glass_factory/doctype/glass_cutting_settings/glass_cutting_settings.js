frappe.ui.form.on("Glass Cutting Settings", {
	refresh(frm) {
		frm.set_intro(
			__("Configure global defaults for the glass cutting workflow, warehouses, and pricing."),
			"blue"
		);
	},
});
