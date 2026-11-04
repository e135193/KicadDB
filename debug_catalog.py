import tempfile
import os
import sqlite3
import db
import product_tree as pt

# Create temp database
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
    db.set_current_user('admin')
    
    catalog = pt.component_catalog()
    print('Catalog:')
    for part in catalog:
        print(f'  id={part["id"]}, table={part["table"]}, key={part["key"]}, cmp={part.get("cmp")}, mpn={part.get("mpn")}, manufacturer={part.get("manufacturer")}')
finally:
    db.get_connection = original_get_connection
    import shutil
    shutil.rmtree(temp_dir)