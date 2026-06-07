frappe.ui.form.on("Quotation", {
	refresh(frm) {
		frm.set_query("raw_sheet_item", "glass_pieces", () => ({
			filters: { gf_glass_item_role: "Raw Sheet", disabled: 0 },
		}));
		gf_toggle_quotation_items_grid(frm);
	},

	async validate(frm) {
		if (!(frm.doc.glass_pieces || []).length) return;
		await gf_sync_glass_items_to_form(frm);
	},

	glass_pieces_add(frm) {
		gf_remove_empty_item_rows(frm);
		gf_toggle_quotation_items_grid(frm);
	},

	glass_pieces_remove(frm) {
		gf_toggle_quotation_items_grid(frm);
	},

	after_save(frm) {
		gf_toggle_quotation_items_grid(frm);
	},
});

frappe.ui.form.on("Quotation Glass Piece", {
	raw_sheet_item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.raw_sheet_item || row.thickness_mm) return;
		frappe.call({
			method: "glass_factory.glass_factory.item_resolver.get_item_glass_meta",
			args: { item_code: row.raw_sheet_item },
		}).then(({ message }) => {
			if (!message?.gf_thickness_mm) return;
			frappe.model.set_value(cdt, cdn, "thickness_mm", message.gf_thickness_mm);
		});
	},

	async qty(frm) {
		await gf_maybe_sync_glass_items(frm);
	},

	async rate(frm) {
		await gf_maybe_sync_glass_items(frm);
	},
});

async function gf_maybe_sync_glass_items(frm) {
	if (!(frm.doc.glass_pieces || []).length) return;
	if (frm.is_new() || frm.doc.docstatus === 0) {
		await gf_sync_glass_items_to_form(frm, { silent: true });
	}
}

async function gf_sync_glass_items_to_form(frm, opts = {}) {
	const glass_pieces = frm.doc.glass_pieces || [];
	if (!glass_pieces.length) return;

	gf_remove_empty_item_rows(frm);

	const manual_items = (frm.doc.items || [])
		.filter((row) => row.item_code && !row.gf_is_glass_item)
		.map((row) => frappe.model.get_doc(row.doctype, row.name));

	const { message } = await frappe.call({
		method: "glass_factory.glass_factory.quotation_glass.build_quotation_items_from_glass",
		args: {
			glass_pieces: glass_pieces,
			manual_items: manual_items,
		},
		freeze: !opts.silent,
		freeze_message: opts.silent ? undefined : __("Resolving glass items..."),
	});

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

	if (!opts.silent) {
		frm.trigger("calculate_taxes_and_totals");
	}
}

function gf_remove_empty_item_rows(frm) {
	const rows = frm.doc.items || [];
	for (let i = rows.length - 1; i >= 0; i -= 1) {
		const row = rows[i];
		if (!row.item_code) {
			frm.get_field("items").grid.grid_rows[i]?.remove();
		}
	}
}

function gf_toggle_quotation_items_grid(frm) {
	const has_glass = (frm.doc.glass_pieces || []).length > 0;
	const items_grid = frm.fields_dict.items?.grid;
	if (!items_grid) return;

	// Hide empty Items only while creating a new quote; show synced rows after save.
	const hide_items = has_glass && frm.is_new();

	if (hide_items) {
		gf_remove_empty_item_rows(frm);
		items_grid.wrapper.hide();
	} else {
		items_grid.wrapper.show();
	}

	frm.set_df_property("items", "reqd", has_glass ? 0 : 1);
}
