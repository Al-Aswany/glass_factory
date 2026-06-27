frappe.provide("glass_factory.sync");

const GF_GLASS_SYNC_METHOD =
	"glass_factory.glass_factory.quotation_glass.build_quotation_items_from_glass";

glass_factory.sync.item_rate_fields = ["rate"];

glass_factory.sync.item_locked_fields = [
	"item_code",
	"item_name",
	"description",
	"qty",
	"uom",
	"stock_uom",
	"conversion_factor",
	"warehouse",
	"gf_is_glass_item",
	"gf_glass_specification",
	"gf_raw_sheet_item",
	"gf_cut_wip_item",
	"gf_final_item",
	"gf_length_mm",
	"gf_width_mm",
	"gf_thickness_mm",
	"gf_processing_flags",
	"gf_area_m2",
	"gf_source_row_id",
	"gf_from_glass_specification",
	"gf_total_area_m2",
	"gf_selling_rate_per_m2",
	"gf_calculated_rate_per_m2",
	"gf_manual_selling_rate_per_m2",
	"gf_price_override",
	"gf_price_difference_per_m2",
	"gf_rate_per_piece",
	"gf_technical_summary",
	"gf_design_attachment_summary",
	"gf_transaction_rate_overridden",
	"delivery_date",
];

glass_factory.sync.cancel_scheduled_sync = function (frm) {
	clearTimeout(frm._gf_sync_timer);
	frm._gf_sync_timer = null;
};

glass_factory.sync.schedule_sync = function (frm, delay = 400) {
	glass_factory.sync.cancel_scheduled_sync(frm);
	frm._gf_sync_timer = setTimeout(() => {
		glass_factory.sync.sync_glass_items_to_form(frm, { silent: true });
	}, delay);
};

glass_factory.sync.enqueue = function (frm, task) {
	const previous = frm._gf_sync_chain || Promise.resolve();
	const current = previous.then(task);
	frm._gf_sync_chain = current.catch(() => {});
	return current;
};

glass_factory.sync.sync_glass_items_to_form = function (frm, opts = {}) {
	return glass_factory.sync.enqueue(frm, () =>
		glass_factory.sync._sync_glass_items_impl(frm, opts)
	);
};

glass_factory.sync._sync_glass_items_impl = async function (frm, opts = {}) {
	const glass_pieces = frm.doc.glass_pieces || [];
	if (!glass_pieces.length) return;

	glass_factory.sync.remove_empty_item_rows(frm);

	const spec_items = (frm.doc.items || [])
		.filter((row) => row.gf_from_glass_specification && row.item_code)
		.map((row) => frappe.model.get_doc(row.doctype, row.name));

	const manual_items = (frm.doc.items || [])
		.filter((row) => row.item_code && !row.gf_is_glass_item && !row.gf_from_glass_specification)
		.map((row) => frappe.model.get_doc(row.doctype, row.name));
	const existing_glass_rates = glass_factory.sync.existing_glass_rates(frm);
	const existing_glass_delivery_dates =
		glass_factory.sync.existing_glass_delivery_dates(frm);
	const existing_glass_warehouses = glass_factory.sync.existing_glass_warehouses(frm);

	let message;
	try {
		({ message } = await glass_factory.sync._call_build_items(
			frm,
			glass_pieces,
			manual_items,
			existing_glass_rates,
			existing_glass_delivery_dates,
			existing_glass_warehouses,
			opts
		));
	} catch (error) {
		if (glass_factory.sync._is_retryable_conflict(error) && opts._retried !== true) {
			await glass_factory.sync._wait(300);
			return glass_factory.sync._sync_glass_items_impl(frm, { ...opts, _retried: true });
		}

		if (!glass_factory.sync._is_retryable_conflict(error)) {
			frappe.msgprint({
				title: __("Glass Items"),
				indicator: "red",
				message: __(
					"Could not build item rows from glass pieces. Check Glass Factory Settings and the glass sheet item."
				),
			});
		}
		throw error;
	}

	frm.clear_table("items");
	(message.items || []).forEach((row) => {
		const child = frm.add_child("items");
		Object.assign(child, row);
	});
	spec_items.forEach((row) => {
		const child = frm.add_child("items");
		Object.assign(child, row);
	});

	(message.glass_pieces || []).forEach((row, index) => {
		if (!frm.doc.glass_pieces[index]) return;
		Object.assign(frm.doc.glass_pieces[index], row);
	});

	glass_factory.sync.apply_sales_order_delivery_dates(frm);
	glass_factory.sync.apply_sales_order_warehouses(frm);

	frm.refresh_field("items");
	frm.refresh_field("glass_pieces");
	glass_factory.sync.toggle_items_grid(frm);

	if (!opts.silent) {
		frm.trigger("calculate_taxes_and_totals");
	}
};

glass_factory.sync.existing_glass_rates = function (frm) {
	const rates = {};
	(frm.doc.items || []).forEach((row) => {
		if (row.gf_is_glass_item && row.gf_source_row_id) {
			rates[row.gf_source_row_id] = row.rate;
		}
	});
	return rates;
};

glass_factory.sync.existing_glass_delivery_dates = function (frm) {
	const dates = {};
	(frm.doc.items || []).forEach((row) => {
		if (row.gf_is_glass_item && row.gf_source_row_id && row.delivery_date) {
			dates[row.gf_source_row_id] = row.delivery_date;
		}
	});
	return dates;
};

glass_factory.sync.apply_sales_order_delivery_dates = function (frm) {
	if (frm.doc.doctype !== "Sales Order" || !frm.doc.delivery_date) return;
	(frm.doc.items || []).forEach((row) => {
		row.delivery_date = frm.doc.delivery_date;
	});
};

glass_factory.sync.existing_glass_warehouses = function (frm) {
	const warehouses = {};
	(frm.doc.items || []).forEach((row) => {
		if (row.gf_is_glass_item && row.gf_source_row_id && row.warehouse) {
			warehouses[row.gf_source_row_id] = row.warehouse;
		}
	});
	return warehouses;
};

glass_factory.sync.apply_sales_order_warehouses = function (frm) {
	if (frm.doc.doctype !== "Sales Order" || !frm.doc.set_warehouse) return;
	(frm.doc.items || []).forEach((row) => {
		row.warehouse = frm.doc.set_warehouse;
	});
};

glass_factory.sync.remove_empty_item_rows = function (frm) {
	const rows = frm.doc.items || [];
	for (let i = rows.length - 1; i >= 0; i -= 1) {
		const row = rows[i];
		if (!row.item_code) {
			frm.get_field("items").grid.grid_rows[i]?.remove();
		}
	}
};

glass_factory.sync.grid_has_field = function (items_grid, fieldname) {
	return (items_grid.docfields || []).some((df) => df.fieldname === fieldname);
};

glass_factory.sync.set_grid_field_read_only = function (items_grid, fieldname, read_only) {
	if (!glass_factory.sync.grid_has_field(items_grid, fieldname)) return;
	items_grid.update_docfield_property(fieldname, "read_only", read_only ? 1 : 0);
};

glass_factory.sync.toggle_items_grid = function (frm, opts = {}) {
	const has_glass = (frm.doc.glass_pieces || []).length > 0;
	const items_grid = frm.fields_dict.items?.grid;
	if (!items_grid) return;

	items_grid.wrapper.show();
	items_grid.cannot_add_rows = has_glass;
	items_grid.cannot_delete_rows = has_glass;
	items_grid.only_sortable = !has_glass;

	glass_factory.sync.item_locked_fields.forEach((fieldname) => {
		glass_factory.sync.set_grid_field_read_only(items_grid, fieldname, has_glass);
	});
	glass_factory.sync.item_rate_fields.forEach((fieldname) => {
		glass_factory.sync.set_grid_field_read_only(items_grid, fieldname, false);
	});

	frm.set_df_property("items", "reqd", has_glass ? 0 : 1);
	if (opts.mark_clean) {
		frm.doc.__unsaved = 0;
		frm.toolbar?.refresh();
	} else {
		frm.refresh_field("items");
	}
};

glass_factory.sync._call_build_items = function (
	frm,
	glass_pieces,
	manual_items,
	existing_glass_rates,
	existing_glass_delivery_dates,
	existing_glass_warehouses,
	opts
) {
	const is_sales_order = frm.doc.doctype === "Sales Order";
	return frappe.call({
		method: GF_GLASS_SYNC_METHOD,
		args: {
			glass_pieces,
			manual_items,
			existing_glass_rates,
			existing_glass_delivery_dates,
			existing_glass_warehouses,
			price_list: frm.doc.selling_price_list,
			company: frm.doc.company,
			parent_doctype: frm.doc.doctype,
			delivery_date: is_sales_order ? frm.doc.delivery_date : undefined,
			set_warehouse: is_sales_order ? frm.doc.set_warehouse : undefined,
		},
		freeze: !opts.silent,
		freeze_message: opts.silent ? undefined : __("Resolving glass items..."),
	});
};

glass_factory.sync._is_retryable_conflict = function (error) {
	const text = [error?.message, error?.exc_type, error?.exc]
		.filter(Boolean)
		.join(" ");
	return /concurrent conflicting request|QueryDeadlockError|Deadlock/i.test(text);
};

glass_factory.sync._wait = function (ms) {
	return new Promise((resolve) => setTimeout(resolve, ms));
};

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
