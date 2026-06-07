frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		if (!frappe.model.can_create("Cutting Job")) return;
		if (!gf_sales_order_has_glass_items(frm)) return;

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
	},
});

function gf_sales_order_has_glass_items(frm) {
	return (frm.doc.items || []).some((row) => row.gf_is_glass_item);
}
