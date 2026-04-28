"""
Stock Entry builder for glass-factory Repack operations.

ERPNext Repack costing — key invariants read from the source:
  • validate_repack_entry() throws if there are multiple unique is_finished_item
    item codes and ANY of them lacks set_basic_rate_manually = 1.
  • get_basic_rate_for_repacked_items() returns None when there are multiple
    unique finished item codes, then falls back to get_valuation_rate() which
    fails for brand-new items.
  • Therefore: set set_basic_rate_manually = 1 on every finished-good row and
    compute area-proportional rates ourselves before insert.

Serial numbers — approach:
  • use_serial_batch_fields = 1 on every serialized row, combined with the
    serial_no field, activates the legacy serial path that ERPNext still
    honours in v14/v15.  This avoids needing to create Serial-and-Batch Bundle
    documents (which require row.name to be known before insert).
  • Pre-create Serial No documents for all outputs so their names are known
    when we build the SE rows.
  • For inputs (consumed sheets) the Serial No already exists in the DB.

One Stock Entry per glass spec (material).  Multiple specs → multiple SEs,
all linked back to the same Cutting Job via the custom cutting_job field.
"""
import frappe
from collections import defaultdict
from frappe.utils import flt, nowdate, nowtime
from typing import Dict, List, Tuple, Any


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_stock_entries(cutting_job, payload: Dict[str, Any]) -> List:
	"""
	Build (but do not insert) Stock Entry documents for one Cutting Job.

	Returns a list of unsaved Stock Entry documents.  The caller is responsible
	for inserting and submitting each one inside an appropriate transaction.
	"""
	settings = frappe.get_doc("Glass Cutting Settings")
	_assert_settings(settings)

	consumed: List[Dict] = payload.get("consumed", [])
	remnants: List[Dict] = payload.get("remnants", [])
	sheets: List[Dict] = payload.get("sheets", [])
	scrap_m2: float = flt(payload.get("scrap_m2", 0))

	# Group consumed sheets by material (one SE per spec)
	consumed_by_spec: Dict[str, List[Dict]] = defaultdict(list)
	for item in consumed:
		consumed_by_spec[item["material"]].append(item)

	# Group tabular pieces by parent material
	pieces_by_spec: Dict[str, List[Dict]] = defaultdict(list)
	for sheet in sheets:
		for piece in sheet["pieces"]:
			material = _parent_item_for_piece(piece, cutting_job)
			if material:
				pieces_by_spec[material].append(piece)

	remnants_by_spec: Dict[str, List[Dict]] = defaultdict(list)
	for rem in remnants:
		remnants_by_spec[rem["material"]].append(rem)

	stock_entries = []
	for material, spec_consumed in consumed_by_spec.items():
		se = _build_entry_for_spec(
			cutting_job=cutting_job,
			material=material,
			spec_consumed=spec_consumed,
			pieces=pieces_by_spec.get(material, []),
			remnants=remnants_by_spec.get(material, []),
			scrap_m2=scrap_m2,
			settings=settings,
		)
		stock_entries.append(se)

	return stock_entries


# ---------------------------------------------------------------------------
# Per-spec SE builder
# ---------------------------------------------------------------------------

def _build_entry_for_spec(
	cutting_job,
	material: str,
	spec_consumed: List[Dict],
	pieces: List[Dict],
	remnants: List[Dict],
	scrap_m2: float,
	settings,
) -> "frappe.model.document.Document":

	company = frappe.get_value("Cutting Job", cutting_job.name, "company") \
		or frappe.defaults.get_user_default("Company") \
		or frappe.get_value("Company", {"is_group": 0}, "name")

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Repack"
	se.purpose = "Repack"
	se.company = company
	se.posting_date = nowdate()
	se.posting_time = nowtime()
	se.cutting_job = cutting_job.name

	# ------------------------------------------------------------------
	# Step 1: resolve which existing serials to consume (FIFO)
	# ------------------------------------------------------------------
	consumed_serials: List[str] = []  # ordered list of serial_no names
	consumed_serials_by_row: List[Tuple[Dict, List[str]]] = []

	for item in spec_consumed:
		serials = _fifo_pick_serials(
			item_code=item["material"],
			length=item["length"],
			width=item["width"],
			qty=int(item["qty"]),
			warehouse=settings.raw_warehouse,
		)
		consumed_serials.extend(serials)
		consumed_serials_by_row.append((item, serials))

	# ------------------------------------------------------------------
	# Step 2: determine total input cost for area-proportional allocation
	# ------------------------------------------------------------------
	total_input_cost = _estimate_input_cost(material, len(consumed_serials), settings.raw_warehouse)

	# ------------------------------------------------------------------
	# Step 3: collect all outputs and compute their areas
	# ------------------------------------------------------------------
	# Pre-create Serial No docs for outputs so names are known before SE rows
	cut_piece_rows: List[Dict] = []  # {serial_no, area_m2, item_code}
	remnant_rows: List[Dict] = []
	scrap_qty: float = 0.0

	for piece in pieces:
		area = (flt(piece["length"]) * flt(piece["width"])) / 1_000_000.0
		cut_code = _lazy_create_item(material, "CUT", piece["length"], piece["width"])
		serial = _new_serial(cut_code, piece["length"], piece["width"], cutting_job.name)
		cut_piece_rows.append({"item_code": cut_code, "serial_no": serial, "area_m2": area})

	min_area = flt(settings.min_remnant_area_m2) or 0.1
	min_side = flt(settings.min_remnant_side_mm) or 100

	for rem in remnants:
		length = flt(rem["length"])
		width = flt(rem["width"])
		qty = int(rem["qty"])
		area = (length * width) / 1_000_000.0

		if area < min_area or length < min_side or width < min_side:
			# Too small — add to geometric scrap total
			scrap_qty += area * qty
			continue

		rem_code = _lazy_create_item(material, "REM", length, width)
		for _ in range(qty):
			serial = _new_serial(rem_code, length, width, cutting_job.name)
			remnant_rows.append({"item_code": rem_code, "serial_no": serial, "area_m2": area})

	scrap_qty += scrap_m2

	# ------------------------------------------------------------------
	# Step 4: compute area-proportional rates
	# ------------------------------------------------------------------
	total_output_area = (
		sum(r["area_m2"] for r in cut_piece_rows)
		+ sum(r["area_m2"] for r in remnant_rows)
		+ scrap_qty
	)

	def proportional_rate(area: float) -> float:
		"""Cost allocated to one unit of this output, proportional to its area."""
		if total_output_area <= 0:
			return 0.0
		return (area / total_output_area) * total_input_cost

	# ------------------------------------------------------------------
	# Step 5: add source rows (consumed sheets)
	# ------------------------------------------------------------------
	for item, serials in consumed_serials_by_row:
		for sn in serials:
			se.append("items", {
				"item_code": item["material"],
				"s_warehouse": settings.raw_warehouse,
				"qty": 1,
				"transfer_qty": 1,
				"uom": frappe.get_cached_value("Item", item["material"], "stock_uom") or "Nos",
				"stock_uom": frappe.get_cached_value("Item", item["material"], "stock_uom") or "Nos",
				"conversion_factor": 1,
				"is_finished_item": 0,
				"use_serial_batch_fields": 1,
				"serial_no": sn,
				# basic_rate for outgoing rows is overridden by ERPNext's
				# set_rate_for_outgoing_items during validate — leave at 0.
			})

	# ------------------------------------------------------------------
	# Step 6: add target rows (cut pieces)
	# ------------------------------------------------------------------
	for row in cut_piece_rows:
		uom = frappe.get_cached_value("Item", row["item_code"], "stock_uom") or "Nos"
		se.append("items", {
			"item_code": row["item_code"],
			"t_warehouse": settings.cut_pieces_warehouse,
			"qty": 1,
			"transfer_qty": 1,
			"uom": uom,
			"stock_uom": uom,
			"conversion_factor": 1,
			"is_finished_item": 1,
			"set_basic_rate_manually": 1,
			"basic_rate": proportional_rate(row["area_m2"]),
			"use_serial_batch_fields": 1,
			"serial_no": row["serial_no"],
		})

	# ------------------------------------------------------------------
	# Step 7: add remnant rows
	# ------------------------------------------------------------------
	for row in remnant_rows:
		uom = frappe.get_cached_value("Item", row["item_code"], "stock_uom") or "Nos"
		se.append("items", {
			"item_code": row["item_code"],
			"t_warehouse": settings.remnants_warehouse,
			"qty": 1,
			"transfer_qty": 1,
			"uom": uom,
			"stock_uom": uom,
			"conversion_factor": 1,
			"is_finished_item": 1,
			"set_basic_rate_manually": 1,
			"basic_rate": proportional_rate(row["area_m2"]),
			"use_serial_batch_fields": 1,
			"serial_no": row["serial_no"],
		})

	# ------------------------------------------------------------------
	# Step 8: add scrap row (non-serialised, zero rate)
	# ------------------------------------------------------------------
	if scrap_qty > 0:
		scrap_item = settings.scrap_item_code or "Glass Scrap"
		scrap_uom = frappe.get_cached_value("Item", scrap_item, "stock_uom") or "Sq m"
		se.append("items", {
			"item_code": scrap_item,
			"t_warehouse": settings.scrap_warehouse,
			"qty": scrap_qty,
			"transfer_qty": scrap_qty,
			"uom": scrap_uom,
			"stock_uom": scrap_uom,
			"conversion_factor": 1,
			"is_finished_item": 1,
			"set_basic_rate_manually": 1,
			"basic_rate": 0,  # scrap written off at zero
		})

	return se


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_settings(settings):
	required = ["raw_warehouse", "cut_pieces_warehouse", "remnants_warehouse", "scrap_warehouse"]
	missing = [f for f in required if not settings.get(f)]
	if missing:
		frappe.throw(
			f"Glass Cutting Settings is missing warehouse links: {', '.join(missing)}. "
			"Run the after_install hook or configure them manually."
		)


def _fifo_pick_serials(item_code: str, length: float, width: float, qty: int, warehouse: str) -> List[str]:
	"""
	FIFO-pick existing serial numbers matching item + dimensions from warehouse.
	Raises if fewer serials are available than requested.
	"""
	rows = frappe.db.get_all(
		"Serial No",
		filters={
			"item_code": item_code,
			"length_mm": int(length),
			"width_mm": int(width),
			"warehouse": warehouse,
			"status": "Active",
		},
		fields=["name"],
		order_by="purchase_date asc, creation asc",
		limit=qty,
	)

	if len(rows) < qty:
		frappe.throw(
			f"Not enough stock of {item_code} ({int(length)}×{int(width)} mm) in {warehouse}. "
			f"Need {qty}, found {len(rows)}."
		)

	return [r["name"] for r in rows]


def _estimate_input_cost(material: str, qty_consumed: int, raw_warehouse: str) -> float:
	"""
	Estimate total cost of consumed sheets using the current Bin valuation rate.

	Bin.valuation_rate is the warehouse-level weighted average and is the best
	approximation available before the Stock Entry is submitted.  The actual
	debit will be computed by ERPNext's SLE engine on submit, but we need a
	figure now to distribute proportionally across outputs.
	"""
	val_rate = flt(
		frappe.db.get_value(
			"Bin",
			{"item_code": material, "warehouse": raw_warehouse},
			"valuation_rate",
		)
	)
	return val_rate * qty_consumed


def _lazy_create_item(parent_code: str, suffix: str, length: float, width: float) -> str:
	"""
	Return item code for a derived cut-piece or remnant item, creating it if absent.

	Uses frappe.copy_doc(parent) so all glass-spec custom fields (glass_type,
	thickness_mm, color_tint, coating, UOM, item_group, has_serial_no …) are
	inherited without touching the Item Variant / Attribute machinery.
	"""
	derived_code = f"{parent_code}-{suffix}-{int(length)}x{int(width)}"
	if frappe.db.exists("Item", derived_code):
		return derived_code

	parent_item = frappe.get_doc("Item", parent_code)
	new_item = frappe.copy_doc(parent_item, ignore_links=True)
	new_item.item_code = derived_code
	new_item.item_name = derived_code
	new_item.has_variants = 0
	# Store dimensions on the item for the Remnant Inventory report
	new_item.length_mm = int(length)
	new_item.width_mm = int(width)
	new_item.insert(ignore_permissions=True)
	frappe.db.commit()

	return derived_code


def _new_serial(item_code: str, length: float, width: float, cutting_job_name: str) -> str:
	"""
	Insert a new Serial No document for a cut piece or remnant output.

	Returns the auto-assigned serial number name so it can be referenced in the
	Stock Entry item row.
	"""
	serial = frappe.new_doc("Serial No")
	serial.item_code = item_code
	serial.length_mm = int(length)
	serial.width_mm = int(width)
	serial.area_m2 = (length * width) / 1_000_000.0
	serial.cutting_job = cutting_job_name
	# Warehouse and status will be set by ERPNext's stock ledger on SE submit
	serial.insert(ignore_permissions=True)
	frappe.db.commit()
	return serial.name


def _parent_item_for_piece(piece: Dict, cutting_job) -> str:
	"""
	Resolve the parent glass-spec item code for a tabular piece dict.

	Pieces are linked to a Sales Order item via (sales_order, sales_order_item_idx).
	The idx stored in the label is 0-based and refers to the cut_pieces child
	table on the Sales Order — NOT the items table.
	"""
	so_name = piece.get("sales_order")
	if not so_name:
		return ""

	so_item_idx = int(piece.get("sales_order_item_idx", 0))
	cut_pieces = frappe.get_all(
		"Glass Cut Piece",
		filters={"parent": so_name, "parenttype": "Sales Order"},
		fields=["parent_item"],
		order_by="idx asc",
	)

	if so_item_idx < len(cut_pieces):
		return cut_pieces[so_item_idx].get("parent_item", "")

	return ""
