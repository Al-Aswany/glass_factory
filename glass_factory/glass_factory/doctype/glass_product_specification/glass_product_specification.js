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

frappe.ui.form.on("Glass Product Specification", {
	refresh(frm) {
		const color_map = {
			Draft: "gray",
			Ready: "blue",
			Used: "green",
			Cancelled: "red",
		};
		frm.page.set_indicator(frm.doc.status, color_map[frm.doc.status] || "gray");

		if (!frm.is_new()) {
			frm.add_custom_button(__("Refresh Pricing"), () => {
				gf_refresh_spec_pricing(frm);
			});

			frm.add_custom_button(__("Refresh Operation Rates"), () => {
				gf_refresh_operation_rates(frm);
			}, __("Pricing"));

			frm.add_custom_button(__("Reset Operation Rates to Settings"), () => {
				frappe.confirm(
					__("Reset all operation rates to settings defaults? Manual overrides will be lost."),
					() => gf_reset_operation_rates(frm)
				);
			}, __("Pricing"));

			if (!frm.doc.items_generated) {
				frm.add_custom_button(__("Generate Items"), () => {
					gf_generate_spec_items(frm);
				});
			}

			if (frm.doc.generation_status === "Regeneration Required") {
				frm.add_custom_button(__("Regenerate Items"), () => {
					gf_generate_spec_items(frm);
				});
			}

			if (frm.doc.items_generated) {
				frm.add_custom_button(__("Reset Generated Links"), () => {
					gf_reset_spec_generated_items(frm);
				});
			}

			if (glass_factory.sync.spec_transaction_buttons_visible(frm)) {
				frm.add_custom_button(__("Add to Quotation"), () => {
					glass_factory.sync.show_add_spec_from_spec_dialog(frm, "Quotation");
				}, __("Create"));

				frm.add_custom_button(__("Add to Sales Order"), () => {
					glass_factory.sync.show_add_spec_from_spec_dialog(frm, "Sales Order");
				}, __("Create"));
			}
		}

		frm.add_custom_button(__("Refresh Preview"), () => {
			gf_refresh_spec_preview(frm);
		});

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
		if (frm.is_new()) {
			gf_refresh_spec_preview(frm);
			return;
		}
		gf_refresh_spec_pricing(frm, { silent: true });
	});
});

frappe.ui.form.on("Glass Spec Operation Pricing", {
	rate(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const qty = flt(row.quantity);
		const rate = flt(row.rate);
		const default_rate = flt(row.default_rate);
		frappe.model.set_value(cdt, cdn, "amount", flt(qty * rate, 2));
		frappe.model.set_value(cdt, cdn, "is_overridden", rate !== default_rate ? 1 : 0);
		frappe.model.set_value(cdt, cdn, "source", rate !== default_rate ? "Manual" : "Settings");
	},
});

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
				__("Raw Selling / Piece: {0}", [
					format_currency(message.raw_selling_amount_per_finished_piece, currency),
				]),
				__("Processing Amount / Piece: {0}", [
					format_currency(message.processing_amount_per_piece, currency),
				]),
				__("Calculated Rate / m²: {0}", [
					format_currency(message.calculated_rate_per_m2, currency),
				]),
				__("Final Selling Rate / m²: {0}", [
					format_currency(message.selling_rate_per_m2, currency),
				]),
				__("Rate / Piece: {0}", [format_currency(message.rate_per_piece, currency)]),
				__("Total Amount: {0}", [format_currency(message.amount, currency)]),
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
		const currency = message.currency || frm.doc.currency || "";
		const lines = [
			__("Processing Amount / Piece: {0}", [
				format_currency(message.processing_amount_per_piece, currency),
			]),
			__("Rate / Piece: {0}", [format_currency(message.rate_per_piece, currency)]),
		];
		if (message.warnings && message.warnings.length) {
			message.warnings.forEach((w) => lines.push(`<span style="color:orange">${w}</span>`));
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
