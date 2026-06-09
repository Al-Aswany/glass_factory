frappe.ui.form.on("Stock Entry", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		if (frm.doc.gf_glass_stock_flow !== "Raw to Cut WIP" || !frm.doc.gf_cutting_job) return;

		frm.add_custom_button(__("Start Processing"), () => {
			frappe.call({
				method: "glass_factory.glass_factory.stock_entry_hooks.start_processing_from_stock_entry",
				args: { stock_entry_name: frm.doc.name },
			}).then(({ message }) => {
				const processing_job = message?.processing_job;
				if (processing_job) {
					frappe.set_route("Form", "Glass Processing Job", processing_job);
				} else {
					frm.reload_doc();
				}
			});
		}, __("Actions"));
	},
});
