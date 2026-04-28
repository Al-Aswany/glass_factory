frappe.ui.form.on("Cutting Job", {
	refresh(frm) {
		frm.trigger("set_status_indicator");
		frm.trigger("add_custom_buttons");
	},

	set_status_indicator(frm) {
		const color_map = {
			"Draft": "gray",
			"Files Generated": "blue",
			"Awaiting Optimization": "orange",
			"Result Uploaded": "yellow",
			"Completed": "green",
		};
		frm.page.set_indicator(frm.doc.status, color_map[frm.doc.status] || "gray");
	},

	add_custom_buttons(frm) {
		if (frm.doc.docstatus === 2) return;

		if (!frm.is_new() && frm.doc.status === "Draft") {
			frm.add_custom_button(__("Pull Pieces from Sales Orders"), () => {
				frm.call({
					method: "pull_pieces_from_sales_orders",
					freeze: true,
					freeze_message: __("Pulling pieces..."),
					callback(r) {
						if (r.message) {
							frappe.show_alert({ message: r.message.message, indicator: "green" });
							frm.refresh();
						}
					},
				});
			}, __("Actions"));

			frm.add_custom_button(__("Generate COP Files"), () => {
				frm.call({
					method: "generate_cop_files",
					freeze: true,
					freeze_message: __("Generating files..."),
					callback(r) {
						if (r.message) {
							frappe.show_alert({ message: r.message.message, indicator: "green" });
							frm.refresh();
						}
					},
				});
			}, __("Actions"));
		}

		if (!frm.is_new() && frm.doc.status === "Awaiting Optimization") {
			frm.add_custom_button(__("Process Result"), () => {
				frm.call({
					method: "process_result",
					freeze: true,
					freeze_message: __("Processing result files..."),
					callback(r) {
						if (!r.message) return;
						const payload = r.message;
						const warnings = payload.warnings || [];
						const msg = `
							<b>Pieces produced:</b> ${payload.pieces_produced}<br>
							<b>Remnants created:</b> ${payload.remnants_created}<br>
							<b>Scrap area (m²):</b> ${(payload.scrap_m2 || 0).toFixed(4)}
							${warnings.length ? "<br><br><b>Warnings:</b><br>" + warnings.join("<br>") : ""}
						`;
						frappe.confirm(
							msg + "<br><br>Confirm and post stock entries?",
							() => {
								frm.call({
									method: "confirm_and_post",
									args: { parsed_payload: JSON.stringify(payload) },
									freeze: true,
									freeze_message: __("Posting stock entries..."),
									callback(r2) {
										if (r2.message) {
											frappe.show_alert({ message: r2.message.message, indicator: "green" });
											frm.refresh();
										}
									},
								});
							}
						);
					},
				});
			}, __("Actions"));
		}

		if (!frm.is_new() && frm.doc.status === "Result Uploaded") {
			frm.add_custom_button(__("Confirm & Post"), () => {
				frappe.confirm(
					__("Confirm and post stock entries and delivery notes?"),
					() => {
						frm.call({
							method: "confirm_and_post",
							freeze: true,
							freeze_message: __("Posting..."),
							callback(r) {
								if (r.message) {
									frappe.show_alert({ message: r.message.message, indicator: "green" });
									frm.refresh();
								}
							},
						});
					}
				);
			}, __("Actions"));
		}
	},
});
