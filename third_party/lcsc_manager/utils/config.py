"""Brensan host adapter: no KiCad plugin configuration files are read or written."""

def get_config():
    return {"api_timeout": 30, "download_timeout": 60, "api_cache_enabled": False}
