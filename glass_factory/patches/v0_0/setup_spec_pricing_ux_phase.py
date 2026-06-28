"""Setup Glass Product Specification UX and Pricing Upgrade.

Adds raw buying/selling price split, operation pricing child table, and
margin fields to Glass Product Specification. No data migration required;
all new fields default to 0 / empty.
"""

import frappe


def execute():
    frappe.db.commit()
