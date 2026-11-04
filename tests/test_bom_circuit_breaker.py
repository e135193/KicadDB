"""BOM job circuit breaker: an unreachable supplier or stock server stops the remaining lookups."""
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

import Company_Kicad_DB as module
import digikey_api


def run_bom_job(source, digikey_side_effect, stock_side_effect):
    """Drive App._run_bom_job synchronously with mocked suppliers; returns (dialogs, output frame)."""
    app = module.App.__new__(module.App)
    app.bom_file_label_var = Mock()
    app.status_var = Mock()
    app.bom_file_path = source

    def run(worker, on_success, on_error=None, **kwargs):
        on_success(worker(Mock(), lambda: False))
    app.run_in_background = run
    with patch.object(module, 'get_digikey_part_data', side_effect=digikey_side_effect) as lookup, \
         patch('bom_stock.fetch_selected_stock', side_effect=stock_side_effect) as stock, \
         patch.object(module, 'messagebox') as dialogs:
        app.process_bom_list_with_digikey()
    folder = os.path.dirname(source)
    output = pd.read_excel(os.path.join(folder, 'BOM_DigiKey_Updated.xlsx'))
    return dialogs, lookup, stock, output


class TestBomCircuitBreaker(unittest.TestCase):
    def make_bom(self, folder, rows=6):
        source = os.path.join(folder, 'BOM.xlsx')
        pd.DataFrame({'MPN': [f'PART-{i}' for i in range(rows)],
                      'Company Part Number': [f'CMP-{i}' for i in range(rows)]}).to_excel(source, index=False)
        return source

    def test_supplier_down_stops_after_the_failure_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.make_bom(folder)
            dialogs, lookup, _, output = run_bom_job(
                source, requests.ConnectionError('refused'), [0] * 6)
        # Only SUPPLIER_FAILURE_LIMIT rows hit the network; the rest are marked without a call.
        self.assertEqual(lookup.call_count, module.SUPPLIER_FAILURE_LIMIT)
        statuses = list(output['Part Status'])
        self.assertTrue(all(s.startswith('Error: refused') for s in statuses[:module.SUPPLIER_FAILURE_LIMIT]))
        self.assertTrue(all(s == 'Error: DigiKey unreachable' for s in statuses[module.SUPPLIER_FAILURE_LIMIT:]))
        dialogs.showwarning.assert_called_once()
        self.assertIn('could not be reached', dialogs.showwarning.call_args.args[1])
        dialogs.showinfo.assert_not_called()

    def test_isolated_timeouts_do_not_trip_the_breaker(self):
        product = {'ProductVariations': [{'PackageType': {'Name': 'Cut Tape (CT)'}, 'QuantityAvailable': 5,
                                          'StandardPricing': [{'BreakQuantity': 1, 'UnitPrice': 1}]}]}
        with tempfile.TemporaryDirectory() as folder:
            source = self.make_bom(folder)
            effects = [requests.Timeout('slow'), product, requests.Timeout('slow'), product,
                       requests.Timeout('slow'), product]
            dialogs, lookup, _, output = run_bom_job(source, effects, [0] * 6)
        self.assertEqual(lookup.call_count, 6)
        self.assertEqual(list(output['Digikey Stock'].fillna(-1)), [-1, 5, -1, 5, -1, 5])
        dialogs.showinfo.assert_called_once()
        dialogs.showwarning.assert_not_called()

    def test_stock_server_down_is_reported_once_and_digikey_still_runs(self):
        product = {'ProductVariations': [{'PackageType': {'Name': 'Cut Tape (CT)'}, 'QuantityAvailable': 5,
                                          'StandardPricing': [{'BreakQuantity': 1, 'UnitPrice': 1}]}]}
        with tempfile.TemporaryDirectory() as folder:
            source = self.make_bom(folder, rows=4)
            dialogs, lookup, stock, output = run_bom_job(
                source, [product] * 4, requests.ConnectionError('offline'))
        self.assertEqual(stock.call_count, 1)
        self.assertEqual(lookup.call_count, 4)
        self.assertEqual(list(output['Company Stock QTY']), ['Stock server unreachable'] * 4)
        self.assertEqual(list(output['Digikey Stock']), [5] * 4)
        dialogs.showwarning.assert_called_once()
        self.assertIn('local stock server', dialogs.showwarning.call_args.args[1])


class TestDigikeyKeyRotationOnNetworkError(unittest.TestCase):
    def test_connection_error_is_raised_without_trying_every_key(self):
        class Manager:
            def __init__(self):
                self.keys = [{'id': f'k{i}', 'client_id': 'c', 'client_secret': 's'} for i in range(5)]
                self.blocked_keys = set()
                self.usage = {}

            def rotate_and_get(self, exclude=()):
                for entry in self.keys:
                    if entry['id'] not in exclude:
                        return entry, None
                return None, 0

            def increment_usage(self, entry_id, amount=1):
                self.usage[entry_id] = self.usage.get(entry_id, 0) + amount

        attempts = []

        def attempt(part_number, cid, csecret, entry_id=None, counter=None):
            attempts.append(entry_id)
            raise requests.ConnectionError('network down')

        with patch.object(digikey_api, 'get_manager', return_value=Manager()), \
             patch.object(digikey_api, '_attempt_search_with_credentials', side_effect=attempt):
            with self.assertRaises(requests.ConnectionError):
                digikey_api.get_digikey_part_data('RC0603')
        self.assertEqual(attempts, ['k0'])

    def test_other_errors_still_rotate_to_the_next_key(self):
        class Manager:
            def __init__(self):
                self.keys = [{'id': 'k0', 'client_id': 'c', 'client_secret': 's'},
                             {'id': 'k1', 'client_id': 'c', 'client_secret': 's'}]
                self.blocked_keys = set()

            def rotate_and_get(self, exclude=()):
                for entry in self.keys:
                    if entry['id'] not in exclude:
                        return entry, None
                return None, 0

            def increment_usage(self, entry_id, amount=1):
                pass

        calls = []

        def attempt(part_number, cid, csecret, entry_id=None, counter=None):
            calls.append(entry_id)
            if entry_id == 'k0':
                raise RuntimeError('bad json')
            return {'ok': True}

        with patch.object(digikey_api, 'get_manager', return_value=Manager()), \
             patch.object(digikey_api, '_attempt_search_with_credentials', side_effect=attempt):
            self.assertEqual(digikey_api.get_digikey_part_data('RC0603'), {'ok': True})
        self.assertEqual(calls, ['k0', 'k1'])


if __name__ == '__main__':
    unittest.main()
