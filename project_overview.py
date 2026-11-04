"""Read-only project health summary for the latest linked BOM revisions."""
from decimal import Decimal
import re

import db
import product_tree as model


CRITICAL_CATEGORIES = {'ic', 'mcu', 'mosfet'}
SCOPE = '''WITH RECURSIVE scope(id) AS (
    SELECT id FROM PT_Projects WHERE id=?
    UNION SELECT p.id FROM PT_Projects p JOIN scope s ON p.parent_id=s.id
) '''


def _rows(conn, sql, params=()):
    cursor = conn.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor]


def _revision_order(revision):
    """Final letter revisions precede prototypes, regardless of import order."""
    label = model.text(revision).lstrip('-').strip().casefold()
    if re.fullmatch(r'[a-z]+', label):
        # A ... Z, AA ... AZ, BA ...; all finals rank above X-number prototypes.
        number = 0
        for letter in label:
            number = number * 26 + ord(letter) - ord('a') + 1
        return 2, number
    prototype = re.fullmatch(r'x(\d+)', label)
    if prototype:
        return 0, int(prototype[1])
    # Retain support for legacy numeric/mixed labels, with numeric-aware sorting.
    return 1, tuple((1, int(token)) if token.isdecimal() else (0, token)
                    for token in re.findall(r'\d+|\D+', label))


def build_overview(project_id):
    """Use one latest linked revision per placement and its local board quantity."""
    with db.connection() as conn:
        conn.execute('BEGIN')
        project = _rows(conn, 'SELECT * FROM PT_Projects WHERE id=?', (project_id,))
        if not project:
            raise ValueError('Project not found. Refresh the tree.')
        descendants = conn.execute(SCOPE + 'SELECT COUNT(*) FROM scope', (project_id,)).fetchone()[0] - 1
        cards = _rows(conn, SCOPE + '''SELECT pb.project_id,p.code AS project_code,b.id AS board_id,
            b.part_number,b.name AS board_name,b.responsible,r.id AS revision_id,r.revision,r.status,
            pb.quantity AS board_quantity
            FROM PT_ProjectBoards pb JOIN scope s ON s.id=pb.project_id
            JOIN PT_Projects p ON p.id=pb.project_id JOIN PT_Revisions r ON r.id=pb.revision_id
            JOIN PT_Boards b ON b.id=r.board_id ORDER BY p.code,b.part_number''', (project_id,))
        latest = {}
        for card in cards:
            key = card['project_id'], card['board_id']
            previous = latest.get(key)
            rank = _revision_order(card['revision']), card['revision_id']
            if previous is None or rank > (_revision_order(previous['revision']), previous['revision_id']):
                latest[key] = card
        cards = list(latest.values())
        revision_ids = list(dict.fromkeys(card['revision_id'] for card in cards))
        saved_lines = {revision_id: [] for revision_id in revision_ids}
        # Read only the chosen BOMs, once per shared revision; bound parameters stay small.
        for offset in range(0, len(revision_ids), 500):
            batch = revision_ids[offset:offset + 500]
            for line in _rows(conn, 'SELECT * FROM PT_Lines WHERE revision_id IN (' +
                              ','.join('?' for _ in batch) + ') ORDER BY revision_id,line_number', batch):
                saved_lines[line['revision_id']].append(line)
        lines = []
        for card in cards:
            card_lines = saved_lines[card['revision_id']]
            card['line_count'] = len(card_lines)
            lines.extend(dict(line, project_id=card['project_id'], project_code=card['project_code'],
                              part_number=card['part_number'], revision=card['revision'],
                              board_quantity=card['board_quantity']) for line in card_lines)
        catalog = model.component_catalog(conn, register=False)
    groups, unmatched_lines = {}, set()
    for line, part in zip(lines, model.matched_components(lines, catalog)):
        matched = bool(part and line['component_id'] == part['id'] and part['id'])
        if not matched:
            unmatched_lines.add(line['id'])
        if part:
            key = ('catalog', part['table'], part['key'])
        elif line['component_id']:
            key = ('deleted', line['component_id'])
        else:
            identifiers = tuple(model.normalized(line[f]) for f in ('br', 'mpn', 'manufacturer'))
            key = ('unmatched', *identifiers) if any(identifiers[:2]) else ('line', line['id'])
        source = part or {}
        group = groups.setdefault(key, dict(
            cmp=source.get('cmp') or line.get('cmp'), mpn=source.get('mpn') or line.get('mpn'),
            description=source.get('description') or line['description'],
            component_id=source.get('id'), category=source.get('table', 'Unknown'),
            product_status=source.get('product_status', ''),
            alternate_part_number=source.get('alternate_part_number', ''),
            stock=model.stock_number(source.get('stock')), quantity=Decimal(0),
            critical=bool(part and model.normalized(part['table']) in CRITICAL_CATEGORIES),
            unmatched=False, cards=set(), uses=set()))
        group['quantity'] += Decimal(str(line['quantity'])) * Decimal(str(line['board_quantity']))
        group['unmatched'] |= not matched
        group['cards'].add((line['project_id'], line['revision_id']))
        group['uses'].add(f"{line['project_code']} / {line['part_number']} / Rev. {line['revision']}")
    materials = []
    for group in groups.values():
        status = model.normalized(group['product_status'])
        group['obsolete'] = status == 'obsolete'
        group['lifecycle_risk'] = model.is_obsolete_status(status)
        group['missing_alternative'] = group['lifecycle_risk'] and not model.identifier(group['alternate_part_number'])
        group['shortage'] = (float(max(Decimal(0), group['quantity'] - Decimal(str(group['stock']))))
                            if group['stock'] is not None else None)
        group['quantity'] = float(group['quantity'])
        group['card_count'] = len(group['cards'])
        group['uses'] = '; '.join(sorted(group['uses']))
        del group['cards']
        materials.append(group)
    materials.sort(key=lambda p: (not p['lifecycle_risk'], not p['critical'], model.normalized(p.get('cmp')), model.normalized(p['mpn'])))
    revisions = {card['revision_id']: card for card in cards}
    summary = dict(subprojects=descendants, cards=len({c['board_id'] for c in cards}),
        card_links=len(cards), latest_boms=len(revisions),
        approved_boms=sum(c['status'] == 'Approved' for c in revisions.values()),
        draft_boms=sum(c['status'] != 'Approved' for c in revisions.values()),
        empty_boms=sum(c['line_count'] == 0 for c in revisions.values()),
        materials=len(materials), obsolete=sum(p['obsolete'] for p in materials),
        lifecycle_risk=sum(p['lifecycle_risk'] for p in materials),
        critical=sum(p['critical'] for p in materials),
        shortages=sum(p['shortage'] is not None and p['shortage'] > 0 for p in materials),
        unknown_stock=sum(p['stock'] is None for p in materials),
        unknown_status=sum(not model.normalized(p['product_status']) for p in materials),
        unmatched_parts=sum(p['unmatched'] for p in materials), unmatched_lines=len(unmatched_lines),
        missing_alternatives=sum(p['missing_alternative'] for p in materials))
    suggestions = []
    for count, message in (
        (summary['missing_alternatives'], 'lifecycle-risk parts have no alternate part number; review replacement options.'),
        (summary['shortages'], 'parts have insufficient stock for one project; review purchasing needs.'),
        (summary['unknown_stock'], 'parts have unknown stock; refresh or complete Company stock data.'),
        (summary['unmatched_lines'], 'saved BOM rows need confirmed component matches; use Edit BOM / Update match.'),
        (summary['empty_boms'], 'latest BOMs are empty; open their revisions from Cards / BOMs and use Add BOM.'),
        (summary['draft_boms'], 'latest BOMs are Draft; review them before requesting approval.'),
        (summary['unknown_status'], 'parts have no lifecycle status; complete their database details.')):
        if count:
            suggestions.append(f'{count} {message}')
    if not cards:
        suggestions = ['No linked cards in this project. Add a card or link a saved revision.']
    elif not suggestions:
        suggestions = ['No lifecycle, stock or matching issues found in the available database information.']
    return dict(project=project[0], summary=summary, materials=materials, cards=cards, suggestions=suggestions)
