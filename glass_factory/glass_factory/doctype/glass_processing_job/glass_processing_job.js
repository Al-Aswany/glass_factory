frappe.ui.form.on("Glass Processing Job", {
	refresh(frm) {
		const color_map = {
			"Draft": "gray",
			"Ready for Processing": "blue",
			"Processing In Progress": "orange",
			"Final Stock Posted": "green",
			"Completed": "green",
			"Cancelled": "red",
		};
		frm.page.set_indicator(frm.doc.status, color_map[frm.doc.status] || "gray");
		frm.trigger("add_spec_buttons");
		frm.trigger("add_dynamic_actions");
	},

	add_spec_buttons(frm) {
		const specs = [...new Set(
			(frm.doc.inputs || [])
				.map((row) => row.glass_specification)
				.filter(Boolean)
		)];
		specs.forEach((spec_name) => {
			frm.add_custom_button(__("Open Glass Specification"), () => {
				frappe.set_route("Form", "Glass Product Specification", spec_name);
			}, __("Specification"));
		});
	},

	add_dynamic_actions(frm) {
		if (frm.doc.docstatus !== 1) return;

		frm.call("get_valid_actions").then(({ message }) => {
			(message || []).forEach((action) => {
				frm.add_custom_button(__(action.label), () => {
					frm.call("run_action", { action: action.action }).then(({ message: result }) => {
						if (result?.stock_entry) {
							frappe.set_route("Form", "Stock Entry", result.stock_entry);
						} else {
							frm.reload_doc();
						}
					});
				}, __("Actions"));
			});
		});
	},
});
