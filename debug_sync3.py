import tempfile
import os
import sqlite3
import db
import product_tree as pt

# Create temp database exactly like the test
temp_dir = tempfile.mkdtemp()
path = os.path.join(temp_dir, 'parts.sqlite')

# Mock the database connection
original_get_connection = db.get_connection
db.get_connection = lambda: sqlite3.connect(path)

try:
    with db.connection(commit=True) as conn:
        conn.execute('CREATE TABLE Users (Username TEXT UNIQUE COLLATE NOCASE, Role TEXT, Is_Active INTEGER)')
        conn.executemany('INSERT INTO Users VALUES (?,?,?)',
                         [('admin', 'admin', 1), ('engineer', 'user', 1), ('inactive', 'admin', 0)])
        for name in ('Resistor', 'Other'):
            conn.execute(f'CREATE TABLE {name} (id INTEGER PRIMARY KEY, Company_Part_Number TEXT, Manufacturer TEXT, Manufacturer_Part_Number TEXT, Company_Stock REAL)')
        conn.execute("INSERT INTO Resistor VALUES (1,'CMP-1','Yageo','MPN-1',25)")
        conn.execute('CREATE TABLE PT_Lines (id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL, line_number INTEGER NOT NULL, component_id TEXT, br TEXT NOT NULL, cmp TEXT NOT NULL DEFAULT "", mpn TEXT NOT NULL, manufacturer TEXT NOT NULL, description TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity > 0), refs TEXT NOT NULL, UNIQUE(revision_id, line_number))')
    db.set_current_user('admin')
    
    catalog = pt.component_catalog()
    identity = catalog[0]["id"]
    print(f'Identity: {identity}')
    print(f'Catalog: {catalog}')
    
    # Insert Other parts - exactly like test
    with db.connection(commit=True) as conn:
        conn.executemany('INSERT INTO Other VALUES (?,?,?,?,?)', [
            (1, 'CMP-OTHER', 'Other maker', 'MPN-1', 1),
            (2, 'CMP-UNIQUE', 'Maker', 'UNIQUE-A', 2),
            (3, '--', 'Maker', 'NO-BR', 3),
            (4, 'CMP-NONE', 'Maker', '-- mixed values --', 4),
        ])
    
    # Refresh catalog
    catalog = pt.component_catalog()
    print(f'Catalog after Other insert: {catalog}')
    
    # Create project and board - exactly like test
    meta = dict(project_code='P1', project_name='Project', board_number='PCB1', board_name='Board', revision='A', board_quantity=1)
    lines = [
        dict(cmp='', mpn=' mpn-1 ', manufacturer=' yAgEo ', description='Resistor', quantity=3, references='R1', component_id=None),
        dict(cmp='', manufacturer='', description='Resistor', quantity=3, references='R2', component_id=None),
        dict(cmp='', mpn='UNIQUE-A', manufacturer='', description='Resistor', quantity=3, references='R3', component_id=None),
        dict(cmp='', mpn='NO-BR', manufacturer='Maker', description='Resistor', quantity=3, references='R4', component_id=None),
        dict(cmp='', mpn='UNIQUEA', manufacturer='', description='Resistor', quantity=3, references='R5', component_id=None),
        dict(cmp='', mpn='-- mixed values --', manufacturer='', description='Resistor', quantity=3, references='R6', component_id=None),
        dict(cmp='', manufacturer='Wrong maker', description='Resistor', quantity=3, references='R7', component_id=None),
    ]
    
    revision = pt.save_bom(meta, lines, 'BOM.xlsx')
    print(f'Revision created: {revision}')
    
    # Check lines before sync
    before = pt.revision_lines(revision)
    print(f'Before sync:')
    for row in before:
        print(f'  {row}')
    
    # Sync - exactly like test
    db.set_current_user('engineer')
    result = pt.sync_missing_company_numbers(revision)
    print(f'Sync result: {result}')
    
    # Check lines after sync
    after = pt.revision_lines(revision)
    print(f'After sync:')
    for row in after:
        print(f'  {row}')
    
    # Check specific assertions
    print(f'after[0]: {after[0]}')
    print(f'before[0]: {before[0]}')
    expected_after_0 = dict(before[0], br='CMP-1', component_id=identity)
    print(f'expected_after_0: {expected_after_0}')
    print(f'Match: {after[0] == expected_after_0}')
    print(f'after[2]["cmp"]: {after[2].get("cmp")}')
    
finally:
    db.get_connection = original_get_connection
    import shutil
    shutil.rmtree(temp_dir)