const GF_PIECE_RATE_FIELDS = [
	"glass_rate",
	"processing_rate",
	"rate",
	"polish_rate",
	"bevel_rate",
	"holes_rate",
	"slots_rate",
	"temper_rate",
	"sandblast_rate",
	"laminate_rate",
];

const GF_PIECE_RECALC_FIELDS = [
	"raw_sheet_item",
	"length_mm",
	"width_mm",
	"thickness_mm",
	"process_polish",
	"process_bevel",
	"process_holes",
	"process_slots",
	"process_temper",
	"process_sandblast",
	"process_laminate",
];

const GF_ITEM_RATE_FIELDS = ["rate"];
const GF_ITEM_LOCKED_FIELDS = [
	"item_code",
	"item_name",
	"description",
	"qty",
	"uom",
	"stock_uom",
	"conversion_factor",
	"warehouse",
	"s_warehouse",
	"t_warehouse",
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

function gf_register_glass_piece_handlers(doctype) {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			frm.set_query("raw_sheet_item", "glass_pieces", () => ({
				filters: { gf_glass_item_role: ["in", ["Raw Sheet", "Remnant"]], disabled: 0 },
			}));
			gf_toggle_items_grid(frm);
		},

		async validate(frm) {
			if (!(frm.doc.glass_pieces || []).length) return;
			await gf_sync_glass_items_to_form(frm);
		},

		glass_pieces_add(frm) {
			gf_remove_empty_item_rows(frm);
			gf_toggle_items_grid(frm);
		},

		glass_pieces_remove(frm) {
			gf_toggle_items_grid(frm);
		},

		selling_price_list(frm) {
			gf_recalculate_glass_piece_rates(frm);
		},

		company(frm) {
			gf_recalculate_glass_piece_rates(frm);
		},

		after_save(frm) {
			gf_toggle_items_grid(frm);
		},
	});
}

frappe.ui.form.on("Quotation Glass Piece", {
	raw_sheet_item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.raw_sheet_item || row.thickness_mm) {
			gf_recalculate_glass_piece_rates(frm);
			return;
		}
		frappe.call({
			method: "glass_factory.glass_factory.item_resolver.get_item_glass_meta",
			args: { item_code: row.raw_sheet_item },
		}).then(({ message }) => {
			if (message?.gf_thickness_mm) {
				frappe.model.set_value(cdt, cdn, "thickness_mm", message.gf_thickness_mm);
			}
			gf_recalculate_glass_piece_rates(frm);
		});
	},

	qty(frm) {
		gf_maybe_sync_glass_items(frm);
	},
});

GF_PIECE_RECALC_FIELDS.forEach((fieldname) => {
	if (fieldname === "raw_sheet_item") return;
	frappe.ui.form.on("Quotation Glass Piece", fieldname, (frm) => {
		gf_recalculate_glass_piece_rates(frm);
	});
});

async function gf_recalculate_glass_piece_rates(frm) {
	const glass_pieces = frm.doc.glass_pieces || [];
	if (!glass_pieces.length) return;

	const { message } = await frappe.call({
		method: "glass_factory.glass_factory.quotation_glass.calculate_glass_piece_rates",
		args: {
			glass_pieces: glass_pieces,
			price_list: frm.doc.selling_price_list,
			company: frm.doc.company,
		},
	});

	(message || []).forEach((row, index) => {
		if (!frm.doc.glass_pieces[index]) return;
		GF_PIECE_RATE_FIELDS.forEach((fieldname) => {
			frm.doc.glass_pieces[index][fieldname] = row[fieldname];
		});
	});

	frm.refresh_field("glass_pieces");
	await gf_maybe_sync_glass_items(frm);
}

function gf_is_piece_ready(piece) {
	return (
		piece.raw_sheet_item &&
		flt(piece.length_mm) > 0 &&
		flt(piece.width_mm) > 0 &&
		flt(piece.qty) > 0
	);
}

function gf_all_pieces_ready(glass_pieces) {
	return (glass_pieces || []).every(gf_is_piece_ready);
}

async function gf_maybe_sync_glass_items(frm) {
	const glass_pieces = frm.doc.glass_pieces || [];
	if (!glass_pieces.length) return;
	if (!gf_all_pieces_ready(glass_pieces)) return;
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
	const existing_glass_rates = gf_existing_glass_rates(frm);

	const { message } = await frappe.call({
		method: "glass_factory.glass_factory.quotation_glass.build_quotation_items_from_glass",
		args: {
			glass_pieces: glass_pieces,
			manual_items: manual_items,
			existing_glass_rates: existing_glass_rates,
			price_list: frm.doc.selling_price_list,
			company: frm.doc.company,
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
	gf_toggle_items_grid(frm);

	if (!opts.silent) {
		frm.trigger("calculate_taxes_and_totals");
	}
}

function gf_existing_glass_rates(frm) {
	const rates = {};
	(frm.doc.items || []).forEach((row) => {
		if (row.gf_is_glass_item && row.gf_source_row_id) {
			rates[row.gf_source_row_id] = row.rate;
		}
	});
	return rates;
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

function gf_toggle_items_grid(frm) {
	const has_glass = (frm.doc.glass_pieces || []).length > 0;
	const items_grid = frm.fields_dict.items?.grid;
	if (!items_grid) return;

	const hide_items = has_glass && frm.is_new();
	items_grid.wrapper.toggle(!hide_items);
	items_grid.cannot_add_rows = has_glass;
	items_grid.cannot_delete_rows = has_glass;
	items_grid.only_sortable = !has_glass;

	GF_ITEM_LOCKED_FIELDS.forEach((fieldname) => {
		if (items_grid.get_field(fieldname)) {
			items_grid.update_docfield_property(fieldname, "read_only", has_glass ? 1 : 0);
		}
	});
	GF_ITEM_RATE_FIELDS.forEach((fieldname) => {
		if (items_grid.get_field(fieldname)) {
			items_grid.update_docfield_property(fieldname, "read_only", 0);
		}
	});

	frm.set_df_property("items", "reqd", has_glass ? 0 : 1);
	frm.refresh_field("items");
}


gf_register_glass_piece_handlers("Quotation");
