"""Authorization must hold even when the form or importer is bypassed."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import db


class TestVerifiedPermissions(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.folder.name, "test.sqlite")
        self.connection_patch = patch.object(db, "get_connection", lambda: sqlite3.connect(self.path))
        self.connection_patch.start()
        with db.connection(commit=True) as conn:
            conn.execute("CREATE TABLE Users (Username TEXT, Role TEXT, Is_Active INTEGER)")
            conn.executemany("INSERT INTO Users VALUES (?, ?, ?)",
                             [("admin", "admin", 1), ("engineer", "user", 1), ("inactive", "admin", 0)])
            for table in ("Resistor", "Capacitor"):
                conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, Description TEXT)")
        db.set_current_user("admin")
        self.verified = db.insert_data("Resistor", {"Description": "Approved", "Verified": "1"})
        self.draft = db.insert_data("Resistor", {"Description": "Draft", "Verified": "0"})
        db.set_current_user("engineer")

    def tearDown(self):
        db.set_current_user("")
        self.connection_patch.stop()
        self.folder.cleanup()

    def snapshot(self):
        with db.connection() as conn:
            return [conn.execute(f'SELECT * FROM "{table}"').fetchall()
                    for table in ("Resistor", "Capacitor", "Change_Log")]

    def test_non_admin_cannot_approve_any_truthy_representation(self):
        for value in ("1", True, "true", "YES", "checked", " Verified "):
            for actor in ("engineer", "inactive", "", "unknown"):
                db.set_current_user(actor)
                before = self.snapshot()
                with self.subTest(value=value, actor=actor):
                    with self.assertRaises(PermissionError):
                        db.insert_data("Capacitor", {"Description": "New", "VERIFIED": value})
                    with self.assertRaises(PermissionError):
                        db.update_data("Resistor", {"verified": value}, "id", self.draft)
                    with self.assertRaises(PermissionError):
                        db.move_data_to_table("Resistor", "Capacitor", {"Description": "Moved", "Verified": value}, "id", self.draft)
                    self.assertEqual(before, self.snapshot())

    def test_approved_record_cannot_be_changed_unapproved_deleted_or_moved(self):
        before = self.snapshot()
        for data in ({"Description": "Edited"}, {"Verified": "0"}, {"Comment": "Import change"}):
            with self.assertRaises(PermissionError):
                db.update_data("Resistor", data, "id", self.verified)
        with self.assertRaises(PermissionError):
            db.delete_data("Resistor", "id", self.verified)
        with self.assertRaises(PermissionError):
            db.move_data_to_table("Resistor", "Capacitor", {"Description": "Moved", "Verified": "0"}, "id", self.verified)
        self.assertEqual(before, self.snapshot())

    def test_user_can_manage_drafts(self):
        key = db.insert_data("Capacitor", {"Description": "New", "Verified": "NOT Verified"})
        db.update_data("Capacitor", {"Description": "Edited"}, "id", key)
        moved = db.move_data_to_table("Capacitor", "Resistor", {"Description": "Moved"}, "id", key)
        self.assertEqual(db.delete_data("Resistor", "id", moved), 1)

    def test_admin_can_approve_edit_move_and_delete(self):
        db.set_current_user("admin")
        db.update_data("Resistor", {"Verified": "Verified"}, "id", self.draft)
        db.update_data("Resistor", {"Description": "Edited"}, "id", self.verified)
        moved = db.move_data_to_table("Resistor", "Capacitor", {"Description": "Moved", "Verified": "1"}, "id", self.verified)
        self.assertEqual(db.delete_data("Capacitor", "id", moved), 1)

    def test_role_changes_take_effect_without_logging_in_again(self):
        db.set_current_user("admin")
        with db.connection(commit=True) as conn:
            conn.execute("UPDATE Users SET Role='user' WHERE Username='admin'")
        with self.assertRaises(PermissionError):
            db.update_data("Resistor", {"Description": "Edited"}, "id", self.verified)

    def test_bulk_match_rolls_back_if_any_record_is_verified(self):
        db.set_current_user("admin")
        db.update_data("Resistor", {"Description": "Shared"}, "id", self.verified)
        db.update_data("Resistor", {"Description": "Shared"}, "id", self.draft)
        db.set_current_user("engineer")
        before = self.snapshot()
        with self.assertRaises(PermissionError):
            db.update_data("Resistor", {"Comment": "Changed"}, "Description", "Shared")
        self.assertEqual(before, self.snapshot())

    def test_ui_disables_approval_and_write_buttons_for_verified_selection(self):
        from Company_Kicad_DB import App
        app = Mock(selected_key_value=self.verified)
        app.verified_var.get.return_value = True
        App.apply_verified_permissions(app)
        app.verified_combo.configure.assert_called_with(state="disabled")
        app.update_record_button.configure.assert_called_with(state="disabled")
        app.delete_record_button.configure.assert_called_with(state="disabled")
        app.selected_key_value = None
        App.apply_verified_permissions(app)
        app.save_record_button.configure.assert_called_with(state="normal")


if __name__ == "__main__":
    unittest.main()
