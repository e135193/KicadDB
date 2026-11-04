"""User accounts: password hashing, account rules and login (users.py)."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import users


class UsersTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.original_get_connection = db.get_connection
        db.get_connection = lambda: sqlite3.connect(self.db_path)
        db.set_current_user("tester")

    def tearDown(self):
        db.get_connection = self.original_get_connection
        db.set_current_user("")
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def raw(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


class TestPasswordHashing(unittest.TestCase):
    def test_same_password_gives_different_digests(self):
        salt_a, digest_a, _ = users.hash_password("gizli123")
        salt_b, digest_b, _ = users.hash_password("gizli123")
        self.assertNotEqual(salt_a, salt_b)
        self.assertNotEqual(digest_a, digest_b)

    def test_verify_accepts_only_the_right_password(self):
        salt, digest, iterations = users.hash_password("gizli123")
        self.assertTrue(users.verify_password("gizli123", salt, digest, iterations))
        self.assertFalse(users.verify_password("gizli124", salt, digest, iterations))
        self.assertFalse(users.verify_password("", salt, digest, iterations))

    def test_corrupt_stored_values_do_not_raise(self):
        self.assertFalse(users.verify_password("x", "not-hex", "zz", 1000))
        self.assertFalse(users.verify_password("x", "00", None, "abc"))

    def test_turkish_characters_round_trip(self):
        salt, digest, iterations = users.hash_password("şifreİğü1")
        self.assertTrue(users.verify_password("şifreİğü1", salt, digest, iterations))


class TestStorage(UsersTestCase):
    def test_password_is_not_stored_in_the_database_file(self):
        users.create_user("engin", "CokGizli-Sifre-42", role=users.ROLE_ADMIN)
        rows = self.raw("SELECT Password_Hash, Password_Salt, Password_Iterations FROM Users")
        digest, salt, iterations = rows[0]
        self.assertNotIn("CokGizli", digest)
        self.assertEqual(len(bytes.fromhex(digest)), 32)
        self.assertEqual(len(bytes.fromhex(salt)), users.SALT_BYTES)
        self.assertGreaterEqual(iterations, 100_000)
        with open(self.db_path, "rb") as f:
            self.assertNotIn(b"CokGizli-Sifre-42", f.read())

    def test_password_never_reaches_the_change_log(self):
        users.create_user("engin", "CokGizli-Sifre-42", role=users.ROLE_ADMIN)
        users.set_password("engin", "Baska-Sifre-43")
        text = " ".join(str(v) for row in self.raw("SELECT * FROM Change_Log") for v in row)
        self.assertNotIn("CokGizli", text)
        self.assertNotIn("Baska-Sifre", text)
        self.assertIn("USER_CREATE", text)

    def test_users_table_is_hidden_from_component_tables(self):
        users.create_user("engin", "gizli123", role=users.ROLE_ADMIN)
        self.assertNotIn("Users", db.get_tables())
        self.assertNotIn("Change_Log", db.get_tables())


class TestAccountRules(UsersTestCase):
    def test_create_and_list(self):
        users.create_user("engin", "gizli123", role=users.ROLE_ADMIN, full_name="Engin B.")
        users.create_user("ayse", "gizli456", must_change_password=True)
        listed = {u["Username"]: u for u in users.list_users()}
        self.assertEqual(set(listed), {"engin", "ayse"})
        self.assertEqual(listed["engin"]["Role"], users.ROLE_ADMIN)
        self.assertEqual(listed["engin"]["Created_By"], "tester")
        self.assertTrue(listed["ayse"]["Must_Change_Password"])
        self.assertNotIn("Password_Hash", listed["engin"])

    def test_usernames_are_unique_ignoring_case(self):
        users.create_user("Engin", "gizli123")
        with self.assertRaises(users.UserError):
            users.create_user("engin", "gizli456")

    def test_invalid_input_is_rejected(self):
        for username, password, role in [
            ("", "gizli123", "user"),
            ("a b", "gizli123", "user"),
            ("engin", "12345", "user"),
            ("engin", " gizli123", "user"),
            ("engin", "gizli123", "root"),
        ]:
            with self.assertRaises(users.UserError, msg=(username, password, role)):
                users.create_user(username, password, role=role)
        self.assertEqual(users.count_users(), 0)

    def test_last_active_admin_is_protected(self):
        users.create_user("engin", "gizli123", role=users.ROLE_ADMIN)
        users.create_user("ayse", "gizli456")
        for action in (
            lambda: users.delete_user("engin"),
            lambda: users.update_user("engin", role=users.ROLE_USER),
            lambda: users.update_user("engin", is_active=False),
        ):
            with self.assertRaises(users.UserError):
                action()
        # With a second admin the first one can be changed.
        users.update_user("ayse", role=users.ROLE_ADMIN)
        users.update_user("engin", is_active=False)
        self.assertFalse(users.get_user("engin")["Is_Active"])

    def test_update_records_who_changed_what(self):
        users.create_user("engin", "gizli123", role=users.ROLE_ADMIN)
        users.create_user("ayse", "gizli456")
        users.update_user("ayse", full_name="Ayşe K.", actor="engin")
        user = users.get_user("ayse")
        self.assertEqual(user["Full_Name"], "Ayşe K.")
        self.assertEqual(user["Modified_By"], "engin")
        actions = [r[0] for r in self.raw("SELECT Action FROM Change_Log WHERE Record_Key = 'ayse'")]
        self.assertEqual(actions, ["USER_CREATE", "USER_UPDATE"])


class TestLogin(UsersTestCase):
    def setUp(self):
        super().setUp()
        users.create_user("engin", "gizli123", role=users.ROLE_ADMIN)

    def test_correct_password_signs_in_and_stamps_last_login(self):
        user = users.authenticate("ENGIN", "gizli123")
        self.assertEqual(user["Username"], "engin")
        self.assertTrue(user["Last_Login_At"])
        self.assertTrue(users.get_user("engin")["Last_Login_At"])

    def test_wrong_password_and_unknown_user_give_the_same_message(self):
        with self.assertRaises(users.UserError) as wrong:
            users.authenticate("engin", "yanlis")
        with self.assertRaises(users.UserError) as unknown:
            users.authenticate("yok", "gizli123")
        self.assertEqual(str(wrong.exception), str(unknown.exception))
        self.assertEqual(str(wrong.exception), users.LOGIN_FAILED_MESSAGE)

    def test_inactive_account_cannot_sign_in(self):
        users.create_user("ayse", "gizli456")
        users.update_user("ayse", is_active=False)
        with self.assertRaises(users.UserError) as ctx:
            users.authenticate("ayse", "gizli456")
        self.assertIn("disabled", str(ctx.exception))

    def test_admin_reset_then_forced_change(self):
        users.create_user("ayse", "gizli456")
        users.set_password("ayse", "gecici789", must_change_password=True, actor="engin")
        with self.assertRaises(users.UserError):
            users.authenticate("ayse", "gizli456")
        self.assertTrue(users.authenticate("ayse", "gecici789")["Must_Change_Password"])
        users.change_own_password("ayse", "gecici789", "kalici012")
        self.assertFalse(users.authenticate("ayse", "kalici012")["Must_Change_Password"])

    def test_own_password_change_needs_the_current_password(self):
        with self.assertRaises(users.UserError):
            users.change_own_password("engin", "yanlis", "yenisifre1")
        with self.assertRaises(users.UserError):
            users.change_own_password("engin", "gizli123", "gizli123")
        users.authenticate("engin", "gizli123")


if __name__ == "__main__":
    unittest.main()
