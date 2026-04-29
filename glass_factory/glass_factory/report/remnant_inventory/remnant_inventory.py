"""Remnant Inventory query report."""
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import frappe
from frappe import _


REM_DIM_RE = re.compile(r"-REM-(\d+)x(\d+)", re.IGNORECASE)


def execute(filters=None):
	filters = filters or {}
	columns = _columns()
	data = _data(filters)
	message = _message(data, filters)
	chart = _chart(data)
	report_summary = _summary(data)
	return columns, data, message, chart, report_summary


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def _columns() -> List[Dict]:
	return [
		{"fieldname": "item_code", "label": _("Item Code"),
		 "fieldtype": "Link", "options": "Item", "width": 230},
		{"fieldname": "parent_item", "label": _("Parent Item"),
		 "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "size_label", "label": _("Size"),
		 "fieldtype": "Data", "width": 80},
		{"fieldname": "length_mm", "label": _("Length (mm)"),
		 "fieldtype": "Int", "width": 100},
		{"fieldname": "width_mm", "label": _("Width (mm)"),
		 "fieldtype": "Int", "width": 100},
		{"fieldname": "area_m2", "label": _("Area (m²)"),
		 "fieldtype": "Float", "precision": 3, "width": 100},
		{"fieldname": "qty_on_hand", "label": _("Qty on Hand"),
		 "fieldtype": "Float", "precision": 2, "width": 110},
		{"fieldname": "warehouse", "label": _("Warehouse"),
		 "fieldtype": "Link", "options": "Warehouse", "width": 160},
		{"fieldname": "valuation_rate", "label": _("Valuation Rate"),
		 "fieldtype": "Currency", "width": 120},
		{"fieldname": "total_value", "label": _("Total Value"),
		 "fieldtype": "Currency", "width": 120},
		{"fieldname": "cutting_job", "label": _("Source Cutting Job"),
		 "fieldtype": "Link", "options": "Cutting Job", "width": 160},
		{"fieldname": "age_days", "label": _("Age (days)"),
		 "fieldtype": "Int", "width": 100},
	]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _data(filters: Dict) -> List[Dict]:
	parent_filter = filters.get("parent_item")
	warehouse_filter = filters.get("warehouse")
	min_area = float(filters.get("min_area_m2") or 0)
	include_empty = bool(filters.get("include_zero_stock"))

	# Pull all remnant items in one query
	item_filters = [["item_code", "like", "%-REM-%"]]
	if parent_filter:
		item_filters.append(["item_code", "like", f"{parent_filter}%"])

	items = frappe.db.get_list(
		"Item",
		filters=item_filters,
		fields=["name", "item_name", "creation", "disabled"],
		limit_page_length=0,
	)
	if not items:
		return []

	item_codes = [i["name"] for i in items]

	# Bins (one item can be in many warehouses)
	bin_filters = {"item_code": ["in", item_codes]}
	if warehouse_filter:
		bin_filters["warehouse"] = warehouse_filter
	bins = frappe.db.get_list(
		"Bin",
		filters=bin_filters,
		fields=["item_code", "warehouse", "actual_qty", "valuation_rate", "stock_value"],
		limit_page_length=0,
	)
	bins_by_item: Dict[str, List[Dict]] = defaultdict(list)
	for b in bins:
		bins_by_item[b["item_code"]].append(b)

	# Source cutting jobs by item (most recent serial wins)
	jobs_by_item = _cutting_jobs_for_items(item_codes)

	now = frappe.utils.now_datetime()
	rows: List[Dict] = []

	for it in items:
		code = it["name"]
		length, width = _parse_dimensions(code)
		area_m2 = (length * width) / 1_000_000 if length and width else 0
		if area_m2 < min_area:
			continue

		bin_rows = bins_by_item.get(code, [])
		if not bin_rows:
			if include_empty:
				rows.append(_make_row(it, length, width, area_m2,
				                      qty=0, warehouse="", val_rate=0, stock_value=0,
				                      job=jobs_by_item.get(code, ""), now=now))
			continue

		for b in bin_rows:
			qty = float(b.get("actual_qty") or 0)
			if qty <= 0 and not include_empty:
				continue
			rows.append(_make_row(
				it, length, width, area_m2,
				qty=qty,
				warehouse=b.get("warehouse") or "",
				val_rate=float(b.get("valuation_rate") or 0),
				stock_value=float(b.get("stock_value") or 0),
				job=jobs_by_item.get(code, ""),
				now=now,
			))

	rows.sort(key=lambda r: (r["total_value"], r["area_m2"]), reverse=True)
	return rows


def _make_row(item, length, width, area_m2, qty, warehouse, val_rate, stock_value, job, now):
	# ERPNext stores stock_value already; prefer it when present
	total_value = stock_value if stock_value else qty * val_rate
	creation = item.get("creation")
	age_days = (now - creation).days if creation else 0
	return {
		"item_code": item["name"],
		"item_name": item.get("item_name") or item["name"],
		"parent_item": _parent_code(item["name"]),
		"size_label": _size_bucket(area_m2),
		"length_mm": int(length),
		"width_mm": int(width),
		"area_m2": round(area_m2, 3),
		"qty_on_hand": qty,
		"warehouse": warehouse,
		"valuation_rate": val_rate,
		"total_value": total_value,
		"cutting_job": job,
		"age_days": age_days,
	}


def _parse_dimensions(item_code: str) -> Tuple[int, int]:
	m = REM_DIM_RE.search(item_code)
	if not m:
		return 0, 0
	return int(m.group(1)), int(m.group(2))


def _parent_code(item_code: str) -> str:
	if "-REM-" in item_code:
		return item_code.split("-REM-")[0]
	return item_code


def _size_bucket(area_m2: float) -> str:
	if area_m2 >= 2.0:
		return "XL"
	if area_m2 >= 1.0:
		return "L"
	if area_m2 >= 0.4:
		return "M"
	if area_m2 > 0:
		return "S"
	return "—"


def _cutting_jobs_for_items(item_codes: List[str]) -> Dict[str, str]:
	if not item_codes:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT item_code, cutting_job, MAX(creation) AS creation
		FROM `tabSerial No`
		WHERE item_code IN %(items)s
		  AND IFNULL(cutting_job, '') != ''
		GROUP BY item_code, cutting_job
		ORDER BY creation DESC
		""",
		{"items": tuple(item_codes)},
		as_dict=True,
	)
	out: Dict[str, str] = {}
	for r in rows:
		out.setdefault(r["item_code"], r["cutting_job"])
	return out


# ---------------------------------------------------------------------------
# Summary cards
# ---------------------------------------------------------------------------

def _summary(rows: List[Dict]) -> List[Dict]:
	if not rows:
		return []

	total_qty = sum(r["qty_on_hand"] for r in rows)
	total_area = sum(r["area_m2"] * r["qty_on_hand"] for r in rows)
	total_value = sum(r["total_value"] for r in rows)
	distinct_items = len({r["item_code"] for r in rows})
	largest = max(rows, key=lambda r: r["area_m2"])

	currency = frappe.defaults.get_global_default("currency") or ""

	return [
		{"label": _("Distinct Remnants"), "value": distinct_items,
		 "indicator": "Blue", "datatype": "Int"},
		{"label": _("Total Pieces"), "value": f"{total_qty:.0f}",
		 "indicator": "Green", "datatype": "Data"},
		{"label": _("Total Area"), "value": f"{total_area:.2f} m²",
		 "indicator": "Orange", "datatype": "Data"},
		{"label": _("Total Value"), "value": f"{total_value:,.2f} {currency}".strip(),
		 "indicator": "Purple", "datatype": "Data"},
		{"label": _("Largest Piece"),
		 "value": f"{largest['length_mm']}×{largest['width_mm']} mm",
		 "indicator": "Grey", "datatype": "Data"},
	]


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------

def _chart(rows: List[Dict]) -> Optional[Dict]:
	if not rows:
		return None
	by_parent: Dict[str, float] = defaultdict(float)
	for r in rows:
		by_parent[r["parent_item"] or "—"] += r["area_m2"] * r["qty_on_hand"]

	labels = list(by_parent.keys())
	values = [round(v, 3) for v in by_parent.values()]
	return {
		"data": {
			"labels": labels,
			"datasets": [{"name": _("Stocked Area (m²)"), "values": values}],
		},
		"type": "bar",
		"colors": ["#5B8FF9"],
		"barOptions": {"spaceRatio": 0.4},
	}


# ---------------------------------------------------------------------------
# HTML message: visual remnant tiles
# ---------------------------------------------------------------------------

PALETTE = [
	"#5B8FF9", "#5AD8A6", "#F6BD16", "#E86452", "#6DC8EC",
	"#945FB9", "#FF9845", "#1E9493", "#FF99C3", "#7666F9",
]


def _message(rows: List[Dict], filters: Dict) -> str:
	if not rows:
		return _styles() + (
			'<div class="rm-empty">No remnants match the current filters. '
			'Try clearing filters or enable <i>Include Zero Stock</i>.</div>'
		)

	# Aggregate per item_code (sum qty across warehouses) for the visual grid
	by_item: Dict[str, Dict] = {}
	for r in rows:
		key = r["item_code"]
		if key not in by_item:
			by_item[key] = {**r, "qty_on_hand": 0, "total_value": 0, "warehouses": set()}
		by_item[key]["qty_on_hand"] += r["qty_on_hand"]
		by_item[key]["total_value"] += r["total_value"]
		if r["warehouse"]:
			by_item[key]["warehouses"].add(r["warehouse"])

	items = sorted(by_item.values(), key=lambda r: r["area_m2"] * r["qty_on_hand"], reverse=True)

	# Color by parent_item
	parents = []
	for it in items:
		if it["parent_item"] not in parents:
			parents.append(it["parent_item"])
	parent_color = {p: PALETTE[i % len(PALETTE)] for i, p in enumerate(parents)}

	# Find max length/width for proportional rendering
	max_len = max((it["length_mm"] for it in items), default=1) or 1
	max_wid = max((it["width_mm"] for it in items), default=1) or 1
	tile_box = 180.0  # px; the largest dimension maps to this

	tiles = []
	for it in items:
		length = it["length_mm"]
		width = it["width_mm"]
		ratio_w = (length / max_len) if max_len else 0
		ratio_h = (width / max_wid) if max_wid else 0
		# Scale so largest dim hits tile_box
		scale = tile_box / max(max_len, max_wid)
		w_px = max(40, int(length * scale))
		h_px = max(28, int(width * scale))
		color = parent_color.get(it["parent_item"], "#888")
		warehouses = ", ".join(sorted(it["warehouses"])) or "—"
		job_html = (f'<a href="/app/cutting-job/{it["cutting_job"]}" class="rm-link">'
		            f'{frappe.utils.escape_html(it["cutting_job"])}</a>'
		            if it["cutting_job"] else '<span class="rm-muted">—</span>')

		tiles.append(f"""
		<div class="rm-tile">
			<div class="rm-tile-visual" style="height:{tile_box + 20}px;">
				<div class="rm-shape" style="
					width:{w_px}px; height:{h_px}px;
					background:{color}; border-color:{color};">
					<span class="rm-shape-label">{length}×{width}</span>
				</div>
			</div>
			<div class="rm-tile-body">
				<div class="rm-tile-title">
					<a href="/app/item/{frappe.utils.escape_html(it["item_code"])}"
					   class="rm-link" title="{frappe.utils.escape_html(it["item_code"])}">
						{frappe.utils.escape_html(it["item_code"])}
					</a>
					<span class="rm-bucket rm-bucket-{it["size_label"]}">{it["size_label"]}</span>
				</div>
				<div class="rm-tile-meta">
					<span><b>{it["qty_on_hand"]:.0f}</b> pcs</span>
					<span>{it["area_m2"]:.2f} m²/pc</span>
					<span class="rm-value">{it["total_value"]:,.2f}</span>
				</div>
				<div class="rm-tile-foot">
					<span title="Warehouse">📦 {frappe.utils.escape_html(warehouses)}</span>
					<span title="Source job">🔧 {job_html}</span>
				</div>
			</div>
		</div>
		""")

	parent_chips = "".join(
		f'<span class="rm-chip"><i style="background:{c}"></i>'
		f'{frappe.utils.escape_html(p)}</span>'
		for p, c in parent_color.items()
	)

	return f"""
	{_styles()}
	<div class="rm-header">
		<div>
			<div class="rm-title">Remnant Inventory</div>
			<div class="rm-sub">Visual overview of stocked off-cuts • tiles are scaled proportionally</div>
		</div>
		<div class="rm-legend">{parent_chips}</div>
	</div>
	<div class="rm-grid">{"".join(tiles)}</div>
	"""


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _styles() -> str:
	return """
<style>
.rm-empty {
	padding: 28px; text-align: center; color: var(--text-muted);
	background: var(--bg-color); border: 1px dashed var(--border-color);
	border-radius: 8px; margin: 16px 0;
}
.rm-header {
	display: flex; justify-content: space-between; align-items: center;
	flex-wrap: wrap; gap: 14px;
	padding: 14px 18px; margin-bottom: 14px;
	background: linear-gradient(135deg, var(--bg-color) 0%, var(--fg-color) 100%);
	border: 1px solid var(--border-color); border-radius: 10px;
}
.rm-title { font-size: 18px; font-weight: 700; color: var(--text-color); }
.rm-sub { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.rm-legend { display: flex; flex-wrap: wrap; gap: 8px; }
.rm-chip {
	display: inline-flex; align-items: center; gap: 6px;
	padding: 4px 10px; font-size: 12px; color: var(--text-color);
	background: var(--bg-color); border: 1px solid var(--border-color);
	border-radius: 999px;
}
.rm-chip i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }

.rm-grid {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
	gap: 14px; margin-bottom: 18px;
}
.rm-tile {
	background: var(--fg-color);
	border: 1px solid var(--border-color);
	border-radius: 10px;
	overflow: hidden;
	transition: transform .15s ease, box-shadow .15s ease;
	display: flex; flex-direction: column;
}
.rm-tile:hover {
	transform: translateY(-2px);
	box-shadow: 0 6px 20px rgba(0,0,0,0.10);
}
.rm-tile-visual {
	display: flex; align-items: center; justify-content: center;
	background:
		repeating-linear-gradient(45deg, transparent, transparent 6px,
		rgba(127,127,127,0.07) 6px, rgba(127,127,127,0.07) 12px);
	padding: 8px;
}
.rm-shape {
	border: 2px solid; border-radius: 4px; opacity: 0.85;
	display: flex; align-items: center; justify-content: center;
	box-shadow: 0 2px 6px rgba(0,0,0,0.12);
	color: #1f2d3d;
	min-width: 40px; min-height: 28px;
}
.rm-shape-label {
	font: 600 11px/1 Inter, system-ui, sans-serif;
	background: rgba(255,255,255,0.85);
	padding: 2px 6px; border-radius: 3px;
	color: #1f2d3d;
}
.rm-tile-body { padding: 10px 12px 12px; flex: 1; }
.rm-tile-title {
	display: flex; align-items: center; justify-content: space-between;
	gap: 8px; margin-bottom: 6px;
}
.rm-tile-title a { font-weight: 600; font-size: 13px; color: var(--text-color);
	text-decoration: none; overflow: hidden; text-overflow: ellipsis;
	white-space: nowrap; flex: 1; }
.rm-tile-title a:hover { color: var(--primary); text-decoration: underline; }
.rm-bucket {
	font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 4px;
	background: rgba(127,127,127,0.15); color: var(--text-muted);
}
.rm-bucket-S { background: rgba(149,165,166,0.20); color: #7f8c8d; }
.rm-bucket-M { background: rgba(52,152,219,0.18); color: #3498db; }
.rm-bucket-L { background: rgba(46,204,113,0.18); color: #27ae60; }
.rm-bucket-XL { background: rgba(231,76,60,0.18); color: #e74c3c; }

.rm-tile-meta {
	display: flex; gap: 10px; flex-wrap: wrap;
	font-size: 12px; color: var(--text-muted); margin-bottom: 6px;
}
.rm-tile-meta b { color: var(--text-color); }
.rm-tile-meta .rm-value { margin-left: auto; font-weight: 600;
	color: var(--green-600, #27ae60); }
.rm-tile-foot {
	display: flex; flex-direction: column; gap: 3px;
	font-size: 11px; color: var(--text-muted);
	padding-top: 6px; border-top: 1px solid var(--border-color);
}
.rm-link { color: var(--primary); text-decoration: none; }
.rm-link:hover { text-decoration: underline; }
.rm-muted { color: var(--text-muted); }
</style>
"""
