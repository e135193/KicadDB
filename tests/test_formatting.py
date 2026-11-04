"""Tests for the KiCad value formatters, BOM column detection and the
per-table Treeview layout persistence.

Importing Brensan_Kicad_DB pulls in tkinter but does not create a window, so
these run headless.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from Company_Kicad_DB import (
    build_tree_columns_settings,
    find_bom_part_number_column,
    format_capacitor_value_for_kicad,
    format_inductor_value_for_kicad,
    format_resistor_value_for_kicad,
    get_default_reference_for_table,
    is_missing_brensan_part_number,
    normalize_tree_columns_by_table,
    parse_tree_columns_settings,
)

OMEGA = "Ω"       # GREEK CAPITAL LETTER OMEGA
OHM_SIGN = "Ω"    # OHM SIGN
MICRO = "µ"       # MICRO SIGN


class TestResistorFormatting(unittest.TestCase):
    def test_multipliers(self):
        cases = {
            "10 Ohms": "10" + OMEGA,
            "10 mOhms": "10m" + OMEGA,
            "10 KOhms": "10K" + OMEGA,
            "4.7 kOhms": "4.7K" + OMEGA,
            "10 MOhms": "10M" + OMEGA,
            "1.5 GOhms": "1.5G" + OMEGA,
        }
        for raw, expected in cases.items():
            self.assertEqual(format_resistor_value_for_kicad(raw), expected, raw)

    def test_no_space_between_magnitude_and_unit(self):
        self.assertNotIn(" ", format_resistor_value_for_kicad("10 mOhms"))

    def test_ohm_sign_is_normalised_to_omega(self):
        self.assertEqual(format_resistor_value_for_kicad("10 " + OHM_SIGN), "10" + OMEGA)

    def test_comma_decimal_is_normalised(self):
        self.assertEqual(format_resistor_value_for_kicad("4,7 kOhms"), "4.7K" + OMEGA)

    def test_unparseable_value_is_returned_unchanged(self):
        self.assertEqual(format_resistor_value_for_kicad("see datasheet"), "see datasheet")
        self.assertEqual(format_resistor_value_for_kicad(""), "")
        self.assertEqual(format_resistor_value_for_kicad(None), "")


class TestCapacitorAndInductorFormatting(unittest.TestCase):
    def test_capacitor_units(self):
        cases = {
            "10 pF": "10pF",
            "10 nF": "10nF",
            "10 uF": "10" + MICRO + "F",
            "10 " + MICRO + "F": "10" + MICRO + "F",
            "10 Microfarads": "10" + MICRO + "F",
            "10 F": "10F",
        }
        for raw, expected in cases.items():
            self.assertEqual(format_capacitor_value_for_kicad(raw), expected, raw)

    def test_inductor_units(self):
        cases = {
            "10 nH": "10nH",
            "10 uH": "10" + MICRO + "H",
            "10 Microhenry": "10" + MICRO + "H",
            "10 mH": "10mH",
            "10 H": "10H",
        }
        for raw, expected in cases.items():
            self.assertEqual(format_inductor_value_for_kicad(raw), expected, raw)

    def test_descriptive_prefix_does_not_corrupt_the_result(self):
        # The formatters normalise "u" to the micro sign across the whole
        # string; the regex anchors on number+unit so the output stays clean.
        self.assertEqual(format_capacitor_value_for_kicad("Aluminum 10 uF"), "10" + MICRO + "F")
        self.assertEqual(format_inductor_value_for_kicad("Unshielded 10 uH"), "10" + MICRO + "H")


class TestMiscHelpers(unittest.TestCase):
    def test_default_reference_designators(self):
        self.assertEqual(get_default_reference_for_table("Resistor"), "R")
        self.assertEqual(get_default_reference_for_table("Capacitor"), "C")
        self.assertEqual(get_default_reference_for_table("Button_Switch"), "B")
        self.assertEqual(get_default_reference_for_table("Unknown"), "")

    def test_missing_brensan_part_number(self):
        for value in ["", "-", "none", "NaN", None, "12345"]:
            self.assertTrue(is_missing_brensan_part_number(value), repr(value))
        self.assertFalse(is_missing_brensan_part_number("CMP-001"))

    def test_bom_column_detection_prefers_mpn_and_skips_brensan(self):
        df = pd.DataFrame(columns=["Company Part Number", "Manufacturer Part Number", "Qty"])
        self.assertEqual(find_bom_part_number_column(df), "Manufacturer Part Number")

    def test_bom_column_detection_returns_none_when_absent(self):
        df = pd.DataFrame(columns=["Qty", "Designator"])
        self.assertIsNone(find_bom_part_number_column(df))


class TestTreeColumnLayouts(unittest.TestCase):
    def test_layouts_are_stored_per_table(self):
        wide = build_tree_columns_settings(
            ["id", "Reference", "Value", "Description", "Package"], {"Description": 360}
        )
        narrow = build_tree_columns_settings(["id", "Reference", "Value"], {})
        stored = normalize_tree_columns_by_table({"Diode": wide, "Header": narrow})

        # Opening the narrow table must not shrink the wide table's layout.
        self.assertEqual(len(parse_tree_columns_settings(stored["Diode"])), 5)
        self.assertEqual(len(parse_tree_columns_settings(stored["Header"])), 3)

    def test_widths_and_order_round_trip(self):
        layout = build_tree_columns_settings(["id", "Value", "Description"], {"Description": 360})
        entries = parse_tree_columns_settings(normalize_tree_columns_by_table({"T": layout})["T"])
        self.assertEqual([e["name"] for e in entries], ["id", "Value", "Description"])
        self.assertEqual(entries[-1]["width"], 360)

    def test_malformed_entries_are_dropped(self):
        self.assertEqual(normalize_tree_columns_by_table({"T": "not a dict"}), {})
        self.assertEqual(normalize_tree_columns_by_table("nonsense"), {})


if __name__ == "__main__":
    unittest.main()
