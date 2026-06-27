"""Phase 4: Quotation / Sales Order integration for Glass Product Specification."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	_create_spec_transaction_custom_fields()
	frappe.db.commit()


def _spec_transaction_item_fields(*, hidden: bool) -> list[dict]:
	meta = {"hidden": 1, "read_only": 1, "allow_on_submit": 1} if hidden else {"read_only": 1}
	return [
		{
			"fieldname": "gf_from_glass_specification",
			"label": "From Glass Specification",
			"fieldtype": "Check",
			"insert_after": "gf_glass_specification",
			**meta,
		},
		{
			"fieldname": "gf_total_area_m2",
			"label": "Total Area (m²)",
			"fieldtype": "Float",
			"insert_after": "gf_area_m2",
			**meta,
		},
		{
			"fieldname": "gf_selling_rate_per_m2",
			"label": "Selling Rate per m²",
			"fieldtype": "Currency",
			"insert_after": "gf_total_area_m2",
			**meta,
		},
		{
			"fieldname": "gf_calculated_rate_per_m2",
			"label": "Calculated Rate per m²",
			"fieldtype": "Currency",
			"insert_after": "gf_selling_rate_per_m2",
			**meta,
		},
		{
			"fieldname": "gf_manual_selling_rate_per_m2",
			"label": "Manual Selling Rate per m²",
			"fieldtype": "Currency",
			"insert_after": "gf_calculated_rate_per_m2",
			**meta,
		},
		{
			"fieldname": "gf_price_override",
			"label": "Price Override",
			"fieldtype": "Check",
			"insert_after": "gf_manual_selling_rate_per_m2",
			**meta,
		},
		{
			"fieldname": "gf_price_difference_per_m2",
			"label": "Price Difference per m²",
			"fieldtype": "Currency",
			"insert_after": "gf_price_override",
			**meta,
		},
		{
			"fieldname": "gf_rate_per_piece",
			"label": "Spec Rate per Piece",
			"fieldtype": "Currency",
			"insert_after": "gf_price_difference_per_m2",
			**meta,
		},
		{
			"fieldname": "gf_technical_summary",
			"label": "Technical Summary",
			"fieldtype": "Small Text",
			"insert_after": "gf_source_row_id",
			**meta,
		},
		{
			"fieldname": "gf_design_attachment_summary",
			"label": "Design Attachment Summary",
			"fieldtype": "Small Text",
			"insert_after": "gf_technical_summary",
			**meta,
		},
		{
			"fieldname": "gf_transaction_rate_overridden",
			"label": "Transaction Rate Overridden",
			"fieldtype": "Check",
			"insert_after": "gf_design_attachment_summary",
			**meta,
		},
	]


def _create_spec_transaction_custom_fields():
	create_custom_fields(
		{
			"Quotation Item": _spec_transaction_item_fields(hidden=True),
			"Sales Order Item": _spec_transaction_item_fields(hidden=False),
		},
		update=True,
	)
