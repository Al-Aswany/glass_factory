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

	const manual_items = (frm.doc.items || [])
		.filter((row) => row.item_code && !row.gf_is_glass_item)
		.map((row) => frappe.model.get_doc(row.doctype, row.name));
	const existing_glass_rates = glass_factory.sync.existing_glass_rates(frm);

	let message;
	try {
		({ message } = await glass_factory.sync._call_build_items(
			frm,
			glass_pieces,
			manual_items,
			existing_glass_rates,
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

	(message.glass_pieces || []).forEach((row, index) => {
		if (!frm.doc.glass_pieces[index]) return;
		Object.assign(frm.doc.glass_pieces[index], row);
	});

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
	opts
) {
	return frappe.call({
		method: GF_GLASS_SYNC_METHOD,
		args: {
			glass_pieces,
			manual_items,
			existing_glass_rates,
			price_list: frm.doc.selling_price_list,
			company: frm.doc.company,
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
