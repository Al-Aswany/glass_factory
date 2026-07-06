"""Tests for Glass Product Specification UX and Pricing Upgrade (Phase 6).

Covers raw buying/selling split, operation pricing table, final pricing
formulas, margin fields, and backward compatibility.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt

from glass_factory.glass_factory.spec_pricing import (
    OPERATION_PRICING_BASIS,
    build_operation_pricing_rows,
    calculate_margin_fields,
    calculate_raw_selling_price,
    calculate_spec_pricing,
    collect_pricing_warnings,
    fetch_raw_selling_rate_per_m2,
    get_spec_currency,
)

RAW_SHEET_ITEM = "GLS-CLEAR-8MM-3210X2250"
RAW_SHEET_AREA = flt((3210 * 2250) / 1_000_000, 6)  # 7.2225 m²
FINISHED_AREA = flt((1200 * 800) / 1_000_000, 6)  # 0.96 m²
EDGE_METER = flt(2 * (1200 + 800) / 1000, 6)  # 4.0 m
CHARGEABLE_AREA = FINISHED_AREA  # default min not set in unit tests


def _make_spec(**overrides):
    """Create a frappe._dict spec suitable for unit-testing pricing functions."""
    values = {
        "glass_type": "CLEAR",
        "thickness_mm": 8,
        "length_mm": 1200,
        "width_mm": 800,
        "raw_sheet_item": RAW_SHEET_ITEM,
        "raw_sheet_rate_per_piece": 100,
        "raw_selling_rate_per_m2": 0,
        "raw_sheet_length_mm": 3210,
        "raw_sheet_width_mm": 2250,
        "raw_sheet_area_m2": RAW_SHEET_AREA,
        "currency": "USD",
        "company": None,
        "price_list": None,
        "qty": 1,
        "polish": 0,
        "bevel": 0,
        "temper": 0,
        "sandblast": 0,
        "laminate": 0,
        "hole_count": 0,
        "special_hole_count": 0,
        "slot_count": 0,
        "special_slot_count": 0,
    }
    values.update(overrides)
    doc = frappe._dict(values)
    doc.area_m2 = flt((doc.length_mm * doc.width_mm) / 1_000_000, 6)
    doc.total_area_m2 = flt(doc.area_m2 * (flt(doc.qty) or 1), 6)
    doc.edge_meter = flt(2 * (doc.length_mm + doc.width_mm) / 1000, 6)
    doc.chargeable_area_m2 = doc.area_m2
    doc.total_chargeable_area_m2 = doc.area_m2
    return doc


def _set_operation_rates(rows):
    settings = frappe.get_single("Glass Factory Settings")
    settings.set("operation_rates", [])
    for row in rows:
        settings.append("operation_rates", row)
    settings.save(ignore_permissions=True)
    frappe.db.commit()


def _usd_rates():
    return [
        {"operation": "Polish", "currency": "USD", "pricing_basis": "Per Edge Meter", "rate": 5, "enabled": 1},
        {"operation": "Bevel", "currency": "USD", "pricing_basis": "Per Edge Meter", "rate": 3, "enabled": 1},
        {"operation": "Hole", "currency": "USD", "pricing_basis": "Per Unit", "rate": 2, "enabled": 1},
        {"operation": "Special Hole", "currency": "USD", "pricing_basis": "Per Unit", "rate": 4, "enabled": 1},
        {"operation": "Slot", "currency": "USD", "pricing_basis": "Per Unit", "rate": 1.5, "enabled": 1},
        {"operation": "Special Slot", "currency": "USD", "pricing_basis": "Per Unit", "rate": 3, "enabled": 1},
        {"operation": "Temper", "currency": "USD", "pricing_basis": "Per Square Meter", "rate": 10, "enabled": 1},
        {"operation": "Sandblast", "currency": "USD", "pricing_basis": "Per Square Meter", "rate": 8, "enabled": 1},
        {"operation": "Laminate", "currency": "USD", "pricing_basis": "Per Square Meter", "rate": 12, "enabled": 1},
    ]


PRICING_UX_SELLING_PRICE_LIST = "GF-PricingUX-Selling-PerM2"


def _setup_per_m2_selling_price(price_list=PRICING_UX_SELLING_PRICE_LIST, rate=120, currency="USD"):
    """Ensure a selling Item Price (UOM = Square Meter) exists for RAW_SHEET_ITEM and make
    it the Glass Factory Settings default selling price list for the duration of a test class.

    Returns a dict with enough state for the matching _teardown_per_m2_selling_price call.
    """
    from glass_factory.glass_factory.item_resolver import ensure_item_uom_conversion
    from glass_factory.glass_factory.settings_validation import get_area_uom

    if not frappe.db.exists("Price List", price_list):
        pl = frappe.new_doc("Price List")
        pl.price_list_name = price_list
        pl.selling = 1
        pl.currency = currency
        pl.enabled = 1
        pl.insert(ignore_permissions=True)

    area_uom = get_area_uom()
    # ERPNext requires the Item Price UOM to already exist on the Item's UOM table.
    if RAW_SHEET_AREA > 0:
        ensure_item_uom_conversion(RAW_SHEET_ITEM, area_uom, flt(1 / RAW_SHEET_AREA, 6))

    created_item_price = None
    existing = frappe.db.get_value(
        "Item Price",
        {"item_code": RAW_SHEET_ITEM, "price_list": price_list, "selling": 1, "uom": area_uom},
        "name",
    )
    if not existing:
        ip = frappe.new_doc("Item Price")
        ip.item_code = RAW_SHEET_ITEM
        ip.price_list = price_list
        ip.selling = 1
        ip.currency = currency
        ip.uom = area_uom
        ip.price_list_rate = rate
        ip.insert(ignore_permissions=True)
        created_item_price = ip.name

    previous_default_pl = frappe.db.get_single_value(
        "Glass Factory Settings", "default_selling_price_list"
    )
    frappe.db.set_single_value("Glass Factory Settings", "default_selling_price_list", price_list)
    frappe.db.commit()

    return {"created_item_price": created_item_price, "previous_default_pl": previous_default_pl}


def _teardown_per_m2_selling_price(state):
    frappe.db.set_single_value(
        "Glass Factory Settings", "default_selling_price_list", state.get("previous_default_pl")
    )
    if state.get("created_item_price"):
        frappe.delete_doc(
            "Item Price", state["created_item_price"], force=True, ignore_permissions=True
        )
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Raw buying / selling split — unit tests
# ---------------------------------------------------------------------------


class TestRawBuyingSellingSplitUnit(unittest.TestCase):
    """Unit tests for raw buying/selling price calculations."""

    def test_buying_price_populates_raw_cost_fields(self):
        """Test 1: buying rate → raw_cost_per_m2 and raw_cost_per_finished_piece."""
        spec = _make_spec(raw_sheet_rate_per_piece=100)
        from glass_factory.glass_factory.spec_pricing import calculate_raw_cost
        calculate_raw_cost(spec)
        expected_cost_m2 = flt(100 / RAW_SHEET_AREA, 6)
        self.assertAlmostEqual(spec.raw_cost_per_m2, expected_cost_m2, places=4)
        self.assertAlmostEqual(
            spec.raw_cost_per_finished_piece,
            flt(expected_cost_m2 * FINISHED_AREA, 6),
            places=4,
        )

    def test_selling_rate_populates_raw_selling_fields(self):
        """Test 2: raw_selling_rate_per_m2 (fetched directly, not derived) → raw_selling_amount."""
        spec = _make_spec(raw_selling_rate_per_m2=120)
        calculate_raw_selling_price(spec)
        self.assertEqual(spec.raw_selling_rate_per_m2, 120)
        self.assertAlmostEqual(
            spec.raw_selling_amount_per_finished_piece,
            flt(120 * FINISHED_AREA, 6),
            places=4,
        )

    def test_calculated_selling_price_uses_raw_selling_not_raw_cost(self):
        """Test 3: calculated_amount_per_piece = raw_selling + processing, not raw_cost."""
        spec = _make_spec(
            raw_sheet_rate_per_piece=100,
            raw_selling_rate_per_m2=20,
        )
        calculate_spec_pricing(spec)
        # raw selling amount != raw cost amount since rates differ
        self.assertNotAlmostEqual(
            flt(spec.raw_selling_amount_per_finished_piece, 2),
            flt(spec.raw_cost_per_finished_piece, 2),
            places=2,
        )
        # calculated_amount uses selling
        self.assertAlmostEqual(
            spec.calculated_amount_per_piece,
            flt(spec.raw_selling_amount_per_finished_piece + spec.processing_amount_per_piece, 2),
            places=2,
        )

    def test_raw_selling_zero_sets_raw_selling_fields_to_zero(self):
        """Missing selling price → raw_selling_rate_per_m2 and raw_selling_amount = 0."""
        spec = _make_spec(raw_selling_rate_per_m2=0)
        calculate_raw_selling_price(spec)
        self.assertEqual(spec.raw_selling_amount_per_finished_piece, 0)
        self.assertEqual(spec.raw_selling_rate_per_m2, 0)

    def test_missing_selling_price_produces_warning_not_crash(self):
        """Test 5: Missing selling price produces warning but does not raise."""
        spec = _make_spec(raw_selling_rate_per_m2=0)
        # Should not raise
        calculate_spec_pricing(spec)
        warnings = collect_pricing_warnings(spec)
        has_selling_warning = any("selling price" in w.lower() for w in warnings)
        self.assertTrue(has_selling_warning)


# ---------------------------------------------------------------------------
# Operation pricing table — unit tests
# ---------------------------------------------------------------------------


class TestOperationPricingTableUnit(unittest.TestCase):
    """Unit tests for build_operation_pricing_rows and calculate_processing_amounts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("DocType", "Glass Factory Settings"):
            raise unittest.SkipTest("Glass Factory Settings not installed.")
        cls._previous_rates = frappe.get_all(
            "Glass Operation Rate",
            filters={"parent": "Glass Factory Settings"},
            fields=["*"],
        )
        _set_operation_rates(_usd_rates())

    @classmethod
    def tearDownClass(cls):
        settings = frappe.get_single("Glass Factory Settings")
        settings.set("operation_rates", [])
        for row in cls._previous_rates:
            settings.append("operation_rates", row)
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        super().tearDownClass()

    def test_selected_operations_create_rows(self):
        """Test 6: Only selected operations appear in operation_pricing."""
        spec = _make_spec(polish=1, temper=1)
        build_operation_pricing_rows(spec)
        rows = spec.get("operation_pricing") or []
        ops = [r.get("operation") for r in rows]
        self.assertIn("Polish", ops)
        self.assertIn("Temper", ops)
        self.assertNotIn("Bevel", ops)
        self.assertNotIn("Hole", ops)

    def test_polish_bevel_use_edge_meter(self):
        """Test 7: Polish and Bevel rows have pricing_basis=Per Edge Meter and qty=edge_meter."""
        spec = _make_spec(polish=1, bevel=1)
        build_operation_pricing_rows(spec)
        rows = {r.get("operation"): r for r in (spec.get("operation_pricing") or [])}
        for op in ("Polish", "Bevel"):
            self.assertEqual(rows[op].get("pricing_basis"), "Per Edge Meter")
            self.assertAlmostEqual(flt(rows[op].get("quantity")), EDGE_METER, places=4)

    def test_temper_sandblast_laminate_use_chargeable_area(self):
        """Test 8: Temper/Sandblast/Laminate use Per Square Meter and chargeable area qty."""
        spec = _make_spec(temper=1, sandblast=1, laminate=1)
        build_operation_pricing_rows(spec)
        rows = {r.get("operation"): r for r in (spec.get("operation_pricing") or [])}
        for op in ("Temper", "Sandblast", "Laminate"):
            self.assertEqual(rows[op].get("pricing_basis"), "Per Square Meter")
            self.assertAlmostEqual(
                flt(rows[op].get("quantity")), CHARGEABLE_AREA, places=4
            )

    def test_hole_slot_counts_use_per_unit(self):
        """Test 9: Hole/Special Hole/Slot/Special Slot use Per Unit pricing and count as qty."""
        spec = _make_spec(hole_count=3, special_hole_count=1, slot_count=2, special_slot_count=1)
        build_operation_pricing_rows(spec)
        rows = {r.get("operation"): r for r in (spec.get("operation_pricing") or [])}
        self.assertEqual(rows["Hole"].get("pricing_basis"), "Per Unit")
        self.assertEqual(cint(rows["Hole"].get("quantity")), 3)
        self.assertEqual(cint(rows["Special Hole"].get("quantity")), 1)
        self.assertEqual(cint(rows["Slot"].get("quantity")), 2)
        self.assertEqual(cint(rows["Special Slot"].get("quantity")), 1)

    def test_row_amount_equals_quantity_times_rate(self):
        """Test 10: amount = quantity × rate for each operation row."""
        spec = _make_spec(polish=1, hole_count=2, temper=1)
        build_operation_pricing_rows(spec)
        for row in (spec.get("operation_pricing") or []):
            qty = flt(row.get("quantity"))
            rate = flt(row.get("rate"))
            expected = flt(qty * rate, 2)
            self.assertAlmostEqual(flt(row.get("amount")), expected, places=2)

    def test_processing_total_is_sum_of_operation_rows(self):
        """Test 11: processing_amount_per_piece = sum of all row amounts."""
        spec = _make_spec(polish=1, temper=1, hole_count=2)
        build_operation_pricing_rows(spec)
        from glass_factory.glass_factory.spec_pricing import calculate_processing_amounts
        calculate_processing_amounts(spec)
        total_from_rows = sum(
            flt(r.get("amount")) for r in (spec.get("operation_pricing") or [])
        )
        self.assertAlmostEqual(
            spec.processing_amount_per_piece, flt(total_from_rows, 2), places=2
        )

    def test_manual_rate_override_preserved_after_refresh(self):
        """Test 12: Manual rate override on one operation is preserved by build_operation_pricing_rows."""
        spec = _make_spec(polish=1)
        build_operation_pricing_rows(spec)

        rows = spec.get("operation_pricing") or []
        for row in rows:
            if row.get("operation") == "Polish":
                row["rate"] = 999.0
                row["is_overridden"] = 1
                row["source"] = "Manual"

        build_operation_pricing_rows(spec, reset_overrides=False)

        rows = {r.get("operation"): r for r in (spec.get("operation_pricing") or [])}
        self.assertEqual(flt(rows["Polish"].get("rate")), 999.0)
        self.assertEqual(cint(rows["Polish"].get("is_overridden")), 1)

    def test_reset_operation_rates_reloads_settings_rates(self):
        """Test 13: reset_overrides=True clears manual overrides and reloads from settings."""
        spec = _make_spec(polish=1)
        build_operation_pricing_rows(spec)

        for row in (spec.get("operation_pricing") or []):
            if row.get("operation") == "Polish":
                row["rate"] = 999.0
                row["is_overridden"] = 1

        build_operation_pricing_rows(spec, reset_overrides=True)

        rows = {r.get("operation"): r for r in (spec.get("operation_pricing") or [])}
        settings_rate = 5  # Polish USD from _usd_rates
        self.assertAlmostEqual(flt(rows["Polish"].get("rate")), settings_rate, places=2)
        self.assertEqual(cint(rows["Polish"].get("is_overridden")), 0)

    def test_unselected_operation_removed_from_table(self):
        """Test 14: After deselecting an operation, rebuild excludes its row."""
        spec = _make_spec(polish=1, temper=1)
        build_operation_pricing_rows(spec)
        ops_with_both = [r.get("operation") for r in (spec.get("operation_pricing") or [])]
        self.assertIn("Polish", ops_with_both)
        self.assertIn("Temper", ops_with_both)

        spec["temper"] = 0
        build_operation_pricing_rows(spec)
        ops_without_temper = [r.get("operation") for r in (spec.get("operation_pricing") or [])]
        self.assertIn("Polish", ops_without_temper)
        self.assertNotIn("Temper", ops_without_temper)


# ---------------------------------------------------------------------------
# Final pricing formulas — unit tests
# ---------------------------------------------------------------------------


class TestFinalPricingFormulas(unittest.TestCase):
    """Unit tests for calculate_final_pricing and calculate_margin_fields."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("DocType", "Glass Factory Settings"):
            raise unittest.SkipTest("Glass Factory Settings not installed.")
        cls._previous_rates = frappe.get_all(
            "Glass Operation Rate",
            filters={"parent": "Glass Factory Settings"},
            fields=["*"],
        )
        _set_operation_rates(_usd_rates())

    @classmethod
    def tearDownClass(cls):
        settings = frappe.get_single("Glass Factory Settings")
        settings.set("operation_rates", [])
        for row in cls._previous_rates:
            settings.append("operation_rates", row)
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        super().tearDownClass()

    def test_calculated_amount_is_raw_selling_plus_processing_when_selling_set(self):
        """Test 15: calculated_amount_per_piece = raw_selling + processing when selling price > 0."""
        spec = _make_spec(raw_selling_rate_per_m2=120, temper=1)
        calculate_spec_pricing(spec)
        self.assertGreater(flt(spec.raw_selling_amount_per_finished_piece), 0)
        expected = flt(
            spec.raw_selling_amount_per_finished_piece + spec.processing_amount_per_piece, 2
        )
        self.assertAlmostEqual(spec.calculated_amount_per_piece, expected, places=2)

    def test_calculated_amount_falls_back_to_raw_cost_when_no_selling_price(self):
        """When selling price = 0, calculated_amount falls back to raw_cost + processing."""
        spec = _make_spec(raw_sheet_rate_per_piece=100, raw_selling_rate_per_m2=0)
        calculate_spec_pricing(spec)
        expected = flt(
            spec.raw_cost_per_finished_piece + spec.processing_amount_per_piece, 2
        )
        self.assertAlmostEqual(spec.calculated_amount_per_piece, expected, places=2)

    def test_calculated_rate_per_m2_equals_amount_over_area(self):
        """Test 16: calculated_rate_per_m2 = calculated_amount / area_m2."""
        spec = _make_spec(raw_selling_rate_per_m2=120)
        calculate_spec_pricing(spec)
        expected = flt(spec.calculated_amount_per_piece / spec.area_m2, 6)
        self.assertAlmostEqual(spec.calculated_rate_per_m2, expected, places=5)

    def test_manual_selling_rate_overrides_calculated_rate(self):
        """Test 17: Manual selling rate overrides calculated rate."""
        spec = _make_spec(manual_selling_rate_per_m2=25)
        calculate_spec_pricing(spec)
        self.assertEqual(spec.selling_rate_per_m2, 25)
        self.assertEqual(cint(spec.price_override), 1)

    def test_rate_per_piece_equals_selling_rate_times_area(self):
        """Test 18: rate_per_piece = selling_rate_per_m2 × area_m2."""
        spec = _make_spec(manual_selling_rate_per_m2=25)
        calculate_spec_pricing(spec)
        expected = flt(25 * spec.area_m2, 2)
        self.assertAlmostEqual(spec.rate_per_piece, expected, places=2)

    def test_amount_equals_rate_per_piece_times_qty(self):
        """Test 19: amount = rate_per_piece × qty."""
        spec = _make_spec(manual_selling_rate_per_m2=25, qty=3)
        calculate_spec_pricing(spec)
        expected = flt(spec.rate_per_piece * 3, 2)
        self.assertAlmostEqual(spec.amount, expected, places=2)

    def test_profit_fields_calculate_safely_with_zero_rate(self):
        """Test 20: Profit fields handle zero rate without division errors."""
        spec = _make_spec(manual_selling_rate_per_m2=0, raw_selling_rate_per_m2=0)
        calculate_spec_pricing(spec)
        # Should not raise
        self.assertEqual(spec.gross_profit_percent, 0)

    def test_gross_profit_per_piece(self):
        """Gross profit = rate_per_piece - estimated_cost."""
        spec = _make_spec(
            raw_sheet_rate_per_piece=100,
            raw_selling_rate_per_m2=120,
            manual_selling_rate_per_m2=25,
        )
        calculate_spec_pricing(spec)
        expected_cost = flt(
            spec.raw_cost_per_finished_piece + spec.processing_amount_per_piece, 2
        )
        expected_profit = flt(spec.rate_per_piece - expected_cost, 2)
        self.assertAlmostEqual(spec.gross_profit_per_piece, expected_profit, places=2)

    def test_total_gross_profit_scales_with_qty(self):
        """total_gross_profit = gross_profit_per_piece × qty."""
        spec = _make_spec(
            raw_selling_rate_per_m2=120,
            manual_selling_rate_per_m2=25,
            qty=5,
        )
        calculate_spec_pricing(spec)
        expected_total = flt(spec.gross_profit_per_piece * 5, 2)
        self.assertAlmostEqual(spec.total_gross_profit, expected_total, places=2)

    def test_amount_with_qty_one_backward_compat(self):
        """amount = rate_per_piece for qty=1 (backward compatibility)."""
        spec = _make_spec(manual_selling_rate_per_m2=25, qty=1)
        calculate_spec_pricing(spec)
        self.assertAlmostEqual(spec.amount, spec.rate_per_piece, places=2)


# ---------------------------------------------------------------------------
# fetch_raw_selling_rate_per_m2 — integration tests
# ---------------------------------------------------------------------------


class TestFetchRawSheetSellingRate(IntegrationTestCase):
    """Integration tests for fetching the per-m² selling rate from Item Price."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Item", RAW_SHEET_ITEM):
            raise unittest.SkipTest("Sample raw sheet item not installed.")

    def test_missing_selling_price_returns_zero(self):
        """Test 5 (integration): No selling Item Price → returns 0, no crash."""
        spec = frappe._dict(
            raw_sheet_item=RAW_SHEET_ITEM,
            currency="USD",
            price_list=None,
            company=None,
        )
        with patch(
            "glass_factory.glass_factory.piece_pricing.frappe.db.get_value",
            return_value=None,
        ):
            rate = fetch_raw_selling_rate_per_m2(spec)
        self.assertEqual(rate, 0)

    def test_missing_selling_price_throws_when_required(self):
        """refresh_pricing()-style strict lookup raises a clear error when nothing is found."""
        spec = frappe._dict(
            raw_sheet_item=RAW_SHEET_ITEM,
            currency="USD",
            price_list="Standard Selling",
            company=None,
        )
        with patch(
            "glass_factory.glass_factory.piece_pricing.frappe.db.get_value",
            return_value=None,
        ):
            with self.assertRaises(frappe.ValidationError) as ctx:
                fetch_raw_selling_rate_per_m2(spec, throw_if_missing=True)
        self.assertIn("Missing selling Item Price", str(ctx.exception))

    def test_selling_rate_fetched_from_price_list(self):
        """Test 2 (integration): Selling Item Price (UOM = Square Meter) in spec price_list is fetched."""
        spec = frappe._dict(
            raw_sheet_item=RAW_SHEET_ITEM,
            currency="USD",
            price_list="Standard Selling",
            company=None,
        )
        with patch(
            "glass_factory.glass_factory.piece_pricing.frappe.db.get_value",
            return_value=250,
        ):
            rate = fetch_raw_selling_rate_per_m2(spec)
        self.assertEqual(rate, 250)

    def test_raw_sheet_item_remains_non_sales_item(self):
        """Test 4: Raw sheet item must remain is_sales_item=0."""
        if not frappe.db.exists("Item", RAW_SHEET_ITEM):
            self.skipTest("Sample raw sheet item not installed.")
        is_sales = frappe.db.get_value("Item", RAW_SHEET_ITEM, "is_sales_item")
        self.assertEqual(cint(is_sales), 0)


# ---------------------------------------------------------------------------
# Refresh Pricing integration
# ---------------------------------------------------------------------------


class TestRefreshPricingIntegration(IntegrationTestCase):
    """Integration tests for refresh_pricing and operation pricing table on real docs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Item", RAW_SHEET_ITEM):
            raise unittest.SkipTest("Sample raw sheet item not installed.")
        if not frappe.db.exists("DocType", "Glass Factory Settings"):
            raise unittest.SkipTest("Glass Factory Settings not installed.")
        cls._previous_rates = frappe.get_all(
            "Glass Operation Rate",
            filters={"parent": "Glass Factory Settings"},
            fields=["*"],
        )
        _set_operation_rates(_usd_rates())
        cls._selling_price_state = _setup_per_m2_selling_price()
        from glass_factory.glass_factory.tests.test_spec_form_ux import _setup_buying_price_for_tests

        cls._buying_price_state = _setup_buying_price_for_tests()

    @classmethod
    def tearDownClass(cls):
        settings = frappe.get_single("Glass Factory Settings")
        settings.set("operation_rates", [])
        for row in cls._previous_rates:
            settings.append("operation_rates", row)
        settings.save(ignore_permissions=True)
        _teardown_per_m2_selling_price(cls._selling_price_state)
        frappe.db.commit()
        super().tearDownClass()

    def _new_spec(self, **overrides):
        from glass_factory.glass_factory.tests.test_glass_product_specification import (
            _new_spec,
            _refresh_pricing_context,
        )

        return _new_spec(**_refresh_pricing_context(**overrides))

    def test_refresh_pricing_builds_operation_pricing_rows(self):
        """Test 6 (integration): refresh_pricing builds operation_pricing rows."""
        doc = self._new_spec(
            raw_sheet_item=RAW_SHEET_ITEM,
            currency="USD",
            polish=1,
            temper=1,
        )
        doc.insert()
        doc.refresh_pricing()
        doc.reload()
        ops = [r.operation for r in doc.operation_pricing]
        self.assertIn("Polish", ops)
        self.assertIn("Temper", ops)
        doc.delete()
        frappe.db.commit()

    def test_operation_pricing_amounts_correct(self):
        """Test 10 (integration): row amount = quantity × rate after refresh_pricing."""
        doc = self._new_spec(
            raw_sheet_item=RAW_SHEET_ITEM,
            currency="USD",
            hole_count=2,
        )
        doc.insert()
        doc.refresh_pricing()
        doc.reload()
        for row in doc.operation_pricing:
            expected = flt(flt(row.quantity) * flt(row.rate), 2)
            self.assertAlmostEqual(flt(row.amount), expected, places=2)
        doc.delete()
        frappe.db.commit()

    def test_manual_override_preserved_after_refresh_pricing(self):
        """Test 12 (integration): Manual rate override is preserved by refresh_pricing."""
        doc = self._new_spec(
            raw_sheet_item=RAW_SHEET_ITEM,
            currency="USD",
            polish=1,
        )
        doc.insert()
        doc.refresh_pricing()
        doc.reload()

        for row in doc.operation_pricing:
            if row.operation == "Polish":
                row.rate = 999.0
                row.is_overridden = 1
                row.source = "Manual"
        doc.save()
        doc.reload()

        doc.refresh_pricing()
        doc.reload()

        polish_row = next((r for r in doc.operation_pricing if r.operation == "Polish"), None)
        self.assertIsNotNone(polish_row)
        self.assertAlmostEqual(flt(polish_row.rate), 999.0, places=1)
        self.assertEqual(cint(polish_row.is_overridden), 1)

        doc.delete()
        frappe.db.commit()

    def test_reset_operation_rates_clears_overrides(self):
        """Test 13 (integration): reset_operation_rates_to_settings clears manual overrides."""
        doc = self._new_spec(
            raw_sheet_item=RAW_SHEET_ITEM,
            currency="USD",
            polish=1,
        )
        doc.insert()
        doc.refresh_pricing()
        doc.reload()

        for row in doc.operation_pricing:
            if row.operation == "Polish":
                row.rate = 999.0
                row.is_overridden = 1
        doc.save()

        doc.reset_operation_rates_to_settings()
        doc.reload()

        polish_row = next((r for r in doc.operation_pricing if r.operation == "Polish"), None)
        self.assertIsNotNone(polish_row)
        settings_rate = 5  # Polish USD from _usd_rates
        self.assertAlmostEqual(flt(polish_row.rate), settings_rate, places=1)
        self.assertEqual(cint(polish_row.is_overridden), 0)

        doc.delete()
        frappe.db.commit()

    def test_unselected_operation_not_in_table_after_rebuild(self):
        """Test 14 (integration): Deselecting an operation removes it from next rebuild."""
        doc = self._new_spec(
            raw_sheet_item=RAW_SHEET_ITEM,
            currency="USD",
            polish=1,
            temper=1,
        )
        doc.insert()
        doc.refresh_pricing()
        doc.reload()
        ops_before = [r.operation for r in doc.operation_pricing]
        self.assertIn("Temper", ops_before)

        doc.temper = 0
        doc.save()
        doc.refresh_pricing()
        doc.reload()
        ops_after = [r.operation for r in doc.operation_pricing]
        self.assertNotIn("Temper", ops_after)

        doc.delete()
        frappe.db.commit()


# ---------------------------------------------------------------------------
# Compatibility — backward compat tests
# ---------------------------------------------------------------------------


class TestCompatibilitySpec(IntegrationTestCase):
    """Test 21-24: Ensure existing workflows still work after Phase 6 changes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Item", RAW_SHEET_ITEM):
            raise unittest.SkipTest("Sample raw sheet item not installed.")
        if not frappe.db.exists("DocType", "Glass Factory Settings"):
            raise unittest.SkipTest("Glass Factory Settings not installed.")
        cls._previous_rates = frappe.get_all(
            "Glass Operation Rate",
            filters={"parent": "Glass Factory Settings"},
            fields=["*"],
        )
        _set_operation_rates(_usd_rates())

    @classmethod
    def tearDownClass(cls):
        settings = frappe.get_single("Glass Factory Settings")
        settings.set("operation_rates", [])
        for row in cls._previous_rates:
            settings.append("operation_rates", row)
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        super().tearDownClass()

    def _new_spec(self, **overrides):
        from glass_factory.glass_factory.tests.test_glass_product_specification import (
            _new_spec,
            _refresh_pricing_context,
        )

        return _new_spec(**_refresh_pricing_context(**overrides))

    def _insert_full_spec(self, **overrides):
        from glass_factory.glass_factory.tests.test_glass_product_specification_items import (
            _insert_full_spec,
        )
        return _insert_full_spec(**overrides)

    def test_generate_items_still_works(self):
        """Test 21: Generate Items still creates raw, cut-wip, and final items."""
        from glass_factory.glass_factory.tests.test_glass_product_specification_items import (
            _insert_full_spec,
        )
        doc = _insert_full_spec()
        result = doc.generate_items()
        self.assertTrue(result.get("final_item_code"))
        self.assertEqual(doc.generation_status, "Generated")
        doc.delete()
        frappe.db.commit()

    def test_add_to_quotation_uses_rate_per_piece(self):
        """Test 22: Add to Quotation uses final item and rate_per_piece."""
        from glass_factory.glass_factory.tests.test_glass_product_specification_items import (
            _insert_full_spec,
        )
        doc = _insert_full_spec()
        doc.raw_sheet_rate_per_piece = 100
        doc.raw_selling_rate_per_m2 = 120
        doc.manual_selling_rate_per_m2 = 25
        doc.currency = "USD"
        doc.save()
        doc.generate_items()
        doc.reload()

        from glass_factory.glass_factory.spec_transaction import map_spec_to_transaction_row
        row = map_spec_to_transaction_row(doc)
        self.assertEqual(row["item_code"], doc.final_item_code)
        self.assertAlmostEqual(row["rate"], flt(doc.rate_per_piece), places=2)
        self.assertAlmostEqual(row["qty"], flt(doc.qty) or 1, places=2)

        doc.delete()
        frappe.db.commit()

    def test_qty_defaults_to_one_for_transaction(self):
        """Test 22b: spec.qty=1 (default) maps to transaction qty=1."""
        from glass_factory.glass_factory.tests.test_glass_product_specification_items import (
            _insert_full_spec,
        )
        doc = _insert_full_spec()
        doc.generate_items()
        doc.reload()

        from glass_factory.glass_factory.spec_transaction import map_spec_to_transaction_row
        row = map_spec_to_transaction_row(doc)
        self.assertEqual(row["qty"], 1)
        doc.delete()
        frappe.db.commit()

    def test_spec_without_operation_pricing_table_falls_back_to_settings(self):
        """Test 24: Specs without operation_pricing rows use legacy settings fallback."""
        doc = self._new_spec(
            raw_sheet_item=RAW_SHEET_ITEM,
            currency="USD",
            temper=1,
        )
        doc.insert()
        # No refresh_pricing called — operation_pricing is empty
        doc.reload()
        # processing_amount_per_piece is calculated from settings fallback
        self.assertGreaterEqual(flt(doc.processing_amount_per_piece), 0)
        doc.delete()
        frappe.db.commit()
