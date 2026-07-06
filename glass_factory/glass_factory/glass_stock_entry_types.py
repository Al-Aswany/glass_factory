"""Glass Factory Stock Entry Type constants and idempotent seeding."""

from __future__ import annotations

import frappe

GLASS_CUTTING_REPACK_STOCK_ENTRY_TYPE = "Glass Cutting Repack"
GLASS_PROCESSING_REPACK_STOCK_ENTRY_TYPE = "Glass Processing Repack"

GLASS_REPACK_STOCK_ENTRY_TYPES = frozenset(
	{
		"Repack",
		GLASS_CUTTING_REPACK_STOCK_ENTRY_TYPE,
		GLASS_PROCESSING_REPACK_STOCK_ENTRY_TYPE,
	}
)

_GLASS_STOCK_ENTRY_TYPE_SPECS = (
	(GLASS_CUTTING_REPACK_STOCK_ENTRY_TYPE, "Repack"),
	(GLASS_PROCESSING_REPACK_STOCK_ENTRY_TYPE, "Repack"),
)


def ensure_glass_stock_entry_types() -> None:
	"""Create or update the glass-specific Repack Stock Entry Types."""
	for name, purpose in _GLASS_STOCK_ENTRY_TYPE_SPECS:
		if frappe.db.exists("Stock Entry Type", name):
			if frappe.db.get_value("Stock Entry Type", name, "purpose") != purpose:
				frappe.db.set_value("Stock Entry Type", name, "purpose", purpose, update_modified=False)
			continue

		doc = frappe.new_doc("Stock Entry Type")
		doc.purpose = purpose
		doc.is_standard = 0
		doc.insert(set_name=name, ignore_permissions=True)

	frappe.db.commit()
