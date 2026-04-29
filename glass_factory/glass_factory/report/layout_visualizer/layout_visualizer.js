frappe.query_reports["Layout Visualizer"] = {
	filters: [
		{
			fieldname: "cutting_job",
			label: __("Cutting Job"),
			fieldtype: "Link",
			options: "Cutting Job",
			reqd: 1,
			default: frappe.utils.get_url_arg("cutting_job") || ""
		}
	],

	onload(report) {
		report.page.add_inner_button(__("Print Layout"), () => {
			const wrapper = report.page.main.find(".report-message").get(0);
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
	}
};
