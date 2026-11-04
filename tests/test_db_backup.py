"""Tests for the daily database backup (db_backup.py)."""
import os
import sqlite3
import sys
import tempfile
import shutil
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_backup import backup_database, backup_file_name, list_backups, describe_backup_result


class TestDailyBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brensan_backup_")
        self.db = os.path.join(self.tmp, "BrensanDB.sqlite")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE Resistor (id INTEGER PRIMARY KEY, Value TEXT)")
        conn.execute("INSERT INTO Resistor (Value) VALUES ('10K')")
        conn.commit()
        conn.close()
        self.backup_dir = os.path.join(self.tmp, "_dbBackup")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, day, **kwargs):
        return backup_database(self.db, self.backup_dir, keep=10, today=day, **kwargs)

    def test_first_run_of_the_day_creates_a_dated_copy(self):
        day = date(2026, 9, 17)
        result = self._run(day)
        self.assertEqual(result["status"], "created")
        self.assertEqual(os.path.basename(result["path"]), "BrensanDB_2026-09-17.sqlite")
        self.assertTrue(os.path.isfile(result["path"]))
        # The copy is a real, readable SQLite database with the data in it.
        conn = sqlite3.connect(result["path"])
        self.assertEqual(conn.execute("SELECT Value FROM Resistor").fetchone()[0], "10K")
        conn.close()
        self.assertFalse(os.path.exists(result["path"] + ".part"))

    def test_second_run_on_the_same_day_does_nothing(self):
        day = date(2026, 9, 17)
        self._run(day)
        first_mtime = os.path.getmtime(os.path.join(self.backup_dir, backup_file_name(self.db, day)))
        result = self._run(day)
        self.assertEqual(result["status"], "exists")
        self.assertEqual(len(list_backups(self.backup_dir, self.db)), 1)
        self.assertEqual(os.path.getmtime(result["path"]), first_mtime)

    def test_force_recreates_todays_backup(self):
        day = date(2026, 9, 17)
        self._run(day)
        result = self._run(day, force=True)
        self.assertEqual(result["status"], "created")
        self.assertEqual(len(list_backups(self.backup_dir, self.db)), 1)

    def test_only_the_newest_ten_backups_are_kept(self):
        start = date(2026, 1, 1)
        for offset in range(13):
            result = self._run(start + timedelta(days=offset))
            self.assertEqual(result["status"], "created")
        backups = list_backups(self.backup_dir, self.db)
        self.assertEqual(len(backups), 10)
        days = [day for day, _ in backups]
        # Oldest three (Jan 1-3) are gone, Jan 4-13 remain, oldest first.
        self.assertEqual(days[0], "2026-01-04")
        self.assertEqual(days[-1], "2026-01-13")

    def test_unrelated_files_in_the_backup_folder_are_never_deleted(self):
        os.makedirs(self.backup_dir)
        stray = os.path.join(self.backup_dir, "notes.txt")
        other = os.path.join(self.backup_dir, "OtherDB_2020-01-01.sqlite")
        for path in (stray, other):
            with open(path, "w") as f:
                f.write("keep me")
        start = date(2026, 1, 1)
        for offset in range(12):
            self._run(start + timedelta(days=offset))
        self.assertTrue(os.path.isfile(stray))
        self.assertTrue(os.path.isfile(other))

    def test_missing_database_is_reported_not_raised(self):
        result = backup_database(os.path.join(self.tmp, "nope.sqlite"), self.backup_dir, keep=10)
        self.assertEqual(result["status"], "missing")
        self.assertIn("not found", describe_backup_result(result))

    def test_messages_are_turkish_and_name_the_file(self):
        day = date(2026, 9, 17)
        created = describe_backup_result(self._run(day))
        exists = describe_backup_result(self._run(day))
        self.assertIn("BrensanDB_2026-09-17.sqlite", created)
        self.assertIn("already exists", exists)


if __name__ == "__main__":
    unittest.main()
