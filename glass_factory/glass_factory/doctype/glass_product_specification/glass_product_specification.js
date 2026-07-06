const GF_SPEC_PREVIEW_FIELDS = [
	"length_mm",
	"width_mm",
	"qty",
	"polish",
	"bevel",
	"temper",
	"sandblast",
	"laminate",
	"hole_count",
	"special_hole_count",
	"slot_count",
	"special_slot_count",
];

const GF_SPEC_PRICING_FIELDS = [
	"manual_selling_rate_per_m2",
	"currency",
	"price_list",
	"qty",
	"polish",
	"bevel",
	"temper",
	"sandblast",
	"laminate",
	"hole_count",
	"special_hole_count",
	"slot_count",
	"special_slot_count",
	"length_mm",
	"width_mm",
];

const GF_SPEC_PRICING_RESULT_FIELDS = [
	"raw_sheet_rate_per_piece",
	"raw_sheet_selling_rate_per_piece",
	"raw_cost_per_m2",
	"raw_selling_rate_per_m2",
	"raw_cost_per_finished_piece",
	"raw_selling_amount_per_finished_piece",
	"edge_meter",
	"chargeable_area_m2",
	"total_chargeable_area_m2",
	"area_processing_amount_per_piece",
	"edge_processing_amount_per_piece",
	"unit_processing_amount_per_piece",
	"processing_amount_per_piece",
	"calculated_amount_per_piece",
	"calculated_rate_per_m2",
	"selling_rate_per_m2",
	"price_override",
	"price_difference_per_m2",
	"rate_per_piece",
	"amount",
	"estimated_cost_per_piece",
	"gross_profit_per_piece",
	"gross_profit_per_m2",
	"gross_profit_percent",
	"total_gross_profit",
	"status",
];

const GF_SPEC_RAW_SHEET_FIELDS = [
	"glass_type",
	"thickness_mm",
	"raw_sheet_length_mm",
	"raw_sheet_width_mm",
	"raw_sheet_area_m2",
	"raw_sheet_rate_per_piece",
	"raw_sheet_selling_rate_per_piece",
];

const GF_SPEC_STATUS_COLORS = {
	Draft: "gray",
	Priced: "orange",
	Ready: "blue",
	"Items Generated": "purple",
	Used: "green",
	Submitted: "darkgrey",
	Cancelled: "red",
};

const GF_SPEC_PRICING_REFRESHED_STATUSES = new Set(["Priced", "Ready", "Items Generated", "Used", "Submitted"]);

frappe.ui.form.on("Glass Product Specification", {
	refresh(frm) {
		gf_set_spec_status_indicator(frm);
		gf_apply_spec_form_locks(frm);
		gf_setup_spec_operation_pricing_grid(frm);
		gf_setup_spec_custom_buttons(frm);

		frm.set_query("raw_sheet_item", () => ({
			filters: {
				gf_glass_item_role: ["in", ["Raw Sheet", "Remnant"]],
				is_sales_item: 0,
				is_purchase_item: 1,
			},
		}));

		frm.set_query("price_list", () => ({
			filters: { selling: 1, enabled: 1 },
		}));
	},

	raw_sheet_item(frm) {
		gf_pull_from_raw_sheet_item(frm);
	},
});

GF_SPEC_PREVIEW_FIELDS.forEach((fieldname) => {
	frappe.ui.form.on("Glass Product Specification", fieldname, (frm) => {
		if (gf_spec_is_locked(frm)) {
			return;
		}
		gf_refresh_spec_preview(frm);
		if (!frm.is_new() && GF_SPEC_PRICING_FIELDS.includes(fieldname)) {
			gf_refresh_spec_pricing(frm, { silent: true });
		}
	});
});

GF_SPEC_PRICING_FIELDS.forEach((fieldname) => {
	if (GF_SPEC_PREVIEW_FIELDS.includes(fieldname)) {
		return;
	}
	frappe.ui.form.on("Glass Product Specification", fieldname, (frm) => {
		if (gf_spec_is_locked(frm)) {
			return;
		}
		if (frm.is_new()) {
			gf_refresh_spec_preview(frm);
			return;
		}
		gf_refresh_spec_pricing(frm, { silent: true });
	});
});

frappe.ui.form.on("Glass Spec Operation Pricing", {
	is_overridden(frm, cdt, cdn) {
		gf_sync_operation_pricing_row_rate_state(frm, cdt, cdn);
	},
	rate(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const qty = flt(row.quantity);
		const rate = flt(row.rate);
		const default_rate = flt(row.default_rate);
		frappe.model.set_value(cdt, cdn, "amount", flt(qty * rate, 2));
		if (cint(row.is_overridden)) {
			frappe.model.set_value(cdt, cdn, "source", rate !== default_rate ? "Manual" : "Settings");
		}
	},
});

function gf_spec_is_locked(frm) {
	return frm.doc.docstatus === 1;
}

function gf_spec_is_submitted(frm) {
	return frm.doc.docstatus === 1;
}

function gf_spec_pricing_is_valid(frm) {
	return flt(frm.doc.calculated_amount_per_piece) > 0 && !!frm.doc.item_code_preview;
}

function gf_spec_can_refresh_pricing(frm) {
	if (frm.is_new()) {
		return false;
	}
	if (gf_spec_is_submitted(frm) && !cint(frm.doc.allow_regeneration)) {
		return false;
	}
	return frm.doc.status !== "Cancelled";
}

function gf_spec_can_generate_items(frm) {
	if (frm.is_new() || gf_spec_is_submitted(frm)) {
		return false;
	}
	if (cint(frm.doc.items_generated) && frm.doc.generation_status !== "Regeneration Required") {
		return cint(frm.doc.allow_regeneration);
	}
	return gf_spec_pricing_is_valid(frm);
}

function gf_set_spec_status_indicator(frm) {
	const status = frm.doc.status || "Draft";
	const color = GF_SPEC_STATUS_COLORS[status] || "gray";
	frm.page.set_indicator(status, color);
}

function gf_apply_spec_form_locks(frm) {
	const locked = gf_spec_is_locked(frm);
	const pricing_locked = locked && !cint(frm.doc.allow_regeneration);

	[
		"raw_sheet_item",
		"length_mm",
		"width_mm",
		"qty",
		"polish",
		"bevel",
		"temper",
		"sandblast",
		"laminate",
		"hole_count",
		"special_hole_count",
		"slot_count",
		"special_slot_count",
		"operation_pricing",
		"manual_selling_rate_per_m2",
		"price_override",
		"raw_sheet_rate_per_piece",
		"raw_cost_per_m2",
		"raw_selling_rate_per_m2",
		"raw_cost_per_finished_piece",
	].forEach((fieldname) => {
		frm.set_df_property(fieldname, "read_only", pricing_locked);
	});

	if (locked) {
		frm.set_df_property("allow_regeneration", "read_only", 1);
	}
}

function gf_setup_spec_operation_pricing_grid(frm) {
	const grid = frm.fields_dict.operation_pricing?.grid;
	if (!grid) {
		return;
	}

	const locked = gf_spec_is_locked(frm) && !cint(frm.doc.allow_regeneration);
	grid.toggle_enable("is_overridden", !locked);
	grid.toggle_enable("rate", !locked);

	(frm.doc.operation_pricing || []).forEach((row) => {
		gf_sync_operation_pricing_row_rate_state(frm, row.doctype, row.name);
	});
}

function gf_sync_operation_pricing_row_rate_state(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row) {
		return;
	}

	const grid = frm.fields_dict.operation_pricing?.grid;
	const locked = gf_spec_is_locked(frm) && !cint(frm.doc.allow_regeneration);
	const rate_editable = !locked && cint(row.is_overridden);

	if (grid) {
		grid.toggle_enable("rate", rate_editable, cdn);
	}

	if (!cint(row.is_overridden) && flt(row.rate) !== flt(row.default_rate)) {
		frappe.model.set_value(cdt, cdn, "rate", flt(row.default_rate));
		frappe.model.set_value(cdt, cdn, "source", "Settings");
		frappe.model.set_value(cdt, cdn, "amount", flt(flt(row.quantity) * flt(row.default_rate), 2));
	}
}

function gf_setup_spec_custom_buttons(frm) {
	if (gf_spec_can_refresh_pricing(frm)) {
		frm.add_custom_button(__("Refresh Pricing"), () => {
			gf_refresh_spec_pricing(frm);
		});
	}

	frm.add_custom_button(__("Refresh Preview"), () => {
		gf_refresh_spec_preview(frm);
	});

	if (!frm.is_new() && gf_spec_can_refresh_pricing(frm)) {
		frm.add_custom_button(__("Refresh Operation Rates"), () => {
			gf_refresh_operation_rates(frm);
		}, __("Pricing"));

		frm.add_custom_button(__("Reset Operation Rates to Settings"), () => {
			frappe.confirm(
				__("Reset all operation rates to settings defaults? Manual overrides will be lost."),
				() => gf_reset_operation_rates(frm)
			);
		}, __("Pricing"));
	}

	if (gf_spec_can_generate_items(frm)) {
		const label =
			frm.doc.generation_status === "Regeneration Required"
				? __("Regenerate Items")
				: __("Generate Items");
		frm.add_custom_button(label, () => {
			gf_generate_spec_items(frm);
		});
	}

	if (
		!frm.is_new() &&
		cint(frm.doc.items_generated) &&
		!gf_spec_is_submitted(frm)
	) {
		frm.add_custom_button(__("Reset Generated Links"), () => {
			gf_reset_spec_generated_items(frm);
		});
	}

	if (glass_factory.sync.spec_transaction_buttons_visible(frm)) {
		frm.add_custom_button(__("Create Quotation"), () => {
			glass_factory.sync.show_add_spec_from_spec_dialog(frm, "Quotation");
		}, __("Create"));

		frm.add_custom_button(__("Create Sales Order"), () => {
			glass_factory.sync.show_add_spec_from_spec_dialog(frm, "Sales Order");
		}, __("Create"));
	}

	if (frm.doc.final_item_code || frm.doc.generated_item) {
		frm.add_custom_button(__("Open Final Item"), () => {
			const item_code = frm.doc.final_item_code || frm.doc.generated_item;
			frappe.set_route("Form", "Item", item_code);
		});
	}
}

function gf_pull_from_raw_sheet_item(frm) {
	if (!frm.doc.raw_sheet_item) {
		frm.set_value("glass_type", "");
		frm.set_value("thickness_mm", 0);
		frm.set_value("raw_sheet_length_mm", 0);
		frm.set_value("raw_sheet_width_mm", 0);
		frm.set_value("raw_sheet_area_m2", 0);
		frm.set_value("raw_sheet_rate_per_piece", 0);
		frm.set_value("raw_sheet_selling_rate_per_piece", 0);
		return;
	}

	frm.call({
		doc: frm.doc,
		method: "refresh_preview",
		freeze: false,
	}).then(({ message }) => {
		if (!message) {
			return;
		}
		Object.assign(frm.doc, message);
		frm.refresh_fields([
			...GF_SPEC_RAW_SHEET_FIELDS,
			"area_m2",
			"total_area_m2",
			"item_code_preview",
			"operation_code_preview",
			"technical_summary",
			...GF_SPEC_PRICING_RESULT_FIELDS,
		]);
	});
}

function gf_refresh_spec_preview(frm) {
	if (!frm.doc.raw_sheet_item || !frm.doc.length_mm || !frm.doc.width_mm) {
		return;
	}

	frm.call({
		doc: frm.doc,
		method: "refresh_preview",
		freeze: false,
	}).then(({ message }) => {
		if (!message) {
			return;
		}
		Object.assign(frm.doc, message);
		frm.refresh_fields([
			...GF_SPEC_RAW_SHEET_FIELDS,
			"area_m2",
			"total_area_m2",
			"item_code_preview",
			"operation_code_preview",
			"technical_summary",
			...GF_SPEC_PRICING_RESULT_FIELDS,
		]);
	});
}

function gf_refresh_spec_pricing(frm, options = {}) {
	if (frm.is_new()) {
		return;
	}

	frm.call({
		doc: frm.doc,
		method: "refresh_pricing",
		freeze: !options.silent,
		freeze_message: options.silent ? undefined : __("Refreshing pricing..."),
	}).then(({ message }) => {
		if (!message) {
			return;
		}

		if (!options.silent) {
			const currency = message.currency || frm.doc.currency || "";
			const lines = [
				__("Raw Glass Charge: {0}", [
					format_currency(message.raw_selling_amount_per_finished_piece, currency),
				]),
				__("Processing Charge: {0}", [
					format_currency(message.processing_amount_per_piece, currency),
				]),
				__("Calculated Price / Piece: {0}", [
					format_currency(message.calculated_amount_per_piece, currency),
				]),
				__("Selling Rate / m²: {0}", [
					format_currency(message.selling_rate_per_m2, currency),
				]),
				__("Final Price / Piece: {0}", [format_currency(message.rate_per_piece, currency)]),
				__("Total Price: {0}", [format_currency(message.amount, currency)]),
				__("Profit / Piece: {0} ({1}%)", [
					format_currency(message.gross_profit_per_piece, currency),
					flt(message.gross_profit_percent, 1),
				]),
			];

			if (message.warnings && message.warnings.length) {
				message.warnings.forEach((w) => lines.push(`<span style="color:orange">${w}</span>`));
			}

			frappe.msgprint({
				title: __("Pricing Updated"),
				message: lines.join("<br>"),
				indicator: message.warnings && message.warnings.length ? "orange" : "green",
			});
			frm.reload_doc();
			return;
		}

		Object.assign(frm.doc, message);
		frm.refresh_fields([...GF_SPEC_PRICING_RESULT_FIELDS]);
	});
}

function gf_refresh_operation_rates(frm) {
	if (frm.is_new()) {
		frappe.msgprint(__("Save the document first before refreshing operation rates."));
		return;
	}

	frm.call({
		doc: frm.doc,
		method: "refresh_operation_rates",
		freeze: true,
		freeze_message: __("Refreshing operation rates..."),
	}).then(({ message }) => {
		if (!message) {
			return;
		}
		frappe.show_alert({
			message: __("Operation rates refreshed. Manual overrides preserved."),
			indicator: "green",
		});
		frm.reload_doc();
	});
}

function gf_reset_operation_rates(frm) {
	if (frm.is_new()) {
		return;
	}

	frm.call({
		doc: frm.doc,
		method: "reset_operation_rates_to_settings",
		freeze: true,
		freeze_message: __("Resetting operation rates..."),
	}).then(() => {
		frappe.show_alert({
			message: __("Operation rates reset to settings defaults."),
			indicator: "green",
		});
		frm.reload_doc();
	});
}

function gf_generate_spec_items(frm) {
	frm.call({
		doc: frm.doc,
		method: "generate_items",
		freeze: true,
		freeze_message: __("Generating Items..."),
	}).then(({ message }) => {
		if (!message) {
			return;
		}
		frappe.show_alert({
			message: __("Generated Raw {0}, Cut WIP {1}, Final {2}", [
				message.raw_item_code,
				message.cut_wip_item_code,
				message.final_item_code,
			]),
			indicator: "green",
		});
		frm.reload_doc();
	});
}

function gf_reset_spec_generated_items(frm) {
	frappe.confirm(
		__("Clear generated item links on this specification? Item records will not be deleted."),
		() => {
			frm.call({
				doc: frm.doc,
				method: "reset_generated_items",
				freeze: true,
				freeze_message: __("Resetting generated links..."),
			}).then(() => {
				frappe.show_alert({
					message: __("Generated item links cleared."),
					indicator: "green",
				});
				frm.reload_doc();
			});
		}
	);
}
