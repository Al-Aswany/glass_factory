import frappe


def compute_area(doc, method=None):
	"""Auto-compute area_m2 from length_mm × width_mm before save."""
	length = doc.get("length_mm") or 0
	width = doc.get("width_mm") or 0
	if length and width:
		doc.area_m2 = (length * width) / 1_000_000.0
