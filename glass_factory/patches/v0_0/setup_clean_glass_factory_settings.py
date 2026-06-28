"""
Reorganize Glass Factory Settings to the clean Phase 3+ layout.

Changes applied by this patch (idempotent):
- Seeds cost_rate = 0 for existing operation rate rows that lack it.
- Migrates old per-m² values for Temper, Sandblast, and Laminate into the
  operation_rates table (only for rows with rate = 0, preserving manual values).
- Polish, Bevel, Holes, and Slots are NOT migrated because their old labels
  (per m²) do not match their new pricing basis (Per Edge Meter / Per Unit).
  Business approval is required before migrating those values.
- default_buying_price_list and default_selling_price_list columns are added
  via the DocType JSON sync that runs before this patch.
- cost_rate column on Glass Operation Rate is also added via DocType JSON sync.
- Deprecated per-m² fields are hidden via the DocType JSON (no data deletion).
"""

import frappe
from frappe.utils import flt

# Safe migrations: old label was per m², new pricing basis is also Per Square Meter
_SAFE_RATE_MIGRATIONS = {
	"Temper": "temper_rate_per_m2",
	"Sandblast": "sandblast_rate_per_m2",
	"Laminate": "laminate_rate_per_m2",
}
_SAFE_COST_MIGRATIONS = {
	"Temper": "temper_cost_per_m2",
	"Sandblast": "sandblast_cost_per_m2",
	"Laminate": "laminate_cost_per_m2",
}


def execute():
	if not frappe.db.exists("DocType", "Glass Factory Settings"):
		return

	settings = frappe.get_single("Glass Factory Settings")
	_seed_cost_rate_for_existing_rows(settings)
	_migrate_area_operation_rates(settings)
	settings.save(ignore_permissions=True)
	frappe.db.commit()


def _seed_cost_rate_for_existing_rows(settings) -> None:
	"""Set cost_rate = 0 for rows that were created before this field existed."""
	for row in settings.get("operation_rates") or []:
		if row.get("cost_rate") is None:
			row.cost_rate = 0


def _migrate_area_operation_rates(settings) -> None:
	"""Copy legacy per-m² rates for Temper/Sandblast/Laminate into operation_rates.

	Only rows with rate = 0 are updated, so manually entered rates in the
	operation_rates table are never overwritten.
	"""
	# Index existing rows by (operation, currency, pricing_basis) for O(1) lookup
	existing: dict[tuple, object] = {}
	for row in settings.get("operation_rates") or []:
		if row.operation and row.currency and row.pricing_basis:
			key = (row.operation, row.currency, row.pricing_basis)
			existing[key] = row

	for operation, rate_field in _SAFE_RATE_MIGRATIONS.items():
		cost_field = _SAFE_COST_MIGRATIONS[operation]
		legacy_rate = flt(settings.get(rate_field))
		legacy_cost = flt(settings.get(cost_field))

		if legacy_rate <= 0 and legacy_cost <= 0:
			continue

		# Apply to every currency that already has a row in the table
		for key, row in existing.items():
			op, _currency, basis = key
			if op != operation or basis != "Per Square Meter":
				continue

			if flt(row.rate) == 0 and legacy_rate > 0:
				row.rate = legacy_rate
			if flt(row.get("cost_rate") or 0) == 0 and legacy_cost > 0:
				row.cost_rate = legacy_cost
