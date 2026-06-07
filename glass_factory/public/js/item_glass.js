const GF_ITEM_CODE_HELP = __(
	"Glass items must follow GLS-{TYPE}-{THICKNESS}MM-{LENGTH}X{WIDTH} (example: GLS-CLEAR-8MM-3210X2250)."
);

frappe.ui.form.on("Item", {
	refresh(frm) {
		gf_toggle_glass_dimension_fields(frm);
		if (frm.doc.item_code) {
			gf_sync_glass_fields_from_code(frm, { silent: true });
		}
	},

	item_code(frm) {
		gf_sync_glass_fields_from_code(frm);
	},

	gf_glass_item_role(frm) {
		gf_toggle_glass_dimension_fields(frm);
	},
});

function gf_toggle_glass_dimension_fields(frm) {
	const is_glass = !!frm.doc.gf_glass_item_role;
	const fields = ["gf_base_glass_type", "gf_thickness_mm", "gf_length_mm", "gf_width_mm"];
	fields.forEach((fieldname) => {
		frm.toggle_display(fieldname, is_glass);
	});
}

function gf_sync_glass_fields_from_code(frm, opts = {}) {
	if (!frm.doc.item_code) {
		return;
	}

	frappe.call({
		method: "glass_factory.glass_factory.item_resolver.get_item_glass_meta",
		args: { item_code: frm.doc.item_code },
		async: true,
	}).then(({ message }) => {
		if (!message) return;

		frm.set_value("gf_base_glass_type", message.gf_base_glass_type || "");
		frm.set_value("gf_thickness_mm", message.gf_thickness_mm || 0);
		frm.set_value("gf_length_mm", message.gf_length_mm || 0);
		frm.set_value("gf_width_mm", message.gf_width_mm || 0);

		if (!frm.doc.gf_glass_item_role && message.gf_glass_item_role) {
			frm.set_value("gf_glass_item_role", message.gf_glass_item_role);
		}

		if (!opts.silent && frm.doc.gf_glass_item_role && !message.parsed) {
			frappe.msgprint({
				title: __("Invalid Glass Item Code"),
				indicator: "orange",
				message: GF_ITEM_CODE_HELP,
			});
		}
	});
}
