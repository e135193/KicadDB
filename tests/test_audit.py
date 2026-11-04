"""Record history: Created_*/Modified_* columns, Change_Log table and log lines."""
import json
import logging
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


class AuditTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        conn = sqlite3.connect(self.db_path)
        for table in ("Resistor", "Capacitor"):
            conn.execute(
                f"CREATE TABLE {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "Value TEXT, Manufacturer_Part_Number TEXT, Description TEXT)")
        # A row that existed before this feature was introduced.
        conn.execute("INSERT INTO Resistor (Value, Manufacturer_Part_Number) VALUES ('1K', 'OLD-1K')")
        conn.commit()
        conn.close()
        self.original_get_connection = db.get_connection
        db.get_connection = lambda: sqlite3.connect(self.db_path)
        db.set_current_user("engin")

    def tearDown(self):
        db.get_connection = self.original_get_connection
        db.set_current_user("")
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def row(self, table, key):
        columns, rows = db.load_table_data(table)
        for values in rows:
            record = dict(zip(columns, values))
            if record["id"] == key:
                return record
        return None

    def log_rows(self, **filters):
        columns, rows = db.get_change_log(**filters)
        return [dict(zip(columns, r)) for r in rows]


class TestAuditColumns(AuditTestCase):
    def test_columns_are_added_to_existing_tables(self):
        db.ensure_extra_columns_for_all_tables()
        for table in ("Resistor", "Capacitor"):
            columns = db.get_all_columns(table)
            for name in ("Created_At", "Created_By", "Modified_At", "Modified_By"):
                self.assertIn(name, columns, table)

    def test_rows_from_before_the_feature_have_empty_stamps(self):
        record = self.row("Resistor", 1)
        self.assertEqual(record["Created_By"], "")
        self.assertEqual(record["Created_At"], "")

    def test_insert_stamps_creator_and_time(self):
        new_id = db.insert_data("Resistor", {"Value": "10K", "Manufacturer_Part_Number": "RC0603-10K"})
        record = self.row("Resistor", new_id)
        self.assertEqual(record["Created_By"], "engin")
        self.assertEqual(record["Modified_By"], "engin")
        self.assertRegex(record["Created_At"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(record["Created_At"], record["Modified_At"])

    def test_form_cannot_forge_the_stamps(self):
        new_id = db.insert_data("Resistor", {
            "Value": "10K", "Created_By": "baskasi", "Created_At": "1999-01-01 00:00:00"})
        self.assertEqual(self.row("Resistor", new_id)["Created_By"], "engin")
        db.update_data("Resistor", {"Value": "12K", "Modified_By": "baskasi", "Created_By": "baskasi"}, "id", new_id)
        record = self.row("Resistor", new_id)
        self.assertEqual(record["Modified_By"], "engin")
        self.assertEqual(record["Created_By"], "engin")

    def test_update_changes_modifier_but_keeps_creator(self):
        new_id = db.insert_data("Resistor", {"Value": "10K"})
        db.set_current_user("ayse")
        self.assertEqual(db.update_data("Resistor", {"Value": "12K"}, "id", new_id), 1)
        record = self.row("Resistor", new_id)
        self.assertEqual(record["Value"], "12K")
        self.assertEqual(record["Created_By"], "engin")
        self.assertEqual(record["Modified_By"], "ayse")

    def test_saving_without_a_real_change_is_not_recorded(self):
        new_id = db.insert_data("Resistor", {"Value": "10K", "Description": ""})
        db.set_current_user("ayse")
        self.assertEqual(db.update_data("Resistor", {"Value": "10K", "Description": ""}, "id", new_id), 1)
        self.assertEqual(self.row("Resistor", new_id)["Modified_By"], "engin")
        self.assertEqual([r["Action"] for r in self.log_rows(table="Resistor", record_key=new_id)], ["INSERT"])

    def test_update_of_a_missing_row_reports_zero(self):
        self.assertEqual(db.update_data("Resistor", {"Value": "1"}, "id", 9999), 0)

    def test_snapshot_update_rejects_concurrent_edits_without_changes_or_audit(self):
        key = db.insert_data('Resistor', {'Value': '10K', 'Description': 'Original'})
        snapshot = self.row('Resistor', key)
        db.update_data('Resistor', {'Description': 'Edited by another user'}, 'id', key)
        current, logs = self.row('Resistor', key), self.log_rows()
        with self.assertRaisesRegex(ValueError, 'changed since'):
            db.update_data('Resistor', {'Value': '20K'}, 'id', key, expected_row=snapshot)
        self.assertEqual(self.row('Resistor', key), current)
        self.assertEqual(self.log_rows(), logs)
        self.assertEqual(db.update_data('Resistor', {'Value': '20K'}, 'id', key, expected_row=current), 1)
        self.assertEqual(self.row('Resistor', key)['Value'], '20K')

    def test_snapshot_update_rejects_deleted_rows(self):
        key = db.insert_data('Resistor', {'Value': '10K'})
        snapshot = self.row('Resistor', key)
        db.delete_data('Resistor', 'id', key)
        logs = self.log_rows()
        with self.assertRaisesRegex(ValueError, 'changed since'):
            db.update_data('Resistor', {'Value': '20K'}, 'id', key, expected_row=snapshot)
        self.assertEqual(self.log_rows(), logs)

    def test_move_keeps_the_original_creator(self):
        new_id = db.insert_data("Resistor", {"Value": "10K", "Manufacturer_Part_Number": "X1"})
        created_at = self.row("Resistor", new_id)["Created_At"]
        db.set_current_user("ayse")
        columns, _ = db.load_table_data("Resistor")
        data = {"Value": "10K", "Manufacturer_Part_Number": "X1", "Description": ""}
        moved_id = db.move_data_to_table("Resistor", "Capacitor", data, "id", new_id)
        self.assertIsNone(self.row("Resistor", new_id))
        record = self.row("Capacitor", moved_id)
        self.assertEqual(record["Created_By"], "engin")
        self.assertEqual(record["Created_At"], created_at)
        self.assertEqual(record["Modified_By"], "ayse")

    def test_move_preserves_live_stock_and_ignores_submitted_stock(self):
        key = db.insert_data('Resistor', {'Value': '10K'})
        with db.connection(commit=True) as conn:
            conn.execute('UPDATE Resistor SET Brensan_Stock=42 WHERE id=?', (key,))
        moved = db.move_data_to_table('Resistor', 'Capacitor',
                                      {'Value': '10K', 'Company_Stock': 999}, 'id', key)
        self.assertEqual(self.row('Capacitor', moved)['Company_Stock'], 42)
        self.assertIsNone(self.row('Resistor', key))

    def test_move_rejects_stale_or_missing_source(self):
        key = db.insert_data('Resistor', {'Value': '10K'})
        snapshot = self.row('Resistor', key)
        db.update_data('Resistor', {'Value': '20K'}, 'id', key)
        logs = self.log_rows()
        with self.assertRaisesRegex(ValueError, 'changed since'):
            db.move_data_to_table('Resistor', 'Capacitor', snapshot, 'id', key,
                                  expected_row=snapshot)
        self.assertEqual(self.row('Resistor', key)['Value'], '20K')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            db.move_data_to_table('Resistor', 'Capacitor', snapshot, 'id', 9999)
        self.assertEqual(db.load_table_data('Capacitor')[1], [])
        self.assertEqual(self.log_rows(), logs)

    def test_move_rejects_non_unique_source_key(self):
        for _ in range(2):
            db.insert_data('Resistor', {'Value': '10K'})
        logs = self.log_rows()
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            db.move_data_to_table('Resistor', 'Capacitor', {'Value': '10K'}, 'Value', '10K')
        self.assertEqual(db.load_table_data('Capacitor')[1], [])
        self.assertEqual(self.log_rows(), logs)


class TestChangeLog(AuditTestCase):
    def test_every_write_is_logged_with_details(self):
        new_id = db.insert_data("Resistor", {"Value": "10K", "Manufacturer_Part_Number": "RC0603-10K"})
        db.set_current_user("ayse")
        db.update_data("Resistor", {"Value": "12K"}, "id", new_id)
        db.delete_data("Resistor", "id", new_id)

        entries = list(reversed(self.log_rows(table="Resistor", record_key=new_id)))
        self.assertEqual([e["Action"] for e in entries], ["INSERT", "UPDATE", "DELETE"])
        self.assertEqual([e["Changed_By"] for e in entries], ["engin", "ayse", "ayse"])
        self.assertTrue(all(e["Part_Number"] == "RC0603-10K" for e in entries))

        self.assertEqual(json.loads(entries[0]["Details"])["values"]["Value"], "10K")
        self.assertEqual(json.loads(entries[1]["Details"])["changes"],
                         {"Value": {"old": "10K", "new": "12K"}})
        deleted = json.loads(entries[2]["Details"])["deleted_row"]
        self.assertEqual(deleted["Value"], "12K")
        self.assertEqual(deleted["Created_By"], "engin")

    def test_change_log_is_not_a_component_table(self):
        db.insert_data("Resistor", {"Value": "10K"})
        self.assertEqual(db.get_tables(), ["Capacitor", "Resistor"])
        self.assertEqual(db.get_total_material_count(), 2)
        _, _, items = db.search_all_tables("INSERT")
        self.assertEqual(items, [])

    def test_search_filter_uses_and_tokens(self):
        db.insert_data("Resistor", {"Value": "10K", "Manufacturer_Part_Number": "AAA"})
        db.insert_data("Capacitor", {"Value": "1uF", "Manufacturer_Part_Number": "BBB"})
        self.assertEqual(len(self.log_rows(text="insert")), 2)
        self.assertEqual(len(self.log_rows(text="insert + bbb")), 1)
        self.assertEqual(len(self.log_rows(text="insert + yok")), 0)

    def test_failed_write_leaves_no_log_entry(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE Fuse (id INTEGER PRIMARY KEY, Value TEXT NOT NULL UNIQUE)")
        conn.execute("INSERT INTO Fuse (Value) VALUES ('1A')")
        conn.commit()
        conn.close()
        with self.assertRaises(sqlite3.IntegrityError):
            db.insert_data("Fuse", {"Value": "1A"})
        self.assertEqual(self.log_rows(table="Fuse"), [])


class TestLogFileLines(AuditTestCase):
    def test_changes_reach_the_application_log(self):
        records = []

        class Collector(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Collector()
        db.audit_log.addHandler(handler)
        previous_level = db.audit_log.level
        db.audit_log.setLevel(logging.INFO)
        try:
            new_id = db.insert_data("Resistor", {"Value": "10K", "Manufacturer_Part_Number": "RC0603-10K"})
            db.update_data("Resistor", {"Value": "12K"}, "id", new_id)
            db.delete_data("Resistor", "id", new_id)
        finally:
            db.audit_log.removeHandler(handler)
            db.audit_log.setLevel(previous_level)

        self.assertEqual(len(records), 3)
        self.assertTrue(records[0].startswith("INSERT user=engin table=Resistor"))
        self.assertIn("part=RC0603-10K", records[0])
        self.assertTrue(records[1].startswith("UPDATE user=engin"))
        self.assertIn('"old": "10K"', records[1])
        self.assertIn('"new": "12K"', records[1])
        self.assertTrue(records[2].startswith("DELETE user=engin"))


if __name__ == "__main__":
    unittest.main()
