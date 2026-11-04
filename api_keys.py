import os
import json
from datetime import datetime, timezone, timedelta

from config import APP_DIR, API_KEYS_FILE

# Paths come from config.py, which resolves them from the exe's folder. They
# used to be derived from `__file__` here, which in a frozen build points
# inside the bundled runtime rather than next to the exe.
DEFAULT_API_KEYS_FILE = API_KEYS_FILE
CACHE_FILE_NAME = "API-Keys-usage-cache.json"
# The manager stores its cache next to the keys file it was given (see
# ApiKeyManager.cache_file); this module-level name is kept for compatibility.
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(API_KEYS_FILE)) or APP_DIR, CACHE_FILE_NAME)

# Usage is bucketed per UTC day; buckets older than this are dropped on save so
# the cache file does not grow forever.
USAGE_HISTORY_DAYS = 7


def _utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class ApiKeyManager:
    """Manages a list of API keys with simple daily-usage rotation.

    API-Keys.json format:
    {
      "keys": [
        {"id": "user1", "key": "ABC...", "daily_limit": 1000},
        ... up to many entries ...
      ]
    }
    """

    def __init__(self, path=None, min_keys=10):
        self.path = path or DEFAULT_API_KEYS_FILE
        self.cache_file = os.path.join(os.path.dirname(os.path.abspath(self.path)), CACHE_FILE_NAME)
        self.min_keys = min_keys
        self.keys = []
        self.usage = {}
        self.blocked_keys = set()
        self.load()

    def load(self):
        try:
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                keys = data.get("keys") if isinstance(data, dict) else None
                if not isinstance(keys, list):
                    keys = []
            else:
                keys = []
        except Exception:
            keys = []

        # Ensure at least min_keys entries (pad with empty placeholders)
        while len(keys) < self.min_keys:
            keys.append({"id": f"placeholder_{len(keys)+1}", "key": "", "daily_limit": 0})

        self.keys = keys
        self._load_usage_cache()

    def _read_cache(self):
        try:
            if os.path.isfile(self.cache_file):
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _load_usage_cache(self):
        data = self._read_cache()
        today = _utc_today()
        usage = data.get(today, {})
        self.usage = usage if isinstance(usage, dict) else {}
        self.usage_day = today

    def refresh_day(self):
        if getattr(self, "usage_day", None) != _utc_today():
            self.blocked_keys.clear()
            self._load_usage_cache()

    def _save_usage_cache(self):
        try:
            today = _utc_today()
            full = self._read_cache()
            full[today] = self.usage
            cutoff = (datetime.now(timezone.utc) - timedelta(days=USAGE_HISTORY_DAYS)).strftime("%Y-%m-%d")
            full = {day: value for day, value in full.items() if str(day) >= cutoff}
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(full, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_available_key(self, exclude=()):
        """Return a key dict that has remaining quota, or None.

        Accept entries that provide either:
         - a non-empty 'key' (single-token or cid:csecret), or
         - both 'client_id' and 'client_secret' fields.
        """
        self.refresh_day()
        for entry in self.keys:
            if entry.get("id") in self.blocked_keys or entry.get("id") in exclude:
                continue
            key = str(entry.get("key") or "").strip()
            client_id = str(entry.get("client_id") or "").strip()
            client_secret = str(entry.get("client_secret") or "").strip()
            # Skip entries that provide no usable credentials
            if not key and not (client_id and client_secret):
                continue
            limit = int(entry.get("daily_limit") or 0)
            used = int(self.usage.get(entry.get("id"), 0) or 0)
            if limit <= 0 or used < limit:
                return entry
        return None

    def increment_usage(self, entry_id, amount=1):
        self.refresh_day()
        try:
            self.usage[entry_id] = int(self.usage.get(entry_id, 0) or 0) + int(amount)
            self._save_usage_cache()
        except Exception:
            pass

    def rotate_and_get(self, exclude=()):
        """Attempt to find any key with remaining quota; returns (entry, remaining_quota)."""
        entry = self.get_available_key(exclude=exclude)
        if not entry:
            return None, 0
        used = int(self.usage.get(entry.get("id"), 0) or 0)
        limit = int(entry.get("daily_limit") or 0)
        remaining = (limit - used) if limit > 0 else None
        return entry, remaining


if __name__ == "__main__":
    mgr = ApiKeyManager()
    print("Loaded keys:", len(mgr.keys))
    e, r = mgr.rotate_and_get()
    print(e, r)
