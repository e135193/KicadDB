import unittest
from unittest.mock import patch
import pandas as pd
import requests
from bom_stock import (COLUMN, NO_CODE, NOT_FOUND, LOOKUP_FAILED, SERVER_UNREACHABLE,
                       prepare_stock_column, fill_stock_row, stock_server_unreachable)


class TestBomStock(unittest.TestCase):
    def test_column_is_next_to_digikey_and_stale_values_are_cleared(self):
        df = pd.DataFrame({'Company Part Number': ['CMP-1'], COLUMN: [999],
                           'In-Stock': [100], 'price': [1]})
        self.assertEqual(prepare_stock_column(df), 'Company Part Number')
        self.assertEqual(list(df.columns), ['Company Part Number', 'In-Stock', COLUMN, 'price'])
        self.assertEqual(df.at[0, COLUMN], '')
        self.assertEqual(df.at[0, 'In-Stock'], 100)

    def test_live_stock_zero_and_duplicate_codes(self):
        df = pd.DataFrame({'Company_Part_Numer': ['CMP-1', 'CMP-1']})
        col = prepare_stock_column(df)
        cache = {}
        with patch('bom_stock.fetch_selected_stock', return_value=0) as fetch:
            for index in df.index:
                fill_stock_row(df, index, col, cache)
        self.assertEqual(list(df[COLUMN]), [0, 0])
        fetch.assert_called_once_with('CMP-1')

    def test_missing_codes_not_found_and_lookup_error_are_not_zero(self):
        df = pd.DataFrame({'BPN': [None, '-', 'CMP-1', 'CMP-2']})
        col = prepare_stock_column(df)
        with patch('bom_stock.fetch_selected_stock', side_effect=[None, OSError('offline')]):
            cache = {}
            for index in df.index:
                fill_stock_row(df, index, col, cache)
        self.assertEqual(list(df[COLUMN]), [NO_CODE, NO_CODE, NOT_FOUND, LOOKUP_FAILED])
        self.assertFalse(stock_server_unreachable(cache))

    def test_unreachable_server_stops_further_lookups(self):
        # One connection failure marks the whole job: later codes are not
        # queried at all (no per-code timeout) and are labelled accordingly.
        df = pd.DataFrame({'BPN': ['CMP-1', 'CMP-2', '-', 'CMP-3']})
        col = prepare_stock_column(df)
        cache = {}
        with patch('bom_stock.fetch_selected_stock',
                   side_effect=requests.ConnectionError('refused')) as fetch:
            for index in df.index:
                fill_stock_row(df, index, col, cache)
        self.assertEqual(list(df[COLUMN]), [SERVER_UNREACHABLE, SERVER_UNREACHABLE, NO_CODE, SERVER_UNREACHABLE])
        fetch.assert_called_once_with('CMP-1')
        self.assertTrue(stock_server_unreachable(cache))

    def test_timeout_also_trips_the_breaker(self):
        df = pd.DataFrame({'BPN': ['CMP-1', 'CMP-2']})
        col = prepare_stock_column(df)
        cache = {}
        with patch('bom_stock.fetch_selected_stock', side_effect=requests.Timeout('slow')) as fetch:
            for index in df.index:
                fill_stock_row(df, index, col, cache)
        self.assertEqual(list(df[COLUMN]), [SERVER_UNREACHABLE, SERVER_UNREACHABLE])
        self.assertEqual(fetch.call_count, 1)

    def test_bom_without_br_column(self):
        df = pd.DataFrame({'Manufacturer Part Number': ['00123']})
        col = prepare_stock_column(df)
        fill_stock_row(df, 0, col, {})
        self.assertEqual(df.at[0, COLUMN], NO_CODE)


if __name__ == '__main__':
    unittest.main()
