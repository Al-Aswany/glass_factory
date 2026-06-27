import frappe
from frappe.utils import flt


def compute_area(doc, method=None):
	"""Auto-compute gf_area_m2 from length × width before save."""
	length = flt(doc.get("gf_length_mm"))
	width = flt(doc.get("gf_width_mm"))
	if length and width and frappe.get_meta("Batch").has_field("gf_area_m2"):
		doc.gf_area_m2 = (length * width) / 1_000_000.0
