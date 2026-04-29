"""Quotation and Sales Order hooks for the glass factory cut-pieces workflow."""
import frappe
from frappe.utils import flt

# Marker embedded in Quotation Item description so we can identify generated rows
_GCP_MARKER = "__glass_cut_piece__"

def compute_cut_pieces(doc, method=None):
    """
    Syncs the cut_pieces child table to standard Items.
    """
    # 1. Loop guard to prevent infinite recursion during save
    if getattr(doc.flags, "cut_pieces_already_synced", False):
        return

    # 2. Basic validation
    if not doc.get("cut_pieces"):
        doc.flags.cut_pieces_already_synced = True
        return

    settings = _get_settings()
    
    # Use a local constant if not defined globally
    marker = locals().get("_GCP_MARKER", "")

    # 3. Filter out previously generated rows
    # We keep manually added rows and ignore empty rows
    valid_manual_items = []
    for item in doc.get("items"):
        description = item.description or ""
        if item.item_code and marker not in description:
            valid_manual_items.append(item)
    
    doc.set("items", valid_manual_items)

    # 4. Generate fresh items from cut_pieces
    for cut_piece in doc.cut_pieces:
        parent_item = cut_piece.parent_item
        if not parent_item:
            continue

        # Area Calculation
        piece_area_m2 = max(
            (flt(cut_piece.length_mm) * flt(cut_piece.width_mm)) / 1_000_000.0,
            flt(settings.min_chargeable_area_m2) or 0.05,
        )

        # Pricing Logic
        material_rate = _get_item_price(
            parent_item, 
            doc.get("selling_price_list") or "Standard Selling", 
            doc.get("currency")
        )
        
        material_cost = piece_area_m2 * material_rate
        edge_cost = _compute_edge_cost(cut_piece, settings)
        price_per_piece = material_cost + edge_cost

        total_qty_pieces = flt(cut_piece.qty) or 1
        total_area = piece_area_m2 * total_qty_pieces
        line_total = price_per_piece * total_qty_pieces
        
        # Determine the rate per UOM (m2)
        rate_per_m2 = line_total / total_area if total_area > 0 else 0
        description = _build_description(cut_piece)

        # Fetch Item Metadata efficiently
        item_name = frappe.get_cached_value("Item", parent_item, "item_name")
        stock_uom = frappe.get_cached_value("Item", parent_item, "stock_uom") or "Sq m"

        # 5. Build the row dictionary
        item_row_data = {
            "item_code": parent_item,
            "item_name": item_name or parent_item,
            "qty": total_area,
            "uom": stock_uom,
            "conversion_factor": 1.0,
            "stock_qty": total_area,
            "price_list_rate": rate_per_m2,
            "discount_percentage": 0,
            "rate": rate_per_m2,
            "amount": line_total, # Explicitly use total to avoid rounding drift
            "description": f"{description}\n{marker}",         
        }

        if doc.doctype == "Sales Order":
            item_row_data["delivery_date"] = doc.delivery_date

        # 6. Append and Link
        # doc.append returns the newly created Row object
        new_item_row = doc.append("items", item_row_data)
        
        # Ensure the row has a name (ID) so the link is stable
        if not new_item_row.name:
            new_item_row.set_new_name()
            
        cut_piece.linked_quotation_item = new_item_row.name

    # 7. Finalize
    doc.flags.cut_pieces_already_synced = True
    doc.run_method("calculate_taxes_and_totals")
# def compute_cut_pieces(doc, method=None):
# 	"""
# 	Mirror the cut_pieces child table to standard Quotation/Sales Order Items.

# 	Strategy: items tagged with _GCP_MARKER in their description were generated
# 	by a previous run of this hook.  We remove those and re-add fresh ones so
# 	the pricing always stays in sync with the current cut_pieces table.
# 	Non-tagged items (manually added by the user) are left untouched.

# 	Loop guard prevents re-entrancy when the save triggered by this hook fires
# 	another before_save event.
# 	"""
# 	if getattr(doc.flags, "cut_pieces_already_synced", False):
# 		return

# 	if not doc.get("cut_pieces"):
# 		doc.flags.cut_pieces_already_synced = True
# 		return

# 	settings = _get_settings()

# 	# Remove previously generated rows and blank rows; keep non-empty manually-added rows
# 	doc.items = [
# 		item for item in doc.get("items")
# 		if item.get("item_code") and _GCP_MARKER not in (item.description or "")
# 	]

# 	for cut_piece in doc.cut_pieces:
# 		parent_item = cut_piece.parent_item
# 		if not parent_item:
# 			continue

# 		piece_area_m2 = max(
# 			(flt(cut_piece.length_mm) * flt(cut_piece.width_mm)) / 1_000_000.0,
# 			flt(settings.min_chargeable_area_m2) or 0.05,
# 		)

# 		material_rate = _get_item_price(parent_item, doc.get("selling_price_list") or "Standard Selling", doc.get("currency"))
# 		material_cost = piece_area_m2 * material_rate
# 		edge_cost = _compute_edge_cost(cut_piece, settings)
# 		price_per_piece = material_cost + edge_cost

# 		total_qty_pieces = flt(cut_piece.qty) or 1
# 		total_area = piece_area_m2 * total_qty_pieces
# 		line_total = price_per_piece * total_qty_pieces
# 		rate_per_m2 = line_total / total_area if total_area > 0 else 0

# 		description = _build_description(cut_piece)

# 		item_meta = frappe.db.get_value(
# 			"Item",
# 			parent_item,
# 			["item_name", "stock_uom", "description"],
# 			as_dict=True,
# 		) or {}

# 		uom = item_meta.get("stock_uom") or "Sq m"
# 		item_row =  {
# 			"item_code": parent_item,
# 			"item_name": item_meta.get("item_name") or parent_item,
# 			"qty": total_area,
# 			"uom": uom,
# 			"conversion_factor": 1.0,
# 			"stock_qty": total_area,
# 			"ordered_qty": 0,
# 			"price_list_rate": rate_per_m2,
# 			"discount_percentage": 0,
# 			"rate": rate_per_m2,
# 			"amount": rate_per_m2 * total_area,
# 			"description": description,			
# 		}
# 		if doc.doctype == "Sales Order":
# 			delivery_date = doc.get("delivery_date")
# 			item_row["delivery_date"] = delivery_date

# 		doc.append("items", item_row)
	
# 		# Pre-assign a name so linked_quotation_item is stable across saves.
# 		# Frappe honours pre-set child row names during insert.
# 		if not item_row.name:
# 			item_row.name = frappe.generate_hash(length=10)

# 		cut_piece.linked_quotation_item = item_row.name

# 	doc.flags.cut_pieces_already_synced = True
# 	doc.run_method("calculate_taxes_and_totals")


def copy_cut_pieces_to_so(doc, method=None):
	"""
	Before a Sales Order is inserted, copy cut_pieces from the source Quotation.

	This fires on before_insert so the child rows are included in the initial
	write — no second save required.
	"""
	if doc.get("cut_pieces"):
		return  # already populated (e.g. manual SO)

	# Identify source quotation via the prevdoc_docname on any SO item
	quotation_name = None
	for item in doc.get("items") or []:
		if item.get("prevdoc_docname"):
			quotation_name = item.prevdoc_docname
			break

	if not quotation_name:
		return

	try:
		quotation = frappe.get_doc("Quotation", quotation_name)
	except frappe.DoesNotExistError:
		return

	for cp in quotation.get("cut_pieces") or []:
		doc.append("cut_pieces", {
			"parent_item": cp.parent_item,
			"length_mm": cp.length_mm,
			"width_mm": cp.width_mm,
			"qty": cp.qty,
			"polish_top": cp.polish_top,
			"polish_bottom": cp.polish_bottom,
			"polish_left": cp.polish_left,
			"polish_right": cp.polish_right,
			"bevel": cp.bevel,
			"holes": cp.holes,
			"rate_override": cp.rate_override,
			"user_label": cp.user_label,
		})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_settings():
	return frappe.get_doc("Glass Cutting Settings")


def _compute_edge_cost(cut_piece, settings):
	edge_rate = flt(settings.get("edge_polish_rate"))
	hole_rate = flt(settings.get("hole_drill_rate"))
	bevel_rate = flt(settings.get("bevel_rate"))

	edge_meters = 0.0
	if cut_piece.get("polish_top"):
		edge_meters += flt(cut_piece.length_mm) / 1000.0
	if cut_piece.get("polish_bottom"):
		edge_meters += flt(cut_piece.length_mm) / 1000.0
	if cut_piece.get("polish_left"):
		edge_meters += flt(cut_piece.width_mm) / 1000.0
	if cut_piece.get("polish_right"):
		edge_meters += flt(cut_piece.width_mm) / 1000.0

	return (
		edge_meters * edge_rate
		+ flt(cut_piece.get("holes")) * hole_rate
		+ (bevel_rate if cut_piece.get("bevel") else 0.0)
	)


def _get_item_price(item_code, price_list, currency=None):
	filters = {"item_code": item_code, "price_list": price_list}
	if currency:
		filters["currency"] = currency
	rate = frappe.db.get_value("Item Price", filters, "price_list_rate")
	if not rate:
		rate = frappe.db.get_value("Item Price", {"item_code": item_code, "price_list": price_list}, "price_list_rate")
	return flt(rate) or flt(frappe.db.get_value("Item", item_code, "valuation_rate"))


def _build_description(cut_piece):
	lines = [f"{cut_piece.length_mm} × {cut_piece.width_mm} mm"]

	finishes = []
	if cut_piece.get("polish_top"):
		finishes.append("Polish Top")
	if cut_piece.get("polish_bottom"):
		finishes.append("Polish Bottom")
	if cut_piece.get("polish_left"):
		finishes.append("Polish Left")
	if cut_piece.get("polish_right"):
		finishes.append("Polish Right")
	if finishes:
		lines.append("Finishes: " + ", ".join(finishes))
	if cut_piece.get("bevel"):
		lines.append("Bevel: Yes")
	if cut_piece.get("holes"):
		lines.append(f"Holes: {cut_piece.holes}")

	lines.append(_GCP_MARKER)  # invisible marker for next-save detection
	return "\n".join(lines)
