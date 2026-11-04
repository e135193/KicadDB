"""Read Company inventory from local SQLite database; never treat missing/invalid responses as zero."""
from decimal import Decimal, InvalidOperation
import sqlite3
import db
from config import DB_FILE

BR_ALIASES = db.CMP_PART_NUMBER_ALIASES


class AmbiguousStockResult(ValueError):
    pass


def stock_quantity(value, code):
    try:
        quantity = Decimal(value)
        if not quantity.is_finite():
            raise InvalidOperation
        return int(quantity) if quantity == quantity.to_integral_value() else float(quantity)
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError(f"{code}: could not read stock quantity.") from None


def get_stock_from_db(code):
    """Fetch stock quantity for a given CMP code from the local database."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        # Search across all component tables for the matching CMP code
        tables = db.get_tables()
        for table in tables:
            if db.is_system_table(table):
                continue
            columns = db.get_all_columns(table)
            br_column = db.get_column_name_by_alias(columns, BR_ALIASES)
            stock_column = db.get_column_name_by_alias(columns, ["Company_Stock"])
            if not br_column or not stock_column:
                continue
            query = f"SELECT {stock_column} FROM {db.quote_identifier(table)} WHERE trim({db.quote_identifier(br_column)}) = ?"
            row = conn.execute(query, (code,)).fetchone()
            if row and row[0] is not None:
                return stock_quantity(row[0], code)
    return None


def find_missing_code_from_db(mpn, manufacturer=''):
    """Find a CMP code and stock quantity by exact MPN (and manufacturer) match from the local database."""
    if db.missing_part_identifier(mpn):
        raise ValueError('A manufacturer part number is required.')
    normalize = lambda value: ' '.join(str(value).split()).casefold()
    matches = []
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        tables = db.get_tables()
        for table in tables:
            if db.is_system_table(table):
                continue
            columns = db.get_all_columns(table)
            br_column = db.get_column_name_by_alias(columns, BR_ALIASES)
            mpn_column = db.get_column_name_by_alias(columns, db.PART_NUMBER_ALIASES)
            manufacturer_column = db.get_column_name_by_alias(columns, db.MANUFACTURER_ALIASES)
            stock_column = db.get_column_name_by_alias(columns, ["Company_Stock"])
            if not br_column or not mpn_column or not stock_column:
                continue
            # Build query with exact MPN match (and manufacturer if provided)
            params = [mpn]
            where_clauses = [f"trim({db.quote_identifier(mpn_column)}) = ?"]
            if manufacturer and not db.missing_part_identifier(manufacturer):
                where_clauses.append(f"trim({db.quote_identifier(manufacturer_column)}) = ?")
                params.append(manufacturer)
            query = f"SELECT {br_column}, {stock_column} FROM {db.quote_identifier(table)} WHERE {' AND '.join(where_clauses)}"
            rows = conn.execute(query, params).fetchall()
            for row in rows:
                code = row[br_column]
                quantity = row[stock_column]
                if not db.missing_part_identifier(code) and code.upper().startswith('CMP-'):
                    matches.append({'code': code, 'quantity': stock_quantity(quantity, code) if quantity is not None else None})
    if not matches:
        return None
    if len(matches) != 1:
        raise AmbiguousStockResult(f'{mpn}: multiple exact inventory matches; CMP code retained.')
    return matches[0]


def fetch_selected_stock(code):
    """Fetch stock quantity for a given CMP code from the local database."""
    return get_stock_from_db(code)


def fetch_missing_part(mpn, manufacturer=''):
    """Find a CMP code and stock quantity by exact MPN (and manufacturer) match from the local database."""
    return find_missing_code_from_db(mpn, manufacturer)


def update_all_stock(report, is_cancelled):
    groups, missing_groups = {}, {}
    result = {"updated": 0, "missing": 0, "failed": 0, "cancelled": False, "errors": [],
              "codes_filled": 0, "code_missing": 0, "code_ambiguous": 0, "code_skipped": 0,
              "code_protected": 0, "code_stale": 0, "unreachable": False}
    for table in db.get_tables():
        if is_cancelled():
            result['cancelled'] = True
            return result
        columns, rows = db.load_table_data(table)
        br_column = db.get_column_name_by_alias(columns, BR_ALIASES)
        if not br_column:
            continue
        mpn_column = db.get_column_name_by_alias(columns, db.PART_NUMBER_ALIASES)
        manufacturer_column = db.get_column_name_by_alias(columns, db.MANUFACTURER_ALIASES)
        key_columns = db.get_primary_key_columns(table)
        for row in rows:
            record = dict(zip(columns, row))
            code = str(record.get(br_column) or "").strip()
            if not db.missing_part_identifier(code):
                groups.setdefault(code, set()).add((table, br_column))
                continue
            mpn = str(record.get(mpn_column) or '').strip()
            manufacturer = str(record.get(manufacturer_column) or '').strip()
            if db.missing_part_identifier(mpn) or len(key_columns) != 1 or key_columns[0] == br_column:
                result['code_skipped'] += 1
                continue
            key = (mpn.casefold(), ' '.join(manufacturer.split()).casefold())
            missing_groups.setdefault(key, []).append((table, record, mpn, manufacturer))
    tasks = [('stock', code, targets) for code, targets in groups.items()] + [
        ('code', targets[0][2], targets) for targets in missing_groups.values()]
    total = len(tasks)
    report(f"Local stock: 0/{total} queries completed", 0)
    for index, (kind, identifier, targets) in enumerate(tasks, 1):
        if is_cancelled():
            result["cancelled"] = True
            break
        action = 'Looking up local stock' if kind == 'stock' else 'Finding missing CMP code'
        report(f"{action}: {index}/{total} — {identifier}", 100 * (index - 1) / total)
        try:
            found = (fetch_selected_stock(identifier) if kind == 'stock' else
                     fetch_missing_part(identifier, targets[0][3]))
            if is_cancelled():
                result["cancelled"] = True
                break
            if found is None:
                result['missing' if kind == 'stock' else 'code_missing'] += 1 if kind == 'stock' else len(targets)
                continue
            if kind == 'stock':
                for table, br_column in targets:
                    result["updated"] += db.update_company_stock(table, br_column, identifier, found)
            else:
                for table, record, _, _ in targets:
                    if is_cancelled():
                        result['cancelled'] = True
                        break
                    try:
                        affected = db.fill_missing_brensan_part_number(table, record, found['code'], found['quantity'])
                    except PermissionError:
                        result['code_protected'] += 1
                        continue
                    result['codes_filled'] += affected
                    result['updated'] += affected
                    result['code_stale'] += not affected
                if result['cancelled']:
                    break
        except AmbiguousStockResult as exc:
            result['code_ambiguous'] += len(targets)
            if len(result['errors']) < 5:
                result['errors'].append(str(exc))
        except (ValueError, UnicodeError) as exc:
            result["failed"] += 1
            if len(result["errors"]) < 5:
                result["errors"].append(f"{identifier}: {exc}")
        finally:
            report(f"Local stock: {index}/{total} queries completed — {identifier}", 100 * index / total)
    return result