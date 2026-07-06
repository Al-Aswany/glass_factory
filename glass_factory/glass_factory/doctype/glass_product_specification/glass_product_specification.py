"""Glass Product Specification controller."""

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime

from glass_factory.glass_factory.item_resolver import (
    build_final_item_code_from_spec,
    build_glass_operation_code,
    generate_items_from_spec,
    get_item_glass_meta,
    spec_is_used_in_transaction,
    validate_glass_type,
)
from glass_factory.glass_factory.spec_pricing import (
    build_operation_pricing_rows,
    calculate_raw_cost,
    calculate_spec_pricing,
    collect_pricing_warnings,
    fetch_raw_selling_rate_per_m2,
    fetch_raw_sheet_rate,
    get_spec_currency,
    pricing_result,
)

OPERATION_LABELS = {
    "polish": "Polish",
    "bevel": "Bevel",
    "temper": "Temper",
    "sandblast": "Sandblast",
    "laminate": "Laminate",
}

TECHNICAL_SPEC_FIELDS = (
    "glass_type",
    "thickness_mm",
    "length_mm",
    "width_mm",
    "raw_sheet_item",
    "raw_sheet_length_mm",
    "raw_sheet_width_mm",
    "polish",
    "bevel",
    "temper",
    "sandblast",
    "laminate",
    "hole_count",
    "special_hole_count",
    "slot_count",
    "special_slot_count",
)

TRANSACTION_READY_STATUSES = frozenset({"Ready", "Items Generated", "Used"})
PRICING_REFRESHED_STATUSES = frozenset({"Priced", "Ready", "Items Generated", "Used"})

REFRESH_PRICING_REQUIRED_FIELDS = (
    "customer",
    "company",
    "currency",
    "price_list",
    "raw_sheet_item",
    "length_mm",
    "width_mm",
    "qty",
)


class GlassProductSpecification(Document):
    def validate(self):
        self.pull_from_raw_sheet_item()
        self._validate_glass_dimensions()
        self.calculate_area()
        self.validate_counts()
        self.validate_primary_design_attachment()
        self.update_item_code_preview()
        self.build_technical_summary()
        self.calculate_pricing()
        self._check_regeneration_required()

    def before_submit(self):
        self._validate_submit_prerequisites()

    def on_cancel(self):
        self.status = "Cancelled"

    def on_submit(self):
        if self.status != "Cancelled":
            self.db_set("status", "Submitted", update_modified=False)

    def _validate_submit_prerequisites(self):
        if not cint(self.items_generated):
            frappe.throw("Generate items before submitting this specification.")
        if flt(self.rate_per_piece) <= 0:
            frappe.throw("Final Price / Piece must be greater than zero before submit.")
        final_item = self.get("final_item_code") or self.get("generated_item")
        if not final_item:
            frappe.throw("Final item must exist before submit.")
        if self.get("generation_status") not in ("Generated",):
            frappe.throw("Generation status must be Generated before submit.")

    def _validate_refresh_pricing_prerequisites(self):
        for fieldname in REFRESH_PRICING_REQUIRED_FIELDS:
            if not self.get(fieldname):
                frappe.throw(f"{self.meta.get_label(fieldname)} is required before refreshing pricing.")

        if flt(self.get("qty")) <= 0:
            frappe.throw(f"{self.meta.get_label('qty')} must be greater than zero.")

        self.pull_from_raw_sheet_item()
        self._validate_glass_dimensions()
        self.calculate_area()

        if flt(self.raw_sheet_area_m2) <= 0:
            frappe.throw("Raw Sheet Area m² must be greater than zero.")

        if flt(self.raw_sheet_rate_per_piece) <= 0:
            self.fetch_raw_sheet_rate(fetch_from_item_price=True)
        if flt(self.raw_sheet_rate_per_piece) <= 0:
            frappe.throw(
                "Missing buying Item Price for the raw sheet item. Configure a buying price before refreshing pricing."
            )

        if flt(self.raw_selling_rate_per_m2) <= 0:
            self.fetch_raw_selling_rate_per_m2(throw_if_missing=True)

        build_operation_pricing_rows(self)
        for row in self.get("operation_pricing") or []:
            if flt(row.rate) <= 0:
                frappe.throw(
                    f"Missing operation rate for {row.operation}. Configure it in Glass Factory Settings."
                )

    def _has_valid_pricing(self) -> bool:
        return flt(self.calculated_amount_per_piece) > 0 and bool(self.item_code_preview)

    def _validate_generate_items_prerequisites(self):
        if (self.status or "Draft") not in PRICING_REFRESHED_STATUSES and not self._has_valid_pricing():
            frappe.throw("Refresh pricing before generating items.")

        if flt(self.calculated_amount_per_piece) <= 0:
            frappe.throw("Calculated Price / Piece must be greater than zero before generating items.")

        if not self.item_code_preview:
            frappe.throw("Item Code Preview is required before generating items.")

        final_item = self.get("final_item_code") or self.get("generated_item")
        if final_item and not cint(self.allow_regeneration):
            if self.generation_status != "Regeneration Required":
                frappe.throw(
                    "Items already generated. Enable Allow Regeneration or use Regenerate Items."
                )

    def _pricing_ready_for_generation(self) -> bool:
        return (
            flt(self.calculated_amount_per_piece) > 0
            and bool(self.item_code_preview)
            and (self.status or "Draft") in PRICING_REFRESHED_STATUSES
        )

    def _update_status_after_pricing_refresh(self):
        if self.docstatus == 1 or (self.status or "Draft") == "Cancelled":
            return
        if cint(self.items_generated) and self.generation_status == "Generated":
            self.status = "Items Generated"
            return
        self.status = "Ready" if self._pricing_ready_for_generation() else "Priced"

    def calculate_pricing(self):
        calculate_spec_pricing(self)

    def calculate_raw_cost(self):
        calculate_raw_cost(self)

    def calculate_processing_quantities(self):
        from glass_factory.glass_factory.spec_pricing import calculate_processing_quantities

        calculate_processing_quantities(self)

    def calculate_processing_amounts(self):
        from glass_factory.glass_factory.spec_pricing import calculate_processing_amounts

        calculate_processing_amounts(self)

    def calculate_final_pricing(self):
        from glass_factory.glass_factory.spec_pricing import calculate_final_pricing

        calculate_final_pricing(self)

    def fetch_raw_sheet_rate(self, *, fetch_from_item_price: bool = False):
        rate = fetch_raw_sheet_rate(self, fetch_from_item_price=fetch_from_item_price)
        if rate > 0:
            self.raw_sheet_rate_per_piece = rate
        return rate

    def fetch_raw_selling_rate_per_m2(self, *, throw_if_missing: bool = False):
        rate = fetch_raw_selling_rate_per_m2(self, throw_if_missing=throw_if_missing)
        if rate > 0:
            self.raw_selling_rate_per_m2 = rate
        return rate

    def calculate_area(self):
        length = flt(self.length_mm)
        width = flt(self.width_mm)
        qty = flt(self.get("qty")) or 1
        self.area_m2 = flt((length * width) / 1_000_000, 6) if length > 0 and width > 0 else 0
        self.total_area_m2 = flt(self.area_m2 * qty, 6)

    def pull_from_raw_sheet_item(self):
        if not self.raw_sheet_item:
            self.glass_type = ""
            self.thickness_mm = 0
            self.raw_sheet_length_mm = 0
            self.raw_sheet_width_mm = 0
            self.raw_sheet_area_m2 = 0
            self.raw_selling_rate_per_m2 = 0
            return

        meta = get_item_glass_meta(self.raw_sheet_item)
        glass_type = meta.get("gf_base_glass_type") or ""
        if glass_type:
            self.glass_type = glass_type

        thickness = flt(meta.get("gf_thickness_mm"))
        if thickness > 0:
            self.thickness_mm = thickness

        length = flt(meta.get("gf_length_mm"))
        width = flt(meta.get("gf_width_mm"))
        if not length or not width:
            item = frappe.get_doc("Item", self.raw_sheet_item)
            length = flt(item.get("gf_length_mm")) or length
            width = flt(item.get("gf_width_mm")) or width

        self.raw_sheet_length_mm = length
        self.raw_sheet_width_mm = width
        self.raw_sheet_area_m2 = (
            flt((length * width) / 1_000_000, 6) if length > 0 and width > 0 else 0
        )

        rate = fetch_raw_sheet_rate(self, fetch_from_item_price=True)
        if rate > 0:
            self.raw_sheet_rate_per_piece = rate

        # Selling is priced per m² directly from Item Price (UOM = Square Meter).
        # No fallback to a per-sheet selling price; leave 0 here and let
        # refresh_pricing() raise a clear error when it is required.
        selling_rate = fetch_raw_selling_rate_per_m2(self)
        if selling_rate > 0:
            self.raw_selling_rate_per_m2 = selling_rate

    def validate_counts(self):
        for fieldname in ("hole_count", "special_hole_count", "slot_count", "special_slot_count"):
            if cint(self.get(fieldname)) < 0:
                frappe.throw(f"{self.meta.get_label(fieldname)} cannot be negative.")

    def validate_primary_design_attachment(self):
        primary_rows = [row for row in self.design_attachments or [] if cint(row.is_primary)]
        if len(primary_rows) > 1:
            frappe.throw("Only one design attachment can be marked as primary.")

    def update_item_code_preview(self):
        if (
            not self.glass_type
            or flt(self.thickness_mm) <= 0
            or flt(self.length_mm) <= 0
            or flt(self.width_mm) <= 0
        ):
            self.item_code_preview = ""
            self.operation_code_preview = ""
            return

        self.item_code_preview = build_final_item_code_from_spec(self)
        self.operation_code_preview = build_glass_operation_code(
            polish=cint(self.polish),
            bevel=cint(self.bevel),
            hole_count=cint(self.hole_count),
            special_hole_count=cint(self.special_hole_count),
            slot_count=cint(self.slot_count),
            special_slot_count=cint(self.special_slot_count),
            temper=cint(self.temper),
            sandblast=cint(self.sandblast),
            laminate=cint(self.laminate),
        )

    def build_technical_summary(self):
        if (
            not self.glass_type
            or flt(self.thickness_mm) <= 0
            or flt(self.length_mm) <= 0
            or flt(self.width_mm) <= 0
        ):
            self.technical_summary = ""
            return

        parts = [
            f"{self.glass_type.upper()} {flt(self.thickness_mm):g}mm",
            f"{flt(self.length_mm):g} x {flt(self.width_mm):g} mm",
            f"Area {flt(self.area_m2):g} m²",
        ]

        operations = self._operation_summary_parts()
        if operations:
            parts.append(f"Operations: {', '.join(operations)}")

        self.technical_summary = ", ".join(parts)

    def _validate_glass_dimensions(self):
        if not self.raw_sheet_item:
            frappe.throw("Raw Glass Sheet is required.")
        if self.glass_type:
            validate_glass_type(self.glass_type, context="Glass type")
        if flt(self.thickness_mm) <= 0:
            frappe.throw("Thickness must be greater than zero.")
        if flt(self.length_mm) <= 0:
            frappe.throw("Finished Length must be greater than zero.")
        if flt(self.width_mm) <= 0:
            frappe.throw("Finished Width must be greater than zero.")

    def _operation_summary_parts(self) -> list[str]:
        parts: list[str] = []
        for fieldname, label in OPERATION_LABELS.items():
            if cint(self.get(fieldname)):
                parts.append(label)

        count_labels = {
            "hole_count": "Hole",
            "special_hole_count": "Special Hole",
            "slot_count": "Slot",
            "special_slot_count": "Special Slot",
        }
        for fieldname, label in count_labels.items():
            count = cint(self.get(fieldname))
            if count > 0:
                suffix = "s" if count != 1 else ""
                parts.append(f"{count} {label}{suffix}")

        return parts

    def _check_regeneration_required(self):
        if not cint(self.items_generated):
            return

        before = self.get_doc_before_save()
        if not before:
            return

        for fieldname in TECHNICAL_SPEC_FIELDS:
            if self.get(fieldname) != before.get(fieldname):
                self.generation_status = "Regeneration Required"
                return

    @frappe.whitelist()
    def refresh_preview(self):
        self.pull_from_raw_sheet_item()
        self.calculate_area()
        self.update_item_code_preview()
        self.build_technical_summary()
        if flt(self.length_mm) > 0 and flt(self.width_mm) > 0:
            self.calculate_pricing()
        result = {
            "glass_type": self.get("glass_type"),
            "thickness_mm": self.get("thickness_mm"),
            "area_m2": self.get("area_m2"),
            "total_area_m2": self.get("total_area_m2"),
            "raw_sheet_length_mm": self.get("raw_sheet_length_mm"),
            "raw_sheet_width_mm": self.get("raw_sheet_width_mm"),
            "raw_sheet_area_m2": self.get("raw_sheet_area_m2"),
            "raw_sheet_rate_per_piece": flt(self.get("raw_sheet_rate_per_piece")),
            "raw_selling_rate_per_m2": flt(self.get("raw_selling_rate_per_m2")),
            "item_code_preview": self.get("item_code_preview"),
            "operation_code_preview": self.get("operation_code_preview"),
            "technical_summary": self.get("technical_summary"),
        }
        result.update(pricing_result(self))
        return result

    @frappe.whitelist()
    def refresh_pricing(self):
        """Fetch prices, rebuild operation pricing rows, recalculate, and save.

        Raw selling is strict: a selling Item Price for the raw sheet item with UOM
        Square Meter is required. There is no fallback to a per-sheet selling price;
        if none is found, this raises a clear validation error instead of saving.
        """
        if self.docstatus == 1 and not cint(self.allow_regeneration):
            frappe.throw("Submitted specifications cannot be repriced unless regeneration is allowed.")

        self._validate_refresh_pricing_prerequisites()
        self.calculate_pricing()
        self._update_status_after_pricing_refresh()
        self.save()

        result = pricing_result(self)
        result["currency"] = get_spec_currency(self)
        result["status"] = self.status
        warnings = collect_pricing_warnings(self)
        if warnings:
            result["warnings"] = warnings

        return result

    @frappe.whitelist()
    def refresh_operation_rates(self):
        """Rebuild operation pricing rows from selected operations, preserving manual overrides."""
        self.calculate_area()
        if flt(self.length_mm) > 0 and flt(self.width_mm) > 0:
            from glass_factory.glass_factory.spec_pricing import calculate_processing_quantities

            calculate_processing_quantities(self)

        build_operation_pricing_rows(self, reset_overrides=False)
        self.calculate_pricing()
        self.save()

        result = pricing_result(self)
        result["currency"] = get_spec_currency(self)
        warnings = collect_pricing_warnings(self)
        if warnings:
            result["warnings"] = warnings
        return result

    @frappe.whitelist()
    def reset_operation_rates_to_settings(self):
        """Clear all manual operation rate overrides and reload from Glass Factory Settings."""
        self.calculate_area()
        if flt(self.length_mm) > 0 and flt(self.width_mm) > 0:
            from glass_factory.glass_factory.spec_pricing import calculate_processing_quantities

            calculate_processing_quantities(self)

        build_operation_pricing_rows(self, reset_overrides=True)
        self.calculate_pricing()
        self.save()

        result = pricing_result(self)
        result["currency"] = get_spec_currency(self)
        return result

    @frappe.whitelist()
    def generate_items(self):
        if self.docstatus == 1:
            frappe.throw("Submitted specifications cannot generate items.")

        self._validate_generate_items_prerequisites()
        self.validate()

        if cint(self.items_generated):
            if self.generation_status != "Regeneration Required" and not cint(
                self.allow_regeneration
            ):
                frappe.throw(
                    "Items already generated. Enable regeneration or use Regenerate Items."
                )

        items = generate_items_from_spec(self)

        self.raw_item_code = items["raw_item_code"]
        self.cut_wip_item_code = items["cut_wip_item_code"]
        self.final_item_code = items["final_item_code"]
        self.generated_item = items["final_item_code"]
        self.items_generated = 1
        self.generated_on = now_datetime()
        self.generated_by = frappe.session.user
        self.generation_status = "Generated"
        self.status = "Items Generated"
        self.save()

        return {
            "raw_item_code": self.raw_item_code,
            "cut_wip_item_code": self.cut_wip_item_code,
            "final_item_code": self.final_item_code,
            "generated_item": self.generated_item,
            "items_generated": self.items_generated,
            "generation_status": self.generation_status,
            "status": self.status,
        }

    @frappe.whitelist()
    def reset_generated_items(self):
        if not cint(self.items_generated):
            return {
                "items_generated": 0,
                "generation_status": self.generation_status or "Not Generated",
                "status": self.status,
            }

        if spec_is_used_in_transaction(self.name):
            frappe.throw(
                "Cannot reset generated item links because this specification is already used in a transaction."
            )

        self.raw_item_code = None
        self.cut_wip_item_code = None
        self.final_item_code = None
        self.generated_item = None
        self.items_generated = 0
        self.generated_on = None
        self.generated_by = None
        self.generation_status = "Not Generated"
        self.status = "Draft"
        self.save()

        return {
            "raw_item_code": self.raw_item_code,
            "cut_wip_item_code": self.cut_wip_item_code,
            "final_item_code": self.final_item_code,
            "generated_item": self.generated_item,
            "items_generated": self.items_generated,
            "generation_status": self.generation_status,
            "status": self.status,
        }
