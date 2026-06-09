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

	selling_price_list(frm) {
		gf_recalculate_glass_piece_rates(frm);
	},

	company(frm) {
		gf_recalculate_glass_piece_rates(frm);
	},

	after_save(frm) {
		gf_toggle_quotation_items_grid(frm);
	},
});

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

	const { message } = await frappe.call({
		method: "glass_factory.glass_factory.quotation_glass.build_quotation_items_from_glass",
		args: {
			glass_pieces: glass_pieces,
			manual_items: manual_items,
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
