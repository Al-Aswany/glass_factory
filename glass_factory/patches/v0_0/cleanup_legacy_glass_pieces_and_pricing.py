"""
Controlled cleanup — Phase B + C combined patch.

Phase B: Remove the legacy per-m² pricing field values from the Glass Factory
Settings Single document.  The field definitions were removed from the DocType
JSON, but Frappe does not drop Single-doc rows, so the values can linger in
`tabSingles`.  This patch explicitly deletes them.

Phase C: Hide the `glass_pieces` custom Table field on Quotation and Sales Order
so the old entry path is no longer visible on new or existing documents.
Old rows in `tabQuotation Glass Piece` are preserved (the DocType stays).
"""

import frappe


_LEGACY_PRICING_FIELDS = [
    "default_item_group",
    "polish_rate_per_m2",
    "bevel_rate_per_m2",
    "holes_rate_per_m2",
    "slots_rate_per_m2",
    "temper_rate_per_m2",
    "sandblast_rate_per_m2",
    "laminate_rate_per_m2",
    "polish_cost_per_m2",
    "bevel_cost_per_m2",
    "holes_cost_per_m2",
    "slots_cost_per_m2",
    "temper_cost_per_m2",
    "sandblast_cost_per_m2",
    "laminate_cost_per_m2",
]


def execute():
    # Phase B: wipe stale Single-doc rows for removed fields
    if frappe.db.table_exists("Singles"):
        for field in _LEGACY_PRICING_FIELDS:
            frappe.db.delete(
                "Singles",
                {"doctype": "Glass Factory Settings", "field": field},
            )

    # Phase C: hide glass_pieces Table field on Quotation and Sales Order
    for parent_doctype in ("Quotation", "Sales Order"):
        name = frappe.db.get_value(
            "Custom Field",
            {"dt": parent_doctype, "fieldname": "glass_pieces"},
            "name",
        )
        if name:
            frappe.db.set_value("Custom Field", name, "hidden", 1, update_modified=False)

    frappe.db.commit()
