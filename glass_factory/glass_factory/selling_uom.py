"""Sell glass in square metres while stock, cutting, and processing count pieces.

Spec-generated Quotation / Sales Order / Delivery Note / Sales Invoice rows use
Square Meter as the selling UOM and the Item's own stock UOM (Nos) as the stock UOM.
The conversion factor is pieces per square metre for that Item's own dimensions, so
``qty (m2) * conversion_factor = stock_qty (pieces)``.

Everything that compares production or delivery limits must use ``row_pieces`` or
``sales_order_item_pieces`` so both sides of a comparison are in the same unit. Rows
sold in Nos, and ordinary non-glass rows, have a conversion factor of 1 and behave as
before.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import cint, flt

from glass_factory.glass_factory.item_resolver import _parse_raw_item_code, item_role

AREA_ITEM_ROLES = ("Raw Sheet", "Final")
SELLING_DOCTYPES = ("Quotation", "Sales Order", "Delivery Note", "Sales Invoice")
AREA_SNAP_TOLERANCE_M2 = 0.0006  # quantity entry rounds to 3 decimals in the desk UI


def find_area_uom() -> str | None:
	"""Return the site's area UOM without raising."""
	from glass_factory.glass_factory.settings_validation import find_area_uom_name

	return find_area_uom_name()


def require_selling_area_uom() -> str:
	area_uom = find_area_uom()
	if not area_uom:
		frappe.throw(
			_("Square Meter Unit of Measure is required to sell glass by area. Create it in ERPNext first.")
		)
	if cint(frappe.db.get_value("UOM", area_uom, "must_be_whole_number")):
		frappe.throw(
			_("UOM {0} must allow fractional quantities so glass can be sold by area.").format(area_uom)
		)
	return area_uom


def item_area_m2(item_code: str) -> float:
	"""Exact area of one piece, from the Item's own dimensions (item code is the source of truth)."""
	length, width = frappe.db.get_value("Item", item_code, ["gf_length_mm", "gf_width_mm"]) or (0, 0)
	if flt(length) <= 0 or flt(width) <= 0:
		parsed = _parse_raw_item_code(item_code) or {}
		length = flt(parsed.get("length_mm"))
		width = flt(parsed.get("width_mm"))
	if flt(length) <= 0 or flt(width) <= 0:
		return 0.0
	return flt(flt(length) * flt(width) / 1_000_000, 6)


def qty_precision() -> int:
	"""Decimals ERPNext keeps on a selling Quantity (3 unless the site raised its float precision)."""
	return cint(frappe.get_precision("Sales Order Item", "qty")) or 3


def billing_area_m2(area_m2: float) -> float:
	"""Area billed per finished piece: the exact area rounded to the Quantity precision.

	ERPNext rounds a selling Quantity to that precision *before* it derives the stock quantity,
	so an exact area such as 0.699678 m² could never convert back to a whole number of pieces.
	Billing each piece at its rounded area makes any whole number of pieces an exact Quantity.
	Sizes whose area is already a clean multiple (1200 x 800 mm = 0.96 m²) are unchanged.
	"""
	rounded = flt(area_m2, qty_precision())
	return rounded if rounded > 0 else flt(area_m2, 6)


def item_billing_area_m2(item_code: str) -> float:
	return billing_area_m2(item_area_m2(item_code))


def pieces_per_m2(area_m2: float) -> float:
	return flt(1 / area_m2, 9) if flt(area_m2) > 0 else 0.0


def ensure_area_uom_conversion(item) -> bool:
	"""Make sure an Item document carries the Square Meter conversion for its own dimensions.

	Modifies ``item.uoms`` in place and returns True when it changed. Safe to call from
	validate. Skips Items without dimensions or stocked in the area UOM itself.
	"""
	area_uom = find_area_uom()
	if not area_uom or (item.get("stock_uom") or "") == area_uom:
		return False

	length = flt(item.get("gf_length_mm"))
	width = flt(item.get("gf_width_mm"))
	if length <= 0 or width <= 0:
		return False

	area = flt(length * width / 1_000_000, 6)
	if item.get("gf_glass_item_role") == "Final":
		area = billing_area_m2(area)  # finished pieces are sold by their billed area
	factor = pieces_per_m2(area)
	for row in item.get("uoms") or []:
		if row.uom == area_uom:
			if abs(flt(row.conversion_factor) - factor) <= 1e-9:
				return False
			row.conversion_factor = factor
			return True

	item.append("uoms", {"uom": area_uom, "conversion_factor": factor})
	return True


def ensure_item_area_conversion(item_code: str) -> float:
	"""Create or correct the Square Meter conversion on a saved glass Item and return it."""
	item = frappe.get_doc("Item", item_code)
	area_uom = require_selling_area_uom()
	if (item.stock_uom or "") == area_uom:
		frappe.throw(
			_("Glass Item {0} is stocked in {1}. Glass must be stocked in pieces so cutting counts pieces.").format(
				item_code, area_uom
			)
		)
	if ensure_area_uom_conversion(item):
		item.save(ignore_permissions=True)
	for row in item.get("uoms") or []:
		if row.uom == area_uom:
			return flt(row.conversion_factor)
	frappe.throw(_("Glass Item {0} has no valid dimensions for a Square Meter conversion.").format(item_code))


def backfill_area_uom_conversions() -> int:
	"""Idempotently add the conversion to existing Raw Sheet and Final Items."""
	if not find_area_uom():
		return 0
	changed = 0
	names = frappe.get_all(
		"Item",
		filters={"gf_glass_item_role": ["in", list(AREA_ITEM_ROLES)]},
		pluck="name",
	)
	for name in names:
		item = frappe.get_doc("Item", name)
		if ensure_area_uom_conversion(item):
			item.save(ignore_permissions=True)
			changed += 1
	return changed


def is_area_sold_row(row) -> bool:
	"""True for a Final glass Item row sold in the area UOM against a piece stock UOM."""
	item_code = row.get("item_code")
	# Cheap exit first: ordinary rows (Nos, Kg, ...) must cost no database lookups.
	from glass_factory.glass_factory.settings_validation import AREA_UOM_ALIASES

	if not item_code or (row.get("uom") or "").strip().lower() not in AREA_UOM_ALIASES:
		return False
	area_uom = find_area_uom()
	if not area_uom or (row.get("uom") or "") != area_uom:
		return False
	stock_uom = row.get("stock_uom") or frappe.get_cached_value("Item", item_code, "stock_uom")
	if stock_uom == area_uom:
		return False
	return item_role(item_code) == "Final"


def row_pieces(row) -> float:
	"""Stock-UOM quantity of any selling row, in the same unit cutting and stock use."""
	stock_qty = row.get("stock_qty")
	if stock_qty not in (None, ""):
		return flt(stock_qty, 6)
	return flt(flt(row.get("qty")) * (flt(row.get("conversion_factor")) or 1), 6)


def sales_order_item_pieces(so_item_name: str) -> float:
	values = frappe.db.get_value(
		"Sales Order Item", so_item_name, ["qty", "stock_qty", "conversion_factor"], as_dict=True
	)
	if not values:
		return 0.0
	return row_pieces(values)


def delivered_pieces(so_detail: str, exclude_parent: str | None = None) -> float:
	"""Pieces already delivered against a Sales Order row, net of returns."""
	dni = frappe.qb.DocType("Delivery Note Item")
	dn = frappe.qb.DocType("Delivery Note")
	query = (
		frappe.qb.from_(dni)
		.join(dn)
		.on(dn.name == dni.parent)
		.select(Sum(dni.stock_qty))
		.where((dni.so_detail == so_detail) & (dn.docstatus == 1))
	)
	if exclude_parent:
		query = query.where(dn.name != exclude_parent)
	total = flt((query.run() or [[0]])[0][0])

	sii = frappe.qb.DocType("Sales Invoice Item")
	si = frappe.qb.DocType("Sales Invoice")
	invoiced = (
		frappe.qb.from_(sii)
		.join(si)
		.on(si.name == sii.parent)
		.select(Sum(sii.stock_qty))
		.where((sii.so_detail == so_detail) & (si.docstatus == 1) & (si.update_stock == 1))
	)
	if exclude_parent:
		invoiced = invoiced.where(si.name != exclude_parent)
	total += flt((invoiced.run() or [[0]])[0][0])
	return flt(total, 6)


def _snap_tolerance_pieces(factor: float) -> float:
	return max(1e-6, min(AREA_SNAP_TOLERANCE_M2 * factor, 0.25))


def _same_factor(first: float, second: float) -> bool:
	return abs(flt(first) - flt(second)) <= max(1e-6, flt(second) * 1e-6)


def _source_row_factor(row) -> float | None:
	"""Conversion factor of the line this row fulfils, returns, or was quoted from.

	A document keeps the billing rule of the order it came from, so orders taken before the
	billing precision was changed can still be delivered, invoiced, and returned.
	"""
	for fieldname, doctype in (
		("dn_detail", "Delivery Note Item"),
		("so_detail", "Sales Order Item"),
		("quotation_item", "Quotation Item"),
	):
		name = row.get(fieldname)
		if name:
			factor = flt(frappe.db.get_value(doctype, name, "conversion_factor"))
			if factor:
				return factor
	return None


def snap_area_quantities(doc, method=None) -> None:
	"""Before validate: make area rows hold a whole number of pieces.

	A quantity typed in m² that is within quantity rounding of a whole number of pieces is
	replaced by the exact billed area of those pieces. Anything else is rejected, so a row can
	never hold a fraction of a piece. Returns are snapped the same way, so a typed return
	credits exactly what the returned pieces were billed at.
	"""
	if doc.doctype not in SELLING_DOCTYPES or doc.docstatus != 0:
		return
	for row in doc.get("items") or []:
		if not is_area_sold_row(row) or not flt(row.get("qty")):
			continue
		item_area = item_billing_area_m2(row.item_code)
		if item_area <= 0:
			frappe.throw(_("Row {0}: {1} has no valid dimensions.").format(row.idx, row.item_code))
		item_factor = pieces_per_m2(item_area)
		factor = flt(row.get("conversion_factor"))
		if not factor:
			factor = row.conversion_factor = item_factor
		area = item_area
		if not _same_factor(factor, item_factor):
			source_factor = _source_row_factor(row)
			if source_factor is None or not _same_factor(factor, source_factor):
				frappe.throw(
					_(
						"Row {0}: UOM conversion factor {1} does not match {2}, which has {3} pieces per m². "
						"Refresh the row so the conversion comes from the item's own dimensions."
					).format(row.idx, factor, row.item_code, item_factor)
				)
			area = flt(1 / factor, 6)
		pieces = flt(row.qty) * factor
		whole = round(pieces)
		if whole == 0 or abs(pieces - whole) > _snap_tolerance_pieces(factor):
			frappe.throw(
				_(
					"Row {0}: {1} m² is not a whole number of pieces. One {2} piece is {3} m², "
					"so enter a multiple of {3} m²."
				).format(row.idx, flt(row.qty, 6), row.item_code, area)
			)
		row.qty = flt(abs(whole) * area, 9) * (1 if whole > 0 else -1)


def snap_stock_quantities(doc, method=None) -> None:
	"""After ERPNext derives stock_qty: store exactly the whole piece count."""
	if doc.doctype not in SELLING_DOCTYPES:
		return
	for row in doc.get("items") or []:
		if not is_area_sold_row(row) or not flt(row.get("qty")):
			continue
		factor = flt(row.get("conversion_factor")) or 1
		pieces = flt(row.qty) * factor
		whole = round(pieces)
		# ERPNext rounds Qty to 3 decimals after the quantity was snapped, so allow that much.
		if abs(pieces - whole) > _snap_tolerance_pieces(factor):
			frappe.throw(
				_("Row {0}: {1} m² converts to {2} pieces, which is not a whole number.").format(
					row.idx, flt(row.qty, 6), flt(pieces, 6)
				)
			)
		row.stock_qty = whole


# --------------------------------------------------------------------------- billing precision rule

BILLING_AREA_DECIMALS = (2, 3, 6)
BILLING_QUANTITY_FIELDS = (
	("Quotation Item", "qty"),
	("Sales Order Item", "qty"),
	("Sales Order Item", "delivered_qty"),
	("Delivery Note Item", "qty"),
	("Delivery Note Item", "returned_qty"),
	("Sales Invoice Item", "qty"),
	("Sales Invoice Item", "delivered_qty"),
)
# Informational physical-area columns on the selling rows, kept as exact as the billed area.
BILLING_AREA_COLUMNS = ("gf_area_m2", "gf_total_area_m2")
BILLING_ROW_DOCTYPES = ("Quotation Item", "Sales Order Item", "Delivery Note Item", "Sales Invoice Item")


def _billing_precision_fields() -> list[tuple[str, str]]:
	fields = list(BILLING_QUANTITY_FIELDS)
	for doctype in BILLING_ROW_DOCTYPES:
		meta = frappe.get_meta(doctype)
		fields.extend((doctype, fieldname) for fieldname in BILLING_AREA_COLUMNS if meta.has_field(fieldname))
	return fields


def set_billing_area_precision(decimals: int) -> dict:
	"""Choose how many decimals of m² one finished piece is billed at.

	Each piece is billed at its area rounded to the Quantity precision of the selling item rows,
	so this one setting is the billing rule:

	* 6: exact area. Every integer-millimetre size converts to a whole number of pieces and back,
	  and the specification, Quotation, Sales Order, Delivery Note, Sales Invoice and returns all
	  carry the same area and total.
	* 3: ERPNext's default. Each piece is billed at its area rounded to 0.001 m².
	* 2: each piece is billed at its area rounded to 0.01 m².

	Only the Quantity fields of the four selling documents change; stock, cutting, and every other
	Float field on the site keep the system precision. Orders already taken keep the rule they were
	quoted under, because every document follows the conversion factor of the line it fulfils.
	Run on the site, for example:
	bench --site SITE execute glass_factory.glass_factory.selling_uom.set_billing_area_precision --args "[6]"
	"""
	decimals = cint(decimals)
	if decimals not in BILLING_AREA_DECIMALS:
		frappe.throw(_("Billing area precision must be one of {0}.").format(", ".join(map(str, BILLING_AREA_DECIMALS))))

	for doctype, fieldname in _billing_precision_fields():
		name = frappe.db.exists(
			"Property Setter", {"doc_type": doctype, "field_name": fieldname, "property": "precision"}
		)
		if name:
			frappe.delete_doc("Property Setter", name, force=True, ignore_permissions=True)
		if decimals != 3:
			frappe.make_property_setter(
				{
					"doctype": doctype,
					"doctype_or_field": "DocField",
					"fieldname": fieldname,
					"property": "precision",
					"value": str(decimals),
					"property_type": "Select",
				},
				is_system_generated=False,
			)
	frappe.clear_cache()
	converted = backfill_area_uom_conversions()
	open_orders = frappe.db.sql(
		"""select count(distinct so.name) from `tabSales Order` so join `tabSales Order Item` item on item.parent = so.name
		where so.docstatus = 1 and so.status not in ('Completed', 'Closed', 'Cancelled') and item.gf_from_glass_specification = 1""",
	)[0][0]
	frappe.db.commit()
	return {"decimals": decimals, "item_conversions_updated": converted, "open_glass_orders_keeping_their_rule": open_orders}
