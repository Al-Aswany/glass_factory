const GF_SPEC_PREVIEW_FIELDS = [
	"glass_type",
	"thickness_mm",
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
		}

		frm.add_custom_button(__("Refresh Preview"), () => {
			gf_refresh_spec_preview(frm);
		});

		frm.set_query("raw_sheet_item", () => ({
			filters: {
				gf_glass_item_role: ["in", ["Raw Sheet", "Remnant"]],
			},
		}));
	},

	raw_sheet_item(frm) {
		gf_refresh_spec_preview(frm);
	},
});

GF_SPEC_PREVIEW_FIELDS.forEach((fieldname) => {
	frappe.ui.form.on("Glass Product Specification", fieldname, (frm) => {
		gf_refresh_spec_preview(frm);
	});
});

function gf_refresh_spec_preview(frm) {
	if (!frm.doc.glass_type || !frm.doc.thickness_mm || !frm.doc.length_mm || !frm.doc.width_mm) {
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
			"area_m2",
			"total_area_m2",
			"raw_sheet_length_mm",
			"raw_sheet_width_mm",
			"raw_sheet_area_m2",
			"item_code_preview",
			"operation_code_preview",
			"technical_summary",
		]);
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
