frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Add Glass Specification"), () => {
				glass_factory.sync.show_add_spec_to_transaction_dialog(frm, "Sales Order");
			}, __("Glass"));

			const has_spec_rows = (frm.doc.items || []).some(
				(row) => row.gf_from_glass_specification && row.gf_glass_specification
			);
			if (has_spec_rows) {
				frm.add_custom_button(__("Open Glass Specification"), () => {
					glass_factory.sync.open_selected_glass_specification(frm);
				}, __("Glass"));
			}
		}

		if (frm.doc.docstatus === 1) {
			if (frappe.model.can_create("Cutting Job") && gf_so_has_glass_items(frm)) {
				frm.add_custom_button(
					__("Cutting Job"),
					() => {
						frappe.model.open_mapped_doc({
							method: "glass_factory.glass_factory.doctype.cutting_job.cutting_job.make_cutting_job",
							frm,
						});
					},
					__("Create")
				);
			}
		}
	},

	set_warehouse(frm) {
		glass_factory.sync.apply_sales_order_warehouses(frm);
		frm.refresh_field("items");
	},

	delivery_date(frm) {
		glass_factory.sync.apply_sales_order_delivery_dates(frm);
		frm.refresh_field("items");
	},
});

function gf_so_has_glass_items(frm) {
	return (frm.doc.items || []).some(
		(row) => row.gf_is_glass_item || row.gf_from_glass_specification
	);
}
