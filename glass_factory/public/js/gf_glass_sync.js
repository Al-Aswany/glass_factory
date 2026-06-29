frappe.provide("glass_factory.sync");

// ---------------------------------------------------------------------------
// Sales Order header → item row propagation (useful for spec-based rows too)
// ---------------------------------------------------------------------------

glass_factory.sync.apply_sales_order_delivery_dates = function (frm) {
	if (frm.doc.doctype !== "Sales Order" || !frm.doc.delivery_date) return;
	(frm.doc.items || []).forEach((row) => {
		row.delivery_date = frm.doc.delivery_date;
	});
};

glass_factory.sync.apply_sales_order_warehouses = function (frm) {
	if (frm.doc.doctype !== "Sales Order" || !frm.doc.set_warehouse) return;
	(frm.doc.items || []).forEach((row) => {
		row.warehouse = frm.doc.set_warehouse;
	});
};

// ---------------------------------------------------------------------------
// Glass Product Specification → transaction integration
// ---------------------------------------------------------------------------

const GF_SPEC_TRANSACTION_METHOD =
	"glass_factory.glass_factory.spec_transaction.add_spec_to_transaction";

glass_factory.sync.spec_ready_filters = function () {
	return {
		items_generated: 1,
		generation_status: "Generated",
		status: "Ready",
	};
};

glass_factory.sync.add_spec_to_transaction = function (args) {
	return frappe.call({
		method: GF_SPEC_TRANSACTION_METHOD,
		args,
		freeze: true,
		freeze_message: __("Adding Glass Specification..."),
	});
};

glass_factory.sync.show_add_spec_from_spec_dialog = function (frm, target_doctype) {
	const d = new frappe.ui.Dialog({
		title: __("Add to {0}", [target_doctype]),
		fields: [
			{
				fieldname: "create_new",
				fieldtype: "Check",
				label: __("Create New"),
				default: 1,
			},
			{
				fieldname: "target_name",
				fieldtype: "Link",
				options: target_doctype,
				label: target_doctype,
				depends_on: "eval:!doc.create_new",
				get_query: () => ({ filters: { docstatus: 0 } }),
			},
		],
		primary_action_label: __("Add"),
		primary_action(values) {
			const payload = {
				spec_name: frm.doc.name,
				target_doctype,
				target_name: values.create_new ? null : values.target_name,
			};
			glass_factory.sync._submit_spec_to_transaction(payload, d, target_doctype);
		},
	});
	d.show();
};

glass_factory.sync.show_add_spec_to_transaction_dialog = function (frm, target_doctype) {
	const d = new frappe.ui.Dialog({
		title: __("Add Glass Specification"),
		fields: [
			{
				fieldname: "spec_name",
				fieldtype: "Link",
				options: "Glass Product Specification",
				label: __("Glass Product Specification"),
				reqd: 1,
				get_query: () => ({ filters: glass_factory.sync.spec_ready_filters() }),
			},
		],
		primary_action_label: __("Add"),
		primary_action(values) {
			const payload = {
				spec_name: values.spec_name,
				target_doctype,
				target_name: frm.doc.name,
			};
			glass_factory.sync._submit_spec_to_transaction(payload, d, target_doctype, {
				reload: true,
				frm,
			});
		},
	});
	d.show();
};

glass_factory.sync._submit_spec_to_transaction = function (
	payload,
	dialog,
	target_doctype,
	opts = {}
) {
	glass_factory.sync
		.add_spec_to_transaction(payload)
		.then(({ message }) => {
			dialog.hide();
			if (opts.reload && opts.frm) {
				opts.frm.reload_doc().then(() => {
					frappe.show_alert({
						message: __("Glass Specification added."),
						indicator: "green",
					});
				});
				return;
			}
			frappe.set_route("Form", message.doctype, message.name);
		})
		.catch((error) => {
			const text = [error?.message, error?.exc_type, error?.exc].filter(Boolean).join(" ");
			if (!/already exists in this transaction/i.test(text)) {
				return;
			}
			frappe.confirm(
				__(
					"This Glass Product Specification already exists in this transaction. Update the existing row?"
				),
				() => {
					glass_factory.sync
						.add_spec_to_transaction({ ...payload, update_existing: 1 })
						.then(({ message }) => {
							dialog.hide();
							if (opts.reload && opts.frm) {
								opts.frm.reload_doc();
								return;
							}
							frappe.set_route("Form", message.doctype, message.name);
						});
				}
			);
		});
};

glass_factory.sync.open_selected_glass_specification = function (frm) {
	const grid = frm.fields_dict.items?.grid;
	const selected = grid?.get_selected()?.length
		? grid.get_selected()
		: (frm.doc.items || []).filter((row) => row.gf_from_glass_specification && row.gf_glass_specification);
	if (!selected.length) {
		frappe.msgprint(__("Select an item row linked to a Glass Product Specification."));
		return;
	}
	const row = selected[0];
	if (!row.gf_glass_specification) {
		frappe.msgprint(__("This item row is not linked to a Glass Product Specification."));
		return;
	}
	frappe.set_route("Form", "Glass Product Specification", row.gf_glass_specification);
};

glass_factory.sync.spec_transaction_buttons_visible = function (frm) {
	return (
		!frm.is_new() &&
		cint(frm.doc.items_generated) &&
		frm.doc.generation_status === "Generated" &&
		flt(frm.doc.rate_per_piece) > 0
	);
};
