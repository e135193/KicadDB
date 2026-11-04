import os
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
from api_keys import ApiKeyManager
import lcsc_api
import lcsc_settings


class TestApiSettings(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.manager = ApiKeyManager(os.path.join(self.folder.name, 'keys.json'), min_keys=0)

    def tearDown(self):
        self.folder.cleanup()

    def test_daily_count_survives_reload_and_rolls_over(self):
        with patch('api_keys._utc_today', return_value='2099-01-01'):
            self.manager.increment_usage('lcsc')
            self.manager._load_usage_cache()
            self.assertEqual(self.manager.usage['lcsc'], 1)
        with patch('api_keys._utc_today', return_value='2099-01-02'):
            self.manager.increment_usage('lcsc')
            self.assertEqual(self.manager.usage['lcsc'], 1)

    def test_lcsc_counts_requests_and_enforces_limit(self):
        response = {'code': 200, 'data': {'componentPageInfo': {'list': []}}}
        with patch.object(lcsc_api, 'get_usage_manager', return_value=self.manager), \
             patch.object(lcsc_api, 'load_settings', return_value={**lcsc_settings.DEFAULTS, 'daily_limit': 1}), \
             patch.object(lcsc_api.LCSCAPIClient, '_make_request', return_value=response) as request:
            client = lcsc_api.LcscAPI()
            self.assertEqual(client.search('test'), [])
            with self.assertRaises(lcsc_api.LCSCRateLimitError):
                client.search('test')
        request.assert_called_once()
        self.assertEqual(self.manager.usage['lcsc'], 1)

    def test_blocked_key_does_not_falsify_usage(self):
        self.manager.keys = [{'id': 'a', 'key': 'test', 'daily_limit': 100}]
        self.manager.increment_usage('a', 2)
        self.manager.blocked_keys.add('a')
        self.assertIsNone(self.manager.get_available_key())
        self.assertEqual(self.manager.usage['a'], 2)

    def test_both_settings_panels_build_without_network(self):
        from ui.settings_window import ApiKeysTab, LcscSettingsTab
        root = tk.Tk()
        root.withdraw()
        try:
            with patch('ui.settings_window.load_keys', return_value=[]), \
                 patch('api_manager.get_manager', return_value=self.manager), \
                 patch.object(lcsc_settings, 'get_usage_manager', return_value=self.manager), \
                 patch.object(lcsc_settings, 'load_settings', return_value=dict(lcsc_settings.DEFAULTS)):
                digikey = ApiKeysTab(root, Mock(), root)
                lcsc = LcscSettingsTab(root, root)
                digikey.frame.pack()
                lcsc.frame.pack()
                root.update_idletasks()
                self.assertEqual(str(digikey.key_entry.cget('show')), '•')
                self.assertIn('Used: 0', lcsc.usage_var.get())
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
