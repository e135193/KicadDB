"""Path resolution: every data file lives next to the exe/script (config.APP_DIR)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


class TestConfigPaths(unittest.TestCase):
    def setUp(self):
        # Ignore whatever overrides the developer's settings.json carries.
        self._real_setting = config._setting
        config._setting = lambda name: None

    def tearDown(self):
        config._setting = self._real_setting

    def test_database_sits_next_to_the_exe(self):
        self.assertEqual(config.resolve_db_file(), os.path.join(config.APP_DIR, "KicadDB.sqlite"))

    def test_api_keys_sit_next_to_the_exe(self):
        self.assertEqual(config.resolve_api_keys_file(), os.path.join(config.APP_DIR, "API-Keys.json"))

    def test_backups_go_to_dbBackup_next_to_the_exe(self):
        self.assertEqual(config.DB_BACKUP_DIR, os.path.join(config.APP_DIR, "_dbBackup"))

    def test_nothing_resolves_into_an_internal_folder(self):
        for path in (config.resolve_db_file(), config.resolve_api_keys_file(),
                     config.DB_BACKUP_DIR, config.SETTINGS_FILE, config.LOG_FILE):
            self.assertNotIn("_internal", path.replace("\\", "/").split("/"), path)

    def test_library_root_is_the_nearest_folder_with_the_company_footprints(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root = os.path.join(folder, "kicad_library")
            app = os.path.join(root, "Kicad", "_Source Code")
            os.makedirs(app)
            os.makedirs(os.path.join(root, "library", "footprints.pretty"))
            # A .kicad_dbl one level up must not win over the folder holding library/footprints.pretty.
            open(os.path.join(root, "Kicad", "KicadDB.kicad_dbl"), "w").close()
            self.assertEqual(config.find_library_root(app), os.path.join(root, "library"))
            # Deployed: the exe folder itself holds the library.
            os.makedirs(os.path.join(app, "library", "footprints.pretty"))
            self.assertEqual(config.find_library_root(app), os.path.join(app, "library"))

    def test_library_root_falls_back_to_kicad_dbl_then_parent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            app = os.path.join(folder, "deploy", "Kicad")
            os.makedirs(app)
            self.assertEqual(config.find_library_root(app), os.path.join(folder, "deploy", "library"))
            open(os.path.join(folder, "deploy", "KicadDB.kicad_dbl"), "w").close()
            self.assertEqual(config.find_library_root(app), os.path.join(folder, "deploy", "library"))
        self.assertEqual(config.COMPANY_FOOTPRINT_LIBRARY_NICKNAME, "KicadDB")

    def test_database_path_override_is_honoured(self):
        config._setting = lambda name: "data/Other.sqlite" if name == "database_path" else None
        self.assertEqual(config.resolve_db_file(), os.path.join(config.APP_DIR, "data/Other.sqlite"))

    def test_backup_keep_defaults_to_ten_and_rejects_bad_values(self):
        self.assertEqual(config.resolve_db_backup_keep(), 10)
        for bad in ("abc", 0, -3, ""):
            config._setting = lambda name, bad=bad: bad
            self.assertEqual(config.resolve_db_backup_keep(), 10, repr(bad))
        config._setting = lambda name: 5
        self.assertEqual(config.resolve_db_backup_keep(), 5)


if __name__ == "__main__":
    unittest.main()
