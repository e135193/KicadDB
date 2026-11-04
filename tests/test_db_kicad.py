import os
import sqlite3
import tempfile
import unittest
import shutil

import db
import kicad_symbols
from lcsc_api import valid_lcsc_id, LCSCAPIError, extract_lcsc_fields

class TestDBAndKiCad(unittest.TestCase):
    def setUp(self):
        # Create a temporary sqlite DB file
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        # Initialize schema
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE Parts (id INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT, Brensan_Part_Number TEXT)")
        cur.execute("CREATE TABLE Other (id INTEGER PRIMARY KEY AUTOINCREMENT, Value TEXT)")
        conn.commit()
        conn.close()
        # Monkeypatch db.get_connection to use this test DB
        self.original_get_connection = db.get_connection
        db.get_connection = lambda: sqlite3.connect(self.db_path)

    def tearDown(self):
        # Restore
        db.get_connection = self.original_get_connection
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def test_insert_update_delete_and_counts(self):
        # Insert a row
        db.insert_data("Parts", {"Name": "Resistor 10k", "Company_Part_Number": "CMP-001"})
        cols, rows = db.load_table_data("Parts")
        self.assertEqual(len(rows), 1)
        # Update the row
        pk = rows[0][0]
        affected = db.update_data("Parts", {"Name": "Resistor 22k"}, "id", pk)
        self.assertEqual(affected, 1)
        cols, rows = db.load_table_data("Parts")
        self.assertIn("Resistor 22k", str(rows))
        # Total material count
        total = db.get_total_material_count()
        self.assertGreaterEqual(total, 1)
        # Delete
        db.delete_data("Parts", "id", pk)
        cols, rows = db.load_table_data("Parts")
        self.assertEqual(len(rows), 0)

    def test_search_functions(self):
        db.insert_data("Parts", {"Name": "Capacitor 1uF", "Company_Part_Number": "CMP-002"})
        db.insert_data("Other", {"Value": "Capacitor 1uF"})
        display_cols, display_rows, items = db.search_all_tables("Capacitor")
        # Should find at least one match
        self.assertTrue(len(items) >= 1)
        cols, rows, items2 = db.search_selected_table_by_keywords("Parts", "Capacitor")
        self.assertTrue(len(items2) >= 1)

    def test_move_data_between_tables(self):
        db.insert_data("Parts", {"Name": "WillMove", "Company_Part_Number": "CMP-003"})
        cols, rows = db.load_table_data("Parts")
        self.assertTrue(len(rows) >= 1)
        pk = rows[0][0]
        row_dict = dict(zip(cols, rows[0]))
        # Create target table with similar columns
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE Target (id INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT, Brensan_Part_Number TEXT)")
        conn.commit()
        conn.close()
        # Move
        db.move_data_to_table("Parts", "Target", row_dict, "id", pk)
        cols_t, rows_t = db.load_table_data("Target")
        self.assertTrue(any("WillMove" in str(r) for r in rows_t))

    def test_kicad_symbol_extraction(self):
        content = '''(kicad_symbol_lib
  (symbol "LibA:U1"
    (description "Test")
  )
  (symbol "R_1"
  )
)
'''
        fd, path = tempfile.mkstemp(suffix=".kicad_sym")
        os.close(fd)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        try:
            symbols = kicad_symbols.extract_kicad_symbols_from_file(path)
            # The extractor prefixes symbols with the library filename, so check for the
            # symbol name substrings instead of exact `LibA:` prefix.
            self.assertTrue(any('U1' in s for s in symbols))
            self.assertTrue(any('R_1' in s for s in symbols))
        finally:
            os.remove(path)

    def test_lcsc_part_identity(self):
        # The removed HTML scraper is replaced by the Manager JSON contract.
        self.assertEqual(valid_lcsc_id(' c25804 '), 'C25804')
        with self.assertRaises(LCSCAPIError):
            valid_lcsc_id('0603WAF1002T5E')

    def test_lcsc_field_extraction(self):
        product = {
            'description': 'Test Component',
            'lcsc_id': 'C25804',
            'manufacturer_part': 'ABC123',
            'manufacturer': 'Acme Corp',
            'datasheet': 'https://lcsc.com/abc.pdf',
            'price': [{'qty': 1, 'price': 1.23}],
            'stock': '42',
            'url': 'https://lcsc.com/product-detail/abc',
        }
        extracted = extract_lcsc_fields(product)
        self.assertEqual(extracted['Description'], 'Test Component')
        self.assertEqual(extracted['LCSC Part Number'], 'C25804')
        self.assertEqual(extracted['Manufacturer Part Number'], 'ABC123')
        self.assertEqual(extracted['price'], '1.23')
        self.assertEqual(extracted['stock'], '42')
        self.assertEqual(extracted['url'], 'https://lcsc.com/product-detail/abc')


if __name__ == '__main__':
    unittest.main()
