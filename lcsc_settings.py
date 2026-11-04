"""Connection and destination settings for the bundled KiCad LCSC Manager core."""
import json
import os
from config import APP_DIR, COMPANY_LCSC_LIBRARY_DIR
from api_keys import ApiKeyManager

SETTINGS_PATH = os.path.join(APP_DIR, "LCSC-settings.json")
DEFAULTS = {"timeout": 30, "daily_limit": 0,
            "library_directory": COMPANY_LCSC_LIBRARY_DIR, "kicad_config_directory": ''}
_manager = None


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as file:
            saved = json.load(file)
            if not isinstance(saved, dict):
                return dict(DEFAULTS)
            return _validate({key: saved.get(key, value) for key, value in DEFAULTS.items()})
    except (OSError, ValueError, TypeError):
        return dict(DEFAULTS)


def _validate(values):
    timeout, limit = int(values["timeout"]), int(values["daily_limit"])
    if not 1 <= timeout <= 120 or limit < 0:
        raise ValueError('Timeout must be 1–120 seconds; daily limit must be at least 0.')
    result = {key: values.get(key, value) for key, value in DEFAULTS.items()}
    result.update(timeout=timeout, daily_limit=limit)
    for key in ('library_directory', 'kicad_config_directory'):
        result[key] = str(result[key] or '').strip()
        if result[key] and not os.path.isabs(result[key]):
            raise ValueError('Choose an absolute folder path for KiCad libraries/settings.')
    return result


def save_settings(values):
    values = _validate(values)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as file:
        json.dump(values, file, indent=2)


def get_usage_manager():
    global _manager
    if _manager is None:
        _manager = ApiKeyManager(path=SETTINGS_PATH, min_keys=0)
        _manager.cache_file = os.path.join(APP_DIR, "LCSC-usage-cache.json")
        _manager._load_usage_cache()
    return _manager
