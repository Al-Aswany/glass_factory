"""Layout Visualizer report for Cutting Job tabular layout rendering."""
import frappe
import json
from typing import List, Dict


def execute(filters=None):
	"""Generate layout visualization for a Cutting Job."""
	if not filters or not filters.get("cutting_job"):
		return [], []

	job_name = filters.get("cutting_job")
	job = frappe.get_doc("Cutting Job", job_name)

	# Build SVG per sheet from tabular files
	svg_html = _build_svg_layout(job)

	return [], [{"html": svg_html}]


def _build_svg_layout(job):
	"""Build SVG rendering of all sheets in the Cutting Job."""
	html_parts = []
	html_parts.append('<div class="cutting-layout">')

	if not job.tabular_files:
		return '<div>No tabular files uploaded yet.</div>'

	# Group pieces by sheet index
	from collections import defaultdict
	sheets_data = defaultdict(list)

	for tf_idx, tf in enumerate(job.tabular_files, 1):
		try:
			rows = _load_excel_rows(tf.attached_file)
			sheets_data[tf.sheet_index] = rows
		except Exception as e:
			frappe.log_error(f"Error loading tabular file {tf.attached_file}: {str(e)}")

	# Render each sheet
	for sheet_idx in sorted(sheets_data.keys()):
		pieces = sheets_data[sheet_idx]
		svg = _render_sheet_svg(sheet_idx, pieces, job)
		html_parts.append(svg)

	html_parts.append('</div>')
	return "\n".join(html_parts)


def _render_sheet_svg(sheet_idx: int, pieces: List[Dict], job) -> str:
	"""Render a single sheet as SVG."""
	if not pieces:
		return f'<div class="sheet">Sheet {sheet_idx}: No pieces</div>'

	# Determine sheet dimensions from pieces or use defaults
	max_x = max((float(p.get("Left", 0)) + float(p.get("Length", 0))) for p in pieces) if pieces else 3000
	max_y = max((float(p.get("Top", 0)) + float(p.get("Width", 0))) for p in pieces) if pieces else 2000

	padding = 50
	scale = 0.15  # Scale to fit on screen (mm → pixels)

	svg_width = int((max_x + 2 * padding) * scale)
	svg_height = int((max_y + 2 * padding) * scale)

	svg_parts = [
		f'<div class="sheet-container" style="margin-bottom: 20px;">',
		f'<h3>Sheet {sheet_idx}</h3>',
		f'<svg width="{svg_width}" height="{svg_height}" style="border: 1px solid #ccc; background: #f9f9f9;">',
		f'  <!-- Sheet background -->',
		f'  <rect x="{padding*scale}" y="{padding*scale}" width="{max_x*scale}" height="{max_y*scale}" fill="white" stroke="black" stroke-width="2"/>',
	]

	# Render each piece
	colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#FFA07A", "#98D8C8", "#F7DC6F"]
	for piece_idx, piece in enumerate(pieces):
		left = float(piece.get("Left", 0))
		top = float(piece.get("Top", 0))
		length = float(piece.get("Length", 0))
		width = float(piece.get("Width", 0))
		rotated = bool(piece.get("Rotated", False))
		label = piece.get("Label", f"Piece {piece_idx}")

		# Convert to SVG coordinates
		x = (left + padding) * scale
		y = (top + padding) * scale
		w = length * scale
		h = width * scale

		color = colors[piece_idx % len(colors)]

		svg_parts.append(
			f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{color}" stroke="black" stroke-width="1" opacity="0.7"/>'
		)

		# Add label
		label_x = x + w / 2
		label_y = y + h / 2
		svg_parts.append(
			f'  <text x="{label_x}" y="{label_y}" text-anchor="middle" dominant-baseline="middle" '
			f'font-size="9" font-weight="bold" fill="black">{label[:20]}</text>'
		)

		# Rotation indicator if rotated
		if rotated:
			svg_parts.append(
				f'  <text x="{label_x}" y="{label_y + 8}" text-anchor="middle" font-size="7" fill="red">R</text>'
			)

	svg_parts.append("</svg>")
	svg_parts.append("</div>")

	return "\n".join(svg_parts)


def _load_excel_rows(file_url):
	"""Load rows from an Excel file attachment."""
	from openpyxl import load_workbook

	file_path = frappe.get_site_path("private", "files") + "/" + file_url.split("/")[-1]

	try:
		wb = load_workbook(file_path, data_only=True)
		ws = wb.active

		rows = []
		headers = None
		for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
			if row_idx == 0:
				headers = [h or "" for h in row]
				continue
			if not any(row):
				break
			row_dict = dict(zip(headers, row))
			rows.append(row_dict)

		return rows
	except Exception as e:
		frappe.log_error(f"Error loading Excel {file_url}: {str(e)}")
		return []
