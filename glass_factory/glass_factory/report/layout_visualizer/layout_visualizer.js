frappe.query_reports["Layout Visualizer"] = {
	filters: [
		{
			fieldname: "cutting_job",
			label: __("Cutting Job"),
			fieldtype: "Link",
			options: "Cutting Job",
			reqd: 1,
			default:
				frappe.utils.get_url_arg("cutting_job") ||
				(frappe.route_options && frappe.route_options.cutting_job) ||
				"",
		},
	],

	onload(report) {
		report._layout_html = null;
		report._layout_node = null;

		const render_layout_html = (html) => {
			if (!html || !html.includes("lv-")) {
				return false;
			}

			report.hide_status();
			report.toggle_message(false);

			if (!report._layout_node) {
				report._layout_node = $('<div class="report-message layout-visualizer-message"></div>')
					.hide()
					.insertBefore(report.$report);
			}

			report._layout_node.html(html).show();
			report.$report.show();
			report.toggle_print_buttons(true);
			return true;
		};

		const origRefresh = report.refresh.bind(report);
		const origCall = frappe.call.bind(frappe);

		report.refresh = function (have_filters_changed) {
			report._layout_html = null;
			if (report._layout_node) {
				report._layout_node.hide().empty();
			}

			frappe.call = function (opts) {
				if (
					opts.method === "frappe.desk.query_report.run" &&
					opts.args?.report_name === "Layout Visualizer"
				) {
					const userCallback = opts.callback;
					opts.callback = function (response) {
						report._layout_html = response?.message?.message || null;
						return userCallback?.(response);
					};
				}
				return origCall(opts);
			};

			return origRefresh(have_filters_changed).finally(() => {
				frappe.call = origCall;
				render_layout_html(report._layout_html);
			});
		};

		const origToggleNothing = report.toggle_nothing_to_show.bind(report);
		report.toggle_nothing_to_show = function (flag) {
			if (flag && render_layout_html(report._layout_html)) {
				return;
			}
			origToggleNothing(flag);
		};

		report.page.add_inner_button(__("Print Layout"), () => {
			const wrapper =
				report._layout_node?.get(0) ||
				report.page.main.find(".report-message").get(0);
			if (!wrapper) {
				frappe.msgprint(__("Nothing to print yet."));
				return;
			}
			const win = window.open("", "_blank", "width=1100,height=800");
			win.document.write(`
				<html><head><title>${__("Cutting Layout")}</title>
				<style>
					body { font-family: Inter, system-ui, sans-serif; margin: 24px; color:#1f2d3d; }
					.lv-card { page-break-inside: avoid; margin-bottom: 18px; }
				</style>
				</head><body>${wrapper.innerHTML}</body></html>
			`);
			win.document.close();
			setTimeout(() => win.print(), 300);
		});
	},
};
