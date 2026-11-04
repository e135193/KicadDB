import os
import re
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from config import DB_FILE

# Every data change is also written to the application log through this
# logger (Company_Kicad_DB.log), in addition to the Change_Log table.
audit_log = logging.getLogger("company.audit")

USERS_TABLE = "Users"
CHANGE_LOG_TABLE = "Change_Log"
# Tables that are not component categories: never listed, searched, counted
# or given the extra component columns.
SYSTEM_TABLES = ("sqlite_sequence", USERS_TABLE, CHANGE_LOG_TABLE,
                 "Password_Reset_Tokens",
                 "PT_Projects", "PT_Boards", "PT_Revisions", "PT_ProjectBoards",
                 "PT_Components", "PT_Lines")

# Who/when columns kept on every component row. They are written only by this
# module; values coming from the form are ignored.
AUDIT_COLUMNS = {
    "Created_At": "TEXT DEFAULT ''",
    "Created_By": "TEXT DEFAULT ''",
    "Modified_At": "TEXT DEFAULT ''",
    "Modified_By": "TEXT DEFAULT ''",
}

_current_user = ""


def set_current_user(username):
    """Name written into Created_By / Modified_By and the change log."""
    global _current_user
    _current_user = str(username or "").strip()


def get_current_user():
    return _current_user or "-"


def current_user_is_admin(conn=None):
    """Resolve current permissions from the account, never from submitted data."""
    if conn is None:
        with connection() as active:
            return current_user_is_admin(active)
    if not _current_user or not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND lower(name)=lower(?)",
        (USERS_TABLE,),
    ).fetchone():
        return False
    row = conn.execute(
        f"SELECT Role, Is_Active FROM {quote_identifier(USERS_TABLE)} WHERE Username = ? COLLATE NOCASE",
        (_current_user,),
    ).fetchone()
    return bool(row and row[0] == "admin" and row[1] == 1)


def current_user_is_active(conn=None):
    if conn is None:
        with connection() as active:
            return current_user_is_active(active)
    if not _current_user or not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND lower(name)=lower(?)",
        (USERS_TABLE,),
    ).fetchone():
        return False
    row = conn.execute(
        f"SELECT Role, Is_Active FROM {quote_identifier(USERS_TABLE)} WHERE Username=? COLLATE NOCASE",
        (_current_user,),
    ).fetchone()
    return bool(row and row[0] in ('admin', 'user') and row[1] == 1)


def record_is_verified(record):
    return any(_normalize_key(key) == "verified" and
               str(value).strip().lower() in {"1", "true", "yes", "checked", "verified"}
               for key, value in record.items())


def _check_verified_write(conn, payload, existing=()):
    if current_user_is_admin(conn):
        return
    if any(record_is_verified(row) for row in existing):
        raise PermissionError('Only administrators can edit, move or delete verified components.')
    if record_is_verified(payload):
        raise PermissionError('Only administrators can mark components as verified.')


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_key(text):
    if text is None:
        return ""
    text = str(text).lower().replace("µ", "u").replace("μ", "u").replace("°", "")
    return re.sub(r"[^a-z0-9]+", "", text)


def quote_identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def get_connection():
    return sqlite3.connect(DB_FILE)


@contextmanager
def connection(commit=False):
    """Connection that is always closed, and rolled back on error.

    Every helper here used to call conn.close() as a plain statement after the
    query, so any failing execute leaked the connection (and on Windows kept a
    lock on the database file). get_connection is resolved at call time so the
    tests can still monkeypatch it.
    """
    conn = get_connection()
    try:
        yield conn
        if commit:
            conn.commit()
    except Exception:
        if commit:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        conn.close()


def is_system_table(name):
    return str(name or "").lower() in {t.lower() for t in SYSTEM_TABLES}


def get_tables():
    with connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [r[0] for r in cur.fetchall() if not is_system_table(r[0])]


def get_total_material_count():
    total = 0
    try:
        tables = get_tables()
        with connection() as conn:
            cur = conn.cursor()
            for table in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}")
                    count = cur.fetchone()[0]
                    total += int(count or 0)
                except Exception:
                    pass
    except Exception:
        return 0
    return total


def get_all_columns(table):
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({quote_identifier(table)})")
        return [r[1] for r in cur.fetchall()]


EXTRA_DB_COLUMNS = {
    "Company_Stock": "REAL DEFAULT NULL",
    "Verified": "TEXT DEFAULT ''",
    "Comment": "TEXT DEFAULT ''",
    "Alternate_Part_Number": "TEXT DEFAULT ''",
    **AUDIT_COLUMNS,
}
AUDIT_COLUMN_KEYS = {_normalize_key(name) for name in AUDIT_COLUMNS}


def is_audit_column(name):
    return _normalize_key(name) in AUDIT_COLUMN_KEYS


def ensure_extra_columns_for_table(table):
    if is_system_table(table):
        return
    columns = get_all_columns(table)
    normalized = {_normalize_key(c) for c in columns}
    missing = [(name, coltype) for name, coltype in EXTRA_DB_COLUMNS.items()
               if _normalize_key(name) not in normalized]
    if not missing:
        return
    with connection(commit=True) as conn:
        cur = conn.cursor()
        for col_name, col_type in missing:
            cur.execute(
                f"ALTER TABLE {quote_identifier(table)} ADD COLUMN {quote_identifier(col_name)} {col_type}"
            )


def ensure_extra_columns_for_all_tables():
    for table in get_tables():
        try:
            ensure_extra_columns_for_table(table)
        except Exception:
            pass


def get_table_info(table):
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({quote_identifier(table)})")
        return cur.fetchall()


def get_primary_key_columns(table):
    return [row[1] for row in get_table_info(table) if row[5]]


def get_immutable_update_columns(table, key_column=None):
    immutable = {"id"}
    if key_column:
        immutable.add(str(key_column))
    for pk_col in get_primary_key_columns(table):
        immutable.add(pk_col)
    # Company_Stock can be edited by all users; not treated as immutable.
    return { _normalize_key(col) for col in immutable if col }


def get_primary_key_column(table):
    info = get_table_info(table)
    names = [r[1] for r in info]
    for row in info:
        if row[5] == 1:
            return row[1]
    if "Company_Part_Number" in names:
        return "Company_Part_Number"
    return names[0] if names else None


def get_column_name_by_alias(columns, aliases):
    lookup = { _normalize_key(c): c for c in columns }
    for alias in aliases:
        key = _normalize_key(alias)
        if key in lookup:
            return lookup[key]
    return None


def get_row_value_by_alias(row_dict, aliases):
    lookup = { _normalize_key(k): k for k in row_dict.keys() }
    for alias in aliases:
        key = _normalize_key(alias)
        if key in lookup:
            return row_dict.get(lookup[key], "")
    return ""


# ============================================================
# CHANGE LOG
# ============================================================

PART_NUMBER_ALIASES = [
    "Manufacturer_Part_Number", "Manufacturer Part Number", "ManufacturerPartNumber",
    "Mfr Part Number", "MPN", "Part Number",
]
CMP_PART_NUMBER_ALIASES = ["Company_Part_Number", "Company Part Number", "CompanyPartNumber", "CMP_Part_Number", "CMP Part Number", "CMPPartNumber"]
MANUFACTURER_ALIASES = ["Manufacturer", "Mfr", "Üretici"]


def missing_part_identifier(value):
    return str(value or "").strip().casefold() in {
        "", "-", "--", "—", "–", "n/a", "n.a.", "na", "none", "null", "-- mixed values --",
    }

CHANGE_LOG_COLUMNS = [
    "id", "Changed_At", "Changed_By", "Action", "Table_Name", "Record_Key", "Part_Number", "Details",
]


def _text(value):
    return "" if value is None else str(value)


def ensure_change_log_table(conn):
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {quote_identifier(CHANGE_LOG_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Changed_At TEXT NOT NULL,
            Changed_By TEXT NOT NULL,
            Action TEXT NOT NULL,
            Table_Name TEXT NOT NULL,
            Record_Key TEXT DEFAULT '',
            Part_Number TEXT DEFAULT '',
            Details TEXT DEFAULT ''
        )"""
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_change_log_record "
        f"ON {quote_identifier(CHANGE_LOG_TABLE)} (Table_Name, Record_Key)"
    )


def write_change_log(conn, action, table, record_key="", part_number="", details=None, user=None, stamp=None):
    """Insert one Change_Log row on `conn` and return the matching log line.

    The caller emits the returned line with audit_log only after the
    transaction has committed, so the log file never reports a change that was
    rolled back.
    """
    ensure_change_log_table(conn)
    user = user or get_current_user()
    stamp = stamp or now_text()
    details_text = json.dumps(details, ensure_ascii=False, default=str) if details else ""
    conn.execute(
        f"INSERT INTO {quote_identifier(CHANGE_LOG_TABLE)} "
        "(Changed_At, Changed_By, Action, Table_Name, Record_Key, Part_Number, Details) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (stamp, user, action, str(table), _text(record_key), _text(part_number), details_text),
    )
    # Keys are parsed back by log_history.read_log_history; keep them in sync.
    line = f"{action} user={user} table={table} record={_text(record_key)}"
    if part_number:
        line += f" part={part_number}"
    if details_text:
        line += f" details={details_text}"
    return line


def _fetch_rows(cur, table, key_column, key_value):
    cur.execute(
        f"SELECT * FROM {quote_identifier(table)} WHERE {quote_identifier(key_column)} = ?",
        (key_value,),
    )
    rows = cur.fetchall()
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in rows]


def _part_number_of(row_dict):
    return _text(get_row_value_by_alias(row_dict, PART_NUMBER_ALIASES)).strip()


def _apply_audit_values(payload, table_lookup, values):
    """Set audit columns in `payload`, using the table's real column names."""
    for name, value in values.items():
        real_col = table_lookup.get(_normalize_key(name))
        if real_col:
            payload[real_col] = value


def get_change_log(limit=500, text="", table=None, record_key=None):
    """Newest-first change log as (columns, rows).

    `text` filters like the main search: '+' separates tokens that must all
    appear somewhere in the row.
    """
    columns = list(CHANGE_LOG_COLUMNS)
    with connection(commit=True) as conn:
        ensure_change_log_table(conn)
        sql = f"SELECT {', '.join(columns)} FROM {quote_identifier(CHANGE_LOG_TABLE)}"
        where, params = [], []
        if table:
            where.append("Table_Name = ?")
            params.append(str(table))
        if record_key is not None:
            where.append("Record_Key = ?")
            params.append(_text(record_key))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, params).fetchall()
    tokens = [t.strip().casefold() for t in str(text or "").split("+") if t.strip()]
    if tokens:
        rows = [r for r in rows
                if all(tok in " ".join(_text(v) for v in r).casefold() for tok in tokens)]
    if limit:
        rows = rows[: int(limit)]
    return columns, rows


# ============================================================
# WRITES
# ============================================================

def insert_data(table, data):
    if not data:
        raise ValueError('No data to add.')
    ensure_extra_columns_for_table(table)
    table_columns = get_all_columns(table)
    table_lookup = { _normalize_key(c): c for c in table_columns }
    payload = {}
    for col, value in data.items():
        real_col = table_lookup.get(_normalize_key(col))
        if real_col and _normalize_key(real_col) not in ("id", "companystock") and not is_audit_column(real_col):
            payload[real_col] = value
    if not payload:
        raise ValueError('No compatible columns to insert.')
    user, stamp = get_current_user(), now_text()
    logged_values = {c: _text(v) for c, v in payload.items() if _text(v) != ""}
    part_number = _part_number_of(payload)
    _apply_audit_values(payload, table_lookup, {
        "Created_At": stamp, "Created_By": user, "Modified_At": stamp, "Modified_By": user,
    })
    columns = list(payload.keys())
    sql_cols = ", ".join(quote_identifier(c) for c in columns)
    placeholders = ", ".join(["?"] * len(columns))
    values = [payload[c] for c in columns]
    with connection(commit=True) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        _check_verified_write(conn, payload)
        cur.execute(
            f"INSERT INTO {quote_identifier(table)} ({sql_cols}) VALUES ({placeholders})", values
        )
        new_id = cur.lastrowid
        line = write_change_log(
            conn, "INSERT", table, new_id, part_number,
            {"values": logged_values}, user=user, stamp=stamp,
        )
    audit_log.info(line)
    return new_id


def update_data(table, data, key_column, key_value, *, expected_row=None):
    """Update a component; optionally reject a stale row snapshot under the write lock."""
    if not data:
        return 0
    if not key_column:
        raise ValueError('Key column not found for the update.')
    ensure_extra_columns_for_table(table)
    table_columns = get_all_columns(table)
    table_lookup = { _normalize_key(c): c for c in table_columns }
    immutable = get_immutable_update_columns(table, key_column)
    payload = {}
    for col, value in data.items():
        ncol = _normalize_key(col)
        real_col = table_lookup.get(ncol)
        if not real_col:
            continue
        if _normalize_key(real_col) in immutable:
            continue
        payload[real_col] = value
    if not payload:
        return 0
    user, stamp = get_current_user(), now_text()
    lines = []
    with connection(commit=True) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        old_rows = _fetch_rows(cur, table, key_column, key_value)
        _check_verified_write(conn, payload, old_rows)
        if expected_row is not None and (len(old_rows) != 1 or old_rows[0] != expected_row):
            raise ValueError('The component changed since it was read. Refresh before updating.')
        if not old_rows:
            return 0
        changes = []
        for old in old_rows:
            diff = {c: {"old": _text(old.get(c)), "new": _text(v)}
                    for c, v in payload.items() if _text(old.get(c)) != _text(v)}
            if diff:
                changes.append((old, diff))
        if not changes:
            # Nothing actually differs: leave Modified_* and the log untouched.
            return len(old_rows)
        _apply_audit_values(payload, table_lookup, {"Modified_At": stamp, "Modified_By": user})
        set_clause = ", ".join(f"{quote_identifier(c)} = ?" for c in payload.keys())
        values = list(payload.values()) + [key_value]
        cur.execute(
            f"UPDATE {quote_identifier(table)} SET {set_clause} WHERE {quote_identifier(key_column)} = ?",
            values,
        )
        affected = cur.rowcount
        for old, diff in changes:
            merged = dict(old)
            merged.update(payload)
            lines.append(write_change_log(
                conn, "UPDATE", table, old.get(key_column, key_value), _part_number_of(merged),
                {"changes": diff}, user=user, stamp=stamp,
            ))
    for line in lines:
        audit_log.info(line)
    return affected


def delete_data(table, key_column, key_value):
    user, stamp = get_current_user(), now_text()
    lines = []
    with connection(commit=True) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        old_rows = _fetch_rows(cur, table, key_column, key_value)
        _check_verified_write(conn, {}, old_rows)
        cur.execute(
            f"DELETE FROM {quote_identifier(table)} WHERE {quote_identifier(key_column)} = ?",
            (key_value,),
        )
        affected = cur.rowcount
        for old in old_rows:
            # The whole row is kept so a deleted part can be restored by hand.
            lines.append(write_change_log(
                conn, "DELETE", table, old.get(key_column, key_value), _part_number_of(old),
                {"deleted_row": {c: _text(v) for c, v in old.items() if _text(v) != ""}},
                user=user, stamp=stamp,
            ))
        from product_tree import track_component_change
        track_component_change(conn, table, old_rows)
    for line in lines:
        audit_log.info(line)
    return affected


def move_data_to_table(source_table, target_table, data, key_column, key_value, *, expected_row=None):
    """Move one component, retaining its live stock and stable BOM identity."""
    if source_table == target_table:
        raise ValueError('Source and destination tables are the same.')
    ensure_extra_columns_for_table(source_table)
    ensure_extra_columns_for_table(target_table)
    target_columns = get_all_columns(target_table)
    target_lookup = { _normalize_key(c): c for c in target_columns }
    payload = { c: data[c] for c in target_columns
                if _normalize_key(c) not in ("id", "companystock") and c in data and not is_audit_column(c) }
    if not payload:
        raise ValueError('No common columns found in the destination table.')
    user, stamp = get_current_user(), now_text()
    part_number = _part_number_of(payload)
    with connection(commit=True) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        old_rows = _fetch_rows(cur, source_table, key_column, key_value)
        if expected_row is not None and (len(old_rows) != 1 or old_rows[0] != expected_row):
            raise ValueError('The component changed since it was read. Refresh before moving.')
        if len(old_rows) != 1:
            raise ValueError('Select exactly one existing component to move.')
        _check_verified_write(conn, payload, old_rows)
        original = old_rows[0]
        # Stock is read-only in the form, but must follow the component when moved.
        stock_column = target_lookup.get("companystock")
        source_stock = get_column_name_by_alias(original, ["Company_Stock"])
        if stock_column and source_stock:
            payload[stock_column] = original[source_stock]
        # The part keeps its original creation stamp; only the move is new.
        _apply_audit_values(payload, target_lookup, {
            "Created_At": _text(get_row_value_by_alias(original, ["Created_At"])) or stamp,
            "Created_By": _text(get_row_value_by_alias(original, ["Created_By"])) or user,
            "Modified_At": stamp,
            "Modified_By": user,
        })
        columns = list(payload.keys())
        sql_cols = ", ".join(quote_identifier(c) for c in columns)
        placeholders = ", ".join(["?"] * len(columns))
        values = [payload[c] for c in columns]
        cur.execute(
            f"INSERT INTO {quote_identifier(target_table)} ({sql_cols}) VALUES ({placeholders})", values
        )
        new_id = cur.lastrowid
        from product_tree import track_component_change
        target_pk = [r[1] for r in cur.execute(f"PRAGMA table_info({quote_identifier(target_table)})") if r[5]]
        target_key = payload.get(target_pk[0], new_id) if len(target_pk) == 1 else new_id
        track_component_change(conn, source_table, old_rows, target_table, target_key)
        cur.execute(
            f"DELETE FROM {quote_identifier(source_table)} WHERE {quote_identifier(key_column)} = ?",
            (key_value,),
        )
        line = write_change_log(
            conn, "MOVE", target_table, new_id, part_number,
            {"from_table": source_table, "from_key": _text(key_value), "to_table": target_table},
            user=user, stamp=stamp,
        )
    audit_log.info(line)
    return new_id


def update_company_stock(table, br_column, code, quantity):
    """Inventory-only sync, including Verified rows; no component edits allowed."""
    import math
    if not isinstance(quantity, (int, float)) or not math.isfinite(quantity):
        raise ValueError('Invalid stock quantity.')
    if is_system_table(table):
        raise ValueError('Stock cannot be written to a system table.')
    if _normalize_key(br_column) not in ("companypartnumber", "cmp_part_number"):
        raise ValueError('A CMP code is required to update stock.')
    ensure_extra_columns_for_table(table)
    key_column = get_primary_key_column(table)
    lines = []
    with connection(commit=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {quote_identifier(table)} WHERE trim({quote_identifier(br_column)}) = ?", (code,))
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        for row in rows:
            old = row.get("Company_Stock")
            if old != quantity:
                lines.append(write_change_log(conn, "STOCK_UPDATE", table, row.get(key_column), code,
                    {"changes": {"Company_Stock": {"old": old, "new": quantity}}}))
        cur.execute(f"UPDATE {quote_identifier(table)} SET Company_Stock = ? WHERE trim({quote_identifier(br_column)}) = ?",
                    (quantity, code))
    for line in lines:
        audit_log.info(line)
    return len(rows)


def fill_missing_company_part_number(table, snapshot, code, quantity):
    """Save an exact inventory match only while the component identity is unchanged."""
    import math
    if is_system_table(table) or missing_part_identifier(code) or not str(code).upper().startswith('CMP-'):
        raise ValueError('A component table and valid CMP code are required.')
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity):
        raise ValueError('Invalid stock quantity.')
    ensure_extra_columns_for_table(table)
    logs = []
    with connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not current_user_is_active(conn):
            raise PermissionError('An active user account is required to fill CMP codes.')
        info = conn.execute(f'PRAGMA table_info({quote_identifier(table)})').fetchall()
        columns, keys = [r[1] for r in info], [r[1] for r in info if r[5]]
        br_column = get_column_name_by_alias(columns, CMP_PART_NUMBER_ALIASES)
        mpn_column = get_column_name_by_alias(columns, PART_NUMBER_ALIASES)
        manufacturer_column = get_column_name_by_alias(columns, MANUFACTURER_ALIASES)
        if len(keys) != 1 or not br_column or not mpn_column or keys[0] == br_column:
            raise ValueError('A stable primary key, CMP column and MPN column are required.')
        key_column = keys[0]
        if key_column not in snapshot:
            raise ValueError('Component primary key is missing from the stock request.')
        rows = _fetch_rows(conn.cursor(), table, key_column, snapshot[key_column])
        if not rows:
            return 0
        current = rows[0]
        # Another user may have assigned a code or edited the MPN during the request.
        if not missing_part_identifier(current.get(br_column)):
            return 0
        identity_columns = [mpn_column] + ([manufacturer_column] if manufacturer_column else [])
        if missing_part_identifier(current.get(mpn_column)) or any(
                _text(current.get(c)) != _text(snapshot.get(c)) for c in identity_columns):
            return 0
        _check_verified_write(conn, {br_column: code}, rows)
        user, stamp = get_current_user(), now_text()
        stock_column = get_column_name_by_alias(columns, ['Company_Stock'])
        payload = {br_column: str(code).strip(), stock_column: quantity}
        _apply_audit_values(payload, {_normalize_key(c): c for c in columns},
                            {'Modified_At': stamp, 'Modified_By': user})
        conn.execute(f'UPDATE {quote_identifier(table)} SET ' +
                     ', '.join(f'{quote_identifier(c)}=?' for c in payload) +
                     f' WHERE {quote_identifier(key_column)}=?', (*payload.values(), snapshot[key_column]))
        logs.append(write_change_log(conn, 'CMP_CODE_UPDATE', table, snapshot[key_column], _part_number_of(current),
            {'changes': {br_column: {'old': current.get(br_column), 'new': payload[br_column]}},
             'source': 'Local stock search by exact MPN'}, user=user, stamp=stamp))
        if current.get(stock_column) != quantity:
            logs.append(write_change_log(conn, 'STOCK_UPDATE', table, snapshot[key_column], payload[br_column],
                {'changes': {stock_column: {'old': current.get(stock_column), 'new': quantity}}}, user=user, stamp=stamp))
    for line in logs:
        audit_log.info(line)
    return 1


def load_table_data(table):
    ensure_extra_columns_for_table(table)
    with connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {quote_identifier(table)}")
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
        return columns, rows


def search_all_tables(text):
    text = str(text or "").strip()
    if not text:
        return [], [], []
    tokens = [token.strip().casefold() for token in text.split("+") if token.strip()]
    if not tokens:
        return [], [], []
    ensure_extra_columns_for_all_tables()
    all_columns = []
    result_items = []
    with connection() as conn:
        cur = conn.cursor()
        for table in get_tables():
            columns = get_all_columns(table)
            if not columns:
                continue
            for col in columns:
                if col not in all_columns:
                    all_columns.append(col)
            cur.execute(f"SELECT * FROM {quote_identifier(table)}")
            for row in cur.fetchall():
                row_dict = dict(zip(columns, row))
                combined = " ".join("" if value is None else str(value) for value in row_dict.values()).casefold()
                if all(token in combined for token in tokens):
                    result_items.append({"table": table, "row_dict": row_dict})
    display_columns = ['Table'] + all_columns
    display_rows = [[item["table"]] + [item["row_dict"].get(col, "") for col in all_columns] for item in result_items]
    return display_columns, display_rows, result_items


def search_selected_table_by_keywords(table, text):
    tokens = [t.strip().casefold() for t in text.split("+") if t.strip()]
    if not tokens:
        return [], [], []
    columns, rows = load_table_data(table)
    display_rows = []
    result_items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        combined = " ".join("" if v is None else str(v) for v in row_dict.values()).casefold()
        if all(token in combined for token in tokens):
            display_rows.append([row_dict.get(c, "") for c in columns])
            result_items.append({"table": table, "row_dict": row_dict})
    return columns, display_rows, result_items
