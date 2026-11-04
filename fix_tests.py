#!/usr/bin/env python3
"""
Systematic test fix script for Brensan → Company rebranding.
Applies the following transformations:
1. Import updates: Brensan_Kicad_DB → Company_Kicad_DB
2. Function renames: sync_missing_brensan_numbers → sync_missing_company_numbers
3. Field renames: br→cmp, company_stock→company_stock
5. Column header renames: Brensan Part Number→Company Part Number, Brensan Stock→Company Stock
6. String literal updates: BR-→CMP-, Brensan→Company
"""

import re
import os
from pathlib import Path

# Define all replacements
REPLACEMENTS = [
    # === IMPORTS ===
    (r'from Brensan_Kicad_DB import', 'from Company_Kicad_DB import'),
    (r'import Brensan_Kicad_DB', 'import Company_Kicad_DB'),
    (r'from Brensan_Kicad_DB as', 'from Company_Kicad_DB as'),
    
    # === FUNCTION RENAMES ===
    (r'sync_missing_brensan_numbers', 'sync_missing_company_numbers'),
    (r'_sync_revision_brensan_numbers', '_sync_revision_company_numbers'),
    
    # === FIELD/COLUMN RENAMES (dict keys) ===
    (r"p\['br'\]", "p['cmp']"),
    (r'p\["br"\]', 'p["cmp"]'),
    (r"part\['br'\]", "part['cmp']"),
    (r'part\["br"\]', 'part["cmp"]'),
    (r"line\['br'\]", "line['cmp']"),
    (r'line\["br"\]', 'line["cmp"]'),
    (r"row\['br'\]", "row['cmp']"),
    (r'row\["br"\]', 'row["cmp"]'),
    (r"values\['br'\]", "values['cmp']"),
    (r'values\["br"\]', 'values["cmp"]'),
    
    # company_stock → company_stock
    (r"p\['company_stock'\]", "p['company_stock']"),
    (r'p\["company_stock"\]', 'p["company_stock"]'),
    (r"part\['company_stock'\]", "part['company_stock']"),
    (r'part\["company_stock"\]', 'part["company_stock"]'),
    (r"line\['company_stock'\]", "line['company_stock']"),
    (r'line\["company_stock"\]', 'line["company_stock"]'),
    (r"row\['company_stock'\]", "row['company_stock']"),
    (r'row\["company_stock"\]', 'row["company_stock"]'),
    (r"values\['company_stock'\]", "values['company_stock']"),
    (r'values\["company_stock"\]', 'values["company_stock"]'),
    (r"record\['company_stock'\]", "record['company_stock']"),
    (r'record\["company_stock"\]', 'record["company_stock"]'),
    
    # company_stock variable references
    (r'\.get\("company_stock"\)', '.get("company_stock")'),
    (r".get('company_stock')", ".get('company_stock')"),
    (r'row\["company_stock"\]', 'row["company_stock"]'),
    (r'part\["company_stock"\]', 'part["company_stock"]'),
    
    # === STRING LITERAL REPLACEMENTS ===
    # Column headers
    (r'"Brensan Part Number"', '"Company Part Number"'),
    (r"'Brensan Part Number'", "'Company Part Number'"),
    (r'"Brensan Stock"', '"Company Stock"'),
    (r"'Brensan Stock'", "'Company Stock'"),
    (r'"Brensan_Part_Number"', '"Company_Part_Number"'),
    (r"'Brensan_Part_Number'", "'Company_Part_Number'"),
    (r'"Brensan_Stock"', '"Company_Stock"'),
    (r"'Brensan_Stock'", "'Company_Stock'"),
    
    # BR- → CMP- prefix
    (r'"BR-', '"CMP-'),
    (r"'BR-", "'CMP-"),
    (r"BR-", "CMP-"),  # Careful - only for prefixes
    
    # Brensan → Company in strings
    (r'"Brensan', '"Company'),
    (r"'Brensan", "'Company"),
    
    # Specific test expectations
    (r"'BR-", "'CMP-"),
    (r'"BR-', '"CMP-'),
    (r'BR-001', 'CMP-001'),
    (r'BR-1', 'CMP-1'),
    (r'BR-2', 'CMP-2'),
    (r'BR-NEW', 'CMP-NEW'),
    
    # Labels
    (r'"BR part number"', '"CMP part number"'),
    (r"'BR part number'", "'CMP part number'"),
    (r'"BR part numbers"', '"CMP part numbers"'),
    
    # Constants
    (r'COLUMN = "Brensan Stock QTY"', 'COLUMN = "Company Stock QTY"'),
    (r'NO_CODE = \'No BR code\'', 'NO_CODE = \'No CMP code\''),
]

# Files to process
TEST_FILES = [
    'tests/test_product_tree.py',
    'tests/test_product_tree_export.py',
    'tests/test_project_overview.py',
    'tests/test_project_tree_import.py',
    'tests/test_config_paths.py',
    'tests/test_footprint_suggestions.py',
    'tests/test_bom_stock.py',
    'tests/test_audit.py',
    'tests/test_log_history.py',
    'tests/test_verified_permissions.py',
    'tests/test_lcsc_manager.py',
    'tests/test_step_models.py',
    'tests/test_project_tree_import.py',
    'tests/test_bom_stock.py',
    'tests/test_bom_circuit_breaker.py',
    'tests/test_bom_digikey_export.py',
    'tests/test_formatting.py',
    'tests/test_lcsc_manager.py',
    'tests/test_product_tree_import.py',
    'tests/test_db_kicad.py',
]

def process_file(filepath):
    """Apply all replacements to a file."""
    if not os.path.exists(filepath):
        print(f"  SKIP: {filepath} (not found)")
        return 0
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    changes = 0
    
    for pattern, replacement in REPLACEMENTS:
        new_content, count = re.subn(pattern, replacement, content)
        if count > 0:
            content = new_content
            changes += count
            if count > 1:
                print(f"  {pattern} -> {replacement}: {count} occurrences")
    
    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  OK {filepath}: {changes} total replacements")
    else:
        print(f"  - {filepath}: no changes")
    
    return changes

def main():
    base = Path(r"C:\Users\Engin\Desktop\kicad DB")
    total_changes = 0
    
    for test_file in TEST_FILES:
        filepath = base / test_file
        print(f"\nProcessing: {test_file}")
        changes = process_file(filepath)
        total_changes += changes
    
    print(f"\n{'='*50}")
    print(f"Total changes across all files: {total_changes}")

if __name__ == '__main__':
    main()