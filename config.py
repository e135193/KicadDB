import os
import sys
import json


def get_app_dir():
    """Directory the application runs from.

    Frozen (PyInstaller): the folder that holds the .exe. From source: the
    folder that holds this module. Every data file lives here.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


IS_FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = get_app_dir()

# Nickname under which KiCad's fp-lib-table registers the company
# `Footprints.pretty`; records store footprints as "KicadDB:<name>".
COMPANY_FOOTPRINT_LIBRARY_NICKNAME = "KicadDB"
COMPANY_FOOTPRINT_LIBRARY_DIR_NAME = "footprints.pretty"
COMPANY_SYMBOL_DIR_NAME = "symbols"
COMPANY_3DMODEL_DIR_NAME = "3dmodels"
COMPANY_LCSC_DIR_NAME = "lcsc"


def find_library_root(start=None):
    """Folder that holds the KiCad library (`library/` with symbols/, footprints.pretty/, 3dmodels/, lcsc/).

    The library now lives in a `library/` subdirectory. Walk upwards from
    `start` (the app folder) and take the first folder containing a `library/`
    subdirectory with the expected structure; failing that, the parent folder.
    """
    start = os.path.abspath(start or APP_DIR)
    folders = []
    folder = start
    while True:
        folders.append(folder)
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    for folder in folders:
        lib_dir = os.path.join(folder, "library")
        if os.path.isdir(lib_dir):
            # Check for expected subdirectories
            if (os.path.isdir(os.path.join(lib_dir, COMPANY_FOOTPRINT_LIBRARY_DIR_NAME)) or
                os.path.isdir(os.path.join(lib_dir, COMPANY_SYMBOL_DIR_NAME))):
                return lib_dir
    for folder in folders:
        try:
            if any(name.lower().endswith(".kicad_dbl") for name in os.listdir(folder)):
                return os.path.join(folder, "library")
        except OSError:
            continue
    # Default: library/ next to the app (for deployed EXE) or parent of source
    return os.path.join(os.path.dirname(start), "library")


# The KiCad library root (library/symbols/, library/footprints.pretty/, library/3dmodels/, library/lcsc/):
# the deployed folder's library/ subdir, or the nearest ancestor with a library/ folder.
LIBRARY_ROOT = find_library_root()
COMPANY_FOOTPRINT_LIBRARY_DIR = os.path.join(LIBRARY_ROOT, COMPANY_FOOTPRINT_LIBRARY_DIR_NAME)
COMPANY_SYMBOL_LIBRARY_DIR = os.path.join(LIBRARY_ROOT, COMPANY_SYMBOL_DIR_NAME)
COMPANY_3DMODEL_LIBRARY_DIR = os.path.join(LIBRARY_ROOT, COMPANY_3DMODEL_DIR_NAME)
COMPANY_LCSC_LIBRARY_DIR = os.path.join(LIBRARY_ROOT, COMPANY_LCSC_DIR_NAME)

SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
LEGACY_FOOTPRINT_JSON_FILE = os.path.join(APP_DIR, "footprints.json")
BR_PART_NUMBERS_FILE = os.path.join(APP_DIR, "Company Part Numbers.xlsx")
BOM_TEMPLATE_FILE = os.path.join(APP_DIR, "template", "BOM_Template.xlsx")
PROJECT_IMPORT_TEMPLATE_FILE = os.path.join(APP_DIR, "template", "Project_Import_Template.xlsx")
PART_NUMBER_REQUEST_TEMPLATE = os.path.join(APP_DIR, "MCC-A04FRM100-100-B_Part Number Request (001).xlsx")
PART_NUMBER_REQUEST_FILE = os.path.join(APP_DIR, "Company Part Number Request.xlsx")
LOG_FILE = os.path.join(APP_DIR, "Kicad_DB.log")

DB_FILE_NAME = "KicadDB.sqlite"
API_KEYS_FILE_NAME = "API-Keys.json"

# Daily database backups: `<exe folder>/_dbBackup/KicadDB_YYYY-MM-DD.sqlite`.
DB_BACKUP_DIR_NAME = "_dbBackup"
DB_BACKUP_KEEP_DEFAULT = 10


def _setting(name):
    """Read one override from settings.json without importing the App module.

    config.py must stay import-cycle free, so this deliberately re-reads the
    file instead of reusing load_settings().
    """
    try:
        if os.path.isfile(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data.get(name)
    except Exception:
        pass
    return None


def _path_setting(name):
    value = str(_setting(name) or "").strip()
    if not value:
        return ""
    return value if os.path.isabs(value) else os.path.join(APP_DIR, value)


def resolve_db_file():
    """KicadDB.sqlite sits next to the exe (next to this file from source).

    `database_path` in settings.json overrides it, e.g. to point at the
    library-root copy that KiCad reads over ODBC.
    """
    return _path_setting("database_path") or os.path.join(APP_DIR, DB_FILE_NAME)


def resolve_api_keys_file():
    """API-Keys.json sits next to the exe; `api_keys_path` overrides it."""
    return _path_setting("api_keys_path") or os.path.join(APP_DIR, API_KEYS_FILE_NAME)


def resolve_symbol_library_dir():
    """Locate the KiCad Symbols/ directory.

    Order: `symbol_library_dir` in settings.json, then library/symbols/ next to the
    exe/script, then library/symbols/ in the KiCad library root.
    """
    override = _path_setting("symbol_library_dir")
    if override:
        return override
    local = os.path.join(APP_DIR, "library", "symbols")
    if os.path.isdir(local):
        return local
    shared = os.path.join(LIBRARY_ROOT, COMPANY_SYMBOL_DIR_NAME)
    if os.path.isdir(shared):
        return shared
    return local


def resolve_footprint_library_dir():
    """Locate the KiCad Footprints.pretty directory.

    Order: `footprint_library_dir` in settings.json, then library/footprints.pretty/ next to the
    exe/script, then library/footprints.pretty/ in the KiCad library root.
    """
    override = _path_setting("footprint_library_dir")
    if override:
        return override
    local = os.path.join(APP_DIR, "library", "footprints.pretty")
    if os.path.isdir(local):
        return local
    shared = COMPANY_FOOTPRINT_LIBRARY_DIR
    if os.path.isdir(shared):
        return shared
    return local


def resolve_3dmodel_library_dir():
    """Locate the KiCad 3D models directory.

    Order: `model_library_dir` in settings.json, then library/3dmodels/ next to the
    exe/script, then library/3dmodels/ in the KiCad library root.
    """
    override = _path_setting("model_library_dir")
    if override:
        return override
    local = os.path.join(APP_DIR, "library", "3dmodels")
    if os.path.isdir(local):
        return local
    shared = COMPANY_3DMODEL_LIBRARY_DIR
    if os.path.isdir(shared):
        return shared
    return local


def resolve_lcsc_library_dir():
    """Locate the LCSC library directory.

    Order: `lcsc_library_dir` in settings.json, then library/lcsc/ next to the
    exe/script, then library/lcsc/ in the KiCad library root.
    """
    override = _path_setting("lcsc_library_dir")
    if override:
        return override
    local = os.path.join(APP_DIR, "library", "lcsc")
    if os.path.isdir(local):
        return local
    shared = COMPANY_LCSC_LIBRARY_DIR
    if os.path.isdir(shared):
        return shared
    return local


def resolve_db_backup_keep():
    """How many dated backups to keep; `db_backup_keep` in settings.json overrides."""
    try:
        value = int(_setting("db_backup_keep"))
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return DB_BACKUP_KEEP_DEFAULT


DB_FILE = resolve_db_file()
API_KEYS_FILE = resolve_api_keys_file()
SYMBOL_LIBRARY_DIR = resolve_symbol_library_dir()
FOOTPRINT_LIBRARY_DIR = resolve_footprint_library_dir()
MODEL_LIBRARY_DIR = resolve_3dmodel_library_dir()
LCSC_LIBRARY_DIR = resolve_lcsc_library_dir()
DB_BACKUP_DIR = os.path.join(APP_DIR, DB_BACKUP_DIR_NAME)
DB_BACKUP_KEEP = resolve_db_backup_keep()

# Defaults
DEFAULT_FOOTPRINTS = sorted([
    "Battery", "Button_Switch_SMD", "Button_Switch_THT", "Capacitor_SMD", "Capacitor_THT",
    "Connector", "Connector_USB", "Crystal", "Diode_SMD", "Diode_THT", "Fuse",
    "Inductor_SMD", "Inductor_THT", "LED_SMD", "LED_THT", "Module", "MountingHole",
    "OptoDevice", "Package_BGA", "Package_DFN_QFN", "Package_DIP", "Package_QFP",
    "Package_SO", "Package_TO_SOT_SMD", "Package_TO_SOT_THT", "Relay_SMD", "Relay_THT",
    "Resistor_SMD", "Resistor_THT", "Sensor", "TerminalBlock", "TestPoint", "Transistor_SMD",
    "Transistor_THT",
])
