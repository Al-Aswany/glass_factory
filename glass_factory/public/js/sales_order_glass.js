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
			if (frm.is_new() || frm.is_dirty()) {
				gf_maybe_sync_glass_items(frm);
			}
		},

		async before_save(frm) {
			glass_factory.sync.cancel_scheduled_sync(frm);
			if (!(frm.doc.glass_pieces || []).length) return;
			await glass_factory.sync.sync_glass_items_to_form(frm, { silent: true });
			frm.trigger("calculate_taxes_and_totals");
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
			gf_toggle_items_grid(frm, { mark_clean: true });
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
	gf_maybe_sync_glass_items(frm);
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

function gf_maybe_sync_glass_items(frm) {
	const glass_pieces = frm.doc.glass_pieces || [];
	if (!glass_pieces.length) return;
	if (!gf_all_pieces_ready(glass_pieces)) return;
	if (frm.is_new() || frm.doc.docstatus === 0) {
		glass_factory.sync.schedule_sync(frm);
	}
}

function gf_existing_glass_rates(frm) {
	return glass_factory.sync.existing_glass_rates(frm);
}

function gf_remove_empty_item_rows(frm) {
	glass_factory.sync.remove_empty_item_rows(frm);
}

function gf_toggle_items_grid(frm, opts = {}) {
	glass_factory.sync.toggle_items_grid(frm, opts);
}


gf_register_glass_piece_handlers("Sales Order");

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
