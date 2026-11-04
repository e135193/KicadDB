"""Read-only card and consolidated project BOM exports."""
from collections import defaultdict
from decimal import Decimal
import os
import re
import tempfile

import db
import product_tree as model


BOM_COLUMNS = ('Company Part Number', 'Manufacturer Part Number', 'Manufacturer',
               'Description', 'Quantity', 'Company Stock', 'Reference')


def _add_stocks(conn, lines):
    """Resolve live stock without registering components or changing saved matches."""
    if not lines:
        return
    catalog = model.component_catalog(conn, register=False)
    for line, part in zip(lines, model.matched_components(lines, catalog)):
        line['company_stock'] = model.stock_number(part['stock']) if part else None


def _scope(conn, project_id, board_id=None, revision_id=None, history=False):
    if board_id is not None and revision_id is not None:
        raise ValueError('Select a card or a revision, not both.')
    consolidated = board_id is None and revision_id is None
    project = conn.execute('''SELECT p.code,p.name,parent.code FROM PT_Projects p
        LEFT JOIN PT_Projects parent ON parent.id=p.parent_id WHERE p.id=?''', (project_id,)).fetchone()
    if not project:
        raise ValueError('Project not found. Refresh the list.')
    scope = '''WITH RECURSIVE scope(id) AS (
        SELECT id FROM PT_Projects WHERE id=?
        UNION SELECT p.id FROM PT_Projects p JOIN scope s ON p.parent_id=s.id
    ) ''' if consolidated else ''
    condition = 'pb.project_id IN (SELECT id FROM scope)' if consolidated else 'pb.project_id=?'
    params = [project_id]
    if board_id is not None:
        condition += ' AND r.board_id=?'
        params.append(board_id)
    if revision_id is not None:
        condition += ' AND r.id=?'
        params.append(revision_id)
    elif not history:
        condition += ' AND pb.active=1'
    return project, consolidated, scope, condition, params


def _cards(conn, scope, condition, params):
    cursor = conn.execute(scope + '''SELECT pb.project_id,b.id AS board_id,pb.active,
        p.code AS project_code,p.name AS project_name,parent.code AS parent_code,
        b.part_number,b.name AS board_name,r.id AS revision_id,r.revision,r.status,r.notes,
        pb.quantity AS board_quantity,COUNT(l.id) AS line_count
        FROM PT_ProjectBoards pb JOIN PT_Projects p ON p.id=pb.project_id
        JOIN PT_Revisions r ON r.id=pb.revision_id JOIN PT_Boards b ON b.id=r.board_id
        LEFT JOIN PT_Lines l ON l.revision_id=r.id LEFT JOIN PT_Projects parent ON parent.id=p.parent_id WHERE ''' + condition + '''
        GROUP BY pb.project_id,r.id ORDER BY COALESCE(parent.code,p.code),p.code,b.part_number,r.revision''', params)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def revision_choices(project_id, *, board_id=None, revision_id=None):
    """List linked revisions in the export scope without changing current revisions."""
    if board_id is not None and revision_id is not None:
        raise ValueError('Select a card or a revision, not both.')
    with db.connection() as conn:
        conn.execute('BEGIN')
        if revision_id is not None:
            row = conn.execute('''SELECT r.board_id FROM PT_ProjectBoards pb
                JOIN PT_Revisions r ON r.id=pb.revision_id WHERE pb.project_id=? AND r.id=?''',
                (project_id, revision_id)).fetchone()
            if not row:
                raise ValueError('This revision is no longer linked to the project. Refresh the list.')
            board_id = row[0]
        project, consolidated, scope, condition, params = _scope(conn, project_id, board_id, history=True)
        cards = _cards(conn, scope, condition, params)
    for card in cards:
        card['selected'] = card['revision_id'] == revision_id if revision_id is not None else bool(card['active'])
    project_path = f'{project[2]} / {project[0]}' if project[2] else project[0]
    return {'title': f'{project_path} — {project[1]}', 'cards': cards,
            'board_id': board_id, 'consolidated': consolidated}


def build_export(project_id, *, board_id=None, revision_id=None, selections=None):
    """Export explicit (project, revision) links, or current revisions by default."""
    with db.connection() as conn:
        conn.execute('BEGIN')
        project, consolidated, scope, condition, params = _scope(
            conn, project_id, board_id, revision_id, history=selections is not None)
        cards = _cards(conn, scope, condition, params)
        selected = None if selections is None else set(tuple(link) for link in selections)
        if selected is not None:
            if not selected:
                raise ValueError('Select at least one revision to export.')
            available = {(c['project_id'], c['revision_id']) for c in cards}
            if not selected <= available:
                raise ValueError('A selected revision is no longer linked within this export scope. Reopen Export BOM.')
            cards = [c for c in cards if (c['project_id'], c['revision_id']) in selected]
        if not consolidated and not cards:
            raise ValueError('No current BOM is linked to this card. Select a specific revision or refresh the list.')
        cursor = conn.execute(scope + '''SELECT pb.project_id,p.code AS project_code,parent.code AS parent_code,b.part_number,r.revision,
            pb.quantity AS board_quantity,l.* FROM PT_ProjectBoards pb
            JOIN PT_Projects p ON p.id=pb.project_id JOIN PT_Revisions r ON r.id=pb.revision_id
            JOIN PT_Boards b ON b.id=r.board_id JOIN PT_Lines l ON l.revision_id=r.id
            LEFT JOIN PT_Projects parent ON parent.id=p.parent_id
            WHERE ''' + condition + ' ORDER BY p.code,b.part_number,r.revision,l.line_number', params)
        names = [c[0] for c in cursor.description]
        lines = [dict(zip(names, row)) for row in cursor.fetchall()]
        if selected is not None:
            lines = [line for line in lines if (line['project_id'], line['revision_id']) in selected]
        _add_stocks(conn, lines)
    if consolidated:
        records = consolidate(lines)
        project_path = f'{project[2]} / {project[0]}' if project[2] else project[0]
        title = f'{project_path} — {project[1]} | Combined project BOM'
        filename = f'{project_path}_Combined_BOM.xlsx'
    elif len(cards) > 1:
        # A card export includes one board of each checked revision, never project multipliers.
        records = consolidate([dict(line, board_quantity=1) for line in lines])
        card = cards[0]
        title = f"{card['part_number']} — {card['board_name']} | Selected revisions BOM (one board per revision)"
        filename = f"{card['part_number']}_Selected_Revisions_BOM.xlsx"
    else:
        records = [_bom_record(line, line['quantity'], line['refs']) for line in lines]
        card = cards[0]
        title = f"{card['part_number']} — {card['board_name']} / Rev. {card['revision']} | BOM per board"
        filename = f"{card['part_number']}_{card['revision']}_BOM.xlsx"
    return {'title': title, 'filename': re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename),
            'consolidated': consolidated, 'records': records, 'cards': cards,
            'empty_cards': sum(card['line_count'] == 0 for card in cards)}


def _bom_record(line, quantity, references):
    return dict(zip(BOM_COLUMNS, (line.get('cmp') or line.get('br'), line['mpn'], line['manufacturer'],
                                 line['description'], quantity, line.get('company_stock'), references)))


def consolidate(lines):
    """Prefer stable component matches, then unambiguous BR or manufacturer/MPN."""
    br_ids = defaultdict(set)
    for line in lines:
        if line['component_id'] and model.normalized(line.get('cmp') or line.get('br')):
            br_ids[model.normalized(line.get('cmp') or line.get('br'))].add(line['component_id'])
    keys = []
    mpn_keys = defaultdict(set)
    for line in lines:
        br = model.normalized(line.get('cmp') or line.get('br'))
        if line['component_id']:
            key = ('component', line['component_id'])
        elif br:
            matches = br_ids[br]
            key = (('component', next(iter(matches))) if len(matches) == 1 else
                   ('line', line['id']) if matches else ('cmp', br))
        else:
            key = None
        keys.append(key)
        mpn = (model.normalized(line['manufacturer']), model.normalized(line['mpn']))
        if key and all(mpn):
            mpn_keys[mpn].add(key)
    groups = {}
    for line, key in zip(lines, keys):
        if key is None:
            mpn = (model.normalized(line['manufacturer']), model.normalized(line['mpn']))
            matches = mpn_keys[mpn] if all(mpn) else set()
            key = (next(iter(matches)) if len(matches) == 1 else ('mpn', *mpn) if all(mpn) and not matches
                   else ('line', line['id']))
        group = groups.setdefault(key, {'values': {field: [] for field in ('cmp', 'mpn', 'manufacturer', 'description')},
                                       'quantity': Decimal(0), 'stocks': set(), 'references': []})
        for field, values in group['values'].items():
            value = model.text(line.get(field))
            if value and value not in values:
                values.append(value)
        group['quantity'] += Decimal(str(line['quantity'])) * Decimal(str(line['board_quantity']))
        if line.get('company_stock') is not None:
            group['stocks'].add(line['company_stock'])
        project_path = f"{line['parent_code']} / {line['project_code']}" if line['parent_code'] else line['project_code']
        reference = f"{project_path} / {line['part_number']} / Rev. {line['revision']}"
        if line['refs']:
            reference += ': ' + line['refs']
        if reference not in group['references']:
            group['references'].append(reference)
    records = []
    for group in groups.values():
        values = {key: '\n'.join(items) for key, items in group['values'].items()}
        # Inventory is shared across cards and revisions; never sum or multiply it.
        values['company_stock'] = next(iter(group['stocks'])) if len(group['stocks']) == 1 else None
        total = group['quantity']
        records.append(_bom_record(values, int(total) if total == total.to_integral_value() else float(total),
                                   '\n'.join(group['references'])))
    return sorted(records, key=lambda r: (r['Company Part Number'].casefold(), r['Manufacturer'].casefold(),
                                         r['Manufacturer Part Number'].casefold()))


def write_export(path, report):
    """Write literal part numbers, numeric quantities and a traceable card list."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    book.properties.title = report['title']
    book.properties.description = ('Project quantities = quantity per board × boards per project for each included revision.'
                                   if report['consolidated'] else 'Quantities are per one board of each included revision.')
    book.properties.description += (' Company Stock is the current database stock at export time, not multiplied by board quantities. '
                                    'Blank stock means unavailable or an ambiguous component match.')
    def fill(sheet, headers, records, widths):
        sheet.append(list(headers))
        for record in records:
            sheet.append([record[column] for column in headers])
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
                cell.alignment = Alignment(vertical='top', wrap_text=True)
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='24476A')
        for index, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = 'landscape'
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.print_title_rows = '1:1'
    bom = book.active
    bom.title = 'BOM'
    fill(bom, BOM_COLUMNS, report['records'], (25, 32, 24, 48, 16, 18, 80))
    for row in bom.iter_rows(min_row=2, min_col=5, max_col=6):
        for cell in row:
            cell.number_format = '0.######'
    cards = [{'Project': f"{c['parent_code']} / {c['project_code']}" if c['parent_code'] else c['project_code'],
              'Project name': c['project_name'], 'Card part number': c['part_number'],
              'Card name': c['board_name'], 'Revision': c['revision'], 'Status': c['status'],
              'Boards / project': c['board_quantity'], 'BOM rows': c['line_count'],
              'BOM availability': 'Included' if c['line_count'] else 'Empty BOM', 'Description': c['notes']}
             for c in report['cards']]
    if cards:
        fill(book.create_sheet('Cards'), tuple(cards[0]), cards, (20, 32, 24, 36, 16, 16, 18, 14, 20, 48))
    # A failed write must not damage a file the user is replacing.
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temporary = tempfile.mkstemp(prefix='.bom-export-', suffix='.xlsx', dir=directory)
    os.close(descriptor)
    try:
        book.save(temporary)
        os.replace(temporary, path)
    finally:
        book.close()
        if os.path.exists(temporary):
            os.remove(temporary)
