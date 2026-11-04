from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
from log_history import read_log_history, filter_history


class TestLogHistory(unittest.TestCase):
    def test_archives_multiline_and_daily_search(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'app.log'
            # Archive written by an earlier version (Turkish keys) must stay readable.
            Path(str(path) + '.1').write_text('2026-10-05 10:00:00,001 INFO brensan.audit: UPDATE kullanici=engin tablo=IC kayit=2 parca=CMP-1\n', encoding='utf-8')
            path.write_text('2026-10-06 11:00:00,001 ERROR brensan: failed\nTraceback\nerror details\n'
                            '2026-10-07 09:00:00,001 INFO brensan.audit: DELETE user=ayse table=Resistor record=7 part=RC0603 details={"x": 1}\n',
                            encoding='utf-8')
            rows = read_log_history(path)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]['Changed_By'], 'ayse')
            self.assertEqual(rows[0]['Table_Name'], 'Resistor')
            self.assertEqual(rows[0]['Record_Key'], '7')
            self.assertEqual(rows[0]['Part_Number'], 'RC0603')
            self.assertEqual(rows[0]['Action'], 'DELETE')
            self.assertIn('Traceback\nerror details', rows[1]['Details'])
            self.assertEqual(len(filter_history(rows, day='2026-10-05', text='engin+CMP-1')), 1)
            self.assertEqual(len(filter_history(rows, day='2026-10-06', text='engin')), 0)
            self.assertEqual(rows[2]['Record_Key'], '2')
            self.assertEqual(rows[2]['Changed_By'], 'engin')

    def test_ui_reaches_records_beyond_1000_and_filters_days(self):
        from ui.settings_window import HistoryTab
        root = tk.Tk()
        root.withdraw()
        try:
            records = [{'Changed_At': '2026-10-06 12:00:00', 'Details': str(i)} for i in range(1201)]
            records.append({'Changed_At': '2026-10-05 12:00:00', 'Details': 'older'})
            with patch('ui.settings_window.read_log_history', return_value=records):
                tab = HistoryTab(root, Mock(), root)
                self.assertEqual(len(tab.filtered_rows), 1202)
                for _ in range(4):
                    tab.change_page(1)
                self.assertEqual(len(tab.rows), 202)
                self.assertEqual(tab.rows[-1]['Details'], 'older')
                tab.day_var.set('2026-10-05')
                tab.apply_filters()
                self.assertEqual(len(tab.rows), 1)
                self.assertEqual(tab.page, 0)
        finally:
            root.destroy()

    def test_missing_log_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(read_log_history(Path(folder) / 'missing.log'), [])


if __name__ == '__main__':
    unittest.main()
