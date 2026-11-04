import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

import requests
import footprint_suggestions as fp


def make_library(folder, names):
    """Create `Library:Footprint` files under `folder` and return a root list."""
    for identifier in names:
        library, footprint = identifier.split(":", 1)
        path = Path(folder) / f"{library}.pretty"
        path.mkdir(exist_ok=True)
        (path / f"{footprint}.kicad_mod").write_text(f'(footprint "{footprint}")')
    return [Path(folder)]


KICAD_CHIPS = ["Resistor_SMD:R_0603_1608Metric", "Resistor_SMD:R_0603_1608Metric_Pad0.98x0.95mm_HandSolder",
               "Resistor_SMD:R_0805_2012Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0402_1005Metric",
               "Inductor_SMD:L_0603_1608Metric", "LED_SMD:LED_0603_1608Metric", "Diode_SMD:D_0603_1608Metric",
               "Fuse:Fuse_1206_3216Metric", "Package_TO_SOT_SMD:SOT-23", "Package_TO_SOT_SMD:SOT-23-5",
               "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"]


class TestSizeCodes(unittest.TestCase):
    def test_imperial_codes_in_descriptions(self):
        self.assertEqual(fp.size_codes("RES SMD 10K OHM 1% 1/10W 0603"), [("0603", "1608")])
        self.assertEqual(fp.size_codes("CAP CER 0.1UF 50V X7R 0402"), [("0402", "1005")])
        self.assertEqual(fp.size_codes("0603 (1608 Metric)"), [("0603", "1608")])

    def test_bare_metric_and_part_numbers_are_not_codes(self):
        self.assertEqual(fp.size_codes("1608"), [])
        self.assertEqual(fp.size_codes("RC0603FR-0710KL"), [])
        self.assertEqual(fp.size_codes("0.0603 mm"), [])
        # "0603 Metric" is the metric code of an 0201 part, not an 0603 part.
        self.assertEqual(fp.size_codes("0603 Metric"), [("0201", "0603")])


class TestLocalSuggestions(unittest.TestCase):
    def rank(self, text, names, category="IC", kind="form"):
        return fp.rank_candidates(dict.fromkeys(names, "local.kicad_mod"),
            [{"text": text, "source": "test", "verified": False, "kind": kind}], category)

    def test_description_size_code_resolves_to_kicad_chip_footprint(self):
        ranked = self.rank("RES SMD 10K OHM 1% 1/10W 0603", KICAD_CHIPS, "Resistor")
        self.assertEqual(ranked[0]["name"], "Resistor_SMD:R_0603_1608Metric")
        self.assertEqual(ranked[0]["level"], 'Record form')
        self.assertEqual([r["name"] for r in ranked][:2],
                         ["Resistor_SMD:R_0603_1608Metric", "Resistor_SMD:R_0603_1608Metric_Pad0.98x0.95mm_HandSolder"])
        self.assertFalse(any(r["name"].startswith("Capacitor") for r in ranked))

    def test_each_table_maps_to_its_own_library(self):
        for table, text, expected in [
            ("Capacitor", "CAP CER 100NF 50V X7R 0603", "Capacitor_SMD:C_0603_1608Metric"),
            ("Inductor", "FIXED IND 10UH 0603", "Inductor_SMD:L_0603_1608Metric"),
            ("Fuse", "FUSE BOARD MOUNT 2A 1206", "Fuse:Fuse_1206_3216Metric"),
            ("PTC_NTC", "THERMISTOR NTC 10K 0603", "Resistor_SMD:R_0603_1608Metric"),
        ]:
            with self.subTest(table=table):
                self.assertEqual(self.rank(text, KICAD_CHIPS, table)[0]["name"], expected)

    def test_diode_table_prefers_led_footprint_only_when_description_says_led(self):
        self.assertEqual(self.rank("LED GREEN CLEAR 0603 SMD", KICAD_CHIPS, "Diode")[0]["name"], "LED_SMD:LED_0603_1608Metric")
        self.assertEqual(self.rank("DIODE SCHOTTKY 30V 0603", KICAD_CHIPS, "Diode")[0]["name"], "Diode_SMD:D_0603_1608Metric")

    def test_unknown_table_still_offers_size_matches(self):
        names = [r["name"] for r in self.rank("something 0603", KICAD_CHIPS, "Miscellaneous")]
        self.assertIn("Resistor_SMD:R_0603_1608Metric", names)
        self.assertIn("Capacitor_SMD:C_0603_1608Metric", names)
        self.assertNotIn("Resistor_SMD:R_0805_2012Metric", names)

    def test_package_family_from_description(self):
        ranked = self.rank("IC REG LINEAR 3.3V SOT-23-5", KICAD_CHIPS, "IC")
        self.assertEqual([r["name"] for r in ranked], ["Package_TO_SOT_SMD:SOT-23-5"])

    def test_pin_count_dimensions_and_pitch_filter(self):
        names = ["Package_QFP:LQFP-48_7x7mm_P0.5mm", "Package_QFP:LQFP-64_10x10mm_P0.5mm",
                 "Package_QFP:LQFP-48_9x9mm_P0.5mm", "Package_QFP:LQFP-48_7x7mm_P0.8mm"]
        self.assertEqual([r["name"] for r in self.rank("48-LQFP 7x7mm pitch 0.5mm", names)], names[:1])

    def test_sot_suffix_and_metric_not_confused(self):
        names = ["Package_TO_SOT_SMD:SOT-23", "Package_TO_SOT_SMD:SOT-23-5"]
        self.assertEqual([r["name"] for r in self.rank("SOT-23-5", names)], names[1:])
        names = ["Resistor_SMD:R_0201_0603Metric", "Resistor_SMD:R_0603_1608Metric"]
        self.assertEqual([r["name"] for r in self.rank("0603 Metric", names, "Resistor")], names[:1])

    def test_only_installed_files_and_qualified_names(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Path(folder) / "Package_SO.pretty"
            library.mkdir()
            (library / "SOIC-8.kicad_mod").write_text('(footprint "SOIC-8")')
            self.assertEqual(list(fp.installed_footprints([Path(folder)])), ["Package_SO:SOIC-8"])

    def test_company_library_uses_the_kicad_nickname(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Path(folder) / "footprints.pretty"
            library.mkdir()
            (library / "SOT-23-3.kicad_mod").write_text('(footprint "SOT-23-3")')
            self.assertEqual(list(fp.installed_footprints([Path(folder)])), ["KicadDB:SOT-23-3"])
            self.assertEqual(list(fp.installed_footprints([library])), ["KicadDB:SOT-23-3"])
            self.assertEqual(fp.library_nickname(Path("x/Resistor_SMD.pretty")), "Resistor_SMD")

    def test_company_library_in_the_library_root_is_scanned_first(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "kicad_library"
            lib_dir = root / "library" / "footprints.pretty"
            lib_dir.mkdir(parents=True)
            app = root / "Kicad" / "_Source Code"
            app.mkdir(parents=True)
            with patch.object(fp.config, "APP_DIR", str(app)), patch.object(fp.config, "FOOTPRINT_LIBRARY_DIR", str(lib_dir)), \
                 patch.object(fp.config, "_setting", lambda name: None):
                roots = fp.library_roots()
            self.assertEqual(roots[0], root / "library" / "footprints.pretty")

    def test_suggestion_for_company_footprint_carries_the_nickname(self):
        with tempfile.TemporaryDirectory() as folder:
            roots = make_library(folder, ["Footprints:SOT-23", "Package_TO_SOT_SMD:SOT-23"])
            with patch.object(fp, "library_roots", return_value=roots), \
                 patch.object(fp, "online_evidence", side_effect=AssertionError("web must not be called")):
                result = fp.suggest("", "SOT-23", "Mosfet")
        names = [c["name"] for c in result["candidates"]]
        self.assertIn("KicadDB:SOT-23", names)
        self.assertIn("Package_TO_SOT_SMD:SOT-23", names)
        self.assertNotIn("Footprints:SOT-23", names)


class TestSuggestFlow(unittest.TestCase):
    def test_saved_custom_footprint_is_shown_without_package_or_network(self):
        with tempfile.TemporaryDirectory() as folder:
            roots = make_library(folder, ['Footprints:CustomPart'])
            with patch.object(fp, 'library_roots', return_value=roots), \
                    patch.object(fp, 'online_evidence', side_effect=AssertionError('No network')):
                result = fp.suggest('ABC123', '', 'IC', current_footprint='KicadDB:CustomPart')
            self.assertEqual(result['candidates'][0]['name'], 'KicadDB:CustomPart')
            self.assertEqual(result['candidates'][0]['level'], 'Current assignment')

    def test_missing_saved_tdson_footprint_offers_installed_file_for_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            roots = make_library(folder, ['Footprints:PG-TDSON-8-34',
                                          'Footprints:PG-TSDSON-8', 'Footprints:PG-TDSON-10'])
            with patch.object(fp, 'library_roots', return_value=roots), \
                    patch.object(fp, 'online_evidence', side_effect=AssertionError('No network')):
                result = fp.suggest('BSC019N06NSATMA1', '8-PowerTDFN', 'Mosfet',
                                    description='MOSFET N-CH 60V 100A TDSON-8 FL',
                                    current_footprint='KicadDB:PG-TDSON-8-FL')
            self.assertEqual([c['name'] for c in result['candidates']], ['KicadDB:PG-TDSON-8-34'])
            self.assertTrue(Path(result['candidates'][0]['path']).is_file())
            self.assertIn('Saved footprint not found', result['messages'][0])

    def test_saved_footprint_does_not_resolve_to_same_name_in_wrong_library(self):
        with tempfile.TemporaryDirectory() as folder:
            roots = make_library(folder, ['Other:CustomPart'])
            with patch.object(fp, 'library_roots', return_value=roots):
                result = fp.suggest('', '', 'IC', current_footprint='KicadDB:CustomPart')
            self.assertEqual(result['candidates'], [])

    def test_local_match_skips_the_web_entirely(self):
        with tempfile.TemporaryDirectory() as folder:
            roots = make_library(folder, KICAD_CHIPS)
            with patch.object(fp, "library_roots", return_value=roots), \
                 patch.object(fp, "online_evidence", side_effect=AssertionError("web must not be called")):
                result = fp.suggest("RC0603FR-0710KL", "", "Resistor", description="RES SMD 10K OHM 1% 1/10W 0603")
        self.assertTrue(result["local"])
        self.assertEqual(result["candidates"][0]["name"], "Resistor_SMD:R_0603_1608Metric")
        self.assertIn("no web search", result["messages"][0])

    def test_web_is_used_only_when_the_library_has_no_match(self):
        with tempfile.TemporaryDirectory() as folder:
            roots = make_library(folder, KICAD_CHIPS)
            web = [{"text": "ABC123 in SOT-23-5 package", "source": "https://example.org", "verified": False, "kind": "web"}]
            with patch.object(fp, "library_roots", return_value=roots), \
                 patch.object(fp, "online_evidence", return_value=web) as search:
                result = fp.suggest("ABC123", "", "IC", description="IC REG LINEAR 3.3V")
        search.assert_called_once()
        self.assertFalse(result["local"])
        self.assertEqual([c["name"] for c in result["candidates"]], ["Package_TO_SOT_SMD:SOT-23-5"])
        self.assertEqual(result["candidates"][0]["level"], 'Verification required')

    def test_offline_keeps_existing_package(self):
        with patch.object(fp, "online_evidence", side_effect=requests.ConnectionError), patch.object(fp, "library_roots", return_value=[]):
            result = fp.suggest("ABC123", "LQFP-48", "IC")
        self.assertTrue(any("unavailable" in message for message in result["messages"]))
        self.assertEqual(result["evidence"][0]["text"], "LQFP-48")

    def test_nothing_to_search_without_mpn_or_fields(self):
        with patch.object(fp, "library_roots", return_value=[]), \
             patch.object(fp, "online_evidence", side_effect=AssertionError("no MPN, no web")):
            result = fp.suggest("", "", "IC")
        self.assertEqual(result["candidates"], [])


class TestWebSearch(unittest.TestCase):
    def test_web_requires_exact_mpn(self):
        response = Mock(text='<a class="result__a" href="https://example.org">ABC123X LQFP-48</a><a class="result__snippet">ABC123X package</a><a class="result__a" href="https://example.com">ABC123 LQFP-48</a><a class="result__snippet">7x7mm</a>')
        with patch.object(fp, "google_settings", return_value=None), patch.object(fp.requests, "get", return_value=response):
            result = fp.online_evidence("ABC123")
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["verified"])
        self.assertEqual(result[0]["kind"], "web")

    def test_stops_after_the_first_query_with_package_information(self):
        response = Mock(text='<a class="result__a" href="https://example.org">ABC123 LQFP-48</a><a class="result__snippet">7x7mm</a>')
        with patch.object(fp, "google_settings", return_value=None), patch.object(fp.requests, "get", return_value=response) as get:
            fp.online_evidence("ABC123", manufacturer="Acme")
        self.assertEqual(get.call_count, 1)
        self.assertIn('"ABC123" Acme package', get.call_args.kwargs["params"]["q"])

    def test_tries_further_queries_when_nothing_useful_comes_back(self):
        useless = Mock(text='<a class="result__a" href="https://example.org">ABC123 buy now</a><a class="result__snippet">in stock</a>')
        with patch.object(fp, "google_settings", return_value=None), patch.object(fp.requests, "get", return_value=useless) as get:
            evidence = fp.online_evidence("ABC123")
        # 3 query variants, DuckDuckGo answered each (Bing not needed).
        self.assertEqual(get.call_count, 3)
        self.assertEqual(len(evidence), 1)  # same source de-duplicated

    def test_google_programmable_search_is_preferred_when_configured(self):
        google = Mock()
        google.json.return_value = {"items": [{"title": "ABC123 datasheet", "snippet": "Package: SOT-23-5",
                                               "link": "https://vendor.example/abc123"}]}
        with patch.object(fp, "google_settings", return_value=("key", "cse")), \
             patch.object(fp.requests, "get", return_value=google) as get:
            evidence = fp.online_evidence("ABC123")
        self.assertEqual(get.call_count, 1)
        self.assertIn("googleapis.com/customsearch", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["params"]["cx"], "cse")
        self.assertEqual(evidence[0]["source"], "https://vendor.example/abc123")

    def test_second_search_source_and_offline_errors(self):
        empty = Mock(text="<html>Search unavailable</html>")
        rss = Mock(text='<rss><channel><item><title>ABC123 LQFP-48</title><description>Package 7x7mm</description><link>https://example.org/part</link></item></channel></rss>')
        with patch.object(fp, "google_settings", return_value=None), patch.object(fp.requests, "get", side_effect=[empty, rss]):
            self.assertEqual(len(fp.online_evidence("ABC123")), 1)
        with patch.object(fp, "google_settings", return_value=None), patch.object(fp.requests, "get", side_effect=requests.ConnectionError):
            with self.assertRaises(requests.ConnectionError):
                fp.online_evidence("ABC123")


if __name__ == "__main__":
    unittest.main()
