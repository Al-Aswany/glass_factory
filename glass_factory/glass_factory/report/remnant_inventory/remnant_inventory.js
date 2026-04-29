frappe.query_reports["Remnant Inventory"] = {
	filters: [
		{
			fieldname: "parent_item",
			label: __("Parent Item"),
			fieldtype: "Link",
			options: "Item",
			get_query() {
				return { filters: { item_group: ["like", "%Glass%"] } };
			}
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse"
		},
		{
			fieldname: "min_area_m2",
			label: __("Min Area (m²)"),
			fieldtype: "Float",
			default: 0
		},
		{
			fieldname: "include_zero_stock",
			label: __("Include Zero Stock"),
			fieldtype: "Check",
			default: 0
		}
	],

	formatter(value, row, column, data, default_formatter) {
		const out = default_formatter(value, row, column, data);
		if (column.fieldname === "size_label" && value) {
			const palette = {
				"S": "#7f8c8d", "M": "#3498db",
				"L": "#27ae60", "XL": "#e74c3c"
			};
			const color = palette[value] || "#95a5a6";
			return `<span style="
				display:inline-block; padding:2px 8px; border-radius:4px;
				font-weight:600; font-size:11px;
				background:${color}22; color:${color};">${value}</span>`;
		}
		if (column.fieldname === "qty_on_hand" && data && data.qty_on_hand <= 0) {
			return `<span style="color:#e74c3c">${out}</span>`;
		}
		return out;
	}
};
