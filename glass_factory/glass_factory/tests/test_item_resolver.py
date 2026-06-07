import unittest

from glass_factory.glass_factory.item_resolver import (
	GlassSpec,
	_cut_wip_item_code,
	_final_item_code,
	_raw_item_code,
	infer_glass_role_from_item_code,
	parse_processing_flags,
	processing_flags_from_item_code,
	spec_from_item_code,
)


class TestGlassItemResolver(unittest.TestCase):
	def test_processing_flags_use_fixed_order(self):
		self.assertEqual(parse_processing_flags("TMP,HOL,POL"), ("POL", "HOL", "TMP"))

	def test_processing_flag_aliases_are_normalized(self):
		self.assertEqual(parse_processing_flags({"tempered": 1, "holes": 1, "polish": 1}), ("POL", "HOL", "TMP"))

	def test_unknown_flags_are_ignored(self):
		self.assertEqual(parse_processing_flags("POL,UNKNOWN,TMP"), ("POL", "TMP"))

	def test_deterministic_item_codes(self):
		spec = GlassSpec("CLEAR", 8, 1200, 800, ("POL", "HOL", "TMP"))
		self.assertEqual(_raw_item_code(spec), "GLS-CLEAR-8MM-1200X800")
		self.assertEqual(_cut_wip_item_code(spec), "GLS-CLEAR-8MM-1200X800-CUT")
		self.assertEqual(_final_item_code(spec), "GLS-CLEAR-8MM-1200X800-POL-HOL-TMP")

	def test_infer_glass_role_from_item_code(self):
		self.assertEqual(infer_glass_role_from_item_code("GLS-CLEAR-8MM-3210X2250"), "Raw Sheet")
		self.assertEqual(infer_glass_role_from_item_code("GLS-CLEAR-8MM-1200X800-CUT"), "Cut WIP")
		self.assertEqual(infer_glass_role_from_item_code("GLS-CLEAR-8MM-1200X800-REM"), "Remnant")
		self.assertEqual(infer_glass_role_from_item_code("GLS-CLEAR-8MM-1200X800-POL-HOL-TMP"), "Final")

	def test_spec_from_item_code(self):
		spec = spec_from_item_code("GLS-CLEAR-8MM-1200X800-POL-HOL-TMP")
		self.assertEqual(spec.base_glass_type, "CLEAR")
		self.assertEqual(spec.thickness_mm, 8)
		self.assertEqual(spec.length_mm, 1200)
		self.assertEqual(spec.width_mm, 800)
		self.assertEqual(processing_flags_from_item_code("GLS-CLEAR-8MM-1200X800-POL-HOL-TMP"), ("POL", "HOL", "TMP"))
