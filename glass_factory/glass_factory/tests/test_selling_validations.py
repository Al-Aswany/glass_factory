import unittest
from unittest.mock import MagicMock, patch

import frappe

from glass_factory.glass_factory.selling_validations import (
	_reject_non_commercial_item,
	validate_no_manufacturing_for_glass,
	validate_stock_entry,
)


class TestSellingValidations(unittest.TestCase):
	def test_reject_non_commercial_item_blocks_cut_wip(self):
		row = MagicMock(idx=1, item_code="GLS-CLEAR-8MM-1200X800-CUT")
		with patch(
			"glass_factory.glass_factory.selling_validations.item_role",
			return_value="Cut WIP",
		):
			with self.assertRaises(frappe.ValidationError):
				_reject_non_commercial_item(row, "Sales Order")

	def test_validate_no_manufacturing_for_glass_blocks_default_bom(self):
		row = MagicMock(idx=1, gf_is_glass_item=1, item_code="GLS-CLEAR-8MM-1200X800", gf_final_item=None)
		with patch("glass_factory.glass_factory.selling_validations.frappe.db.get_value", return_value="BOM-001"):
			with self.assertRaises(frappe.ValidationError):
				validate_no_manufacturing_for_glass(row)

	def test_validate_stock_entry_rejects_mixed_job_links(self):
		doc = MagicMock(
			doctype="Stock Entry",
			gf_created_by_glass_factory=1,
			gf_cutting_job="CJ-0001",
			gf_processing_job="GPJ-0001",
			stock_entry_type="Repack",
			purpose="Repack",
			gf_glass_stock_flow="Raw to Cut WIP",
			items=[],
		)
		with self.assertRaises(frappe.ValidationError):
			validate_stock_entry(doc)

	def test_validate_stock_entry_rejects_non_repack(self):
		doc = MagicMock(
			doctype="Stock Entry",
			gf_created_by_glass_factory=1,
			gf_cutting_job="CJ-0001",
			gf_processing_job=None,
			stock_entry_type="Material Issue",
			purpose="Material Issue",
			gf_glass_stock_flow="Raw to Cut WIP",
			items=[],
		)
		with self.assertRaises(frappe.ValidationError):
			validate_stock_entry(doc)
