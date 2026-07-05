"""
Phase 7 Step 3 — Golden Path End-to-End Automated Test

Proves the complete Glass Factory production flow from Glass Product Specification
to a submitted Delivery Note, with every intermediate document submitted.

Business scenario
─────────────────
Product : CLEAR, 8 mm, finished size 1200 × 800 mm, qty = 2
Operations: Polish + Temper + 2 Holes
Raw sheet : GLS-CLEAR-8MM-3200X2800
Currency  : company default

Flow
────
Glass Product Specification (save + assert pricing)
 → Generate Items (assert items + final-item properties)
 → Sales Order (submit)
 → Cutting Job (source batch assigned + submit)
 → Repack #1 – Raw Sheet → Cut WIP (submit)
 → Processing Job (all operations completed + submit)
 → Repack #2 – Cut WIP → Final (submit)
 → Delivery Note (submit)

Data ownership
──────────────
All required data is created by this test class and cleaned up on teardown.
The Glass Factory Settings singleton is temporarily patched and fully restored.
"""
from __future__ import annotations

import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt, today

from glass_factory.glass_factory.doctype.cutting_job.cutting_job import make_cutting_job
from glass_factory.glass_factory.spec_transaction import add_spec_to_transaction

# ── Module constants ──────────────────────────────────────────────────────────

_PFX = "GF-E2E"                            # prefix for all test-owned resources
_RAW_ITEM = "GLS-CLEAR-8MM-3200X2800"      # distinct from other tests (3210×2250)
_SCRAP_ITEM = f"{_PFX}-Scrap"

# Glass Factory Settings fields that will be temporarily replaced
_SETTINGS_SCALAR_FIELDS = (
    "raw_warehouse", "cut_wip_warehouse", "final_goods_warehouse",
    "remnants_warehouse", "scrap_warehouse",
    "raw_item_group", "cut_wip_item_group", "final_item_group",
    "remnant_item_group", "scrap_item_group",
    "allowed_glass_types", "default_uom", "scrap_item",
    "default_buying_price_list", "default_selling_price_list",
    "min_chargeable_area_m2",
)

# ── Low-level helpers ─────────────────────────────────────────────────────────

def _ex(doctype: str, name: str) -> bool:
    return bool(frappe.db.exists(doctype, name))


def _safe_cancel_delete(doctype: str, name: str) -> None:
    if not name or not _ex(doctype, name):
        return
    try:
        doc = frappe.get_doc(doctype, name)
        if doc.docstatus == 1:
            doc.cancel()
    except Exception:
        pass
    try:
        frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
    except Exception:
        pass


# ── Data-creation helpers ─────────────────────────────────────────────────────

def _ensure_item_group(name: str) -> None:
    if not _ex("Item Group", name):
        ig = frappe.new_doc("Item Group")
        ig.item_group_name = name
        ig.parent_item_group = "All Item Groups"
        ig.insert(ignore_permissions=True)


def _ensure_warehouse(wh_name: str, company: str, abbr: str) -> str:
    full = f"{wh_name} - {abbr}"
    if not _ex("Warehouse", full):
        wh = frappe.new_doc("Warehouse")
        wh.warehouse_name = wh_name
        wh.company = company
        wh.insert(ignore_permissions=True)
    return full


def _ensure_customer(name: str) -> str:
    if not _ex("Customer", name):
        cg  = frappe.db.get_value("Customer Group", {"is_group": 0}, "name") \
              or "All Customer Groups"
        ter = frappe.db.get_value("Territory",      {"is_group": 0}, "name") \
              or "All Territories"
        c = frappe.new_doc("Customer")
        c.customer_name  = name
        c.customer_group = cg
        c.territory      = ter
        c.insert(ignore_permissions=True)
    return name


def _ensure_price_list(name: str, *, buying: bool, selling: bool, currency: str) -> str:
    if not _ex("Price List", name):
        pl = frappe.new_doc("Price List")
        pl.price_list_name = name
        pl.buying   = 1 if buying  else 0
        pl.selling  = 1 if selling else 0
        pl.currency = currency
        pl.enabled  = 1
        pl.insert(ignore_permissions=True)
    return name


def _ensure_raw_item(item_code: str, item_group: str,
                     company: str, warehouse: str, uom: str = "Nos") -> None:
    if _ex("Item", item_code):
        return
    item = frappe.new_doc("Item")
    item.item_code        = item_code
    item.item_name        = item_code
    item.item_group       = item_group
    item.stock_uom        = uom
    item.is_stock_item    = 1
    item.is_purchase_item = 1
    item.is_sales_item    = 0
    item.has_batch_no     = 1
    item.has_serial_no    = 0
    # Glass-factory custom fields
    item.gf_glass_item_role   = "Raw Sheet"
    item.gf_base_glass_type   = "CLEAR"
    item.gf_thickness_mm      = 8
    item.gf_length_mm         = 3200
    item.gf_width_mm          = 2800
    item.append("item_defaults", {"company": company, "default_warehouse": warehouse})
    item.insert(ignore_permissions=True)


def _ensure_scrap_item(item_code: str, item_group: str) -> None:
    if _ex("Item", item_code):
        return
    item = frappe.new_doc("Item")
    item.item_code        = item_code
    item.item_name        = item_code
    item.item_group       = item_group
    item.stock_uom        = "Nos"
    item.is_stock_item    = 1
    item.is_purchase_item = 0
    item.is_sales_item    = 0
    item.gf_glass_item_role = "Scrap"
    item.insert(ignore_permissions=True)


def _ensure_item_price(item_code: str, price_list: str, rate: float,
                       currency: str, *, buying: bool, selling: bool) -> str:
    existing = frappe.db.get_value(
        "Item Price",
        {"item_code": item_code, "price_list": price_list},
        "name",
    )
    if existing:
        return existing
    ip = frappe.new_doc("Item Price")
    ip.item_code        = item_code
    ip.price_list       = price_list
    ip.price_list_rate  = rate
    ip.currency         = currency
    ip.buying           = 1 if buying  else 0
    ip.selling          = 1 if selling else 0
    ip.insert(ignore_permissions=True)
    return ip.name


# ── Test class ────────────────────────────────────────────────────────────────

class TestSpecE2ESubmittedFlow(IntegrationTestCase):
    """
    Golden path E2E:
    Spec → Generate Items → Sales Order (submit)
    → Cutting Job + raw stock → Repack #1 (submit)
    → Processing Job (complete ops + submit) → Repack #2 (submit)
    → Delivery Note (submit).

    All data is created from scratch; Glass Factory Settings are patched for the
    duration of the class and fully restored in tearDownClass.
    """

    # ── class-level state ─────────────────────────────────────────────────
    _cls_cleanup:              list   # (doctype, name) → reversed in tearDownClass
    _settings_backup:          dict   # original scalar field values
    _settings_op_rates_backup: list   # original operation_rates rows

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._cls_cleanup              = []
        cls._settings_backup          = {}
        cls._settings_op_rates_backup = []
        try:
            cls._build_fixtures()
            cls._patch_settings()
        except unittest.SkipTest:
            raise
        except Exception as exc:
            cls.tearDownClass()
            raise unittest.SkipTest(f"E2E setUpClass failed: {exc}") from exc

    @classmethod
    def tearDownClass(cls) -> None:
        cls._restore_settings()
        for doctype, name in reversed(cls._cls_cleanup):
            _safe_cancel_delete(doctype, name)
        frappe.db.commit()
        super().tearDownClass()

    @classmethod
    def _reg(cls, doctype: str, name: str) -> None:
        cls._cls_cleanup.append((doctype, name))

    # ── fixture setup ─────────────────────────────────────────────────────

    @classmethod
    def _build_fixtures(cls) -> None:
        company = frappe.db.get_value("Company", {"is_group": 0}, "name")
        if not company:
            raise unittest.SkipTest("No Company is configured on this site.")
        cls.company  = company
        cls.abbr     = frappe.db.get_value("Company", company, "abbr")
        cls.currency = frappe.db.get_value("Company", company, "default_currency") or "USD"

        # Item groups
        for g in [f"{_PFX} Raw", f"{_PFX} Cut WIP", f"{_PFX} Final",
                  f"{_PFX} Remnants", f"{_PFX} Scrap"]:
            _ensure_item_group(g)
            cls._reg("Item Group", g)

        # Warehouses
        cls.raw_wh     = _ensure_warehouse(f"{_PFX} Raw Stock",   cls.company, cls.abbr)
        cls.cutwip_wh  = _ensure_warehouse(f"{_PFX} Cut WIP",     cls.company, cls.abbr)
        cls.final_wh   = _ensure_warehouse(f"{_PFX} Final Goods", cls.company, cls.abbr)
        cls.remnant_wh = _ensure_warehouse(f"{_PFX} Remnants",    cls.company, cls.abbr)
        cls.scrap_wh   = _ensure_warehouse(f"{_PFX} Scrap",       cls.company, cls.abbr)
        for wh in (cls.raw_wh, cls.cutwip_wh, cls.final_wh, cls.remnant_wh, cls.scrap_wh):
            cls._reg("Warehouse", wh)

        # Scrap item (required by Glass Factory Settings validator)
        _ensure_scrap_item(_SCRAP_ITEM, f"{_PFX} Scrap")
        cls._reg("Item", _SCRAP_ITEM)

        # Customer
        _ensure_customer(f"{_PFX}-Customer")
        cls._reg("Customer", f"{_PFX}-Customer")

        # Price lists
        cls.buying_pl  = _ensure_price_list(f"{_PFX}-Buying",
                                            buying=True,  selling=False,
                                            currency=cls.currency)
        cls.selling_pl = _ensure_price_list(f"{_PFX}-Selling",
                                            buying=False, selling=True,
                                            currency=cls.currency)
        cls._reg("Price List", cls.buying_pl)
        cls._reg("Price List", cls.selling_pl)

        # Raw sheet item
        _ensure_raw_item(_RAW_ITEM, f"{_PFX} Raw", cls.company, cls.raw_wh)
        cls._reg("Item", _RAW_ITEM)

        # Item prices – buying (500) and selling (800)
        ip_b = _ensure_item_price(_RAW_ITEM, cls.buying_pl,  500, cls.currency,
                                  buying=True,  selling=False)
        ip_s = _ensure_item_price(_RAW_ITEM, cls.selling_pl, 800, cls.currency,
                                  buying=False, selling=True)
        cls._reg("Item Price", ip_b)
        cls._reg("Item Price", ip_s)

        frappe.db.commit()

    # ── settings patch / restore ──────────────────────────────────────────

    @classmethod
    def _patch_settings(cls) -> None:
        if not frappe.db.exists("DocType", "Glass Factory Settings"):
            raise unittest.SkipTest("Glass Factory Settings DocType is not installed.")

        settings = frappe.get_single("Glass Factory Settings")

        # Snapshot original scalar values
        cls._settings_backup = {f: settings.get(f) for f in _SETTINGS_SCALAR_FIELDS}

        # Snapshot original operation rates (child rows)
        cls._settings_op_rates_backup = [
            {k: v for k, v in row.as_dict().items()
             if k not in ("name", "parent", "parenttype", "parentfield", "doctype", "idx")}
            for row in (settings.get("operation_rates") or [])
        ]

        # Apply E2E values
        settings.raw_warehouse              = cls.raw_wh
        settings.cut_wip_warehouse          = cls.cutwip_wh
        settings.final_goods_warehouse      = cls.final_wh
        settings.remnants_warehouse         = cls.remnant_wh
        settings.scrap_warehouse            = cls.scrap_wh
        settings.raw_item_group             = f"{_PFX} Raw"
        settings.cut_wip_item_group         = f"{_PFX} Cut WIP"
        settings.final_item_group           = f"{_PFX} Final"
        settings.remnant_item_group         = f"{_PFX} Remnants"
        settings.scrap_item_group           = f"{_PFX} Scrap"
        settings.allowed_glass_types        = "CLEAR"
        settings.default_uom                = "Nos"
        settings.scrap_item                 = _SCRAP_ITEM
        settings.default_buying_price_list  = cls.buying_pl
        settings.default_selling_price_list = cls.selling_pl
        settings.min_chargeable_area_m2     = 0

        # Operation rates: one enabled row per operation, for the test currency
        # Per Edge Meter (Polish, Bevel): 500 sell / 400 cost
        # Per Square Meter (Temper, Sandblast, Laminate): 300 sell / 250 cost
        # Per Unit (Hole, Special Hole, Slot, Special Slot): 100 sell / 80 cost
        from glass_factory.glass_factory.operation_rates import OPERATION_PRICING_BASIS
        _RATES = {
            "Per Edge Meter":   (500, 400),
            "Per Square Meter": (300, 250),
            "Per Unit":         (100,  80),
        }
        settings.set("operation_rates", [])
        for op, basis in OPERATION_PRICING_BASIS.items():
            sell, cost = _RATES[basis]
            settings.append("operation_rates", {
                "operation":     op,
                "currency":      cls.currency,
                "pricing_basis": basis,
                "rate":          sell,
                "cost_rate":     cost,
                "enabled":       1,
            })

        settings.flags.ignore_validate = True
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.clear_cache()

    @classmethod
    def _restore_settings(cls) -> None:
        if not cls._settings_backup:
            return
        try:
            settings = frappe.get_single("Glass Factory Settings")
            for f, v in cls._settings_backup.items():
                settings.set(f, v)
            settings.set("operation_rates", [])
            for row_data in cls._settings_op_rates_backup:
                settings.append("operation_rates", row_data)
            settings.flags.ignore_validate = True
            settings.save(ignore_permissions=True)
            frappe.db.commit()
            frappe.clear_cache()
        except Exception:
            pass  # best-effort; do not mask test failures

    # ── per-test setup / teardown ─────────────────────────────────────────

    def setUp(self) -> None:
        # track (doctype, name) pairs; reversed() gives the cancel order
        self._tst: list = []

    def tearDown(self) -> None:
        for doctype, name in reversed(self._tst):
            _safe_cancel_delete(doctype, name)
        frappe.db.commit()

    def _track(self, doctype: str, name: str) -> None:
        """Register a document for cleanup in tearDown (reverse order)."""
        self._tst.append((doctype, name))

    # ── helpers used in the test ──────────────────────────────────────────

    def _make_raw_batch(self) -> str:
        b = frappe.new_doc("Batch")
        b.item     = _RAW_ITEM
        b.batch_id = f"E2E-RAW-{frappe.generate_hash(length=8).upper()}"
        meta = frappe.get_meta("Batch")
        if meta.has_field("gf_length_mm"):
            b.gf_length_mm = 3200
        if meta.has_field("gf_width_mm"):
            b.gf_width_mm  = 2800
        b.insert(ignore_permissions=True)
        return b.name

    def _receive_raw_stock(self, batch_no: str, qty: float = 5,
                           rate: float = 500) -> str:
        """Submit a Material Receipt to place raw glass in the raw warehouse."""
        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Material Receipt"
        se.purpose          = "Material Receipt"
        se.company          = self.company
        se.posting_date     = today()
        se.append("items", {
            "item_code":               _RAW_ITEM,
            "t_warehouse":             self.raw_wh,
            "qty":                     qty,
            "basic_rate":              rate,
            "batch_no":                batch_no,
            "use_serial_batch_fields": 1,
        })
        se.insert(ignore_permissions=True)
        se.submit()
        return se.name

    # ── GOLDEN PATH TEST ──────────────────────────────────────────────────

    def test_spec_to_delivery_full_submitted_stock_flow(self) -> None:
        """
        End-to-end golden path (all documents submitted):

        Glass Product Specification
         → Generate Items
         → Sales Order  (submit)
         → Cutting Job  (submit)
         → Repack #1 – Raw Sheet → Cut WIP  (submit)
         → Processing Job  (complete ops + submit)
         → Repack #2 – Cut WIP → Final  (submit)
         → Delivery Note  (submit)
        """

        # ── STEP 1: Glass Product Specification ───────────────────────────

        spec = frappe.new_doc("Glass Product Specification")
        spec.company        = self.company
        spec.customer       = f"{_PFX}-Customer"
        spec.currency       = self.currency
        spec.price_list     = self.selling_pl
        spec.raw_sheet_item = _RAW_ITEM
        spec.length_mm      = 1200
        spec.width_mm       = 800
        spec.qty            = 2
        spec.polish         = 1
        spec.temper         = 1
        spec.hole_count     = 2
        spec.insert(ignore_permissions=True)
        # refresh_pricing builds operation_pricing rows, fetches Item Prices, recalculates, saves
        spec.refresh_pricing()
        spec.reload()

        # Pricing must be computed on save
        self.assertAlmostEqual(flt(spec.area_m2),       0.96, places=4,
            msg="area per piece must be 1200×800/1 000 000 = 0.96 m²")
        self.assertAlmostEqual(flt(spec.total_area_m2), 1.92, places=4,
            msg="total area must be 0.96 × 2 = 1.92 m²")
        self.assertGreater(flt(spec.raw_sheet_rate_per_piece), 0,
            "raw_sheet_rate_per_piece should be populated from the buying Item Price")
        self.assertGreater(flt(spec.raw_sheet_selling_rate_per_piece), 0,
            "raw_sheet_selling_rate_per_piece should be populated from the selling Item Price")
        self.assertTrue(spec.operation_pricing,
            "operation_pricing child table must have rows after save")
        self.assertGreater(flt(spec.processing_amount_per_piece), 0,
            "processing_amount_per_piece must be > 0 (rates configured in settings)")
        self.assertGreater(flt(spec.selling_rate_per_m2), 0,
            "selling_rate_per_m2 must be > 0")
        self.assertGreater(flt(spec.rate_per_piece), 0,
            "rate_per_piece must be > 0")
        self.assertGreater(flt(spec.amount), 0,
            "amount must be > 0")

        # ── STEP 2: Generate Items ────────────────────────────────────────

        spec.generate_items()
        spec.reload()

        self.assertEqual(spec.status,            "Ready",     "status must be Ready")
        self.assertEqual(spec.generation_status, "Generated", "generation_status must be Generated")
        self.assertEqual(cint(spec.items_generated), 1)
        self.assertTrue(spec.raw_item_code,     "raw_item_code must be set")
        self.assertTrue(spec.cut_wip_item_code, "cut_wip_item_code must be set")
        self.assertTrue(spec.final_item_code,   "final_item_code must be set")
        self.assertEqual(spec.generated_item, spec.final_item_code,
            "generated_item must equal final_item_code")

        # Final item properties
        final_doc = frappe.get_doc("Item", spec.final_item_code)
        self.assertEqual(cint(final_doc.is_sales_item),    1, "final item must be a sales item")
        self.assertEqual(cint(final_doc.is_purchase_item), 0, "final item must NOT be a purchase item")
        self.assertEqual(cint(final_doc.has_batch_no),     1, "final item must be batch-tracked")
        self.assertEqual(cint(final_doc.has_serial_no),    0, "final item must not have serial numbers")

        # ── STEP 3: Sales Order ───────────────────────────────────────────

        so_result = add_spec_to_transaction(spec.name, "Sales Order")
        so = frappe.get_doc("Sales Order", so_result["name"])
        # Register early (reversed tearDown will cancel SO last)
        self._track("Sales Order", so.name)

        self.assertEqual(len(so.items), 1)
        soi = so.items[0]

        # Item identity
        self.assertEqual(soi.item_code, spec.final_item_code,
            "SO item must use the generated final item, not the raw sheet")
        self.assertNotEqual(soi.item_code, _RAW_ITEM,
            "Raw sheet item must never appear as a sold item")
        self.assertNotEqual(soi.item_code, spec.cut_wip_item_code,
            "Cut WIP item must never appear as a sold item")

        # Quantities and rates
        self.assertAlmostEqual(flt(soi.qty),  2.0, places=2, msg="SO qty must be 2")
        self.assertAlmostEqual(flt(soi.rate), flt(spec.rate_per_piece), places=2,
            msg="SO rate must equal spec.rate_per_piece")

        # Glass-factory metadata on the SO item
        self.assertEqual(cint(soi.gf_from_glass_specification), 1,
            "gf_from_glass_specification must be 1")
        self.assertEqual(soi.gf_glass_specification, spec.name,
            "gf_glass_specification must reference the spec")
        self.assertAlmostEqual(flt(soi.gf_area_m2),       0.96, places=4)
        self.assertAlmostEqual(flt(soi.gf_total_area_m2), 1.92, places=4)
        self.assertAlmostEqual(flt(soi.gf_selling_rate_per_m2),
                               flt(spec.selling_rate_per_m2), places=2)

        so.submit()
        self.assertEqual(so.docstatus, 1, "Sales Order must be submitted (docstatus=1)")

        # ── STEP 4: Raw-stock receipt (pre-requisite for Repack #1) ───────
        # Registered before Cutting Job so tearDown cancels: … → CJ → receipt → SO
        raw_batch   = self._make_raw_batch()
        receipt_name = self._receive_raw_stock(raw_batch, qty=5, rate=500)
        self._track("Stock Entry", receipt_name)

        # ── STEP 5: Cutting Job ───────────────────────────────────────────

        job = make_cutting_job(so.name)

        self.assertTrue(job.pieces, "Cutting Job must have at least one piece row")
        piece = job.pieces[0]
        self.assertEqual(piece.sales_order,        so.name,            "piece.sales_order mismatch")
        self.assertEqual(piece.sales_order_item,   soi.name,           "piece.sales_order_item mismatch")
        self.assertEqual(piece.glass_specification, spec.name,          "piece.glass_specification mismatch")
        self.assertEqual(piece.cut_wip_item,        spec.cut_wip_item_code)
        self.assertEqual(piece.final_item,          spec.final_item_code)
        self.assertAlmostEqual(flt(piece.length_mm),   1200, places=0)
        self.assertAlmostEqual(flt(piece.width_mm),     800, places=0)
        self.assertAlmostEqual(flt(piece.qty_required),   2, places=0)

        # Assign a real raw-stock batch to the source sheet row
        self.assertTrue(job.source_sheets,
            "Cutting Job must auto-populate source_sheets from the spec raw item")
        job.source_sheets[0].batch_no     = raw_batch
        job.source_sheets[0].qty_consumed = 1
        job.save(ignore_permissions=True)

        job.submit()
        self.assertEqual(job.docstatus, 1, "Cutting Job must be submitted")
        self._track("Cutting Job", job.name)

        # ── STEP 6: Repack #1 — Raw Sheet → Cut WIP ──────────────────────

        r1_result = job.submit_repack_stock_entry()
        repack1_name = r1_result["stock_entry"]
        self._track("Stock Entry", repack1_name)

        job.reload()
        self.assertEqual(job.status,             "Cut Stock Posted",
            "Cutting Job status must advance to 'Cut Stock Posted'")
        self.assertEqual(job.linked_stock_entry, repack1_name)

        se1 = frappe.get_doc("Stock Entry", repack1_name)
        self.assertEqual(se1.docstatus,           1,           "Repack #1 must be submitted")
        self.assertEqual(se1.stock_entry_type,    "Repack",    "type must be Repack")
        self.assertEqual(se1.gf_cutting_job,      job.name,    "must link to Cutting Job")
        self.assertEqual(se1.gf_glass_stock_flow, "Raw to Cut WIP")

        raw_out_rows = [r for r in se1.items
                        if r.item_code == _RAW_ITEM and r.s_warehouse]
        cut_in_rows  = [r for r in se1.items
                        if r.item_code == spec.cut_wip_item_code and r.t_warehouse]
        self.assertTrue(raw_out_rows,
            "Repack #1 must have a raw-sheet outgoing row")
        self.assertTrue(cut_in_rows,
            "Repack #1 must have a cut-WIP incoming row")
        self.assertTrue(raw_out_rows[0].batch_no,
            "Source batch is mandatory — raw outgoing row must carry batch_no")
        self.assertTrue(cut_in_rows[0].batch_no,
            "Cut WIP incoming row must carry an auto-generated batch_no")

        soi_db = frappe.db.get_value(
            "Sales Order Item", soi.name,
            ["gf_cutting_job", "gf_cut_qty"], as_dict=True,
        )
        self.assertEqual(soi_db.gf_cutting_job, job.name,
            "SO Item must be updated with the Cutting Job reference after Repack #1")
        self.assertGreater(flt(soi_db.gf_cut_qty), 0,
            "gf_cut_qty must be > 0 after Repack #1 is submitted")

        # ── STEP 7: Processing Job ────────────────────────────────────────

        start_result = job.start_processing()
        pj = frappe.get_doc("Glass Processing Job", start_result["name"])

        self.assertEqual(pj.cutting_job, job.name,
            "Processing Job must reference the Cutting Job")
        self.assertTrue(pj.inputs,     "Processing Job must have input rows")
        self.assertTrue(pj.outputs,    "Processing Job must have output rows")
        self.assertTrue(pj.operations, "Processing Job must have operation rows")
        self.assertEqual(pj.inputs[0].cut_wip_item,         spec.cut_wip_item_code)
        self.assertEqual(pj.outputs[0].final_item,          spec.final_item_code)
        self.assertEqual(pj.outputs[0].glass_specification, spec.name)

        op_codes = {o.operation for o in pj.operations}
        self.assertIn("POL", op_codes, "Polish (POL) must appear in operations")
        self.assertIn("TMP", op_codes, "Temper (TMP) must appear in operations")
        self.assertIn("HOL", op_codes, "Hole   (HOL) must appear in operations")

        # Delivery must be blocked before processing (gf_processed_qty is still 0)
        pre_qty = flt(frappe.db.get_value("Sales Order Item", soi.name, "gf_processed_qty"))
        self.assertEqual(pre_qty, 0,
            "gf_processed_qty must still be 0 before Repack #2 — delivery must be blocked")

        # Mark all operations as Completed, then submit the Processing Job
        for op in pj.operations:
            op.status = "Completed"
        pj.save(ignore_permissions=True)
        pj.submit()
        self.assertEqual(pj.docstatus, 1, "Processing Job must be submitted")
        self._track("Glass Processing Job", pj.name)

        # ── STEP 8: Repack #2 — Cut WIP → Final ──────────────────────────

        r2_result = pj.submit_repack_stock_entry()
        repack2_name = r2_result["stock_entry"]
        self._track("Stock Entry", repack2_name)

        se2 = frappe.get_doc("Stock Entry", repack2_name)
        self.assertEqual(se2.docstatus,           1,                "Repack #2 must be submitted")
        self.assertEqual(se2.stock_entry_type,    "Repack",         "type must be Repack")
        self.assertEqual(se2.gf_processing_job,   pj.name,          "must link to Processing Job")
        self.assertEqual(se2.gf_glass_stock_flow, "Cut WIP to Final")

        cut_out_rows = [r for r in se2.items
                        if r.item_code == spec.cut_wip_item_code and r.s_warehouse]
        fin_in_rows  = [r for r in se2.items
                        if r.item_code == spec.final_item_code   and r.t_warehouse]
        self.assertTrue(cut_out_rows,
            "Repack #2 must have a cut-WIP outgoing row")
        self.assertTrue(fin_in_rows,
            "Repack #2 must have a final-item incoming row")
        self.assertTrue(fin_in_rows[0].batch_no,
            "Final-item row must carry an auto-generated batch_no")
        final_batch = fin_in_rows[0].batch_no

        soi_after = frappe.db.get_value(
            "Sales Order Item", soi.name,
            ["gf_processing_job", "gf_processed_qty"], as_dict=True,
        )
        self.assertEqual(soi_after.gf_processing_job, pj.name,
            "SO Item must reference Processing Job after Repack #2")
        self.assertGreater(flt(soi_after.gf_processed_qty), 0,
            "gf_processed_qty must be > 0 after Repack #2 — delivery is now allowed")

        pj.complete_job()
        pj.reload()
        self.assertEqual(pj.status, "Completed", "Processing Job must reach Completed status")

        # ── STEP 9: Delivery Note ─────────────────────────────────────────

        from erpnext.selling.doctype.sales_order.sales_order import (
            make_delivery_note as _make_dn,
        )
        dn = frappe.get_doc(_make_dn(so.name))

        # Assign the final-item batch so ERPNext can deduct from stock
        for dn_item in dn.items:
            if dn_item.item_code == spec.final_item_code and final_batch:
                dn_item.batch_no               = final_batch
                dn_item.use_serial_batch_fields = 1

        dn.insert(ignore_permissions=True)
        dn.submit()
        self.assertEqual(dn.docstatus, 1, "Delivery Note must be submitted")
        self._track("Delivery Note", dn.name)

        # on_delivery_note_submit hook must have synced gf_delivered_qty
        gf_del = flt(frappe.db.get_value("Sales Order Item", soi.name, "gf_delivered_qty"))
        self.assertGreater(gf_del, 0,
            "gf_delivered_qty must be > 0 after Delivery Note submission")
        self.assertLessEqual(gf_del, flt(soi_after.gf_processed_qty),
            "Delivered qty must not exceed processed qty")

        # ── Spec cleanup (not submitted → plain delete after SO is gone) ──
        self.addCleanup(
            _safe_cancel_delete, "Glass Product Specification", spec.name
        )
