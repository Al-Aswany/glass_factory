"""Pricing engine for Glass Product Specification."""

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt

from glass_factory.glass_factory.operation_rates import OPERATION_PRICING_BASIS
from glass_factory.glass_factory.piece_pricing import (
    chargeable_area_m2,
    get_buying_price_list,
    get_item_buying_rate,
)

AREA_OPERATIONS = frozenset({"Temper", "Sandblast", "Laminate"})
EDGE_OPERATIONS = frozenset({"Polish", "Bevel"})
UNIT_OPERATIONS = {
    "Hole": "hole_count",
    "Special Hole": "special_hole_count",
    "Slot": "slot_count",
    "Special Slot": "special_slot_count",
}

BOOLEAN_OPERATION_FIELDS = {
    "Polish": "polish",
    "Bevel": "bevel",
    "Temper": "temper",
    "Sandblast": "sandblast",
    "Laminate": "laminate",
}


OPERATION_ORDER = [
    "Polish",
    "Bevel",
    "Hole",
    "Special Hole",
    "Slot",
    "Special Slot",
    "Temper",
    "Sandblast",
    "Laminate",
]

OPERATION_QUANTITY_FIELD = {
    "Polish": "edge_meter",
    "Bevel": "edge_meter",
    "Temper": "chargeable_area_m2",
    "Sandblast": "chargeable_area_m2",
    "Laminate": "chargeable_area_m2",
    "Hole": "hole_count",
    "Special Hole": "special_hole_count",
    "Slot": "slot_count",
    "Special Slot": "special_slot_count",
}


def get_spec_currency(spec) -> str:
    if spec.get("currency"):
        return spec.currency
    if spec.get("company"):
        return frappe.get_cached_value("Company", spec.company, "default_currency") or "USD"
    return "USD"


def get_operation_rate(operation: str, currency: str, pricing_basis: str) -> float:
    """Return configured selling rate for an operation from the Operation Rates table."""
    if frappe.db.exists("DocType", "Glass Factory Settings"):
        settings = frappe.get_single("Glass Factory Settings")
        for row in settings.get("operation_rates") or []:
            if (
                row.operation == operation
                and row.currency == currency
                and row.pricing_basis == pricing_basis
                and cint(row.enabled)
            ):
                return flt(row.rate)
    return 0


def get_operation_cost_rate(operation: str, currency: str, pricing_basis: str) -> float:
    """Return configured cost rate for an operation from the flexible child table."""
    if frappe.db.exists("DocType", "Glass Factory Settings"):
        settings = frappe.get_single("Glass Factory Settings")
        for row in settings.get("operation_rates") or []:
            if (
                row.operation == operation
                and row.currency == currency
                and row.pricing_basis == pricing_basis
                and cint(row.enabled)
            ):
                return flt(row.get("cost_rate") or 0)
    return 0


def _is_operation_selected(spec, operation: str) -> bool:
    """Return True if the operation is currently selected on the spec."""
    boolean_field = BOOLEAN_OPERATION_FIELDS.get(operation)
    if boolean_field:
        return bool(cint(spec.get(boolean_field)))
    unit_field = UNIT_OPERATIONS.get(operation)
    if unit_field:
        return cint(spec.get(unit_field)) > 0
    return False


def fetch_raw_sheet_rate(spec, *, fetch_from_item_price: bool = False) -> float:
    if flt(spec.get("raw_sheet_rate_per_piece")) > 0:
        return flt(spec.get("raw_sheet_rate_per_piece"))

    if not fetch_from_item_price or not spec.get("raw_sheet_item"):
        return 0

    currency = get_spec_currency(spec)
    buying_price_list = get_buying_price_list(spec.get("company"), currency)
    rate = get_item_buying_rate(
        spec.raw_sheet_item,
        buying_price_list,
        spec.get("company"),
        currency=currency,
    )
    if rate and currency and buying_price_list:
        item_price_currency = frappe.db.get_value(
            "Item Price",
            {"item_code": spec.raw_sheet_item, "price_list": buying_price_list, "buying": 1},
            "currency",
            order_by="valid_from desc, creation desc",
        )
        if item_price_currency and item_price_currency != currency:
            return 0
    return flt(rate)


def fetch_raw_sheet_selling_rate(spec) -> float:
    """Fetch raw sheet selling price per piece from Item Price records (selling=1).

    Raw sheet items may have selling Item Price records used for pricing only.
    They are not sold directly on Quotation/Sales Order.

    Lookup priority:
    1. Spec's own price_list
    2. Settings default_selling_price_list
    3. Any selling Item Price matching the spec currency
    """
    if not spec.get("raw_sheet_item"):
        return 0

    currency = get_spec_currency(spec)

    # 1. Spec's own selling price list
    price_list = spec.get("price_list")
    if price_list:
        rate = frappe.db.get_value(
            "Item Price",
            {"item_code": spec.raw_sheet_item, "price_list": price_list, "selling": 1},
            "price_list_rate",
            order_by="valid_from desc, creation desc",
        )
        if rate:
            return flt(rate)

    # 2. Settings default selling price list
    if frappe.db.exists("DocType", "Glass Factory Settings"):
        settings_pl = frappe.db.get_single_value(
            "Glass Factory Settings", "default_selling_price_list"
        )
        if settings_pl and settings_pl != price_list:
            rate = frappe.db.get_value(
                "Item Price",
                {"item_code": spec.raw_sheet_item, "price_list": settings_pl, "selling": 1},
                "price_list_rate",
                order_by="valid_from desc, creation desc",
            )
            if rate:
                return flt(rate)

    # 3. Any selling Item Price in spec currency
    rate = frappe.db.get_value(
        "Item Price",
        {"item_code": spec.raw_sheet_item, "selling": 1, "currency": currency},
        "price_list_rate",
        order_by="valid_from desc, creation desc",
    )
    return flt(rate) if rate else 0


def build_operation_pricing_rows(spec, *, reset_overrides: bool = False) -> None:
    """Build or refresh operation pricing rows on the spec's operation_pricing child table.

    - Includes only currently selected operations.
    - Pulls default rates from Glass Factory Settings.
    - Preserves manually overridden rates unless reset_overrides=True.
    """
    currency = get_spec_currency(spec)

    existing_rows: dict[str, object] = {}
    for row in spec.get("operation_pricing") or []:
        op = row.get("operation") if hasattr(row, "get") else row.operation
        existing_rows[op] = row

    new_rows = []
    for operation in OPERATION_ORDER:
        if not _is_operation_selected(spec, operation):
            continue

        pricing_basis = OPERATION_PRICING_BASIS[operation]
        qty_field = OPERATION_QUANTITY_FIELD[operation]

        if pricing_basis == "Per Unit":
            quantity = cint(spec.get(qty_field))
        else:
            quantity = flt(spec.get(qty_field))

        default_rate = get_operation_rate(operation, currency, pricing_basis)

        existing = existing_rows.get(operation)
        ex_is_overridden = 0
        ex_remarks = ""
        if existing:
            ex_is_overridden = cint(
                existing.get("is_overridden") if hasattr(existing, "get") else existing.is_overridden
            )
            ex_remarks = (
                existing.get("remarks") if hasattr(existing, "get") else existing.remarks
            ) or ""

        if existing and ex_is_overridden and not reset_overrides:
            rate = flt(
                existing.get("rate") if hasattr(existing, "get") else existing.rate
            )
            is_overridden = 1
            source = "Manual"
        else:
            rate = default_rate
            is_overridden = 0
            source = "Settings"

        amount = flt(quantity * rate, 2)

        new_rows.append(
            {
                "operation": operation,
                "operation_label": operation,
                "pricing_basis": pricing_basis,
                "quantity": quantity,
                "default_rate": default_rate,
                "rate": rate,
                "amount": amount,
                "currency": currency,
                "is_overridden": is_overridden,
                "source": source,
                "remarks": ex_remarks,
            }
        )

    if isinstance(spec, Document):
        spec.set("operation_pricing", new_rows)
    else:
        spec["operation_pricing"] = [frappe._dict(r) for r in new_rows]


def calculate_spec_pricing(spec) -> None:
    calculate_processing_quantities(spec)
    calculate_raw_cost(spec)
    calculate_raw_selling_price(spec)
    calculate_processing_amounts(spec)
    calculate_final_pricing(spec)
    calculate_margin_fields(spec)


def calculate_processing_quantities(spec) -> None:
    length = flt(spec.length_mm)
    width = flt(spec.width_mm)
    area_m2 = flt(spec.area_m2)

    spec.edge_meter = flt((2 * (length + width)) / 1000, 6) if length > 0 and width > 0 else 0
    spec.chargeable_area_m2 = chargeable_area_m2(length, width) if length > 0 and width > 0 else 0
    spec.total_chargeable_area_m2 = spec.chargeable_area_m2

    if area_m2 <= 0 and length > 0 and width > 0:
        spec.chargeable_area_m2 = chargeable_area_m2(length, width)


def calculate_raw_cost(spec) -> None:
    raw_rate = flt(spec.get("raw_sheet_rate_per_piece"))
    raw_area = flt(spec.get("raw_sheet_area_m2"))
    area_m2 = flt(spec.get("area_m2"))

    if raw_rate > 0 and raw_area <= 0:
        frappe.throw("Raw sheet dimensions are required to calculate raw cost per m².")

    if raw_area > 0 and raw_rate > 0:
        spec.raw_cost_per_m2 = flt(raw_rate / raw_area, 6)
    else:
        spec.raw_cost_per_m2 = 0

    spec.raw_cost_per_finished_piece = flt(spec.raw_cost_per_m2 * area_m2, 6) if area_m2 > 0 else 0


def calculate_raw_selling_price(spec) -> None:
    """Compute raw selling price fields from raw_sheet_selling_rate_per_piece."""
    selling_rate = flt(spec.get("raw_sheet_selling_rate_per_piece"))
    raw_area = flt(spec.get("raw_sheet_area_m2"))
    area_m2 = flt(spec.get("area_m2"))

    if raw_area > 0 and selling_rate > 0:
        spec.raw_selling_rate_per_m2 = flt(selling_rate / raw_area, 6)
    else:
        spec.raw_selling_rate_per_m2 = 0

    spec.raw_selling_amount_per_finished_piece = (
        flt(spec.raw_selling_rate_per_m2 * area_m2, 6) if area_m2 > 0 else 0
    )


def calculate_processing_amounts(spec) -> None:
    """Sum processing amounts from operation_pricing table; fall back to settings if table is empty."""
    operation_rows = spec.get("operation_pricing") or []

    if operation_rows:
        area_total = 0.0
        edge_total = 0.0
        unit_total = 0.0

        for row in operation_rows:
            operation = row.get("operation") if hasattr(row, "get") else row.operation
            if not _is_operation_selected(spec, operation):
                continue

            pricing_basis = row.get("pricing_basis") if hasattr(row, "get") else row.pricing_basis
            qty_field = OPERATION_QUANTITY_FIELD.get(operation)
            if not qty_field:
                continue

            if pricing_basis == "Per Unit":
                quantity = cint(spec.get(qty_field))
            else:
                quantity = flt(spec.get(qty_field))

            rate = flt(row.get("rate") if hasattr(row, "get") else row.rate)
            amount = flt(quantity * rate, 2)

            row.quantity = quantity
            row.amount = amount

            if pricing_basis == "Per Square Meter":
                area_total += amount
            elif pricing_basis == "Per Edge Meter":
                edge_total += amount
            elif pricing_basis == "Per Unit":
                unit_total += amount

        spec.area_processing_amount_per_piece = flt(area_total, 2)
        spec.edge_processing_amount_per_piece = flt(edge_total, 2)
        spec.unit_processing_amount_per_piece = flt(unit_total, 2)
    else:
        _calculate_processing_amounts_from_settings(spec)

    spec.processing_amount_per_piece = flt(
        spec.area_processing_amount_per_piece
        + spec.edge_processing_amount_per_piece
        + spec.unit_processing_amount_per_piece,
        2,
    )


def _calculate_processing_amounts_from_settings(spec) -> None:
    """Legacy: compute processing amounts directly from Glass Factory Settings rates."""
    currency = get_spec_currency(spec)
    chargeable_area = flt(spec.chargeable_area_m2)
    edge_meter = flt(spec.edge_meter)

    area_rate_total = 0.0
    for operation in AREA_OPERATIONS:
        fieldname = BOOLEAN_OPERATION_FIELDS[operation]
        if cint(spec.get(fieldname)):
            area_rate_total += get_operation_rate(
                operation, currency, OPERATION_PRICING_BASIS[operation]
            )

    edge_rate_total = 0.0
    for operation in EDGE_OPERATIONS:
        fieldname = BOOLEAN_OPERATION_FIELDS[operation]
        if cint(spec.get(fieldname)):
            edge_rate_total += get_operation_rate(
                operation, currency, OPERATION_PRICING_BASIS[operation]
            )

    unit_amount = 0.0
    for operation, count_field in UNIT_OPERATIONS.items():
        count = cint(spec.get(count_field))
        if count > 0:
            rate = get_operation_rate(operation, currency, OPERATION_PRICING_BASIS[operation])
            unit_amount += count * rate

    spec.area_processing_amount_per_piece = flt(chargeable_area * area_rate_total, 2)
    spec.edge_processing_amount_per_piece = flt(edge_meter * edge_rate_total, 2)
    spec.unit_processing_amount_per_piece = flt(unit_amount, 2)


def calculate_final_pricing(spec) -> None:
    area_m2 = flt(spec.get("area_m2"))

    if area_m2 <= 0:
        frappe.throw("Area (m²) must be greater than zero to calculate pricing.")

    raw_selling = flt(spec.get("raw_selling_amount_per_finished_piece"))
    raw_cost = flt(spec.get("raw_cost_per_finished_piece"))
    # Use selling price for customer material charge when available; fall back to buying cost.
    material_charge = raw_selling if raw_selling > 0 else raw_cost
    spec.calculated_amount_per_piece = flt(
        material_charge + flt(spec.processing_amount_per_piece),
        2,
    )
    spec.calculated_rate_per_m2 = flt(spec.calculated_amount_per_piece / area_m2, 6)

    manual_rate = flt(spec.get("manual_selling_rate_per_m2"))
    if manual_rate > 0:
        spec.selling_rate_per_m2 = manual_rate
        spec.price_override = 1
    else:
        spec.selling_rate_per_m2 = spec.calculated_rate_per_m2
        spec.price_override = 0

    spec.price_difference_per_m2 = flt(spec.selling_rate_per_m2 - spec.calculated_rate_per_m2, 6)
    spec.rate_per_piece = flt(spec.selling_rate_per_m2 * area_m2, 2)

    qty = flt(spec.get("qty")) or 1
    spec.amount = flt(spec.rate_per_piece * qty, 2)


def calculate_margin_fields(spec) -> None:
    """Compute gross profit and margin fields using buying cost vs selling price."""
    area_m2 = flt(spec.get("area_m2"))
    qty = flt(spec.get("qty")) or 1

    estimated_cost = flt(spec.get("raw_cost_per_finished_piece")) + flt(
        spec.get("processing_amount_per_piece")
    )
    spec.estimated_cost_per_piece = flt(estimated_cost, 2)

    rate_per_piece = flt(spec.get("rate_per_piece"))
    gross_profit_per_piece = flt(rate_per_piece - estimated_cost, 2)
    spec.gross_profit_per_piece = gross_profit_per_piece
    spec.gross_profit_per_m2 = flt(gross_profit_per_piece / area_m2, 6) if area_m2 > 0 else 0
    spec.gross_profit_percent = (
        flt(gross_profit_per_piece / rate_per_piece * 100, 2) if rate_per_piece != 0 else 0
    )
    spec.total_gross_profit = flt(gross_profit_per_piece * qty, 2)


def collect_pricing_warnings(spec) -> list[str]:
    """Return a list of pricing warning messages for the spec."""
    warnings = []

    if flt(spec.get("raw_sheet_rate_per_piece")) <= 0:
        warnings.append(
            "Warning: Raw buying cost is missing. Profit calculation may be wrong."
        )

    if flt(spec.get("raw_sheet_selling_rate_per_piece")) <= 0:
        warnings.append(
            "Warning: Raw selling price is missing. Customer material price is incomplete."
        )

    zero_rate_ops = [
        (row.get("operation") if hasattr(row, "get") else row.operation)
        for row in (spec.get("operation_pricing") or [])
        if flt(row.get("rate") if hasattr(row, "get") else row.rate) <= 0
    ]
    if zero_rate_ops:
        warnings.append(
            f"Warning: Some selected operations have zero rate: {', '.join(zero_rate_ops)}"
        )

    return warnings


def pricing_result(spec) -> dict:
    return {
        "raw_sheet_rate_per_piece": flt(spec.get("raw_sheet_rate_per_piece")),
        "raw_cost_per_m2": flt(spec.get("raw_cost_per_m2")),
        "raw_cost_per_finished_piece": flt(spec.get("raw_cost_per_finished_piece")),
        "raw_sheet_selling_rate_per_piece": flt(spec.get("raw_sheet_selling_rate_per_piece")),
        "raw_selling_rate_per_m2": flt(spec.get("raw_selling_rate_per_m2")),
        "raw_selling_amount_per_finished_piece": flt(
            spec.get("raw_selling_amount_per_finished_piece")
        ),
        "edge_meter": flt(spec.get("edge_meter")),
        "chargeable_area_m2": flt(spec.get("chargeable_area_m2")),
        "total_chargeable_area_m2": flt(spec.get("total_chargeable_area_m2")),
        "area_processing_amount_per_piece": flt(spec.get("area_processing_amount_per_piece")),
        "edge_processing_amount_per_piece": flt(spec.get("edge_processing_amount_per_piece")),
        "unit_processing_amount_per_piece": flt(spec.get("unit_processing_amount_per_piece")),
        "processing_amount_per_piece": flt(spec.get("processing_amount_per_piece")),
        "calculated_amount_per_piece": flt(spec.get("calculated_amount_per_piece")),
        "calculated_rate_per_m2": flt(spec.get("calculated_rate_per_m2")),
        "manual_selling_rate_per_m2": flt(spec.get("manual_selling_rate_per_m2")),
        "selling_rate_per_m2": flt(spec.get("selling_rate_per_m2")),
        "price_override": cint(spec.get("price_override")),
        "price_difference_per_m2": flt(spec.get("price_difference_per_m2")),
        "rate_per_piece": flt(spec.get("rate_per_piece")),
        "amount": flt(spec.get("amount")),
        "estimated_cost_per_piece": flt(spec.get("estimated_cost_per_piece")),
        "gross_profit_per_piece": flt(spec.get("gross_profit_per_piece")),
        "gross_profit_per_m2": flt(spec.get("gross_profit_per_m2")),
        "gross_profit_percent": flt(spec.get("gross_profit_percent")),
        "total_gross_profit": flt(spec.get("total_gross_profit")),
    }
