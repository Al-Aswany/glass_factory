frappe.ui.form.on("Quotation", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 0) return;

		frm.add_custom_button(__("Add Glass Specification"), () => {
			glass_factory.sync.show_add_spec_to_transaction_dialog(frm, "Quotation");
		}, __("Glass"));

		const has_spec_rows = (frm.doc.items || []).some(
			(row) => row.gf_from_glass_specification && row.gf_glass_specification
		);
		if (has_spec_rows) {
			frm.add_custom_button(__("Open Glass Specification"), () => {
				glass_factory.sync.open_selected_glass_specification(frm);
			}, __("Glass"));
		}
	},
});
