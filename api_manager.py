from api_keys import ApiKeyManager
from config import API_KEYS_FILE

# Shared ApiKeyManager instance for the application
_mgr = None

def get_manager():
    global _mgr
    if _mgr is None:
        _mgr = ApiKeyManager(path=API_KEYS_FILE)
    return _mgr

def reload_manager(path=None):
    global _mgr
    p = path or API_KEYS_FILE
    try:
        _mgr = ApiKeyManager(path=p)
        return True
    except Exception:
        return False

def set_manager(mgr):
    global _mgr
    _mgr = mgr
