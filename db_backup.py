"""Daily backup of KicadDB.sqlite.

On the first start of each calendar day a dated copy of the database is
written to `_dbBackup/` next to the exe (config.DB_BACKUP_DIR) as
`KicadDB_YYYY-MM-DD.sqlite`. Further starts on the same day do nothing.
Only the newest `keep` copies are retained; older ones are deleted.

The copy is made with SQLite's online backup API rather than a plain file
copy, so a backup taken while KiCad has the file open over ODBC is still
consistent. The copy is written to a `.part` file first and renamed once
complete, so an interrupted backup never looks like a valid one.
"""
import os
import re
import sqlite3
from datetime import date

from config import DB_FILE, DB_BACKUP_DIR, DB_BACKUP_KEEP

# KicadDB_2026-09-17.sqlite  ->  stem="KicadDB", day="2026-09-17"
BACKUP_NAME_RE = re.compile(r"^(?P<stem>.+)_(?P<day>\d{4}-\d{2}-\d{2})\.sqlite$", re.IGNORECASE)


def backup_file_name(db_file, day=None):
    day = day or date.today()
    stem = os.path.splitext(os.path.basename(db_file))[0]
    return f"{stem}_{day.isoformat()}.sqlite"


def list_backups(backup_dir, db_file):
    """Existing backups of `db_file` in `backup_dir`, oldest first.

    Returns a list of (day_str, path). Files that do not follow the
    `<stem>_YYYY-MM-DD.sqlite` pattern are ignored so nothing unrelated is ever
    deleted from that folder.
    """
    stem = os.path.splitext(os.path.basename(db_file))[0].lower()
    found = []
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return found
    for name in names:
        match = BACKUP_NAME_RE.match(name)
        if not match or match.group("stem").lower() != stem:
            continue
        path = os.path.join(backup_dir, name)
        if os.path.isfile(path):
            found.append((match.group("day"), path))
    found.sort(key=lambda item: (item[0], os.path.getmtime(item[1])))
    return found


def _copy_sqlite(src_path, dst_path):
    src = sqlite3.connect(src_path)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def prune_backups(backup_dir, db_file, keep):
    """Delete the oldest backups so that at most `keep` remain. Returns deleted paths."""
    deleted = []
    if keep is None or keep < 1:
        return deleted
    backups = list_backups(backup_dir, db_file)
    while len(backups) > keep:
        _, path = backups.pop(0)
        try:
            os.remove(path)
            deleted.append(path)
        except OSError:
            break
    return deleted


def backup_database(db_file=None, backup_dir=None, keep=None, today=None, force=False):
    """Create today's backup if it does not exist yet, then prune old ones.

    Returns a dict:
      status  : "created" | "exists" | "missing" | "error"
      path    : backup file path (for created/exists)
      deleted : list of removed old backups
      error   : message when status == "error"
    `force=True` re-creates today's backup even if it already exists (used by
    the manual "Back up now" button).
    """
    db_file = db_file or DB_FILE
    backup_dir = backup_dir or DB_BACKUP_DIR
    keep = DB_BACKUP_KEEP if keep is None else keep
    today = today or date.today()
    result = {"status": "", "path": "", "deleted": [], "error": "", "backup_dir": backup_dir}

    if not os.path.isfile(db_file):
        result["status"] = "missing"
        result["error"] = f"Database file not found: {db_file}"
        return result

    target = os.path.join(backup_dir, backup_file_name(db_file, today))
    result["path"] = target
    if os.path.isfile(target) and not force:
        result["status"] = "exists"
        return result

    partial = target + ".part"
    try:
        os.makedirs(backup_dir, exist_ok=True)
        _copy_sqlite(db_file, partial)
        os.replace(partial, target)
    except Exception as exc:
        try:
            if os.path.isfile(partial):
                os.remove(partial)
        except OSError:
            pass
        result["status"] = "error"
        result["error"] = str(exc)
        return result

    result["status"] = "created"
    result["deleted"] = prune_backups(backup_dir, db_file, keep)
    return result


def describe_backup_result(result):
    """Turkish one-line status message for the UI."""
    status = result.get("status")
    name = os.path.basename(result.get("path") or "")
    if status == "created":
        text = f"Database backup created: {name}"
        if result.get("deleted"):
            text += f" (old backups deleted: {len(result['deleted'])})"
        return text
    if status == "exists":
        return f"Today's backup already exists: {name}"
    if status == "missing":
        return result.get("error") or 'Database file not found.'
    return f"Database backup failed: {result.get('error') or 'unknown error'}"
