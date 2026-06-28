const GF_ITEM_CODE_HELP = __(
	"Glass items must follow GLS-{TYPE}-{THICKNESS}MM-{LENGTH}X{WIDTH} (example: GLS-CLEAR-8MM-3210X2250)."
);

frappe.ui.form.on("Item", {
	refresh(frm) {
		gf_toggle_glass_dimension_fields(frm);
		gf_set_allowed_glass_types_help(frm);
		gf_add_build_code_button(frm);
		if (frm.doc.item_code) {
			gf_sync_glass_fields_from_code(frm, { silent: true });
		}
	},

	item_code(frm) {
		gf_sync_glass_fields_from_code(frm);
	},

	gf_glass_item_role(frm) {
		gf_toggle_glass_dimension_fields(frm);
		gf_add_build_code_button(frm);
	},
});

function gf_toggle_glass_dimension_fields(frm) {
	const is_glass = !!frm.doc.gf_glass_item_role;
	const fields = ["gf_base_glass_type", "gf_thickness_mm", "gf_length_mm", "gf_width_mm"];
	fields.forEach((fieldname) => {
		frm.toggle_display(fieldname, is_glass);
	});
}

function gf_add_build_code_button(frm) {
	const role = frm.doc.gf_glass_item_role;
	if (role && role !== "Raw Sheet") {
		return;
	}

	frm.add_custom_button(__("Build Raw Sheet Code"), () => {
		gf_show_build_raw_code_dialog(frm);
	}, __("Glass"));
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

function gf_set_allowed_glass_types_help(frm) {
	frappe.call({
		method: "glass_factory.glass_factory.item_resolver.get_allowed_glass_types",
	}).then(({ message }) => {
		const allowed = (message || []).join(", ");
		const description = allowed
			? `${GF_ITEM_CODE_HELP} ${__("Allowed glass types")}: ${allowed}.`
			: GF_ITEM_CODE_HELP;
		frm.set_df_property("gf_base_glass_type", "description", description);
		frm.set_df_property(
			"gf_glass_item_role",
			"description",
			__(
				"Set for glass stock items. Dimensions are auto-filled from the GLS-* item code. Use Build Raw Sheet Code to generate a raw sheet code."
			)
		);
	});
}

function gf_show_build_raw_code_dialog(frm) {
	frappe.call({
		method: "glass_factory.glass_factory.item_resolver.get_allowed_glass_types",
	}).then(({ message: allowed_types }) => {
		const types = allowed_types || [];
		if (!types.length) {
			frappe.msgprint({
				title: __("Glass Factory Setup Required"),
				indicator: "orange",
				message: __("Configure Allowed Glass Types in Glass Factory Settings first."),
			});
			return;
		}

		const d = new frappe.ui.Dialog({
			title: __("Build Raw Sheet Item Code"),
			fields: [
				{
					fieldname: "help",
					fieldtype: "HTML",
					options: `<p class="text-muted small">${GF_ITEM_CODE_HELP}</p>`,
				},
				{
					fieldname: "glass_type",
					fieldtype: "Select",
					label: __("Glass Type"),
					options: types.join("\n"),
					reqd: 1,
					default: frm.doc.gf_base_glass_type || types[0],
				},
				{
					fieldname: "thickness_mm",
					fieldtype: "Float",
					label: __("Thickness (mm)"),
					reqd: 1,
					default: frm.doc.gf_thickness_mm || "",
				},
				{
					fieldname: "length_mm",
					fieldtype: "Float",
					label: __("Length (mm)"),
					reqd: 1,
					default: frm.doc.gf_length_mm || "",
				},
				{
					fieldname: "width_mm",
					fieldtype: "Float",
					label: __("Width (mm)"),
					reqd: 1,
					default: frm.doc.gf_width_mm || "",
				},
				{
					fieldname: "item_code_preview",
					fieldtype: "Data",
					label: __("Item Code Preview"),
					read_only: 1,
				},
			],
			primary_action_label: __("Apply to Item"),
			primary_action(values) {
				gf_apply_raw_item_code_preview(frm, d, values);
			},
		});

		const update_preview = () => {
			const values = d.get_values();
			if (!values.glass_type || !values.thickness_mm || !values.length_mm || !values.width_mm) {
				d.set_value("item_code_preview", "");
				return;
			}

			frappe.call({
				method: "glass_factory.glass_factory.item_resolver.preview_raw_item_code",
				args: {
					glass_type: values.glass_type,
					thickness_mm: values.thickness_mm,
					length_mm: values.length_mm,
					width_mm: values.width_mm,
				},
				async: true,
			}).then(({ message }) => {
				d.set_value("item_code_preview", message && message.valid ? message.item_code : "");
			});
		};

		["glass_type", "thickness_mm", "length_mm", "width_mm"].forEach((fieldname) => {
			d.fields_dict[fieldname].$input.on("change", update_preview);
		});

		d.show();
		update_preview();
	});
}

function gf_apply_raw_item_code_preview(frm, dialog, values) {
	frappe.call({
		method: "glass_factory.glass_factory.item_resolver.preview_raw_item_code",
		args: {
			glass_type: values.glass_type,
			thickness_mm: values.thickness_mm,
			length_mm: values.length_mm,
			width_mm: values.width_mm,
		},
		freeze: true,
		freeze_message: __("Building item code..."),
	}).then(({ message }) => {
		if (!message || !message.valid || !message.item_code) {
			frappe.msgprint({
				title: __("Invalid Dimensions"),
				indicator: "orange",
				message: __("Enter glass type, thickness, length, and width greater than zero."),
			});
			return;
		}

		const apply = () => {
			gf_set_built_item_code(frm, message);
			dialog.hide();
		};

		if (frm.doc.item_code && frm.doc.item_code !== message.item_code) {
			frappe.confirm(
				__("Replace Item Code <b>{0}</b> with <b>{1}</b>?", [frm.doc.item_code, message.item_code]),
				apply
			);
			return;
		}

		apply();
	});
}

function gf_set_built_item_code(frm, preview) {
	frm.set_value("item_code", preview.item_code);
	if (!frm.doc.item_name || frm.doc.item_name === frm.doc.item_code) {
		frm.set_value("item_name", preview.item_code);
	}
	if (!frm.doc.gf_glass_item_role) {
		frm.set_value("gf_glass_item_role", preview.glass_item_role || "Raw Sheet");
	}
	if (!frm.doc.item_group && preview.item_group) {
		frm.set_value("item_group", preview.item_group);
	}
	frm.set_value("gf_base_glass_type", preview.gf_base_glass_type || "");
	frm.set_value("gf_thickness_mm", preview.gf_thickness_mm || 0);
	frm.set_value("gf_length_mm", preview.gf_length_mm || 0);
	frm.set_value("gf_width_mm", preview.gf_width_mm || 0);
}
