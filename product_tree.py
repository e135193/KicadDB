"""Persistent project/card BOMs and stable component usage identities."""
import math
import os
import sqlite3
import uuid
from collections import defaultdict
from contextlib import nullcontext

import pandas as pd
import db


TABLES = ("PT_Projects", "PT_Boards", "PT_Revisions", "PT_ProjectBoards",
          "PT_Components", "PT_Lines")
ALIASES = {
    "cmp": ["Company Part Number", "Company_Part_Number", "CMP", "CMP_Part_Number", "CMPPartNumber", "br", "BR", "Brensan Part Number", "Brensan_Part_Number", "BrensanPartNumber", "BPN", "BPM"],
    "mpn": ["Manufacturer Part Number", "MPN", "Part Number", "Mfr Part Number"],
    "manufacturer": ["Manufacturer", "Mfr", "Üretici"],
    "description": ["Description", "Value", "Açıklama"],
    "quantity": ["Quantity", "Qty", "Adet", "Miktar"],
    "references": ["References", "Reference", "Ref", "Designator", "Designators"],
}


def ensure_schema():
    statements = [
        """CREATE TABLE IF NOT EXISTS PT_Projects (
            id INTEGER PRIMARY KEY, code TEXT NOT NULL COLLATE NOCASE,
            name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS PT_Boards (
            id INTEGER PRIMARY KEY, part_number TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS PT_Revisions (
            id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL, revision TEXT NOT NULL COLLATE NOCASE,
            status TEXT NOT NULL, source TEXT NOT NULL, imported_at TEXT NOT NULL,
            imported_by TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
            UNIQUE(board_id, revision))""",
        """CREATE TABLE IF NOT EXISTS PT_ProjectBoards (
            project_id INTEGER NOT NULL, revision_id INTEGER NOT NULL,
            quantity REAL NOT NULL CHECK(quantity > 0), active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(project_id, revision_id))""",
        """CREATE TABLE IF NOT EXISTS PT_Components (
            id TEXT PRIMARY KEY, table_name TEXT, row_key TEXT,
            UNIQUE(table_name, row_key))""",
        """CREATE TABLE IF NOT EXISTS PT_Lines (
            id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL, line_number INTEGER NOT NULL,
            component_id TEXT, br TEXT DEFAULT '', cmp TEXT NOT NULL DEFAULT '', mpn TEXT NOT NULL,
            manufacturer TEXT NOT NULL, description TEXT NOT NULL,
            quantity REAL NOT NULL CHECK(quantity > 0), refs TEXT NOT NULL,
            UNIQUE(revision_id, line_number))""",
        "CREATE INDEX IF NOT EXISTS PT_usage_idx ON PT_Lines(component_id)",
    ]
    with db.connection(commit=True) as conn:
        # Rebuild legacy project uniqueness atomically without changing project IDs.
        # This is a dedicated connection; other callers keep their FK settings.
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('BEGIN IMMEDIATE')
        for statement in statements:
            conn.execute(statement)
        if 'responsible' not in {row[1] for row in conn.execute('PRAGMA table_info(PT_Boards)')}:
            conn.execute("ALTER TABLE PT_Boards ADD COLUMN responsible TEXT NOT NULL DEFAULT ''")
        if "folder" not in {row[1] for row in conn.execute("PRAGMA table_info(PT_Projects)")}:
            conn.execute("ALTER TABLE PT_Projects ADD COLUMN folder TEXT NOT NULL DEFAULT ''")
        if "parent_id" not in {row[1] for row in conn.execute("PRAGMA table_info(PT_Projects)")}:
            conn.execute("ALTER TABLE PT_Projects ADD COLUMN parent_id INTEGER REFERENCES PT_Projects(id)")
        legacy_unique_code = any(index[2] and not index[4] and
            [column[2] for column in conn.execute(f'PRAGMA index_info({db.quote_identifier(index[1])})')] == ['code']
            for index in conn.execute('PRAGMA index_list(PT_Projects)').fetchall())
        if legacy_unique_code:
            conn.execute('''CREATE TABLE PT_Projects_scoped (
                id INTEGER PRIMARY KEY, code TEXT NOT NULL COLLATE NOCASE,
                name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', folder TEXT NOT NULL DEFAULT '',
                parent_id INTEGER REFERENCES PT_Projects(id))''')
            conn.execute('''INSERT INTO PT_Projects_scoped(id,code,name,notes,folder,parent_id)
                SELECT id,code,name,notes,folder,parent_id FROM PT_Projects''')
            conn.execute('DROP TABLE PT_Projects')
            conn.execute('ALTER TABLE PT_Projects_scoped RENAME TO PT_Projects')
        conn.execute("CREATE INDEX IF NOT EXISTS PT_project_parent_idx ON PT_Projects(parent_id)")
        conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS PT_project_root_code_idx
            ON PT_Projects(code COLLATE NOCASE) WHERE parent_id IS NULL''')
        conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS PT_project_child_code_idx
            ON PT_Projects(parent_id,code COLLATE NOCASE) WHERE parent_id IS NOT NULL''')
        revision_columns = {row[1] for row in conn.execute("PRAGMA table_info(PT_Revisions)")}
        for column in ('approved_by', 'approved_at'):
            if column not in revision_columns:
                conn.execute(f"ALTER TABLE PT_Revisions ADD COLUMN {db.quote_identifier(column)} TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE PT_Revisions SET status='Draft' WHERE status=?", ('Taslak',))
        conn.execute("UPDATE PT_Revisions SET status='Approved' WHERE status=?", ('Onaylı',))

        # Add 'cmp' column as alias for 'br' in PT_Lines
        lines_columns = {row[1] for row in conn.execute("PRAGMA table_info(PT_Lines)")}
        if "cmp" not in lines_columns:
            conn.execute("ALTER TABLE PT_Lines ADD COLUMN cmp TEXT NOT NULL DEFAULT ''")
            # Populate cmp from br
            conn.execute("UPDATE PT_Lines SET cmp = br")

        conn.execute("CREATE INDEX IF NOT EXISTS PT_Lines_cmp_idx ON PT_Lines(cmp)")


def text(value):
    return "" if value is None or pd.isna(value) else str(value).strip()


def normalized(value):
    # Keep punctuation: different MPN suffixes must not collapse together.
    return identifier(value).casefold()


def identifier(value):
    """Spreadsheet placeholders are missing identifiers, never match keys."""
    value = text(value)
    return "" if value.casefold() in {"-", "--", "—", "–", "n/a", "n.a.", "na", "none", "null"} else value


def positive(value):
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError('Quantity must be a positive number.') from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError('Quantity must be a positive finite number.')
    return number


def component_catalog(conn=None, *, register=True):
    """Read current components; optionally register their stable row identities."""
    owns_connection = conn is None
    if owns_connection and register:
        ensure_schema()
    result = []
    with db.connection(commit=register) if owns_connection else nullcontext(conn) as conn:
        if owns_connection:
            conn.execute("BEGIN IMMEDIATE" if register else "BEGIN")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if not db.is_system_table(r[0])]
        has_registry = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='PT_Components'").fetchone())
        for table in tables:
            info = conn.execute(f"PRAGMA table_info({db.quote_identifier(table)})").fetchall()
            keys = [r[1] for r in info if r[5]]
            # Composite/no-primary-key tables cannot provide a safe stable match.
            if len(keys) != 1:
                continue
            cursor = conn.execute(f"SELECT * FROM {db.quote_identifier(table)}")
            columns = [c[0] for c in cursor.description]
            aliases = {field: db.get_column_name_by_alias(columns, names)
                       for field, names in ALIASES.items()}
            stock_column = db.get_column_name_by_alias(columns, ["Company_Stock"])
            value_column = db.get_column_name_by_alias(columns, ["Value"])
            status_column = db.get_column_name_by_alias(columns, ["Product Status"])
            alternate_column = db.get_column_name_by_alias(columns,
                ["Alternate Part Number", "Alt Part Number"])
            for values in cursor.fetchall():
                row = dict(zip(columns, values))
                key = text(row[keys[0]])
                if register:
                    conn.execute("INSERT OR IGNORE INTO PT_Components VALUES (?, ?, ?)",
                                 (str(uuid.uuid4()), table, key))
                identity = conn.execute("SELECT id FROM PT_Components WHERE table_name=? AND row_key=?",
                                        (table, key)).fetchone() if has_registry else None
                result.append({"id": identity[0] if identity else None, "table": table, "key": key,
                               "stock": row.get(stock_column) if stock_column else None,
                               "value": text(row.get(value_column)) if value_column else "",
                               "product_status": text(row.get(status_column)) if status_column else "",
                               "alternate_part_number": text(row.get(alternate_column)) if alternate_column else "",
                               **{f: text(row.get(c)) if c else "" for f, c in aliases.items()}})
    return result


def is_obsolete_status(status):
    """Keep lifecycle warnings consistent with the Obsolete Products report."""
    return normalized(status) in {'obsolete', 'not for new designs', 'last time buy'}


def matched_components(lines, catalog):
    """Resolve saved identities or unique CMP/MPN candidates without saving matches."""
    by_id = {part['id']: part for part in catalog if part['id']}
    by_cmp, by_mpn = defaultdict(list), defaultdict(list)
    for part in catalog:
        # Handle both 'cmp' and 'br' keys for backward compatibility
        cmp_val = part.get('cmp') or part.get('br')
        if normalized(cmp_val):
            by_cmp[normalized(cmp_val)].append(part)
        if normalized(part['mpn']):
            by_mpn[normalized(part['mpn'])].append(part)
    for line in lines:
        if line['component_id']:
            yield by_id.get(line['component_id'])
            continue
        # Handle both 'cmp' and 'br' keys for backward compatibility
        cmp_val = line.get('cmp') or line.get('br')
        candidates = by_cmp.get(normalized(cmp_val), [])
        if not candidates:
            candidates = by_mpn.get(normalized(line['mpn']), [])
            manufacturer = normalized(line['manufacturer'])
            if manufacturer:
                candidates = [p for p in candidates if normalized(p['manufacturer']) == manufacturer]
        yield candidates[0] if len(candidates) == 1 else None


def stock_number(value):
    """Keep missing/invalid inventory distinct from a real zero."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def obsolete_products():
    """Read one product per CMP, retaining all component and usage details."""
    with db.connection() as conn:
        conn.execute('BEGIN')
        parts = [p for p in component_catalog(conn, register=False)
                 if is_obsolete_status(p['product_status'])]
        usages = {}
        has_tree = set(TABLES).issubset({r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        if not has_tree:
            return _group_obsolete_products([dict(p, usage=None)
                for p in sorted(parts, key=lambda p: (p['cmp'], p['mpn'], p['table']))])
        cursor = conn.execute('''SELECT l.component_id, p.code AS project,
            parent.code AS parent_project, b.part_number AS board, r.revision,
            pb.active, l.refs, l.quantity, pb.quantity AS board_quantity
            FROM PT_Lines l JOIN PT_Revisions r ON r.id=l.revision_id
            JOIN PT_Boards b ON b.id=r.board_id
            JOIN PT_ProjectBoards pb ON pb.revision_id=r.id
            JOIN PT_Projects p ON p.id=pb.project_id
            LEFT JOIN PT_Projects parent ON parent.id=p.parent_id
            ORDER BY p.code,b.part_number,r.revision,l.line_number''')
        columns = [c[0] for c in cursor.description]
        for values in cursor:
            row = dict(zip(columns, values))
            usages.setdefault(row['component_id'], []).append(row)
        result = []
        for part in sorted(parts, key=lambda p: (p['cmp'], p['mpn'], p['table'])):
            matches = usages.get(part['id'], []) if part['id'] else []
            result.extend(dict(part, usage=row) for row in matches)
            if not matches:
                result.append(dict(part, usage=None))
        return _group_obsolete_products(result)


def _group_obsolete_products(records):
    """Group display rows only; missing CMPs never identify the same product."""
    groups = {}
    fields = ('mpn', 'alternate_part_number', 'manufacturer', 'description', 'product_status')
    for part in records:
        cmp = normalized(part['cmp'])
        identity = (part['table'], part['key'])
        key = ('cmp', cmp) if cmp else ('component', *identity)
        group = groups.setdefault(key, {'cmp': part['cmp'], 'components': {}, 'usages': []})
        group['components'].setdefault(identity, {k: v for k, v in part.items() if k != 'usage'})
        if part['usage'] is not None:
            group['usages'].append(part)
    result = []
    for group in groups.values():
        group['components'] = list(group['components'].values())
        for field in fields:
            group[field] = '; '.join(dict.fromkeys(p[field] for p in group['components'] if p[field]))
        result.append(group)
    return result


def track_component_change(conn, table, old_rows, target=None, new_key=None):
    """Called inside the component CRUD transaction, including moves/deletes."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='PT_Components'").fetchone():
        return
    keys = [r[1] for r in conn.execute(f"PRAGMA table_info({db.quote_identifier(table)})") if r[5]]
    if len(keys) != 1:
        return
    for row in old_rows:
        conn.execute("UPDATE PT_Components SET table_name=?, row_key=? WHERE table_name=? AND row_key=?",
                     (target, text(new_key) if target else None, table, text(row.get(keys[0]))))


def workbook_sheets(path):
    with pd.ExcelFile(path) as book:
        return book.sheet_names


def read_sheet(path, sheet, header=1):
    return pd.read_excel(path, sheet_name=sheet, header=int(header) - 1, dtype=str).fillna("")


def suggest_mapping(columns):
    return {field: db.get_column_name_by_alias(columns, names) or ""
            for field, names in ALIASES.items()}


def template_mapping(columns):
    mapping = suggest_mapping(columns)
    expected = {"cmp": "Company Part Number", "mpn": "Manufacturer Part Number",
                "manufacturer": "Manufacturer", "description": "Description",
                "quantity": "Quantity", "references": "Reference"}
    missing = [expected[key] for key, column in mapping.items() if not column]
    if missing:
        raise ValueError('Missing BOM_Template.xlsx columns: ' + ", ".join(missing))
    return mapping


def is_not_fitted_bom_row(row):
    """Recognize explicit assembly exclusions without guessing from empty IDs."""
    fields = {'value', 'description', 'dnp', 'fitted', 'populate'}
    markers = {'dnp', 'dnf', 'do not populate', 'do not fit', 'not fitted', 'not mounted'}
    return any(normalized(value) in markers for column, value in row.items()
               if normalized(column) in fields)


def prepare_lines(frame, mapping, catalog):
    if not mapping.get("quantity") or not (mapping.get("cmp") or mapping.get("mpn")):
        raise ValueError('A quantity column and at least one CMP/MPN column are required.')
    selected = [c for c in mapping.values() if c]
    if len(selected) != len(set(selected)):
        raise ValueError('A column cannot be assigned to more than one field.')
    if any(c not in frame.columns for c in selected):
        raise ValueError('Selected column not found in the file.')
    cmp_index, mpn_index = {}, {}
    for part in catalog:
        cmp_key = normalized(part["cmp"])
        mpn_key = (normalized(part["manufacturer"]), normalized(part["mpn"]))
        if cmp_key:
            cmp_index.setdefault(cmp_key, []).append(part["id"])
        if all(mpn_key):
            mpn_index.setdefault(mpn_key, []).append(part["id"])
    lines = []
    for index, (_, row) in enumerate(frame.iterrows(), 1):
        if is_not_fitted_bom_row(row):
            continue
        values = {f: text(row[c]) if c else "" for f, c in mapping.items()}
        if not any(values.values()):
            continue
        for field in ("cmp", "mpn", "manufacturer"):
            if field in values:
                values[field] = identifier(values[field])
        # BOM_Template.xlsx prefixes grouped designators with a display arrow.
        values["references"] = values["references"].lstrip("> \t")
        if not values.get("cmp") and not values.get("mpn"):
            raise ValueError(f"BOM row {index}: CMP or MPN is required.")
        try:
            values["quantity"] = positive(values["quantity"])
        except ValueError as exc:
            raise ValueError(f"BOM row {index}: {exc}") from exc
        candidates = cmp_index.get(normalized(values.get("cmp", "")), []) if values.get("cmp") else []
        method = 'CMP code'
        if not candidates:
            candidates = mpn_index.get((normalized(values["manufacturer"]), normalized(values["mpn"])), [])
            method = 'Manufacturer + MPN'
        values.update(component_id=candidates[0] if len(candidates) == 1 else None,
                      candidates=candidates, match=method if len(candidates) == 1 else
                      ('Multiple candidates' if candidates else 'Unmatched'))
        lines.append(values)
    if not lines:
        raise ValueError('No BOM rows to import.')
    return lines


def rows(sql, params=()):
    with db.connection() as conn:
        cursor = conn.execute(sql, params)
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, r)) for r in cursor.fetchall()]


def revision_for(part_number, revision):
    found = rows("""SELECT r.* FROM PT_Revisions r JOIN PT_Boards b ON b.id=r.board_id
                    WHERE b.part_number=? COLLATE NOCASE AND r.revision=? COLLATE NOCASE""",
                 (part_number.strip(), revision.strip()))
    return found[0] if found else None


def revision_lines(revision_id):
    return rows("SELECT *, COALESCE(NULLIF(cmp, ''), br) AS cmp, refs AS 'references' FROM PT_Lines WHERE revision_id=? ORDER BY line_number", (revision_id,))


def difference(revision_id, lines):
    old = revision_lines(revision_id)
    def signature(line):
        return tuple(line.get(f) for f in ("component_id", "br", "mpn", "manufacturer", "description", "quantity", "references"))
    from collections import Counter
    before, after = Counter(map(signature, old)), Counter(map(signature, lines))
    return sum((after - before).values()), sum((before - after).values())


def _link(conn, project_id, revision_id, quantity):
    quantity = positive(quantity)
    board_id = conn.execute("SELECT board_id FROM PT_Revisions WHERE id=?", (revision_id,)).fetchone()
    if not board_id:
        raise ValueError('Board revision not found.')
    existing = conn.execute('SELECT quantity,active FROM PT_ProjectBoards WHERE project_id=? AND revision_id=?',
                            (project_id, revision_id)).fetchone()
    if existing and (existing[0] != quantity or not existing[1]):
        require_bom_edit(revision_id, conn)
    conn.execute("""UPDATE PT_ProjectBoards SET active=0 WHERE project_id=? AND revision_id IN
                    (SELECT id FROM PT_Revisions WHERE board_id=?)""", (project_id, board_id[0]))
    conn.execute("""INSERT INTO PT_ProjectBoards VALUES (?, ?, ?, 1)
                    ON CONFLICT(project_id, revision_id) DO UPDATE SET quantity=excluded.quantity, active=1""",
                 (project_id, revision_id, quantity))


def _import_project(conn, code, name, folder, parent_id=None):
    code, name = text(code), text(name)
    if not code or not name:
        raise ValueError('Project code and name are required.')
    existing = conn.execute('SELECT id FROM PT_Projects WHERE code=? AND parent_id IS ?', (code, parent_id)).fetchone()
    if existing:
        project_id = existing[0]
        conn.execute('UPDATE PT_Projects SET name=? WHERE id=?', (name, project_id))
    else:
        project_id = conn.execute('INSERT INTO PT_Projects(code,name,parent_id) VALUES (?,?,?)',
                                  (code, name, parent_id)).lastrowid
    if text(folder):
        conn.execute('UPDATE PT_Projects SET folder=? WHERE id=?', (text(folder), project_id))
    return project_id


def _project(conn, metadata):
    subcode, subname = text(metadata.get("subproject_code")), text(metadata.get("subproject_name"))
    if bool(subcode) != bool(subname):
        raise ValueError('Subproject code and name are both required.')
    project_id = _import_project(conn, metadata.get("project_code"), metadata.get("project_name"),
                                 metadata.get("project_folder") if not subcode else "")
    if subcode:
        return _import_project(conn, subcode, subname, metadata.get("project_folder"), project_id)
    return project_id


def _existing_project(conn, metadata):
    root = conn.execute('SELECT id,parent_id FROM PT_Projects WHERE code=? AND parent_id IS NULL',
                        (text(metadata.get('project_code')),)).fetchone()
    if not root or root[1] is not None:
        raise ValueError('Select a top-level project defined in Product tree.')
    subcode = text(metadata.get('subproject_code'))
    if not subcode:
        return root[0]
    child = conn.execute('SELECT id FROM PT_Projects WHERE code=? AND parent_id=?',
                         (subcode, root[0])).fetchone()
    if not child:
        raise ValueError('Select a sub-project belonging to this project in Product tree.')
    return child[0]


def can_approve_bom():
    return db.current_user_is_admin()


def _require_bom_admin(conn, action='approve BOMs'):
    if not db.current_user_is_admin(conn):
        raise PermissionError(f'Only active administrators can {action}.')


def _require_bom_user(conn):
    if not db.current_user_is_active(conn):
        raise PermissionError('An active user account is required to add, edit or delete BOMs.')


def bom_permissions(revision_id=None):
    """UI permissions are hints; writes check again inside their transactions."""
    with db.connection() as conn:
        admin = db.current_user_is_admin(conn)
        active = db.current_user_is_active(conn)
        row = conn.execute('SELECT status,approved_by FROM PT_Revisions WHERE id=?',
                           (revision_id,)).fetchone() if revision_id is not None else None
    editable = bool(active and row and (admin or row[0] == 'Draft'))
    return {'add': active, 'edit': editable,
            'delete': admin or editable, 'approve': admin,
            'status': row[0] if row else None, 'approved_by': row[1] if row else ''}


def require_bom_edit(revision_id, conn=None):
    if conn is None:
        with db.connection() as active:
            return require_bom_edit(revision_id, active)
    _require_bom_user(conn)
    row = conn.execute('SELECT status FROM PT_Revisions WHERE id=?', (revision_id,)).fetchone()
    if not row:
        raise ValueError('BOM revision not found. Refresh the list.')
    if row[0] != 'Draft' and not db.current_user_is_admin(conn):
        raise PermissionError('Only administrators can edit approved BOMs. You can add a new revision or edit a Draft BOM.')


def _set_bom_status(conn, revision_id, status, reason):
    """Set approval inside the caller's transaction; return a committed-log candidate."""
    if status == 'Approved':
        _require_bom_admin(conn)
    old = conn.execute('SELECT status,approved_by,approved_at FROM PT_Revisions WHERE id=?',
                       (revision_id,)).fetchone()
    if not old:
        raise ValueError('BOM revision not found. Refresh the list.')
    if status == 'Draft' and old == ('Draft', '', ''):
        return None
    actor, stamp = db.get_current_user(), db.now_text()
    approved_by, approved_at = (actor, stamp) if status == 'Approved' else ('', '')
    conn.execute('UPDATE PT_Revisions SET status=?,approved_by=?,approved_at=? WHERE id=?',
                 (status, approved_by, approved_at, revision_id))
    return db.write_change_log(conn, 'BOM_APPROVE' if status == 'Approved' else 'BOM_APPROVAL_RESET',
                               'PT_Revisions', revision_id,
                               details={'old_status': old[0], 'new_status': status, 'reason': reason},
                               user=actor, stamp=stamp)


def approve_bom(revision_id):
    """Approve a saved revision for all its project links, using the current account."""
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _require_bom_admin(conn)
        if not conn.execute('SELECT 1 FROM PT_Revisions WHERE id=?', (revision_id,)).fetchone():
            raise ValueError('BOM revision not found. Refresh the list.')
        if not conn.execute('SELECT 1 FROM PT_Lines WHERE revision_id=?', (revision_id,)).fetchone():
            raise ValueError('An empty BOM cannot be approved.')
        log_line = _set_bom_status(conn, revision_id, 'Approved', 'Saved BOM approved')
    db.audit_log.info(log_line)


def return_bom_to_draft(revision_id):
    """Explicitly withdraw approval without changing the saved material list."""
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _require_bom_admin(conn, 'return approved BOMs to Draft')
        log_line = _set_bom_status(conn, revision_id, 'Draft', 'Administrator returned BOM to Draft')
    if log_line:
        db.audit_log.info(log_line)


def bom_edit_snapshot(project_id, revision_id, conn=None):
    """Read the saved BOM and selected membership together for conflict detection."""
    if conn is None:
        with db.connection() as active:
            active.execute('BEGIN')
            return bom_edit_snapshot(project_id, revision_id, active)
    cursor = conn.execute('''SELECT r.*, b.part_number,b.name AS board_name,
        pb.project_id,pb.quantity AS board_quantity,pb.active FROM PT_Revisions r
        JOIN PT_Boards b ON b.id=r.board_id JOIN PT_ProjectBoards pb ON pb.revision_id=r.id
        WHERE pb.project_id=? AND r.id=?''', (project_id, revision_id))
    record = cursor.fetchone()
    if not record:
        raise ValueError('BOM link not found. Refresh the list.')
    snapshot = dict(zip((c[0] for c in cursor.description), record))
    cursor = conn.execute('SELECT *, br AS cmp, refs AS "references" FROM PT_Lines WHERE revision_id=? ORDER BY line_number',
                          (revision_id,))
    snapshot['lines'] = [dict(zip((c[0] for c in cursor.description), r)) for r in cursor.fetchall()]
    return snapshot


def update_saved_bom(snapshot, lines, notes, board_quantity, *, source=None):
    """Replace saved content without changing definitions or activating history."""
    revision_id, project_id = snapshot['id'], snapshot['project_id']
    fields = ('component_id', 'cmp', 'mpn', 'manufacturer', 'description', 'quantity', 'references')
    cleaned = []
    for index, line in enumerate(lines, 1):
        values = {key: text(line.get(key)) for key in fields}
        values['component_id'] = line.get('component_id') or None
        values['quantity'] = positive(line.get('quantity'))
        if not identifier(values['cmp']) and not identifier(values['mpn']):
            raise ValueError(f'BOM row {index}: CMP or MPN is required.')
        cleaned.append(values)
    quantity, notes = positive(board_quantity), str(notes or '')
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        require_bom_edit(revision_id, conn)
        current = bom_edit_snapshot(project_id, revision_id, conn)
        if current != snapshot:
            raise ValueError('This BOM changed while the operation was in progress. Refresh the list and try again.')
        old_lines = [{key: row[key] for key in fields} for row in current['lines']]
        if source is None and cleaned == old_lines and notes == current['notes'] and quantity == current['board_quantity']:
            return
        for line in cleaned:
            identity = line['component_id']
            if identity and not conn.execute('SELECT 1 FROM PT_Components WHERE id=? AND table_name IS NOT NULL',
                                            (identity,)).fetchone():
                # Preserve existing historical matches to deleted components.
                if not any(old['component_id'] == identity and all(line[k] == old[k]
                    for k in ('cmp', 'mpn', 'manufacturer')) for old in old_lines):
                    raise ValueError('The matched component was deleted. Clear or update its match.')
        if cleaned != old_lines:
            conn.execute('DELETE FROM PT_Lines WHERE revision_id=?', (revision_id,))
            for index, line in enumerate(cleaned, 1):
                conn.execute('''INSERT INTO PT_Lines
                    (revision_id,line_number,component_id,br,mpn,manufacturer,description,quantity,refs)
                    VALUES (?,?,?,?,?,?,?,?,?)''', (revision_id, index, *(line[key] for key in fields)))
        conn.execute('UPDATE PT_Revisions SET notes=? WHERE id=?', (notes, revision_id))
        if source is not None:
            conn.execute('UPDATE PT_Revisions SET source=?,imported_at=?,imported_by=? WHERE id=?',
                         (os.path.abspath(source), db.now_text(), db.get_current_user(), revision_id))
        conn.execute('UPDATE PT_ProjectBoards SET quantity=? WHERE project_id=? AND revision_id=?',
                     (quantity, project_id, revision_id))
        log_line = db.write_change_log(conn, 'BOM_IMPORT' if source is not None else 'BOM_EDIT', 'PT_Revisions', revision_id,
            details={'project_id': project_id, 'old_lines': old_lines, 'new_lines': cleaned,
                     'old_notes': current['notes'], 'new_notes': notes,
                     'old_board_quantity': current['board_quantity'], 'new_board_quantity': quantity,
                     'source': os.path.abspath(source) if source is not None else current['source']})
    db.audit_log.info(log_line)


def import_bom_into_revision(snapshot, lines, source):
    """Import into the exact selected revision, retaining project links and notes."""
    if not lines or not text(source):
        raise ValueError('A BOM file and material rows are required.')
    update_saved_bom(snapshot, lines, snapshot['notes'], snapshot['board_quantity'], source=source)
    return snapshot['id']


def save_bom(metadata, lines, source, replace=False, *, existing_definitions=False):
    ensure_schema()
    required = ("project_code", "project_name", "board_number", "board_name", "revision")
    if any(not text(metadata.get(k)) for k in required) or not lines:
        raise ValueError('Project, board, revision information and BOM rows are required.')
    status = metadata.get("status", 'Draft')
    if status not in ('Draft', 'Approved'):
        raise ValueError('Invalid BOM status.')
    with db.connection(commit=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_bom_user(conn)
        if status == 'Approved':
            _require_bom_admin(conn)
        project_id = _existing_project(conn, metadata) if existing_definitions else _project(conn, metadata)
        number, name = text(metadata["board_number"]), text(metadata["board_name"])
        board = conn.execute('SELECT id,name FROM PT_Boards WHERE part_number=?', (number,)).fetchone()
        if existing_definitions:
            if not board:
                raise ValueError('Create this card in Product tree before importing its BOM.')
            name = board[1]
        if board and board[1] != name and conn.execute(
                "SELECT 1 FROM PT_Revisions WHERE board_id=? AND status!='Draft'", (board[0],)).fetchone():
            _require_bom_admin(conn, 'rename boards with approved BOMs')
        conn.execute("""INSERT INTO PT_Boards(part_number,name) VALUES (?,?)
                        ON CONFLICT(part_number) DO UPDATE SET name=excluded.name""", (number, name))
        board_id = conn.execute("SELECT id FROM PT_Boards WHERE part_number=?", (number,)).fetchone()[0]
        old = conn.execute("SELECT id,status FROM PT_Revisions WHERE board_id=? AND revision=?",
                           (board_id, text(metadata["revision"]))).fetchone()
        if old:
            require_bom_edit(old[0], conn)
        if old and not replace:
            raise ValueError('This revision already exists. Confirm replacement of the existing BOM.')
        args = (os.path.abspath(source), db.now_text(), db.get_current_user(), text(metadata.get("notes")))
        if old:
            revision_id = old[0]
            conn.execute("UPDATE PT_Revisions SET source=?,imported_at=?,imported_by=?,notes=? WHERE id=?", (*args, revision_id))
            conn.execute("DELETE FROM PT_Lines WHERE revision_id=?", (revision_id,))
        else:
            revision_id = conn.execute("""INSERT INTO PT_Revisions
                (board_id,revision,status,source,imported_at,imported_by,notes) VALUES (?,?,'Draft',?,?,?,?)""",
                (board_id, text(metadata["revision"]), *args)).lastrowid
        for index, line in enumerate(lines, 1):
            identity = line.get("component_id")
            if identity and not conn.execute("SELECT 1 FROM PT_Components WHERE id=? AND table_name IS NOT NULL", (identity,)).fetchone():
                raise ValueError('The matched component was deleted. Refresh the preview.')
            conn.execute("""INSERT INTO PT_Lines
                (revision_id,line_number,component_id,br,cmp,mpn,manufacturer,description,quantity,refs)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (revision_id, index, identity, text(line.get("cmp") or line.get("br")), text(line.get("cmp") or line.get("br")),
                text(line.get("mpn")), text(line.get("manufacturer")), text(line.get("description")),
                positive(line.get("quantity")), text(line.get("references"))))
        _link(conn, project_id, revision_id, metadata["board_quantity"])
        # Replacing content must not revoke or renew an existing approval.
        approval_log = (None if old and old[1] == 'Approved' else
                        _set_bom_status(conn, revision_id, status, 'BOM replaced' if old else 'BOM imported'))
        log_line = (db.write_change_log(conn, 'BOM_REPLACE', 'PT_Revisions', revision_id,
            details={'source': os.path.abspath(source), 'row_count': len(lines)}) if old else None)
    if approval_log:
        db.audit_log.info(approval_log)
    if log_line:
        db.audit_log.info(log_line)
    return revision_id


def attach_revision(metadata, revision_id, *, existing_definitions=False):
    ensure_schema()
    with db.connection(commit=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_bom_user(conn)
        project_id = _existing_project(conn, metadata) if existing_definitions else _project(conn, metadata)
        _link(conn, project_id, revision_id, metadata["board_quantity"])


def link_card_revision(project_id, revision_id, quantity=1):
    """Reuse an existing revision in an existing project selected in the tree."""
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _require_bom_user(conn)
        if not conn.execute('SELECT 1 FROM PT_Projects WHERE id=?', (project_id,)).fetchone():
            raise ValueError('Project not found. Refresh the list.')
        _link(conn, project_id, revision_id, quantity)


def _add_draft_revision(conn, project_id, board_id, revision, quantity):
    if conn.execute('SELECT 1 FROM PT_Revisions WHERE board_id=? AND revision=?',
                    (board_id, revision)).fetchone():
        raise ValueError('This board revision already exists. Use New card → Link saved card to reuse it.')
    revision_id = conn.execute("""INSERT INTO PT_Revisions
        (board_id,revision,status,source,imported_at,imported_by)
        VALUES (?,?,'Draft','','',?)""", (board_id, revision, db.get_current_user())).lastrowid
    _link(conn, project_id, revision_id, quantity)
    return revision_id


def add_card_revision(project_id, board_id, revision, quantity=1):
    """Add an empty Draft to an existing card in the selected project."""
    revision, quantity = text(revision), positive(quantity)
    if not revision:
        raise ValueError('Revision is required.')
    ensure_schema()
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _require_bom_user(conn)
        board = conn.execute('''SELECT b.part_number FROM PT_Boards b
            JOIN PT_Revisions r ON r.board_id=b.id JOIN PT_ProjectBoards pb ON pb.revision_id=r.id
            JOIN PT_Projects p ON p.id=pb.project_id WHERE b.id=? AND p.id=? LIMIT 1''',
            (board_id, project_id)).fetchone()
        if not board:
            raise ValueError('Selected card is no longer linked to this project. Refresh the list.')
        revision_id = _add_draft_revision(conn, project_id, board_id, revision, quantity)
        log_line = db.write_change_log(conn, 'CARD_REVISION_CREATE', 'PT_Revisions', revision_id, board[0],
            details={'project_id': project_id, 'board_id': board_id, 'revision': revision, 'quantity': quantity})
    db.audit_log.info(log_line)
    return revision_id


def create_card(project_id, part_number, name, revision, quantity=1, responsible=None):
    """Add a draft board revision under a project without requiring a BOM file."""
    part_number, name, revision = text(part_number), text(name), text(revision)
    if not part_number or not name or not revision:
        raise ValueError('Board part number, name and revision are required.')
    quantity = positive(quantity)
    ensure_schema()
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _require_bom_user(conn)
        if not conn.execute('SELECT 1 FROM PT_Projects WHERE id=?', (project_id,)).fetchone():
            raise ValueError('Parent project not found. Refresh the list.')
        board = conn.execute('SELECT id,name FROM PT_Boards WHERE part_number=?', (part_number,)).fetchone()
        if board:
            board_id = board[0]
            if board[1] != name:
                raise ValueError(f'This part number is already named "{board[1]}". Use the existing board name.')
            if responsible is not None:
                saved = conn.execute('SELECT responsible FROM PT_Boards WHERE id=?', (board_id,)).fetchone()[0]
                if saved != text(responsible):
                    raise ValueError('This card has a different responsible person. Use Edit card to change it.')
        else:
            board_id = conn.execute('INSERT INTO PT_Boards(part_number,name,responsible) VALUES (?,?,?)',
                                    (part_number, name, text(responsible))).lastrowid
        revision_id = _add_draft_revision(conn, project_id, board_id, revision, quantity)
        log_line = db.write_change_log(conn, 'CARD_CREATE', 'PT_Revisions', revision_id, part_number,
                                      details={'project_id': project_id, 'revision': revision, 'quantity': quantity,
                                               'responsible': conn.execute('SELECT responsible FROM PT_Boards WHERE id=?',
                                                                           (board_id,)).fetchone()[0]})
    db.audit_log.info(log_line)
    return revision_id


def can_edit_card(board_id):
    with db.connection() as conn:
        if not db.current_user_is_active(conn):
            return False
        protected = conn.execute("SELECT 1 FROM PT_Revisions WHERE board_id=? AND status!='Draft'",
                                 (board_id,)).fetchone()
        return not protected or db.current_user_is_admin(conn)


def update_card(board_id, part_number, name, responsible=None):
    part_number, name = text(part_number), text(name)
    if not part_number or not name:
        raise ValueError('Card part number and name are required.')
    try:
        with db.connection(commit=True) as conn:
            conn.execute('BEGIN IMMEDIATE')
            _require_bom_user(conn)
            old = conn.execute('SELECT part_number,name,responsible FROM PT_Boards WHERE id=?', (board_id,)).fetchone()
            if not old:
                raise ValueError('Card not found. Refresh the list.')
            if conn.execute("SELECT 1 FROM PT_Revisions WHERE board_id=? AND status!='Draft'", (board_id,)).fetchone():
                _require_bom_admin(conn, 'edit cards with approved BOMs')
            responsible = old[2] if responsible is None else text(responsible)
            if old == (part_number, name, responsible):
                return
            conn.execute('UPDATE PT_Boards SET part_number=?,name=?,responsible=? WHERE id=?',
                         (part_number, name, responsible, board_id))
            log_line = db.write_change_log(conn, 'CARD_UPDATE', 'PT_Boards', board_id, part_number,
                                          details={'old_number': old[0], 'old_name': old[1], 'name': name,
                                                   'old_responsible': old[2], 'responsible': responsible})
    except sqlite3.IntegrityError as exc:
        raise ValueError('This card part number is already in use.') from exc
    db.audit_log.info(log_line)


def list_projects():
    return rows("""SELECT p.*, COUNT(pb.revision_id) AS revision_count,
        (SELECT COUNT(*) FROM PT_Projects child WHERE child.parent_id=p.id) AS subproject_count
        FROM PT_Projects p LEFT JOIN PT_ProjectBoards pb ON pb.project_id=p.id
        GROUP BY p.id ORDER BY p.code""")


_UNCHANGED_PARENT = object()


PROJECT_AUDIT_FIELDS = ("code", "name", "notes", "folder", "parent_id")


def _project_audit_row(conn, project_id):
    row = conn.execute("SELECT code,name,notes,folder,parent_id FROM PT_Projects WHERE id=?",
                       (project_id,)).fetchone()
    return dict(zip(PROJECT_AUDIT_FIELDS, row)) if row else None


def save_project(code, name, notes="", project_id=None, folder=None, parent_id=_UNCHANGED_PARENT):
    """Create or edit a project definition.

    Like every other product-tree write this requires an active account
    (checked inside the transaction) and records the change in Change_Log:
    PROJECT_CREATE with the stored values, PROJECT_UPDATE with old/new values
    of the fields that actually changed (no row when nothing changed).
    """
    code, name, notes = text(code), text(name), text(notes)
    if not code or not name:
        raise ValueError('Project code and name are required.')
    ensure_schema()
    log_line = None
    try:
        with db.connection(commit=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            _require_bom_user(conn)
            if parent_id is _UNCHANGED_PARENT:
                existing = conn.execute("SELECT parent_id FROM PT_Projects WHERE id=?", (project_id,)).fetchone()
                parent_id = existing[0] if existing else None
            if parent_id is not None:
                parent = conn.execute("SELECT parent_id FROM PT_Projects WHERE id=?", (parent_id,)).fetchone()
                if not parent:
                    raise ValueError('Parent project not found. Refresh the list.')
                if parent_id == project_id or parent[0] is not None:
                    raise ValueError('Choose a different top-level project as the parent.')
                if conn.execute("SELECT 1 FROM PT_Projects WHERE parent_id=?", (project_id,)).fetchone():
                    raise ValueError('A project with subprojects cannot become a subproject. Move its subprojects first.')
            if project_id is None:
                project_id = conn.execute(
                    "INSERT INTO PT_Projects(code,name,notes,folder,parent_id) VALUES (?,?,?,?,?)",
                    (code, name, notes, text(folder), parent_id)).lastrowid
                log_line = db.write_change_log(conn, 'PROJECT_CREATE', 'PT_Projects', project_id, code,
                                               details=_project_audit_row(conn, project_id))
            else:
                before = _project_audit_row(conn, project_id)
                if before is None:
                    raise ValueError('Project not found. Refresh the list.')
                conn.execute("UPDATE PT_Projects SET code=?,name=?,notes=?,parent_id=? WHERE id=?",
                             (code, name, notes, parent_id, project_id))
                if folder is not None:
                    conn.execute("UPDATE PT_Projects SET folder=? WHERE id=?", (text(folder), project_id))
                after = _project_audit_row(conn, project_id)
                changes = {field: {'old': before[field], 'new': after[field]}
                           for field in PROJECT_AUDIT_FIELDS if before[field] != after[field]}
                if changes:
                    log_line = db.write_change_log(conn, 'PROJECT_UPDATE', 'PT_Projects', project_id, code,
                                                   details={'changes': changes})
    except sqlite3.IntegrityError as exc:
        scope = 'under this parent project' if parent_id is not None else 'by another top-level project'
        raise ValueError(f'This project code is already in use {scope}. Enter a different code.') from exc
    if log_line:
        db.audit_log.info(log_line)
    return project_id


def delete_project(project_id):
    """Remove project membership only; shared boards, revisions and parts survive."""
    with db.connection(commit=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_bom_admin(conn, 'delete projects or BOM links')
        if conn.execute("SELECT 1 FROM PT_Projects WHERE parent_id=?", (project_id,)).fetchone():
            raise ValueError('This project contains subprojects. Move or delete them individually before deleting the parent project.')
        before = _project_audit_row(conn, project_id)
        if before is None:
            raise ValueError('Project not found. Refresh the list.')
        links = conn.execute(
            "SELECT revision_id, quantity, active FROM PT_ProjectBoards WHERE project_id=?",
            (project_id,)).fetchall()
        conn.execute("DELETE FROM PT_ProjectBoards WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM PT_Projects WHERE id=?", (project_id,))
        log_line = db.write_change_log(
            conn, 'PROJECT_DELETE', 'PT_Projects', project_id, before['code'],
            details={'project': before,
                     'removed_links': [{'revision_id': r, 'quantity': q, 'active': a} for r, q, a in links]})
    db.audit_log.info(log_line)


def _project_item_revisions(conn, project_id, board_id, revision_id):
    return conn.execute('''SELECT r.id,r.status FROM PT_ProjectBoards pb
        JOIN PT_Revisions r ON r.id=pb.revision_id WHERE pb.project_id=? AND ''' +
        ('r.id=?' if revision_id is not None else 'r.board_id=?'),
        (project_id, revision_id if revision_id is not None else board_id)).fetchall()


def can_delete_project_item(project_id, *, board_id=None, revision_id=None):
    with db.connection() as conn:
        if not db.current_user_is_active(conn):
            return False
        revisions = _project_item_revisions(conn, project_id, board_id, revision_id)
        return bool(revisions and (db.current_user_is_admin(conn) or all(r[1] == 'Draft' for r in revisions)))


def detach_project_item(project_id, *, board_id=None, revision_id=None, purge_unused=False):
    """Remove only the selected membership; do not promote older revisions."""
    if (board_id is None) == (revision_id is None):
        raise ValueError('Select a board or revision.')
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _require_bom_user(conn)
        revisions = _project_item_revisions(conn, project_id, board_id, revision_id)
        if any(r[1] != 'Draft' for r in revisions):
            _require_bom_admin(conn, 'delete approved BOMs')
        if revision_id is not None:
            changed = conn.execute("DELETE FROM PT_ProjectBoards WHERE project_id=? AND revision_id=?",
                                   (project_id, revision_id)).rowcount
        else:
            changed = conn.execute("""DELETE FROM PT_ProjectBoards WHERE project_id=? AND revision_id IN
                (SELECT id FROM PT_Revisions WHERE board_id=?)""", (project_id, board_id)).rowcount
        if not changed:
            raise ValueError('Selected link not found. Refresh the list.')
        deleted = []
        if purge_unused:
            for rid, _ in revisions:
                if not conn.execute('SELECT 1 FROM PT_ProjectBoards WHERE revision_id=?', (rid,)).fetchone():
                    conn.execute('DELETE FROM PT_Lines WHERE revision_id=?', (rid,))
                    conn.execute('DELETE FROM PT_Revisions WHERE id=?', (rid,))
                    deleted.append(rid)
        log_line = db.write_change_log(conn, 'BOM_DELETE', 'PT_ProjectBoards', project_id,
            details={'revision_ids': [r[0] for r in revisions], 'deleted_bom_ids': deleted})
    db.audit_log.info(log_line)


def project_tree(include_history=False):
    return rows("""SELECT p.id AS project_id,p.code,p.name AS project_name,p.parent_id,
        parent.code AS parent_code,parent.name AS parent_name,b.part_number,b.name AS board_name,b.responsible,
        r.*,pb.quantity AS board_quantity,pb.active FROM PT_ProjectBoards pb
        JOIN PT_Projects p ON p.id=pb.project_id JOIN PT_Revisions r ON r.id=pb.revision_id
        JOIN PT_Boards b ON b.id=r.board_id LEFT JOIN PT_Projects parent ON parent.id=p.parent_id """ +
        ("" if include_history else "WHERE pb.active=1 ") +
        "ORDER BY COALESCE(parent.code,p.code),p.code,b.part_number,r.imported_at DESC")


def usage(component_id, include_history=False):
    result = rows("""SELECT p.id AS project_id,p.code,p.name AS project_name,p.parent_id,
        parent.code AS parent_code,parent.name AS parent_name,b.part_number,b.name AS board_name,
        r.revision,r.id AS revision_id,r.status,pb.active,l.refs,l.quantity,pb.quantity AS board_quantity,
        l.quantity*pb.quantity AS total FROM PT_Lines l
        JOIN PT_Revisions r ON r.id=l.revision_id JOIN PT_Boards b ON b.id=r.board_id
        JOIN PT_ProjectBoards pb ON pb.revision_id=r.id JOIN PT_Projects p ON p.id=pb.project_id
        LEFT JOIN PT_Projects parent ON parent.id=p.parent_id
        WHERE l.component_id=? """ + ("" if include_history else "AND pb.active=1 ") +
        "ORDER BY p.code,b.part_number,r.revision", (component_id,))
    stock, value = None, None
    location = rows("SELECT table_name,row_key FROM PT_Components WHERE id=?", (component_id,))
    if location and location[0]["table_name"]:
        table = location[0]["table_name"]
        columns = db.get_all_columns(table)
        keys = db.get_primary_key_columns(table)
        stock_col = db.get_column_name_by_alias(columns, ["Company_Stock"])
        value_col = db.get_column_name_by_alias(columns, ["Value"])
        if (stock_col or value_col) and len(keys) == 1:
            stock_sql = db.quote_identifier(stock_col) if stock_col else 'NULL'
            value_sql = db.quote_identifier(value_col) if value_col else 'NULL'
            found = rows(f"SELECT {stock_sql} AS stock, {value_sql} AS value FROM {db.quote_identifier(table)} "
                         f"WHERE {db.quote_identifier(keys[0])}=?", (location[0]["row_key"],))
            stock = found[0]["stock"] if found else None
            value = found[0]["value"] if found else None
    for row in result:
        row["company_stock"] = stock
        row["value"] = value
    return result


def rematch_line(line_id, component_id):
    """Update a saved BOM link, including approved BOMs, preserving source values."""
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute("SELECT 1 FROM PT_Components WHERE id=? AND table_name IS NOT NULL", (component_id,)).fetchone():
            raise ValueError('Component not found.')
        line = conn.execute('SELECT revision_id,component_id FROM PT_Lines WHERE id=?', (line_id,)).fetchone()
        if not line:
            raise ValueError('BOM row not found. Refresh the list.')
        require_bom_edit(line[0], conn)
        if line[1] == component_id:
            return
        conn.execute("UPDATE PT_Lines SET component_id=? WHERE id=?", (component_id, line_id))
        log_line = db.write_change_log(conn, 'BOM_MATCH_UPDATE', 'PT_Lines', line_id,
            details={'revision_id': line[0], 'old_component_id': line[1], 'new_component_id': component_id})
    if log_line:
        db.audit_log.info(log_line)


def sync_missing_company_numbers(revision_id=None, *, project_id=None):
    """Fill missing BRs in one revision or all linked revisions below a project.

    An existing live match is authoritative (including manual matches). Otherwise
    require an exact, unique MPN match, narrowed by manufacturer when supplied.
    Project sync skips protected revisions for non-admins, including historical
    revisions when permitted. All writes and audit entries commit together.
    """
    if (revision_id is None) == (project_id is None):
        raise ValueError('Select one project or saved BOM revision to synchronize.')
    counters = ('updated', 'already_present', 'ambiguous', 'not_found', 'missing_database_br')
    result = dict.fromkeys(counters, 0)
    logs = []
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _require_bom_user(conn)
        if project_id is None:
            require_bom_edit(revision_id, conn)
            revision_ids = [revision_id]
        else:
            if not conn.execute('SELECT 1 FROM PT_Projects WHERE id=?', (project_id,)).fetchone():
                raise ValueError('Project not found. Refresh the list.')
            revisions = conn.execute('''WITH RECURSIVE subtree(id) AS (
                SELECT id FROM PT_Projects WHERE id=?
                UNION
                SELECT p.id FROM PT_Projects p JOIN subtree s ON p.parent_id=s.id
            ) SELECT DISTINCT r.id,r.status FROM subtree s
                JOIN PT_ProjectBoards pb ON pb.project_id=s.id
                JOIN PT_Revisions r ON r.id=pb.revision_id ORDER BY r.id''', (project_id,)).fetchall()
            admin = db.current_user_is_admin(conn)
            revision_ids = [rid for rid, status in revisions if admin or status == 'Draft']
            result.update(revisions=len(revision_ids), skipped_revisions=len(revisions) - len(revision_ids))
        catalog = component_catalog(conn)
        by_id = {part['id']: part for part in catalog}
        by_mpn = {}
        for part in catalog:
            if not db.missing_part_identifier(part['mpn']):
                by_mpn.setdefault(normalized(part['mpn']), []).append(part)
        for rid in revision_ids:
            summary, log_line = _sync_revision_company_numbers(conn, rid, by_id, by_mpn)
            for key in counters:
                result[key] += summary[key]
            if log_line:
                logs.append(log_line)
    for log_line in logs:
        db.audit_log.info(log_line)
    return {**result, 'catalog': catalog}


def _sync_revision_company_numbers(conn, revision_id, by_id, by_mpn):
    """Synchronize one revision inside the caller's transaction and catalog snapshot."""
    require_bom_edit(revision_id, conn)
    result = dict(updated=0, already_present=0, ambiguous=0, not_found=0, missing_database_br=0)
    log_line, changes = None, []
    cursor = conn.execute('SELECT *, br AS cmp FROM PT_Lines WHERE revision_id=? ORDER BY line_number', (revision_id,))
    columns = [column[0] for column in cursor.description]
    lines = [dict(zip(columns, values)) for values in cursor.fetchall()]
    for line in lines:
        if not db.missing_part_identifier(line['cmp']):
            result['already_present'] += 1
            continue
        part = by_id.get(line['component_id'])
        if part is None:
            candidates = ([] if db.missing_part_identifier(line['mpn']) else
                          by_mpn.get(normalized(line['mpn']), []))
            if not db.missing_part_identifier(line['manufacturer']):
                candidates = [p for p in candidates
                              if normalized(p['manufacturer']) == normalized(line['manufacturer'])]
            if len(candidates) != 1:
                result['ambiguous' if candidates else 'not_found'] += 1
                continue
            part = candidates[0]
        if db.missing_part_identifier(part['cmp']):
            result['missing_database_br'] += 1
            continue
        conn.execute('UPDATE PT_Lines SET br=?, cmp=?, component_id=? WHERE id=?', (part['cmp'], part['cmp'], part['id'], line['id']))
        changes.append({'line_id': line['id'], 'line_number': line['line_number'],
                        'mpn': line['mpn'], 'references': line['refs'],
                        'old_br': line['cmp'], 'new_br': part['cmp'],
                        'old_component_id': line['component_id'], 'new_component_id': part['id']})
    result['updated'] = len(changes)
    if changes:
        log_line = db.write_change_log(conn, 'BOM_SYNC_DATABASE', 'PT_Revisions', revision_id,
                                       details={'summary': result, 'changes': changes})
    return result, log_line


def update_bom_description(revision_id, description):
    description = str(description or '')
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT notes FROM PT_Revisions WHERE id=?', (revision_id,)).fetchone()
        if not old:
            raise ValueError("BOM revision not found. Refresh the list.")
        require_bom_edit(revision_id, conn)
        if old[0] == description:
            return
        conn.execute('UPDATE PT_Revisions SET notes=? WHERE id=?', (description, revision_id))
        log_line = db.write_change_log(conn, 'BOM_DESCRIPTION_UPDATE', 'PT_Revisions', revision_id,
                                       details={'old_notes': old[0], 'new_notes': description})
    if log_line:
        db.audit_log.info(log_line)


def board_revisions(board_id):
    return rows("SELECT * FROM PT_Revisions WHERE board_id=? ORDER BY imported_at DESC,id DESC", (board_id,))


def compare_revisions(before_id, after_id):
    """Compare saved snapshots, pairing unique references or component identities.

    Repeated ambiguous rows remain additions/removals instead of guessed changes.
    Row order alone is not a BOM change.
    """
    from collections import defaultdict
    import re
    revisions = rows("SELECT * FROM PT_Revisions WHERE id IN (?,?)", (before_id, after_id))
    lookup = {r["id"]: r for r in revisions}
    if before_id not in lookup or after_id not in lookup:
        raise ValueError("Revision not found. Refresh the list.")
    if lookup[before_id]["board_id"] != lookup[after_id]["board_id"]:
        raise ValueError("Select two revisions of the same board.")
    labels = {"br": "BR part number", "mpn": "MPN", "manufacturer": "Manufacturer",
              "description": "Description", "quantity": "Quantity", "references": "References",
              "component_id": "Database match"}
    def refs(line):
        return tuple(sorted(filter(None, re.split(r"[,;\s]+", line["references"].strip().upper()))))
    def signature(line):
        return tuple(refs(line) if key == "references" else line.get(key) for key in labels)
    before = dict(enumerate(revision_lines(before_id)))
    after = dict(enumerate(revision_lines(after_id)))
    exact = defaultdict(list)
    for key, line in after.items():
        exact[signature(line)].append(key)
    for key, line in list(before.items()):
        matching = exact[signature(line)]
        if matching:
            after.pop(matching.pop())
            before.pop(key)
    changes = []
    def pair_unique(key_function):
        old_groups, new_groups = defaultdict(list), defaultdict(list)
        for key, line in before.items():
            if value := key_function(line):
                old_groups[value].append(key)
        for key, line in after.items():
            if value := key_function(line):
                new_groups[value].append(key)
        for value in old_groups.keys() & new_groups.keys():
            if len(old_groups[value]) != 1 or len(new_groups[value]) != 1:
                continue
            old = before.pop(old_groups[value][0])
            new = after.pop(new_groups[value][0])
            fields = [(label, old.get(key), new.get(key)) for key, label in labels.items()
                      if (refs(old) != refs(new) if key == "references" else old.get(key) != new.get(key))]
            changes.append({"kind": "Changed", "before": old, "after": new, "fields": fields})
    pair_unique(refs)
    pair_unique(lambda line: line["component_id"])
    pair_unique(lambda line: normalized(line.get("cmp")))
    pair_unique(lambda line: (normalized(line["manufacturer"]), normalized(line["mpn"]))
                if line["manufacturer"] and line["mpn"] else None)
    changes.extend({"kind": "Removed", "before": line, "after": None, "fields": []} for line in before.values())
    changes.extend({"kind": "Added", "before": None, "after": line, "fields": []} for line in after.values())
    changes.sort(key=lambda c: (c["kind"], (c["after"] or c["before"])["line_number"]))
    metadata = [(label, lookup[before_id][key], lookup[after_id][key])
                for key, label in (("notes", "BOM description"), ("status", "Approval status"))
                if lookup[before_id][key] != lookup[after_id][key]]
    return {"changes": changes, "metadata": metadata, "before": lookup[before_id], "after": lookup[after_id]}
