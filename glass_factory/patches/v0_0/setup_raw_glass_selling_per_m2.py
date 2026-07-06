"""Move raw glass selling pricing from per-sheet to per-m² (Item Price, UOM = Square Meter).

- Adds the gf_sheet_area_m2 custom field on Item (via create_glass_custom_fields).
- Backfills gf_sheet_area_m2 on existing glass Items that already have length/width.
- Does NOT touch or delete any existing Item Price or Glass Product Specification data.
  Legacy per-sheet selling values on raw_sheet_selling_rate_per_piece are left as-is for
  historical specs; new pricing calculated by refresh_pricing() no longer reads them.
"""

import frappe
from frappe.utils import flt

from glass_factory.install import create_glass_custom_fields


def execute():
	create_glass_custom_fields()
	_backfill_sheet_area()
	frappe.db.commit()


def _backfill_sheet_area():
	if not frappe.get_meta("Item").has_field("gf_sheet_area_m2"):
		return

	rows = frappe.get_all(
		"Item",
		filters={"gf_length_mm": [">", 0], "gf_width_mm": [">", 0]},
		fields=["name", "gf_length_mm", "gf_width_mm", "gf_sheet_area_m2"],
		limit=100000,
	)

	updated = 0
	for row in rows:
		area_m2 = flt((flt(row.gf_length_mm) * flt(row.gf_width_mm)) / 1_000_000, 6)
		if area_m2 and flt(row.gf_sheet_area_m2) != area_m2:
			frappe.db.set_value("Item", row.name, "gf_sheet_area_m2", area_m2, update_modified=False)
			updated += 1

	frappe.logger("glass_factory").info(
		f"Backfilled gf_sheet_area_m2 on {updated} glass Item(s)."
	)
