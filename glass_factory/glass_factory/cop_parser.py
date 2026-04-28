"""Pure parser functions for COP (Cutting Optimization Pro) workflow."""
from collections import defaultdict
from typing import List, Tuple, Dict, Any


def parse_stock_diff(pre_rows: List[Dict], post_rows: List[Dict]) -> Tuple[List[Tuple], List[Tuple]]:
	"""
	Parse stock delta between pre and post optimization.

	Returns: (consumed, remnants)
	- consumed: list of (material, length, width, qty_consumed)
	- remnants: list of (material, length, width, qty_added)
	"""
	pre_agg = _aggregate_stock(pre_rows)
	post_agg = _aggregate_stock(post_rows)

	all_keys = set(pre_agg.keys()) | set(post_agg.keys())
	consumed = []
	remnants = []

	for key in all_keys:
		delta = post_agg.get(key, 0) - pre_agg.get(key, 0)
		if delta < 0:
			consumed.append((*key, -delta))
		elif delta > 0:
			remnants.append((*key, delta))

	return consumed, remnants


def parse_tabular_files(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
	"""
	Parse tabular files from COP output.

	Each file is a dict with 'rows' list. Each row has:
	- Label: format "{user_label} | {sales_order}-{idx}"
	- Length, Width, Left, Top, Rotated, CustomerName

	Returns: list of dicts with structure:
	{
		"index": int,
		"pieces": [{
			"sheet_index": int,
			"length": float,
			"width": float,
			"x": float,
			"y": float,
			"rotated": bool,
			"sales_order": str,
			"sales_order_item_idx": int,
			"customer": str,
			"label": str
		}, ...]
	}
	"""
	sheets = []

	for file_idx, file_data in enumerate(files, start=1):
		pieces = []
		for row in file_data.get("rows", []):
			label = row.get("Label", "")
			sales_order, idx = parse_label(label)

			piece = {
				"sheet_index": file_idx,
				"length": float(row.get("Length", 0)),
				"width": float(row.get("Width", 0)),
				"x": float(row.get("Left", 0)),
				"y": float(row.get("Top", 0)),
				"rotated": bool(row.get("Rotated", False)),
				"sales_order": sales_order,
				"sales_order_item_idx": idx,
				"customer": row.get("CustomerName", ""),
				"label": label,
			}
			pieces.append(piece)

		sheets.append({"index": file_idx, "pieces": pieces})

	return sheets


def parse_label(label: str) -> Tuple[str, int]:
	"""
	Parse label format: "{user_label} | {sales_order}-{idx}"

	Returns: (sales_order, idx)
	Defaults to ("", 0) if parsing fails.
	"""
	if not label or "|" not in label:
		return "", 0

	parts = label.split("|")
	if len(parts) < 2:
		return "", 0

	so_part = parts[1].strip()
	components = so_part.rsplit("-", 1)

	if len(components) == 2:
		sales_order = components[0].strip()
		try:
			idx = int(components[1])
			return sales_order, idx
		except (ValueError, IndexError):
			return "", 0

	return "", 0


def cross_validate(
	consumed: List[Tuple],
	sheets: List[Dict[str, Any]],
	requested_pieces: List[Dict[str, Any]]
) -> List[str]:
	"""
	Cross-validate parser outputs.

	Returns: list of warning strings if any validation fails.
	"""
	warnings = []

	# Count consumed sheets and sheet count match
	total_consumed_qty = sum(qty for *_, qty in consumed)
	total_sheets = len(sheets)

	if total_sheets != total_consumed_qty:
		warnings.append(
			f"Sheet count mismatch: consumed {total_consumed_qty} sheets "
			f"but found {total_sheets} tabular files."
		)

	# Count pieces in tabular vs requested
	pieces_in_tabular = sum(len(sheet["pieces"]) for sheet in sheets)
	pieces_requested = sum(item.get("qty", 0) for item in requested_pieces)

	if pieces_in_tabular < pieces_requested:
		warnings.append(
			f"Not all pieces fit: COP produced {pieces_in_tabular} pieces "
			f"but {pieces_requested} were requested. Some pieces did not fit."
		)

	return warnings


def _aggregate_stock(rows: List[Dict]) -> Dict[Tuple[str, float, float], float]:
	"""
	Aggregate stock rows by (material, length, width).

	Returns: dict mapping (material, length, width) -> qty
	"""
	agg = defaultdict(float)
	for row in rows:
		material = row.get("Material", "")
		length = float(row.get("Length", 0))
		width = float(row.get("Width", 0))
		qty = float(row.get("Quantity", 0))

		key = (material, length, width)
		agg[key] += qty

	return dict(agg)
