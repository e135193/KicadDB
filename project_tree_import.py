"""Validate and atomically import Project_Import_Template.xlsx workbooks."""
from collections import defaultdict
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

import db
import product_tree as model


HEADERS = {
    'Projects': ('Project Code', 'Project Name', 'Parent Project Code', 'Project Folder', 'Description'),
    'Cards': ('Project Code', 'Subproject Code', 'Card Part Number', 'Card Name', 'Revision',
              'Boards per Project', 'Current', 'Status', 'Description'),
    'BOM': ('Card Part Number', 'Revision', 'Line Number', 'Company Part Number',
            'Manufacturer Part Number', 'Manufacturer', 'Description', 'Quantity', 'Reference'),
}
LINE_FIELDS = ('cmp', 'mpn', 'manufacturer', 'description', 'quantity', 'references')


def _key(value):
    return str(value).strip().casefold()


def _error(sheet, row, message):
    raise ValueError(f'{sheet}, row {row}: {message}')


def _required(record, column):
    value = record[column]
    if not value or db.missing_part_identifier(value):
        _error(record['_sheet'], record['_row'], f'{column} is required.')
    return value


def _positive(record, column, integer=False):
    try:
        value = model.positive(record[column])
        if integer and not value.is_integer():
            raise ValueError('Use a positive whole number.')
        return int(value) if integer else value
    except ValueError as exc:
        _error(record['_sheet'], record['_row'], f'{column}: {exc}')


def _sheet_rows(book, name):
    if name not in book.sheetnames:
        raise ValueError(f'Missing worksheet: {name}. Use Project_Import_Template.xlsx.')
    sheet = book[name]
    cells = iter(sheet.iter_rows())
    header = next(cells, ())
    names = [str(cell.value or '').strip() for cell in header]
    nonblank = [name for name in names if name]
    if len(nonblank) != len(set(nonblank)):
        _error(name, 1, 'Duplicate column headers.')
    missing, unknown = set(HEADERS[name]) - set(names), set(nonblank) - set(HEADERS[name])
    if missing or unknown:
        _error(name, 1, f'Invalid headers. Missing: {", ".join(sorted(missing)) or "none"}; unknown: {", ".join(sorted(unknown)) or "none"}.')
    records = []
    for number, row in enumerate(cells, 2):
        if not any(cell.value is not None and str(cell.value).strip() for cell in row):
            continue
        record = {'_sheet': name, '_row': number}
        for column, cell in zip(names, row):
            if not column:
                if cell.value is not None:
                    _error(name, number, 'Data found under a blank header.')
                continue
            if cell.data_type in ('f', 'e'):
                _error(name, number, f'{column}: use a value, not a formula or Excel error.')
            if isinstance(cell.value, (date, datetime, bool)):
                _error(name, number, f'{column}: use text for identifiers and numbers for quantities.')
            record[column] = model.text(cell.value)
        records.append(record)
    return records


def read_workbook(path):
    path = Path(path).resolve()
    if path.suffix.lower() != '.xlsx':
        raise ValueError('Select an .xlsx Project Import workbook.')
    content = path.read_bytes()
    book = load_workbook(BytesIO(content), read_only=True, data_only=False)
    try:
        sheets = {name: _sheet_rows(book, name) for name in HEADERS}
    finally:
        book.close()
    projects, boards, revisions, memberships = {}, {}, {}, []
    for row in sheets['Projects']:
        code, name = _required(row, 'Project Code'), _required(row, 'Project Name')
        key = (_key(row['Parent Project Code']), _key(code))
        if key in projects:
            _error('Projects', row['_row'], 'Duplicate project code under the same parent.')
        projects[key] = dict(code=code, name=name, parent=row['Parent Project Code'],
                             folder=row['Project Folder'], notes=row['Description'])
    if not projects:
        raise ValueError('Projects: add at least one project.')
    for key in projects:
        if key[0] and ('', key[0]) not in projects:
            raise ValueError(f'Projects: parent {projects[key]["parent"]} must be a top-level project in this workbook.')
    scope = defaultdict(list)
    seen_links = set()
    for row in sheets['Cards']:
        top = _required(row, 'Project Code')
        sub = row['Subproject Code']
        project = (_key(top), _key(sub)) if sub else ('', _key(top))
        if project not in projects:
            _error('Cards', row['_row'], 'Project/subproject is not defined in Projects.')
        number, name = _required(row, 'Card Part Number'), _required(row, 'Card Name')
        board_key = _key(number)
        if board_key in boards and boards[board_key]['name'] != name:
            _error('Cards', row['_row'], 'One card part number cannot have different names.')
        boards[board_key] = dict(number=number, name=name)
        revision = _required(row, 'Revision')
        rev_key = (board_key, _key(revision))
        status = row['Status'] or 'Draft'
        if status not in ('Draft', 'Approved'):
            _error('Cards', row['_row'], 'Status must be Draft or Approved.')
        current = row['Current'].casefold()
        if current not in ('yes', 'no'):
            _error('Cards', row['_row'], 'Current must be Yes or No.')
        definition = dict(revision=revision, status=status, notes=row['Description'])
        if rev_key in revisions and revisions[rev_key] != definition:
            _error('Cards', row['_row'], 'Shared revisions must have the same status and description.')
        revisions[rev_key] = definition
        link = (project, rev_key)
        if link in seen_links:
            _error('Cards', row['_row'], 'Duplicate project/card/revision row.')
        seen_links.add(link)
        member = dict(project=project, revision=rev_key, quantity=_positive(row, 'Boards per Project'), current=current == 'yes')
        memberships.append(member)
        scope[(project, board_key)].append(member)
    for members in scope.values():
        if sum(m['current'] for m in members) != 1:
            raise ValueError('Cards: select exactly one Current=Yes revision for each card within each project.')
    numbered = defaultdict(dict)
    for row in sheets['BOM']:
        rev_key = (_key(_required(row, 'Card Part Number')), _key(_required(row, 'Revision')))
        if rev_key not in revisions:
            _error('BOM', row['_row'], 'Card/revision is not defined in Cards.')
        number = _positive(row, 'Line Number', integer=True)
        if number in numbered[rev_key]:
            _error('BOM', row['_row'], 'Duplicate line number in this revision.')
        if not model.identifier(row['Company Part Number']) and not model.identifier(row['Manufacturer Part Number']):
            _error('BOM', row['_row'], 'A Company Part Number or Manufacturer Part Number is required.')
        numbered[rev_key][number] = dict(zip(LINE_FIELDS, (
            row['Company Part Number'], row['Manufacturer Part Number'], row['Manufacturer'], row['Description'],
            _positive(row, 'Quantity'), row['Reference'])))
    lines = {}
    for key, revision in revisions.items():
        raw = [line for _, line in sorted(numbered[key].items())]
        lines[key] = model.prepare_lines(pd.DataFrame(raw), {f: f for f in LINE_FIELDS}, []) if raw else []
        if revision['status'] == 'Approved' and not raw:
            raise ValueError(f'Cards: {boards[key[0]]["number"]} / {revision["revision"]}: an empty BOM cannot be Approved.')
    return dict(path=str(path), file_hash=sha256(content).hexdigest(), projects=projects, boards=boards,
                revisions=revisions, memberships=memberships, lines=lines)


def _records(conn, sql, args=()):
    cursor = conn.execute(sql, args)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _state(conn):
    tables = ('PT_Projects', 'PT_Boards', 'PT_Revisions', 'PT_ProjectBoards', 'PT_Lines')
    return sha256(repr([conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in tables]).encode()).hexdigest()


def _plan(conn, data):
    model._require_bom_user(conn)
    projects = {(_key(p['parent_code'] or ''), _key(p['code'])): p for p in _records(conn,
        'SELECT p.*,parent.code AS parent_code FROM PT_Projects p LEFT JOIN PT_Projects parent ON parent.id=p.parent_id')}
    boards = {_key(b['part_number']): b for b in _records(conn, 'SELECT * FROM PT_Boards')}
    revisions = {(_key(r['part_number']), _key(r['revision'])): r for r in _records(conn,
        'SELECT r.*,b.part_number FROM PT_Revisions r JOIN PT_Boards b ON b.id=r.board_id')}
    actions = {}
    for key, incoming in data['projects'].items():
        old = projects.get(key)
        if old and (old['name'] != incoming['name'] or any(incoming[f] and incoming[f] != old[f] for f in ('notes', 'folder'))):
            raise ValueError(f'Project {incoming["code"]} already exists with different details. Edit it in Product tree first.')
    for key, incoming in data['boards'].items():
        if key in boards and boards[key]['name'] != incoming['name']:
            raise ValueError(f'Card {incoming["number"]} already exists with a different name.')
    for key, incoming in data['revisions'].items():
        old = revisions.get(key)
        expected = [tuple(line[f] for f in LINE_FIELDS) for line in data['lines'][key]]
        if old:
            actual = conn.execute('SELECT br,mpn,manufacturer,description,quantity,refs FROM PT_Lines WHERE revision_id=? ORDER BY line_number', (old['id'],)).fetchall()
            if actual == expected and old['notes'] == incoming['notes'] and old['status'] == incoming['status']:
                actions[key] = 'reuse'
                continue
            if actual or old['status'] != 'Draft' or old['notes'] and old['notes'] != incoming['notes']:
                raise ValueError(f'{old["part_number"]} / {old["revision"]}: saved BOM differs. Use Edit BOM or create a new revision; Project Import does not overwrite it.')
        actions[key] = 'fill' if old else 'create'
        if incoming['status'] == 'Approved':
            model._require_bom_admin(conn)
    for member in data['memberships']:
        project, revision = projects.get(member['project']), revisions.get(member['revision'])
        if project and revision:
            old = conn.execute('SELECT quantity,active FROM PT_ProjectBoards WHERE project_id=? AND revision_id=?', (project['id'], revision['id'])).fetchone()
            if old and old[0] != member['quantity']:
                raise ValueError('Cards: saved Boards per Project differs. Edit the quantity in Product tree first.')
            if old and member['current'] and not old[1]:
                model.require_bom_edit(revision['id'], conn)
    summary = dict(projects=sum(k not in projects for k in data['projects']),
                   cards=sum(k not in boards for k in data['boards']), revisions=sum(v == 'create' for v in actions.values()),
                   filled=sum(v == 'fill' for v in actions.values()), reused=sum(v == 'reuse' for v in actions.values()),
                   lines=sum(len(data['lines'][k]) for k, v in actions.items() if v != 'reuse'),
                   approvals=sum(data['revisions'][k]['status'] == 'Approved' for k, v in actions.items() if v != 'reuse'))
    return projects, boards, revisions, actions, summary


def preview_import(path):
    data = read_workbook(path)
    with db.connection() as conn:
        conn.execute('BEGIN')
        *_, summary = _plan(conn, data)
        state = _state(conn)
    return dict(data=data, state=state, summary=summary)


def import_workbook(preview):
    data = preview['data']
    if sha256(Path(data['path']).read_bytes()).hexdigest() != data['file_hash']:
        raise ValueError('The Excel file changed after preview. Select it again.')
    logs = []
    with db.connection(commit=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if _state(conn) != preview['state']:
            raise ValueError('Product tree changed after preview. Select the file again.')
        projects, boards, revisions, actions, summary = _plan(conn, data)
        catalog = model.component_catalog(conn)
        summary = dict(summary, matched=0, unmatched=0)
        for key in sorted(data['projects'], key=lambda k: bool(k[0])):
            if key in projects:
                continue
            p = data['projects'][key]
            parent_id = projects[('', key[0])]['id'] if key[0] else None
            identity = conn.execute('INSERT INTO PT_Projects(code,name,notes,folder,parent_id) VALUES (?,?,?,?,?)',
                (p['code'], p['name'], p['notes'], p['folder'], parent_id)).lastrowid
            projects[key] = dict(p, id=identity)
        for key, b in data['boards'].items():
            if key not in boards:
                identity = conn.execute('INSERT INTO PT_Boards(part_number,name) VALUES (?,?)', (b['number'], b['name'])).lastrowid
                boards[key] = dict(b, id=identity)
        for key, incoming in data['revisions'].items():
            if actions[key] == 'reuse':
                continue
            if key not in revisions:
                identity = conn.execute("INSERT INTO PT_Revisions(board_id,revision,status,source,imported_at,imported_by,notes) VALUES (?,?,'Draft',?,?,?,?)",
                    (boards[key[0]]['id'], incoming['revision'], data['path'], db.now_text(), db.get_current_user(), incoming['notes'])).lastrowid
                revisions[key] = dict(incoming, id=identity)
            identity = revisions[key]['id']
            conn.execute('UPDATE PT_Revisions SET notes=?,source=?,imported_at=?,imported_by=? WHERE id=?',
                (incoming['notes'], data['path'], db.now_text(), db.get_current_user(), identity))
            raw = data['lines'][key]
            lines = model.prepare_lines(pd.DataFrame(raw), {f: f for f in LINE_FIELDS}, catalog) if raw else []
            for index, line in enumerate(lines, 1):
                conn.execute('INSERT INTO PT_Lines(revision_id,line_number,component_id,br,mpn,manufacturer,description,quantity,refs) VALUES (?,?,?,?,?,?,?,?,?)',
                             (identity, index, line['component_id'], *(line[f] for f in LINE_FIELDS)))
            summary['matched'] += sum(bool(line['component_id']) for line in lines)
            summary['unmatched'] += sum(not line['component_id'] for line in lines)
            status_log = model._set_bom_status(conn, identity, incoming['status'], 'Project workbook imported')
            if status_log:
                logs.append(status_log)
            logs.append(db.write_change_log(conn, 'BOM_IMPORT', 'PT_Revisions', identity,
                details={'source': data['path'], 'new_lines': lines, 'notes': incoming['notes']}))
        for member in data['memberships']:
            project_id, revision_id = projects[member['project']]['id'], revisions[member['revision']]['id']
            if not member['current']:
                conn.execute('INSERT OR IGNORE INTO PT_ProjectBoards VALUES (?,?,?,0)', (project_id, revision_id, member['quantity']))
        for member in data['memberships']:
            if member['current']:
                model._link(conn, projects[member['project']]['id'], revisions[member['revision']]['id'], member['quantity'])
        logs.append(db.write_change_log(conn, 'PROJECT_IMPORT', 'PT_Projects', details={
            'source': data['path'], 'summary': summary, 'projects': list(data['projects'].values()),
            'cards': list(data['boards'].values()), 'memberships': data['memberships']}))
    for line in logs:
        db.audit_log.info(line)
    return dict(summary, catalog=catalog)
