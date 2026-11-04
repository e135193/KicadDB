"""Regression tests for the DigiKey extraction, pricing and value formatting.

These paths carried the bugs found in review and had no coverage at all: the
Cut-Tape substring match, the alias maps whose duplicate literal keys silently
discarded half the aliases, and the KiCad value formatters.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import digikey_api
from digikey_api import (
    add_common_aliases,
    add_result,
    extract_digikey_fields,
    get_digikey_price,
    get_digikey_price_for_quantity,
    get_digikey_stock,
    get_from_result,
    is_cut_tape_or_digireel_text,
)


class TestCutTapeDetection(unittest.TestCase):
    def test_real_cut_tape_markers_match(self):
        for text in ["cut tape", "Cut Tape (CT)", "digi-reel", "DigiReel", "digi reel",
                     "311-10.0KCRCT-ND", "ct"]:
            self.assertTrue(is_cut_tape_or_digireel_text(text), text)

    def test_ordinary_words_containing_ct_do_not_match(self):
        # The bare "ct" substring used to match all of these, which made every
        # variation look like Cut Tape and disabled the pricing preference.
        for text in ["Inductor Fixed 10uH", "Connector Header", "product detail",
                     "Tape & Reel (TR)", "Bulk", "Rectifier", "Capacitor"]:
            self.assertFalse(is_cut_tape_or_digireel_text(text), text)


class TestAliasMaps(unittest.TestCase):
    def _aliased(self, source_name, source_value, target):
        result = {}
        add_result(result, source_name, source_value)
        add_common_aliases(result)
        return get_from_result(result, target)

    def test_generic_current_aliases_survive(self):
        # "Current Rating" was unreachable: a second "Current" key later in the
        # same dict literal replaced the list holding it.
        for name in ["Current Rating", "Current Rating (Max)", "Current - Rated"]:
            self.assertEqual(self._aliased(name, "5 A", "Current"), "5 A", name)

    def test_diode_current_aliases_still_work(self):
        for name in ["Current - Peak Pulse", "Peak Pulse Current"]:
            self.assertEqual(self._aliased(name, "30 A", "Current"), "30 A", name)

    def test_generic_and_specific_power_aliases_both_work(self):
        for name in ["Power (Watts)", "Wattage", "Power - Peak Pulse", "Power Dissipation"]:
            self.assertEqual(self._aliased(name, "0.25W", "Power"), "0.25W", name)


class TestPricingAndStock(unittest.TestCase):
    def _product(self):
        return {
            "ProductVariations": [
                {
                    "PackageType": {"Name": "Tape & Reel (TR)"},
                    "QuantityAvailable": 5000,
                    "StandardPricing": [
                        {"BreakQuantity": 1, "UnitPrice": 0.50},
                        {"BreakQuantity": 100, "UnitPrice": 0.20},
                    ],
                },
                {
                    "PackageType": {"Name": "Cut Tape (CT)"},
                    "QuantityAvailable": 42,
                    "StandardPricing": [
                        {"BreakQuantity": 1, "UnitPrice": 0.90},
                        {"BreakQuantity": 100, "UnitPrice": 0.60},
                    ],
                },
            ]
        }

    def test_price_prefers_the_cut_tape_variation(self):
        self.assertEqual(get_digikey_price(self._product()), "0.9")

    def test_price_for_quantity_picks_the_right_break(self):
        product = self._product()
        self.assertEqual(get_digikey_price_for_quantity(product, 1), "0.9")
        self.assertEqual(get_digikey_price_for_quantity(product, 250), "0.6")

    def test_stock_prefers_the_cut_tape_variation(self):
        self.assertEqual(get_digikey_stock(self._product()), "42")

    def test_marketplace_variations_are_excluded_from_stock(self):
        product = {
            "ProductVariations": [
                {"PackageType": {"Name": "Cut Tape (CT)"}, "IsMarketplace": True, "QuantityAvailable": 999},
                {"PackageType": {"Name": "Cut Tape (CT)"}, "IsMarketplace": False, "QuantityAvailable": 7},
            ]
        }
        self.assertEqual(get_digikey_stock(product), "7")

    def test_falls_back_when_no_cut_tape_variation_exists(self):
        product = {
            "ProductVariations": [
                {"PackageType": {"Name": "Tape & Reel (TR)"}, "QuantityAvailable": 12,
                 "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.11}]},
            ]
        }
        self.assertEqual(get_digikey_stock(product), "12")
        self.assertEqual(get_digikey_price(product), "0.11")


class TestFieldExtraction(unittest.TestCase):
    def test_extracts_nested_product_shape(self):
        product = {
            "Description": {"ProductDescription": "RES 10K OHM 1% 1/10W 0603"},
            "Manufacturer": {"Name": "Yageo"},
            "ManufacturerProductNumber": "RC0603FR-0710KL",
            "DigiKeyProductNumber": "311-10.0KHRCT-ND",
            "ProductStatus": {"Status": "Active"},
            "Parameters": [
                {"ParameterText": "Resistance", "ValueText": "10 kOhms"},
                {"ParameterText": "Package / Case", "ValueText": "0603 (1608 Metric)"},
            ],
        }
        extracted = extract_digikey_fields(product)
        self.assertEqual(get_from_result(extracted, "Manufacturer"), "Yageo")
        self.assertEqual(get_from_result(extracted, "Manufacturer_Part_Number"), "RC0603FR-0710KL")
        self.assertEqual(get_from_result(extracted, "Resistance"), "10 kOhms")
        self.assertEqual(get_from_result(extracted, "Package"), "0603 (1608 Metric)")
        self.assertEqual(get_from_result(extracted, "Product Status"), "Active")

    def test_empty_product_is_safe(self):
        self.assertEqual(extract_digikey_fields(None), {})
        self.assertEqual(get_digikey_price(None), "")
        self.assertEqual(get_digikey_stock(None), "")


class TestTokenCache(unittest.TestCase):
    def setUp(self):
        digikey_api._TOKEN_CACHE.clear()
        self.calls = []

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"access_token": "tok-123", "expires_in": 600}

        def fake_post(url, **kwargs):
            self.calls.append(url)
            return FakeResponse()

        self._real_post = digikey_api.requests.post
        digikey_api.requests.post = fake_post

    def tearDown(self):
        digikey_api.requests.post = self._real_post
        digikey_api._TOKEN_CACHE.clear()

    def test_token_is_reused_within_its_lifetime(self):
        first = digikey_api.get_digikey_token("cid", "secret")
        second = digikey_api.get_digikey_token("cid", "secret")
        self.assertEqual(first, "tok-123")
        self.assertEqual(second, "tok-123")
        self.assertEqual(len(self.calls), 1, "second lookup should not re-request a token")

    def test_force_refresh_and_invalidate_bypass_the_cache(self):
        digikey_api.get_digikey_token("cid", "secret")
        digikey_api.get_digikey_token("cid", "secret", force_refresh=True)
        self.assertEqual(len(self.calls), 2)
        digikey_api.invalidate_digikey_token("cid")
        digikey_api.get_digikey_token("cid", "secret")
        self.assertEqual(len(self.calls), 3)

    def test_request_counter_tracks_real_calls(self):
        counter = {"requests": 0}
        digikey_api.get_digikey_token("cid", "secret", counter=counter)
        self.assertEqual(counter["requests"], 1)
        # cached, so no further HTTP call and no further increment
        digikey_api.get_digikey_token("cid", "secret", counter=counter)
        self.assertEqual(counter["requests"], 1)


if __name__ == "__main__":
    unittest.main()
