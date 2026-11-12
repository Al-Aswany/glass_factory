"""Mark operation cost rates that were already entered as specified.

A positive cost rate cannot be the seeded default of zero, so those rows are
ticked. A zero stays unticked until someone ticks Cost Rate Set, which is how
an intentional zero is distinguished from the seeded default.
"""

import frappe


def execute():
	if not frappe.db.table_exists("Glass Operation Rate"):
		return
	if not frappe.db.has_column("Glass Operation Rate", "cost_rate_set"):
		return
	frappe.db.sql(
		"""update `tabGlass Operation Rate`
		set cost_rate_set = 1
		where ifnull(cost_rate, 0) > 0 and ifnull(cost_rate_set, 0) = 0"""
	)
