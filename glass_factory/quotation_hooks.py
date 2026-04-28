"""Quotation and Sales Order hooks for the glass factory cut-pieces workflow."""
import frappe
from frappe.utils import flt

# Marker embedded in Quotation Item description so we can identify generated rows
_GCP_MARKER = "__glass_cut_piece__"


def compute_cut_pieces(doc, method=None):
	"""
	Mirror the cut_pieces child table to standard Quotation/Sales Order Items.

	Strategy: items tagged with _GCP_MARKER in their description were generated
	by a previous run of this hook.  We remove those and re-add fresh ones so
	the pricing always stays in sync with the current cut_pieces table.
	Non-tagged items (manually added by the user) are left untouched.

	Loop guard prevents re-entrancy when the save triggered by this hook fires
	another before_save event.
	"""
	if getattr(doc.flags, "cut_pieces_already_synced", False):
		return

	if not doc.get("cut_pieces"):
		doc.flags.cut_pieces_already_synced = True
		return

	settings = _get_settings()

	# Remove previously generated rows; keep manually-added rows
	doc.items = [item for item in doc.get("items") if _GCP_MARKER not in (item.description or "")]

	for cut_piece in doc.cut_pieces:
		parent_item = cut_piece.parent_item
		if not parent_item:
			continue

		piece_area_m2 = max(
			(flt(cut_piece.length_mm) * flt(cut_piece.width_mm)) / 1_000_000.0,
			flt(settings.min_chargeable_area_m2) or 0.05,
		)

		material_rate = flt(frappe.get_cached_value("Item", parent_item, "standard_rate"))
		material_cost = piece_area_m2 * material_rate
		edge_cost = _compute_edge_cost(cut_piece, settings)
		price_per_piece = material_cost + edge_cost

		total_qty_pieces = flt(cut_piece.qty) or 1
		total_area = piece_area_m2 * total_qty_pieces
		line_total = price_per_piece * total_qty_pieces
		rate_per_m2 = line_total / total_area if total_area > 0 else 0

		description = _build_description(cut_piece)

		item_row = doc.append("items", {
			"item_code": parent_item,
			"qty": total_area,
			"uom": "Sq m",
			"rate": rate_per_m2,
			"description": description,
		})

		# Pre-assign a name so linked_quotation_item is stable across saves.
		# Frappe honours pre-set child row names during insert.
		if not item_row.name:
			item_row.name = frappe.generate_hash(length=10)

		cut_piece.linked_quotation_item = item_row.name

	doc.flags.cut_pieces_already_synced = True


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
