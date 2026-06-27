frappe.ui.form.on("Cutting Job", {
	refresh(frm) {
		frm.trigger("set_status_indicator");
		frm.trigger("set_source_sheet_queries");
		frm.trigger("add_custom_buttons");
	},

	set_source_sheet_queries(frm) {
		frm.set_query("item_code", "source_sheets", () => ({
			filters: {
				gf_glass_item_role: ["in", ["Raw Sheet", "Remnant"]],
				has_batch_no: 1,
				disabled: 0,
			},
		}));

		frm.set_query("batch_no", "source_sheets", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			if (!row.item_code || !row.warehouse) {
				return { filters: { name: ["in", []] } };
			}
			return {
				query: "glass_factory.glass_factory.batch_utils.get_source_sheet_batch_no",
				filters: {
					item_code: row.item_code,
					warehouse: row.warehouse,
					source_role: row.source_role,
					posting_date: frappe.datetime.get_today(),
				},
			};
		});
	},

	set_status_indicator(frm) {
		const color_map = {
			"Draft": "gray",
			"Planned": "blue",
			"Ready for Cutting": "orange",
			"Cutting In Progress": "orange",
			"Cut Stock Posted": "green",
			"Processing Started": "green",
			"Completed": "green",
			"Cancelled": "red",
		};
		frm.page.set_indicator(frm.doc.status, color_map[frm.doc.status] || "gray");
	},

	add_custom_buttons(frm) {
		if (frm.doc.docstatus !== 1) return;

		frm.add_custom_button(__("Export Optimization Job"), () => {
			frappe.call({
				method: "glass_factory.glass_factory.glass_optimizer.export_optimization_job",
				args: { cutting_job_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Exporting optimization job..."),
				callback({ message }) {
					frappe.show_alert({ message: message.message, indicator: "green" });
					if (message.file_url) {
						window.open(message.file_url);
					}
					frm.reload_doc();
				},
			});
		}, __("Glass Optimizer"));

		frm.add_custom_button(__("Import Optimization Result"), () => {
			const input = document.createElement("input");
			input.type = "file";
			input.accept = ".json,application/json";
			input.addEventListener("change", () => {
				const file = input.files[0];
				if (!file) return;
				const reader = new FileReader();
				reader.addEventListener("load", () => {
					frappe.call({
						method: "glass_factory.glass_factory.glass_optimizer.import_optimization_result",
						args: {
							cutting_job_name: frm.doc.name,
							json_text: reader.result,
						},
						freeze: true,
						freeze_message: __("Importing optimization result..."),
						callback({ message }) {
							frappe.show_alert({
								message: __("Optimization result imported successfully."),
								indicator: "green",
							});
							frm.reload_doc();
						},
						error(err) {
							const detail =
								(err.responseJSON && err.responseJSON.exc_type
									? err.responseJSON.exception || err.responseJSON.exc_type
									: null) ||
								__("Could not import optimization result. Check Error Log for details.");
							frappe.msgprint({
								title: __("Import Failed"),
								message: detail,
								indicator: "red",
							});
							frm.reload_doc();
						},
					});
				});
				reader.readAsText(file);
			});
			input.click();
		}, __("Glass Optimizer"));

		if (!frm.doc.pieces || !frm.doc.pieces.length) {
			frm.add_custom_button(__("Pull Sales Orders"), () => {
				frm.call("pull_from_sales_orders").then(() => frm.reload_doc());
			}, __("Actions"));
			return;
		}

		if (!frm.doc.linked_stock_entry) {
			frm.add_custom_button(__("Create Cutting Stock Movement"), () => {
				frm.call("create_repack_stock_entry").then(() => frm.reload_doc());
			}, __("Actions"));
			return;
		}

		if (frm.doc.linked_stock_entry && frm.doc.status !== "Cut Stock Posted" && frm.doc.status !== "Processing Started") {
			frm.add_custom_button(__("Submit Cutting Stock Movement"), () => {
				frm.call("submit_repack_stock_entry").then(() => frm.reload_doc());
			}, __("Actions"));
			return;
		}

		if (frm.doc.status === "Cut Stock Posted" || frm.doc.status === "Processing Started") {
			frm.add_custom_button(__("Start Processing"), () => {
				frm.call("start_processing").then(({ message }) => {
					const processing_job = message?.processing_job;
					if (processing_job) {
						frappe.set_route("Form", "Glass Processing Job", processing_job);
					} else {
						frm.reload_doc();
					}
				});
			}, __("Actions"));
		}

		frappe.db.get_single_value("Glass Factory Settings", "enable_cop").then((enabled) => {
			if (!enabled) return;
			frm.add_custom_button(__("Generate COP Files"), () => {
				frm.call("generate_cop_files").then(() => frm.reload_doc());
			}, __("COP"));
			frm.add_custom_button(__("Process COP Result"), () => {
				frm.call("process_result").then(() => frm.reload_doc());
			}, __("COP"));
		});
	},
});

frappe.ui.form.on("Cutting Job Sales Order", {
	sales_order(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.sales_order) return;
		frappe.db.get_value("Sales Order", row.sales_order, ["customer", "delivery_date"]).then(({ message }) => {
			if (!message) return;
			frappe.model.set_value(cdt, cdn, "customer", message.customer);
			frappe.model.set_value(cdt, cdn, "delivery_date", message.delivery_date);
		});
	},
});

frappe.ui.form.on("Cutting Job Source Sheet", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) {
			frappe.model.set_value(cdt, cdn, "batch_no", "");
			return;
		}
		frappe.call({
			method: "glass_factory.glass_factory.item_resolver.get_item_glass_meta",
			args: { item_code: row.item_code },
		}).then(({ message }) => {
			if (!message) return;
			frappe.model.set_value(cdt, cdn, "source_role", message.gf_glass_item_role || "Raw Sheet");
			frappe.model.set_value(cdt, cdn, "length_mm", message.gf_length_mm || 0);
			frappe.model.set_value(cdt, cdn, "width_mm", message.gf_width_mm || 0);
			frappe.model.set_value(cdt, cdn, "batch_no", "");
		});
	},

	warehouse(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "batch_no", "");
	},

	source_role(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "batch_no", "");
	},
});
