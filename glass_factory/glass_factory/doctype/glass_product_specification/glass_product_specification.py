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
    fetch_raw_sheet_rate,
    fetch_raw_sheet_selling_rate,
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

    def fetch_raw_sheet_selling_rate(self):
        rate = fetch_raw_sheet_selling_rate(self)
        if rate > 0:
            self.raw_sheet_selling_rate_per_piece = rate
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

        selling_rate = fetch_raw_sheet_selling_rate(self)
        if selling_rate > 0:
            self.raw_sheet_selling_rate_per_piece = selling_rate

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
            "raw_sheet_selling_rate_per_piece": flt(self.get("raw_sheet_selling_rate_per_piece")),
            "item_code_preview": self.get("item_code_preview"),
            "operation_code_preview": self.get("operation_code_preview"),
            "technical_summary": self.get("technical_summary"),
        }
        result.update(pricing_result(self))
        return result

    @frappe.whitelist()
    def refresh_pricing(self):
        """Fetch prices, rebuild operation pricing rows, recalculate, and save."""
        self.pull_from_raw_sheet_item()
        self._validate_glass_dimensions()
        self.calculate_area()

        if flt(self.raw_sheet_rate_per_piece) <= 0:
            self.fetch_raw_sheet_rate(fetch_from_item_price=True)

        selling_rate = fetch_raw_sheet_selling_rate(self)
        if selling_rate > 0:
            self.raw_sheet_selling_rate_per_piece = selling_rate

        build_operation_pricing_rows(self)
        self.calculate_pricing()
        self.save()

        result = pricing_result(self)
        result["currency"] = get_spec_currency(self)
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
        self.status = "Ready"
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
