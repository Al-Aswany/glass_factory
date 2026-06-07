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

		if (frm.is_new() || frm.doc.docstatus === 2) return;

		if (!frm.doc.linked_stock_entry) {
			frm.add_custom_button(__("Create Repack #2"), () => {
				frm.call("create_repack_stock_entry").then(() => frm.reload_doc());
			}, __("Actions"));
		}

		if (frm.doc.linked_stock_entry && frm.doc.status !== "Final Stock Posted") {
			frm.add_custom_button(__("Submit Repack #2"), () => {
				frm.call("submit_repack_stock_entry").then(() => frm.reload_doc());
			}, __("Actions"));
		}

		if (frm.doc.status === "Final Stock Posted") {
			frm.add_custom_button(__("Complete Job"), () => {
				frm.call("complete_job").then(() => frm.reload_doc());
			}, __("Actions"));
		}
	},
});
