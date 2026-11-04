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
        conn.execute('CREATE TABLE PT_Lines (id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL, line_number INTEGER NOT NULL, component_id TEXT, br TEXT NOT NULL, cmp TEXT NOT NULL DEFAULT "", mpn TEXT NOT NULL, manufacturer TEXT NOT NULL, description TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity > 0), refs TEXT NOT NULL, UNIQUE(revision_id, line_number))')
        conn.execute("INSERT INTO Resistor VALUES (1,'CMP-1','Yageo','MPN-1',25)")
    db.set_current_user('admin')
    
    catalog = pt.component_catalog()
    identity = catalog[0]['id']
    
    meta = dict(project_code='P1', project_name='Project', board_number='PCB1', board_name='Board', revision='A', board_quantity=1)
    lines = [dict(br='', mpn='MPN-1', manufacturer='Yageo', description='Resistor', quantity=3, references='R1', component_id=None)]
    
    revision = pt.save_bom(meta, lines, 'BOM.xlsx')
    print(f'Revision created: {revision}')
    
    # Check before
    before = pt.revision_lines(revision)
    print(f'Before sync: {before}')
    
    # Project sync
    project = pt.list_projects()[0]
    project_id = project['id']
    print(f'Project ID: {project_id}')
    
    result = pt.sync_missing_company_numbers(project_id=project_id)
    print(f'Sync result: {result}')
    
    # Check after
    after = pt.revision_lines(revision)
    print(f'After sync: {after}')
    
finally:
    db.get_connection = original_get_connection
    import shutil
    shutil.rmtree(temp_dir)