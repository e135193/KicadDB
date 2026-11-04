import gc
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import db
import product_tree as pt


class TestProductTree(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "parts.sqlite")
        self.mock = patch.object(db, "get_connection", lambda: sqlite3.connect(self.path))
        self.mock.start()
        with db.connection(commit=True) as conn:
            conn.execute('CREATE TABLE Users (Username TEXT UNIQUE COLLATE NOCASE, Role TEXT, Is_Active INTEGER)')
            conn.executemany('INSERT INTO Users VALUES (?,?,?)',
                             [('admin', 'admin', 1), ('engineer', 'user', 1), ('inactive', 'admin', 0)])
            for name in ("Resistor", "Other"):
                conn.execute(f'CREATE TABLE {name} (id INTEGER PRIMARY KEY, Company_Part_Number TEXT, Manufacturer TEXT, Manufacturer_Part_Number TEXT, Company_Stock REAL)')
            conn.execute('CREATE TABLE PT_Lines (id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL, line_number INTEGER NOT NULL, component_id TEXT, br TEXT NOT NULL, cmp TEXT NOT NULL DEFAULT "", mpn TEXT NOT NULL, manufacturer TEXT NOT NULL, description TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity > 0), refs TEXT NOT NULL, UNIQUE(revision_id, line_number))')
            conn.execute("INSERT INTO Resistor VALUES (1,'CMP-1','Yageo','MPN-1',25)")
            # Populate cmp column from br for backward compatibility
            conn.execute("UPDATE PT_Lines SET cmp = br")
        db.set_current_user('admin')
        self.catalog = pt.component_catalog()
        self.identity = self.catalog[0]["id"]
        self.meta = dict(project_code="P1", project_name="Project", board_number="PCB1", board_name="Card",
                         revision="A", board_quantity=2, status='Draft', notes="Note")
        self.lines = [dict(br="CMP-1", mpn="MPN-1", manufacturer="Yageo", description="Resistor",
                           quantity=3, references="R1,R2,R3", component_id=self.identity)]

    def tearDown(self):
        # Collect destroyed Tk roots on the UI thread before the next test starts workers.
        gc.collect()
        db.set_current_user('')
        self.mock.stop()
        self.temp.cleanup()

    def test_import_skips_explicit_dnp_but_rejects_unidentified_fitted_rows(self):
        frame = pd.DataFrame([
            {'Reference': 'C1', 'Value': ' DNP ', 'BR': None, 'MPN': None, 'Qty': None},
            {'Reference': 'R1', 'Value': '10k', 'BR': 'CMP-1', 'MPN': 'MPN-1', 'Qty': 1},
        ])
        mapping = dict(cmp='BR', mpn='MPN', quantity='Qty', references='Reference',
                       manufacturer=None, description=None)
        lines = pt.prepare_lines(frame, mapping, self.catalog)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['references'], 'R1')
        self.assertEqual(lines[0]['component_id'], self.identity)
        frame.loc[0, 'Value'] = '100nF'
        with self.assertRaisesRegex(ValueError, 'CMP or MPN'):
            pt.prepare_lines(frame, mapping, self.catalog)

    def test_obsolete_products_reads_live_status_and_all_project_links(self):
        with db.connection(commit=True) as conn:
            conn.execute('ALTER TABLE Resistor ADD COLUMN "Product Status" TEXT')
            conn.execute('UPDATE Resistor SET "Product Status"=?', (' ObSoLeTe ',))
            conn.execute('INSERT INTO Resistor VALUES (2,?,?,?,?,?)', ('CMP-2', 'Maker', 'Unused', 0, 'Obsolete'))
            conn.execute('INSERT INTO Resistor VALUES (3,?,?,?,?,?)', ('CMP-3', 'Maker', 'Active', 0, 'Active'))
            conn.execute('INSERT INTO Resistor VALUES (4,?,?,?,?,?)', ('CMP-4', 'Maker', 'NFND', 0, 'Not For New Designs'))
            conn.execute('INSERT INTO Resistor VALUES (5,?,?,?,?,?)', ('CMP-5', 'Maker', 'LTB', 0, ' Last Time Buy '))
            conn.execute('ALTER TABLE Resistor ADD COLUMN Alternate_Part_Number TEXT')
            conn.execute('UPDATE Resistor SET Alternate_Part_Number=? WHERE id=1', ('ALT-1\nALT-2',))
        old = pt.save_bom(self.meta, self.lines, 'old.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2'), old)
        pt.save_bom(dict(self.meta, revision='B'), self.lines, 'new.xlsx')
        before = pt.rows('SELECT * FROM PT_Components')
        report = pt.obsolete_products()
        self.assertEqual(len(report), 4)
        product, = [p for p in report if p['cmp'] == 'CMP-1']
        used = product['usages']
        self.assertEqual(len(used), 3)
        self.assertEqual(product['alternate_part_number'], 'ALT-1\nALT-2')
        self.assertTrue(all(p['alternate_part_number'] == '' for p in report if not p['usages']))
        self.assertEqual({p['usage']['project'] for p in used}, {'P1', 'P2'})
        self.assertEqual(sum(not p['usage']['active'] for p in used), 1)
        self.assertEqual([p['cmp'] for p in report if not p['usages']], ['CMP-2', 'CMP-4', 'CMP-5'])
        self.assertEqual([p['usage']['quantity'] * p['usage']['board_quantity'] for p in used], [6, 6, 6])
        self.assertEqual(pt.rows('SELECT * FROM PT_Components'), before)
        with db.connection(commit=True) as conn:
            conn.execute('UPDATE Resistor SET "Product Status"=NULL')
        self.assertEqual(pt.obsolete_products(), [])

    def test_obsolete_report_before_product_tree_has_been_initialized(self):
        with db.connection(commit=True) as conn:
            conn.execute('ALTER TABLE Resistor ADD COLUMN "Product Status" TEXT')
            conn.execute('UPDATE Resistor SET "Product Status"=?', ('Last Time Buy',))
            for table in reversed(pt.TABLES):
                conn.execute('DROP TABLE '+db.quote_identifier(table))
        result = pt.obsolete_products()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['cmp'], 'CMP-1')
        self.assertEqual(result[0]['alternate_part_number'], '')
        self.assertEqual(result[0]['usages'], [])
        self.assertEqual(pt.rows("SELECT name FROM sqlite_master WHERE name LIKE 'PT_%'"), [])

    def test_obsolete_groups_shared_br_without_losing_components_or_merging_missing_brs(self):
        with db.connection(commit=True) as conn:
            conn.execute('ALTER TABLE Resistor ADD COLUMN "Product Status" TEXT')
            conn.execute("UPDATE Resistor SET \"Product Status\"='Obsolete'")
            conn.execute('ALTER TABLE Other ADD COLUMN "Product Status" TEXT')
            conn.executemany('INSERT INTO Other VALUES (?,?,?,?,?,?)', [
                (1, ' br-1 ', 'Other maker', 'MPN-2', 0, 'Last Time Buy'),
                (2, '-', 'Other maker', 'NO-BR', 0, 'Obsolete'),
                (3, '-', 'Other maker', 'NO-BR', 0, 'Obsolete'),
                (4, '', 'Other maker', 'OTHER-MPN', 0, 'Obsolete'),
                (5, 'CMP-NEW', 'Other maker', 'UNLINKED', 0, 'Obsolete')])
        other = next(p for p in pt.component_catalog() if p['mpn'] == 'MPN-2')
        pt.save_bom(self.meta, [self.lines[0], dict(self.lines[0], br='br-1', mpn='MPN-2',
            manufacturer='Other maker', component_id=other['id'], quantity=2, references='R4,R5')], 'BOM.xlsx')
        with db.connection() as conn:
            before = list(conn.iterdump())
        report = pt.obsolete_products()
        self.assertEqual(len(report), 5)
        combined, = [p for p in report if pt.normalized(p['cmp']) == 'br-1']
        self.assertEqual(len(combined['components']), 2)
        self.assertEqual(set(combined['mpn'].split('; ')), {'MPN-1', 'MPN-2'})
        self.assertEqual(set(combined['product_status'].split('; ')), {'Obsolete', 'Last Time Buy'})
        self.assertEqual({p['mpn']: p['usage']['quantity'] for p in combined['usages']}, {'MPN-1': 3, 'MPN-2': 2})
        self.assertEqual(len([p for p in report if not pt.normalized(p['cmp'])]), 3)
        with db.connection() as conn:
            self.assertEqual(list(conn.iterdump()), before)

    def test_obsolete_window_refreshes_and_displays_project_usage(self):
        import time
        import tkinter as tk
        from ui.obsolete_products_window import ObsoleteProductsWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        with db.connection(commit=True) as conn:
            conn.execute('ALTER TABLE Resistor ADD COLUMN "Product Status" TEXT')
            conn.execute('UPDATE Resistor SET "Product Status"=?', ('Obsolete',))
            conn.execute('ALTER TABLE Resistor ADD COLUMN Alt_Part_Number TEXT')
            conn.execute('UPDATE Resistor SET Alt_Part_Number=?', ('ALT-1',))
        old = pt.save_bom(self.meta, self.lines, 'BOM.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2'), old)
        pt.save_bom(dict(self.meta, revision='B'), self.lines, 'B.xlsx')
        window = ObsoleteProductsWindow(root)
        def wait_job():
            deadline = time.monotonic() + 8
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            self.assertFalse(window.busy)
        try:
            wait_job()
            self.assertEqual(window.view.get_children(), ('0',))
            self.assertFalse(window.view.item('0', 'open'))
            self.assertEqual(len(window.view.get_children('0')), 3)
            self.assertEqual(window.view.item('0', 'text'), 'CMP-1')
            self.assertEqual(window.view.set('0:0', 'project'), 'P1')
            self.assertEqual(window.view.set('0', 'product_status'), 'Obsolete')
            self.assertEqual(window.view.heading('#0', 'text'), 'BR')
            self.assertEqual(window.view['columns'][:3], ('mpn', 'alternate_part_number', 'manufacturer'))
            self.assertEqual(window.view.heading('alternate_part_number', 'text'), 'Alternate Part Number')
            self.assertEqual(window.view.set('0', 'alternate_part_number'), 'ALT-1')
            self.assertEqual(window.view.set('0:0', 'total'), '6')
            self.assertEqual(window.view.set('0', 'scope'), '3 usage rows')
            self.assertEqual({window.view.set(row, 'scope') for row in window.view.get_children('0')}, {'Current', 'Previous'})
            window.view.item('0', open=True)
            root.update()
            self.assertTrue(window.view.bbox('0:0'))
            self.assertIn('1 products; 3 usage rows.', window.summary.get())
            with db.connection(commit=True) as conn:
                conn.execute('UPDATE Resistor SET Alt_Part_Number=?', ('ALT-NEW',))
            window.refresh()
            wait_job()
            self.assertEqual(window.view.set('0', 'alternate_part_number'), 'ALT-NEW')
            window.view.selection_set('0')
            from ui.product_tree_window import copy_selected_rows
            copy_selected_rows(window.view)
            self.assertEqual(window.clipboard_get().split('\t')[:4], ['CMP-1', 'MPN-1', 'ALT-NEW', 'Yageo'])
            with db.connection(commit=True) as conn:
                conn.execute('UPDATE Resistor SET "Product Status"=?', ('Active',))
            window.refresh()
            wait_job()
            self.assertEqual(window.view.get_children(), ())
            self.assertEqual(window.summary.get(), 'No Obsolete, Not For New Designs or Last Time Buy products found.')
        finally:
            window.close()
            root.destroy()

    def test_sync_fills_only_missing_br_from_live_matches_and_preserves_shared_approval(self):
        import json
        revision = pt.save_bom(dict(self.meta, status='Approved'),
            [dict(self.lines[0], br=''), dict(self.lines[0], br='CMP-KEEP', references='R4')], 'BOM.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2', board_quantity=4), revision)
        other = pt.save_bom(dict(self.meta, revision='B'), [dict(self.lines[0], br='')], 'B.xlsx')
        before = pt.revision_lines(revision)
        metadata = pt.project_tree(True)
        with db.connection(commit=True) as conn:
            conn.execute("UPDATE Resistor SET Company_Part_Number='CMP-UPDATED'")
            # An established match remains authoritative even with duplicate MPNs.
            conn.execute("INSERT INTO Other VALUES (1,'CMP-DUPLICATE','Yageo','MPN-1',10)")
        components = {table: pt.rows(f'SELECT * FROM {table}') for table in ('Resistor', 'Other')}
        result = pt.sync_missing_company_numbers(revision)
        self.assertEqual((result['updated'], result['already_present'], result['ambiguous']), (1, 1, 0))
        self.assertEqual(pt.revision_lines(revision), [dict(before[0], br='CMP-UPDATED', cmp='CMP-UPDATED'), before[1]])
        self.assertEqual(pt.revision_lines(other)[0]['cmp'], '')
        self.assertEqual(pt.project_tree(True), metadata)
        self.assertEqual({table: pt.rows(f'SELECT * FROM {table}') for table in components}, components)
        log = pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_SYNC_DATABASE'")
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]['Changed_By'], 'admin')
        self.assertEqual(json.loads(log[0]['Details'])['changes'][0]['new_br'], 'CMP-UPDATED')
        self.assertEqual(pt.sync_missing_company_numbers(revision)['updated'], 0)
        self.assertEqual(pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_SYNC_DATABASE'"), log)
        self.assertEqual(pt.project_tree(True), metadata)

    def test_sync_exact_mpn_fallback_reports_ambiguity_and_missing_database_numbers(self):
        with db.connection(commit=True) as conn:
            conn.executemany('INSERT INTO Other VALUES (?,?,?,?,?)', [
                (1, 'CMP-OTHER', 'Other maker', 'MPN-1', 1),
                (2, 'CMP-UNIQUE', 'Maker', 'UNIQUE-A', 2),
                (3, '--', 'Maker', 'NO-BR', 3),
                (4, 'CMP-NONE', 'Maker', '-- mixed values --', 4),
            ])
        inputs = [
            dict(self.lines[0], br='', mpn=' mpn-1 ', manufacturer=' yAgEo ', component_id=None),
            dict(self.lines[0], br='', manufacturer='', component_id=None),
            dict(self.lines[0], br='', mpn='UNIQUE-A', manufacturer='', component_id=None),
            dict(self.lines[0], br='', mpn='NO-BR', manufacturer='Maker', component_id=None),
            dict(self.lines[0], br='', mpn='UNIQUEA', manufacturer='', component_id=None),
            dict(self.lines[0], br='', mpn='-- mixed values --', manufacturer='', component_id=None),
            dict(self.lines[0], br='', manufacturer='Wrong maker', component_id=None),
        ]
        revision = pt.save_bom(self.meta, inputs, 'BOM.xlsx')
        before = pt.revision_lines(revision)
        db.set_current_user('engineer')
        result = pt.sync_missing_company_numbers(revision)
        self.assertEqual({key: value for key, value in result.items() if key != 'catalog'},
                         dict(updated=2, already_present=0, ambiguous=1, not_found=3, missing_database_br=1))
        after = pt.revision_lines(revision)
        self.assertEqual(after[0], dict(before[0], br='CMP-1', cmp='CMP-1', component_id=self.identity))
        self.assertEqual(after[2]['cmp'], 'CMP-UNIQUE')
        for index in (1, 3, 4, 5, 6):
            self.assertEqual(after[index], before[index])
        self.assertEqual(len(pt.usage(after[2]['component_id'])), 1)
        self.assertEqual(pt.rows("SELECT Changed_By FROM Change_Log WHERE Action='BOM_SYNC_DATABASE'")[0]['Changed_By'], 'engineer')

    def test_sync_handles_placeholders_deleted_matches_and_components_without_br(self):
        placeholders = ('', ' ', '-', '--', '—', '–', 'N/A', 'n.a.', 'NA', 'none', 'null', '-- mixed values --')
        revision = pt.save_bom(self.meta, [dict(self.lines[0], br=value) for value in placeholders], 'BOM.xlsx')
        self.assertEqual(pt.sync_missing_company_numbers(revision)['updated'], len(placeholders))
        self.assertTrue(all(row['cmp'] == 'CMP-1' for row in pt.revision_lines(revision)))
        with db.connection(commit=True) as conn:
            conn.execute("UPDATE PT_Lines SET br='', cmp='' WHERE revision_id=?", (revision,))
            conn.execute("UPDATE Resistor SET Company_Part_Number='-- mixed values --'")
        before = pt.revision_lines(revision)
        self.assertEqual(pt.sync_missing_company_numbers(revision)['missing_database_br'], len(placeholders))
        self.assertEqual(pt.revision_lines(revision), before)
        db.delete_data('Resistor', 'id', 1)
        self.assertEqual(pt.sync_missing_company_numbers(revision)['not_found'], len(placeholders))
        with db.connection(commit=True) as conn:
            conn.execute("INSERT INTO Other VALUES (1,'CMP-REPLACEMENT','Yageo','MPN-1',10)")
        self.assertEqual(pt.sync_missing_company_numbers(revision)['updated'], len(placeholders))
        self.assertTrue(all(row['component_id'] != self.identity for row in pt.revision_lines(revision)))

    def test_sync_rechecks_current_permissions_and_rolls_back_if_audit_fails(self):
        revision = pt.save_bom(dict(self.meta, status='Approved'), [dict(self.lines[0], br='')], 'BOM.xlsx')
        def snapshot():
            return {table: pt.rows(f'SELECT * FROM {table}') for table in (*pt.TABLES, 'Change_Log')}
        before = snapshot()
        for actor in ('engineer', 'inactive', '', 'missing'):
            db.set_current_user(actor)
            with self.assertRaises(PermissionError):
                pt.sync_missing_company_numbers(revision)
            self.assertEqual(snapshot(), before)
        db.set_current_user('admin')
        with db.connection(commit=True) as conn:
            conn.execute("UPDATE Users SET Role='user' WHERE Username='admin'")
        with self.assertRaises(PermissionError):
            pt.sync_missing_company_numbers(revision)
        with db.connection(commit=True) as conn:
            conn.execute("UPDATE Users SET Role='admin' WHERE Username='admin'")
            conn.execute("INSERT INTO Other VALUES (1,'CMP-NEW','Maker','NEW',10)")
        with patch.object(db, 'write_change_log', side_effect=RuntimeError('Audit unavailable')), \
             patch.object(db.audit_log, 'info') as emit:
            with self.assertRaisesRegex(RuntimeError, 'Audit unavailable'):
                pt.sync_missing_company_numbers(revision)
        emit.assert_not_called()
        self.assertEqual(snapshot(), before)
        with self.assertRaises(ValueError):
            pt.sync_missing_company_numbers(-1)
        pt.return_bom_to_draft(revision)
        db.set_current_user('engineer')
        self.assertEqual(pt.sync_missing_company_numbers(revision)['updated'], 1)

    def test_project_sync_includes_descendants_history_and_shared_revisions_once(self):
        missing = [dict(self.lines[0], br='')]
        direct = pt.save_bom(self.meta, missing, 'Direct.xlsx')
        child_meta = dict(self.meta, subproject_code='S1', subproject_name='Child 1', board_number='PCB2')
        previous = pt.save_bom(dict(child_meta, status='Approved'), missing, 'Previous.xlsx')
        current = pt.save_bom(dict(child_meta, revision='B'), missing, 'Current.xlsx')
        sibling = pt.save_bom(dict(self.meta, subproject_code='S2', subproject_name='Child 2',
                                  board_number='PCB3'), missing, 'Sibling.xlsx')
        outside = pt.save_bom(dict(self.meta, project_code='OUT', board_number='PCB4'), missing, 'Outside.xlsx')
        projects = {row['code']: row['id'] for row in pt.list_projects()}
        pt.link_card_revision(projects['S1'], direct, 3)
        pt.link_card_revision(projects['S2'], direct, 4)
        pt.link_card_revision(projects['OUT'], direct, 5)
        empty = pt.create_card(projects['S2'], 'EMPTY', 'Empty board', 'A')
        before = pt.project_tree(True)
        outside_lines = pt.revision_lines(outside)
        components = pt.rows('SELECT * FROM Resistor')
        with patch.object(pt, 'component_catalog', wraps=pt.component_catalog) as catalog:
            result = pt.sync_missing_company_numbers(project_id=projects['P1'])
        catalog.assert_called_once()
        self.assertEqual((result['updated'], result['revisions'], result['skipped_revisions']), (4, 5, 0))
        for revision in (direct, previous, current, sibling):
            self.assertEqual(pt.revision_lines(revision)[0]['cmp'], 'CMP-1')
        self.assertEqual(pt.revision_lines(empty), [])
        self.assertEqual(pt.revision_lines(outside), outside_lines)
        self.assertEqual(pt.project_tree(True), before)
        self.assertEqual(pt.rows('SELECT * FROM Resistor'), components)
        logs = pt.rows("SELECT Record_Key FROM Change_Log WHERE Action='BOM_SYNC_DATABASE'")
        self.assertEqual(sorted(row['Record_Key'] for row in logs), sorted(map(str, (direct, previous, current, sibling))))
        self.assertEqual(pt.sync_missing_company_numbers(project_id=projects['P1'])['updated'], 0)
        self.assertEqual(pt.rows("SELECT Record_Key FROM Change_Log WHERE Action='BOM_SYNC_DATABASE'"), logs)

    def test_project_sync_skips_protected_boms_and_checks_live_account(self):
        missing = [dict(self.lines[0], br='')]
        draft = pt.save_bom(self.meta, missing, 'Draft.xlsx')
        approved = pt.save_bom(dict(self.meta, board_number='PCB2', status='Approved'), missing, 'Approved.xlsx')
        project = pt.list_projects()[0]['id']
        before = pt.project_tree(True)
        protected_lines = pt.revision_lines(approved)
        for actor in ('inactive', '', 'missing'):
            db.set_current_user(actor)
            with self.assertRaises(PermissionError):
                pt.sync_missing_company_numbers(project_id=project)
            self.assertEqual(pt.revision_lines(draft)[0]['cmp'], '')
        db.set_current_user('admin')
        with db.connection(commit=True) as conn:
            conn.execute("UPDATE Users SET Role='user' WHERE Username='admin'")
        result = pt.sync_missing_company_numbers(project_id=project)
        self.assertEqual((result['updated'], result['revisions'], result['skipped_revisions']), (1, 1, 1))
        self.assertEqual(pt.revision_lines(approved), protected_lines)
        with db.connection(commit=True) as conn:
            conn.execute("UPDATE Users SET Role='admin' WHERE Username='admin'")
        result = pt.sync_missing_company_numbers(project_id=project)
        self.assertEqual((result['updated'], result['revisions'], result['skipped_revisions']), (1, 2, 0))
        self.assertEqual(pt.project_tree(True), before)
        empty_project = pt.save_project('EMPTY', 'Empty project')
        self.assertEqual(pt.sync_missing_company_numbers(project_id=empty_project)['revisions'], 0)
        with self.assertRaisesRegex(ValueError, 'Project not found'):
            pt.sync_missing_company_numbers(project_id=-1)
        for args in ({}, {'revision_id': draft, 'project_id': project}):
            with self.assertRaises(ValueError):
                pt.sync_missing_company_numbers(**args)

    def test_project_sync_rolls_back_all_revisions_when_a_later_audit_fails(self):
        missing = [dict(self.lines[0], br='')]
        pt.save_bom(dict(self.meta, status='Approved'), missing, 'One.xlsx')
        pt.save_bom(dict(self.meta, board_number='PCB2'), missing, 'Two.xlsx')
        project = pt.list_projects()[0]['id']
        before = {table: pt.rows(f'SELECT * FROM {table}') for table in (*pt.TABLES, 'Change_Log')}
        original_write = db.write_change_log
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise RuntimeError('Audit unavailable')
            return original_write(*args, **kwargs)
        with patch.object(db, 'write_change_log', side_effect=fail_second), patch.object(db.audit_log, 'info') as emit:
            with self.assertRaisesRegex(RuntimeError, 'Audit unavailable'):
                pt.sync_missing_company_numbers(project_id=project)
        self.assertEqual(len(calls), 2)
        emit.assert_not_called()
        self.assertEqual({table: pt.rows(f'SELECT * FROM {table}') for table in before}, before)

    def test_window_project_sync_processes_collapsed_projects_and_reports_permissions(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        missing = [dict(self.lines[0], br='')]
        direct = pt.save_bom(self.meta, missing, 'Direct.xlsx')
        child_meta = dict(self.meta, subproject_code='SUB', subproject_name='Child', board_number='PCB2')
        child = pt.save_bom(child_meta, missing, 'Child.xlsx')
        approved = pt.save_bom(dict(child_meta, board_number='PCB3', status='Approved'), missing, 'Approved.xlsx')
        projects = {row['code']: row['id'] for row in pt.list_projects()}
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 8
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(.02)
            root.update()
            self.assertFalse(window.busy)
        try:
            with patch('tkinter.messagebox.showinfo') as notice, patch('tkinter.messagebox.showerror') as error:
                wait_job()
                project_node = f"p{projects['P1']}"
                self.assertEqual(window.tree.selection(), (project_node,))
                self.assertFalse(window.tree.item(project_node, 'open'))
                db.set_current_user('inactive')
                window.apply_bom_permissions()
                self.assertEqual(str(window.sync_database_button.cget('state')), 'disabled')
                db.set_current_user('engineer')
                window.apply_bom_permissions()
                self.assertEqual(str(window.sync_database_button.cget('state')), 'normal')
                window.sync_database_button.invoke()
                self.assertTrue(window.busy)
                self.assertEqual(str(window.sync_database_button.cget('state')), 'disabled')
                wait_job()
                for revision in (direct, child):
                    self.assertEqual(pt.revision_lines(revision)[0]['cmp'], 'CMP-1')
                self.assertEqual(pt.revision_lines(approved)[0]['cmp'], '')
                self.assertEqual(window.tree.selection(), (project_node,))
                self.assertFalse(window.tree.item(project_node, 'open'))
                self.assertIn('BOM revisions skipped (administrator required): 1', notice.call_args.args[1])
                db.set_current_user('admin')
                window.tree.selection_set(f"p{projects['SUB']}")
                window.select_revision()
                window.sync_database_button.invoke()
                wait_job()
                self.assertEqual(pt.revision_lines(approved)[0]['cmp'], 'CMP-1')
                self.assertEqual(pt.revision_for('PCB3', 'A')['status'], 'Approved')
                self.assertIn('BOM revisions checked (including previous revisions): 2', notice.call_args.args[1])
                board = pt.revision_for('PCB2', 'A')['board_id']
                window.tree.selection_set(f"p{projects['SUB']}b{board}")
                window.select_revision()
                self.assertEqual(str(window.sync_database_button.cget('state')), 'disabled')
                window.tree.selection_set((project_node, f"p{projects['SUB']}"))
                window.select_revision()
                self.assertEqual(str(window.sync_database_button.cget('state')), 'disabled')
                error.assert_not_called()
        finally:
            wait_job()
            window.close()
            root.destroy()

    def test_usage_value_is_live_and_optional_in_component_tables(self):
        pt.save_bom(self.meta, self.lines, 'BOM.xlsx')
        self.assertIsNone(pt.usage(self.identity)[0]['value'])
        with db.connection(commit=True) as conn:
            conn.execute('ALTER TABLE Resistor ADD COLUMN Value')
            conn.execute("UPDATE Resistor SET Value='10 kOhm'")
        self.assertEqual(pt.usage(self.identity)[0]['value'], '10 kOhm')
        self.assertEqual(pt.component_catalog()[0]['value'], '10 kOhm')
        for value in (0, '', None):
            with db.connection(commit=True) as conn:
                conn.execute('UPDATE Resistor SET Value=?', (value,))
            self.assertEqual(pt.usage(self.identity)[0]['value'], value)
            self.assertEqual(pt.usage(self.identity)[0]['company_stock'], 25)
        db.delete_data('Resistor', 'id', 1)
        self.assertIsNone(pt.usage(self.identity)[0]['value'])

    def test_window_sync_button_permissions_and_component_detail_columns(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        revision = pt.save_bom(dict(self.meta, status='Approved'), [dict(self.lines[0], br='')], 'BOM.xlsx')
        with db.connection(commit=True) as conn:
            conn.execute('ALTER TABLE Resistor ADD COLUMN Value TEXT')
            conn.execute('ALTER TABLE Resistor ADD COLUMN Description TEXT')
            conn.execute("UPDATE Resistor SET Value='10k', Description='Precision resistor'")
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 8
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(.02)
            root.update()
            self.assertFalse(window.busy)
        try:
            with patch('tkinter.messagebox.showinfo') as notice, patch('tkinter.messagebox.showerror') as error:
                wait_job()
                self.assertEqual(str(window.sync_database_button.cget('state')), 'normal')
                siblings = window.draft_button.master.pack_slaves()
                self.assertEqual(siblings[siblings.index(window.draft_button) + 1], window.sync_database_button)
                node = next(key for key, row in window.tree_rows.items() if row['id'] == revision)
                window.tree.selection_set(node)
                root.update()
                window.select_revision()
                db.set_current_user('engineer')
                window.apply_bom_permissions()
                self.assertEqual(str(window.sync_database_button.cget('state')), 'disabled')
                db.set_current_user('admin')
                window.apply_bom_permissions()
                self.assertEqual(str(window.sync_database_button.cget('state')), 'normal')
                window.bom_description.insert('end', ' unsaved')
                window.sync_database_button.invoke()
                self.assertFalse(window.busy)
                self.assertIn('Save the edited description', notice.call_args.args[1])
                window.select_revision()
                with db.connection(commit=True) as conn:
                    conn.execute("UPDATE Resistor SET Company_Part_Number='CMP-FRESH'")
                window.sync_database_button.invoke()
                self.assertTrue(window.busy)
                self.assertEqual(str(window.sync_database_button.cget('state')), 'disabled')
                wait_job()
                window.select_revision()
                self.assertEqual(window.lines.set(window.lines.get_children()[0], 'cmp'), 'CMP-FRESH')
                self.assertIn('1 Company part numbers updated', notice.call_args.args[1])
                self.assertIn('Approved', window.detail.get())
                self.assertEqual(window.part_list.set(self.identity, 'cmp'), 'CMP-FRESH')
                self.assertEqual(window.part_list.set(self.identity, 'value'), '10k')
                self.assertEqual(window.part_list.set(self.identity, 'description'), 'Precision resistor')
                window.part_list.selection_set(self.identity)
                window.show_usage()
                self.assertNotIn('value', window.usages.cget('columns'))
                self.assertNotIn('description', window.usages.cget('columns'))
                self.assertEqual(window.usages.set('0', 'qty'), '3')
                with db.connection(commit=True) as conn:
                    conn.execute("UPDATE Resistor SET Value='22k', Description='Updated resistor'")
                window.refresh_usage()
                wait_job()
                self.assertEqual(window.part_list.set(self.identity, 'value'), '22k')
                self.assertEqual(window.part_list.set(self.identity, 'description'), 'Updated resistor')
                self.assertEqual(window.usage_data[0]['value'], '22k')
                window.tree.selection_set((node, window.tree.get_children()[0]))
                window.select_revision()
                self.assertEqual(str(window.sync_database_button.cget('state')), 'disabled')
                error.assert_not_called()
        finally:
            wait_job()
            window.close()
            root.destroy()

    def test_roundtrip_shared_revision_and_live_stock(self):
        revision = pt.save_bom(self.meta, self.lines, "BOM.xlsx")
        pt.attach_revision(dict(self.meta, project_code="P2", board_quantity=4), revision)
        self.assertEqual([r["total"] for r in pt.usage(self.identity)], [6, 12])
        with db.connection(commit=True) as conn:
            conn.execute("UPDATE Resistor SET Company_Stock=0")
        self.assertEqual([r["company_stock"] for r in pt.usage(self.identity)], [0, 0])
        self.assertEqual(set(db.get_tables()), {"Resistor", "Other"})
        self.assertEqual(db.get_total_material_count(), 1)

    def test_responsible_is_shared_preserved_and_audited(self):
        import json
        project = pt.save_project('P1', 'Project')
        revision = pt.create_card(project, 'PCB1', 'Card', '-X1', responsible='Engin Kavak')
        pt.import_bom_into_revision(pt.bom_edit_snapshot(project, revision), self.lines, 'Fixture.xlsx')
        board = pt.project_tree()[0]['board_id']
        self.assertEqual(pt.project_tree()[0]['responsible'], 'Engin Kavak')
        pt.add_card_revision(project, board, '-X2')
        pt.update_card(board, 'PCB1', 'Renamed')
        self.assertTrue(all(r['responsible'] == 'Engin Kavak' for r in pt.project_tree(True)))
        pt.update_card(board, 'PCB1', 'Renamed', responsible='Fikret Çelik')
        change = pt.rows("SELECT Details FROM Change_Log WHERE Action='CARD_UPDATE' ORDER BY id DESC")[0]
        self.assertEqual(json.loads(change['Details'])['old_responsible'], 'Engin Kavak')
        self.assertEqual(json.loads(change['Details'])['responsible'], 'Fikret Çelik')
        pt.approve_bom(revision)
        db.set_current_user('engineer')
        with self.assertRaises(PermissionError):
            pt.update_card(board, 'PCB1', 'Renamed', responsible='Other')
        with self.assertRaises(ValueError):
            pt.create_card(project, 'PCB1', 'Renamed', '-X3', responsible='Other')
        self.assertEqual(len(pt.project_tree(True)), 2)
        self.assertTrue(all(r['responsible'] == 'Fikret Çelik' for r in pt.project_tree(True)))

    def test_responsible_schema_migration_preserves_card_ids(self):
        with db.connection(commit=True) as conn:
            conn.execute('ALTER TABLE PT_Boards DROP COLUMN responsible')
            conn.execute("INSERT INTO PT_Boards(id,part_number,name) VALUES (71,'OLD','Existing')")
        pt.ensure_schema()
        pt.ensure_schema()
        self.assertEqual(pt.rows('SELECT * FROM PT_Boards'),
                         [dict(id=71, part_number='OLD', name='Existing', responsible='')])

    def test_create_card_under_selected_subproject_and_import_bom_later(self):
        existing = pt.save_bom(self.meta, self.lines, 'Existing.xlsx')
        project = pt.list_projects()[0]['id']
        upper = pt.save_project('TOP', 'Top-level project')
        pt.save_project('P1', 'Project', project_id=project, parent_id=upper)
        db.set_current_user('engineer')
        revision = pt.create_card(project, 'PCB2', 'Auxiliary board', 'B', 2)
        row = next(r for r in pt.project_tree() if r['id'] == revision)
        self.assertEqual((row['parent_id'], row['project_id'], row['status']), (upper, project, 'Draft'))
        self.assertEqual((row['source'], row['imported_at'], row['approved_by']), ('', '', ''))
        self.assertEqual(pt.revision_lines(revision), [])
        self.assertEqual(len(pt.revision_lines(existing)), 1)
        meta = dict(self.meta, project_code='TOP', project_name='Top-level project',
                    subproject_code='P1', subproject_name='Project', board_number='PCB2',
                    board_name='Auxiliary board', revision='B')
        self.assertEqual(pt.save_bom(meta, self.lines, 'New.xlsx', replace=True), revision)
        self.assertEqual(len(pt.revision_lines(revision)), 1)
        self.assertEqual(len(pt.project_tree()), 2)
        self.assertEqual({r['project_id'] for r in pt.project_tree()}, {project})

    def test_create_card_validation_permissions_and_duplicate_preserve_data(self):
        project = pt.save_project('P1', 'Project')
        for args in [(project, '', 'Card', 'A', 1), (project, 'PCB1', '', 'A', 1),
                     (project, 'PCB1', 'Card', '', 1), (project, 'PCB1', 'Card', 'A', 0),
                     (999, 'PCB1', 'Card', 'A', 1)]:
            with self.assertRaises(ValueError):
                pt.create_card(*args)
        self.assertEqual(pt.rows('SELECT * FROM PT_Boards'), [])
        for actor in ('inactive', '', 'missing'):
            db.set_current_user(actor)
            with self.assertRaises(PermissionError):
                pt.create_card(project, 'PCB1', 'Card', 'A')
        db.set_current_user('admin')
        revision = pt.create_card(project, 'PCB1', 'Card', 'A')
        with self.assertRaisesRegex(ValueError, 'empty BOM'):
            pt.approve_bom(revision)
        before = pt.project_tree(True)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            pt.create_card(project, 'pcb1', 'Card', 'a', 99)
        with self.assertRaisesRegex(ValueError, 'existing board name'):
            pt.create_card(project, 'PCB1', 'Changed name', 'B')
        self.assertEqual(pt.project_tree(True), before)

    def test_create_card_new_revision_preserves_shared_approved_board(self):
        original = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'A.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2'), original)
        project = next(p['id'] for p in pt.list_projects() if p['code'] == 'P1')
        before = pt.revision_for('PCB1', 'A')
        db.set_current_user('engineer')
        new = pt.create_card(project, 'PCB1', 'Card', 'B')
        self.assertEqual(pt.revision_for('PCB1', 'A'), before)
        self.assertEqual({row['code']: row['id'] for row in pt.project_tree()}, {'P1': new, 'P2': original})
        self.assertEqual(len(pt.revision_lines(original)), 1)
        self.assertEqual(pt.revision_lines(new), [])

    def test_add_card_revision_preserves_shared_history_and_uses_existing_definition(self):
        original = pt.save_bom(dict(self.meta, revision='-X1', status='Approved'), self.lines, 'Original.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2'), original)
        project = next(p['id'] for p in pt.list_projects() if p['code'] == 'P1')
        parent = pt.save_project('TOP', 'Top project')
        pt.save_project('P1', 'Project', project_id=project, parent_id=parent)
        before = pt.revision_for('PCB1', '-X1')
        before_lines = pt.revision_lines(original)
        definitions = pt.rows('SELECT * FROM PT_Boards')
        db.set_current_user('engineer')
        new = pt.add_card_revision(project, before['board_id'], ' -X2 ', '3')
        saved = pt.revision_for('PCB1', '-X2')
        self.assertEqual((saved['id'], saved['status'], saved['source'], saved['imported_by']),
                         (new, 'Draft', '', 'engineer'))
        self.assertEqual((saved['notes'], saved['approved_by'], saved['approved_at']), ('', '', ''))
        self.assertEqual(pt.revision_lines(new), [])
        self.assertEqual(pt.revision_for('PCB1', '-X1'), before)
        self.assertEqual(pt.revision_lines(original), before_lines)
        self.assertEqual(pt.rows('SELECT * FROM PT_Boards'), definitions)
        self.assertEqual({r['code']: r['id'] for r in pt.project_tree()}, {'P1': new, 'P2': original})
        self.assertEqual(pt.bom_edit_snapshot(project, new)['board_quantity'], 3)
        self.assertEqual(len(pt.project_tree(True)), 3)
        logs = pt.rows("SELECT * FROM Change_Log WHERE Action='CARD_REVISION_CREATE'")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]['Changed_By'], 'engineer')

    def test_add_card_revision_rejects_duplicates_stale_links_and_inactive_accounts(self):
        original = pt.save_bom(self.meta, self.lines, 'Original.xlsx')
        project = pt.list_projects()[0]['id']
        board = pt.revision_for('PCB1', 'A')['board_id']
        unrelated = pt.save_project('UNRELATED', 'Other project')
        snapshot = {table: pt.rows(f'SELECT * FROM {table}') for table in pt.TABLES}
        db.set_current_user('engineer')
        for args in [(project, board, 'a', 1), (project, board, ' ', 1), (project, board, 'B', 0),
                     (project, board, 'B', 'nan'), (project, -1, 'B', 1), (-1, board, 'B', 1),
                     (unrelated, board, 'B', 1)]:
            with self.assertRaises(ValueError):
                pt.add_card_revision(*args)
            self.assertEqual({table: pt.rows(f'SELECT * FROM {table}') for table in pt.TABLES}, snapshot)
        for actor in ('inactive', '', 'missing'):
            db.set_current_user(actor)
            with self.assertRaises(PermissionError):
                pt.add_card_revision(project, board, 'B')
        self.assertIsNone(pt.revision_for('PCB1', 'B'))
        db.set_current_user('admin')
        pt.detach_project_item(project, revision_id=original)
        with self.assertRaisesRegex(ValueError, 'no longer linked'):
            pt.add_card_revision(project, board, 'B')

    def test_import_requires_existing_definitions_and_never_updates_them(self):
        project = pt.save_project('P1', 'Project', folder=self.temp.name)
        revision = pt.create_card(project, 'PCB1', 'Card', 'A')
        before = pt.list_projects()
        for changes in (dict(project_code='NEW'), dict(subproject_code='UNKNOWN', subproject_name='Unknown'),
                        dict(board_number='NEW')):
            with self.assertRaises(ValueError):
                pt.save_bom(dict(self.meta, **changes), self.lines, 'BOM.xlsx', replace=True, existing_definitions=True)
            self.assertEqual(pt.list_projects(), before)
            self.assertEqual(len(pt.rows('SELECT * FROM PT_Boards')), 1)
            self.assertEqual(pt.revision_lines(revision), [])
        with self.assertRaises(ValueError):
            pt.attach_revision(dict(self.meta, project_code='NEW'), revision, existing_definitions=True)
        wrong_names = dict(self.meta, project_name='Do not rename', board_name='Do not rename', project_folder='different')
        pt.save_bom(wrong_names, self.lines, 'BOM.xlsx', replace=True, existing_definitions=True)
        self.assertEqual(pt.list_projects(), before)
        self.assertEqual(pt.rows('SELECT name FROM PT_Boards')[0]['name'], 'Card')
        self.assertEqual(len(pt.revision_lines(revision)), 1)

    def test_card_definition_edits_preserve_boms_and_check_permissions(self):
        revision = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'BOM.xlsx')
        board_id = pt.revision_for('PCB1', 'A')['board_id']
        lines = pt.revision_lines(revision)
        db.set_current_user('engineer')
        self.assertFalse(pt.can_edit_card(board_id))
        with self.assertRaises(PermissionError):
            pt.update_card(board_id, 'RENAMED', 'Changed')
        self.assertIsNotNone(pt.revision_for('PCB1', 'A'))
        db.set_current_user('admin')
        pt.update_card(board_id, 'RENAMED', 'Changed card')
        self.assertEqual(pt.revision_for('RENAMED', 'A')['id'], revision)
        self.assertEqual(pt.revision_lines(revision), lines)
        project = pt.list_projects()[0]['id']
        new = pt.create_card(project, 'PCB2', 'Second card', 'A')
        second_board = pt.revision_for('PCB2', 'A')['board_id']
        db.set_current_user('engineer')
        self.assertTrue(pt.can_edit_card(second_board))
        pt.update_card(second_board, 'PCB2', 'Draft card renamed')
        with self.assertRaisesRegex(ValueError, 'already in use'):
            pt.update_card(second_board, 'renamed', 'Duplicate')
        self.assertEqual(pt.revision_for('PCB2', 'A')['id'], new)

    def test_project_create_edit_validation(self):
        identity = pt.save_project("EMPTY", "Empty project", "Note")
        self.assertEqual(pt.list_projects()[0]["revision_count"], 0)
        pt.save_project("RENAMED", "New name", "New note", identity)
        self.assertEqual(pt.list_projects()[0]["notes"], "New note")
        with self.assertRaises(ValueError):
            pt.save_project("renamed", "Duplicate")
        with self.assertRaises(ValueError):
            pt.save_project(" ", "Missing code")
        with self.assertRaises(ValueError):
            pt.save_project("X", "Missing project", project_id=999)

    def test_project_hierarchy_migration_preserves_existing_boms(self):
        revision = pt.save_bom(self.meta, self.lines, 'BOM.xlsx')
        before = pt.revision_lines(revision)
        with db.connection(commit=True) as conn:
            conn.execute('DROP INDEX PT_project_parent_idx')
            conn.execute('DROP INDEX PT_project_root_code_idx')
            conn.execute('DROP INDEX PT_project_child_code_idx')
            conn.execute('ALTER TABLE PT_Projects DROP COLUMN parent_id')
        pt.ensure_schema()
        pt.ensure_schema()
        project = pt.list_projects()[0]
        self.assertIsNone(project['parent_id'])
        self.assertEqual(pt.revision_lines(revision), before)
        self.assertEqual(pt.usage(self.identity)[0]['total'], 6)
        upper = pt.save_project('UPPER', 'Top-level project')
        pt.save_project(project['code'], project['name'], project_id=project['id'], parent_id=upper)
        self.assertEqual(pt.project_tree()[0]['parent_code'], 'UPPER')
        self.assertEqual(pt.usage(self.identity)[0]['parent_id'], upper)
        pt.save_project('P1-RENAMED', 'Renamed subproject', project_id=project['id'])
        self.assertEqual(pt.usage(self.identity)[0]['parent_id'], upper)
        self.assertEqual(pt.revision_lines(revision), before)

    def test_subproject_import_shared_boards_and_current_revisions_are_independent(self):
        meta = dict(self.meta, subproject_code='SUB-A', subproject_name='Control', project_folder=self.temp.name)
        a = pt.save_bom(meta, self.lines, 'A.xlsx')
        pt.attach_revision(dict(meta, subproject_code='SUB-B', subproject_name='Power', board_quantity=4), a)
        b = pt.save_bom(dict(meta, revision='B'), self.lines, 'B.xlsx')
        projects = {p['code']: p for p in pt.list_projects()}
        self.assertEqual(projects['P1']['subproject_count'], 2)
        self.assertEqual(projects['SUB-A']['parent_id'], projects['P1']['id'])
        self.assertEqual(projects['SUB-A']['folder'], self.temp.name)
        self.assertEqual(projects['P1']['folder'], '')
        current = {row['code']: row for row in pt.project_tree()}
        self.assertEqual(current['SUB-A']['id'], b)
        self.assertEqual(current['SUB-B']['id'], a)
        self.assertEqual(len(pt.project_tree(True)), 3)
        uses = {row['code']: row for row in pt.usage(self.identity)}
        self.assertEqual(uses['SUB-A']['total'], 6)
        self.assertEqual(uses['SUB-B']['total'], 12)
        self.assertTrue(all(row['parent_code'] == 'P1' and row['company_stock'] == 25 for row in uses.values()))
        pt.detach_project_item(projects['SUB-A']['id'], board_id=pt.revision_for('PCB1', 'A')['board_id'])
        self.assertEqual([row['code'] for row in pt.usage(self.identity)], ['SUB-B'])
        self.assertEqual(pt.revision_lines(a)[0]['quantity'], 3)

    def test_subproject_parent_validation_and_scoped_deletion(self):
        upper = pt.save_project('TOP', 'Parent')
        child = pt.save_project('SUB', 'Child', parent_id=upper)
        other = pt.save_project('OTHER', 'Other parent')
        for parent in (child, upper):
            with self.assertRaises(ValueError):
                pt.save_project('TOP', 'Parent', project_id=upper, parent_id=parent)
        with self.assertRaises(ValueError):
            pt.save_project('DEEP', 'Too deep', parent_id=child)
        with self.assertRaises(ValueError):
            pt.save_project('MISSING', 'Missing parent', parent_id=999)
        with self.assertRaisesRegex(ValueError, 'subprojects'):
            pt.save_project('TOP', 'Parent', project_id=upper, parent_id=other)
        revision = pt.save_bom(dict(self.meta, project_code='TOP', project_name='Parent',
                                   subproject_code='SUB', subproject_name='Child'), self.lines, 'BOM.xlsx')
        pt.attach_revision(dict(self.meta, project_code='OTHER'), revision)
        with self.assertRaisesRegex(ValueError, 'subprojects'):
            pt.delete_project(upper)
        self.assertEqual(len(pt.usage(self.identity)), 2)
        pt.delete_project(child)
        self.assertEqual([row['code'] for row in pt.usage(self.identity)], ['OTHER'])
        self.assertEqual(len(pt.revision_lines(revision)), 1)
        self.assertEqual({p['id'] for p in pt.list_projects()}, {upper, other})
        pt.delete_project(upper)
        self.assertEqual([p['id'] for p in pt.list_projects()], [other])

    def test_import_keeps_same_codes_in_separate_scopes_and_rolls_back_invalid_metadata(self):
        upper = pt.save_project('TOP', 'Parent')
        child = pt.save_project('SUB', 'Child', parent_id=upper)
        before = pt.list_projects()
        for changes in (
            dict(subproject_code='INCOMPLETE'),
            dict(subproject_name='INCOMPLETE'),
            dict(project_code='NEW', subproject_code='SUB', subproject_name='Child', board_quantity=0),
        ):
            with self.assertRaises(ValueError):
                pt.save_bom(dict(self.meta, **changes), self.lines, 'BOM.xlsx')
            self.assertEqual(pt.list_projects(), before)
            self.assertEqual(pt.rows('SELECT * FROM PT_Revisions'), [])
        pt.save_bom(dict(self.meta, project_code='NEW', subproject_code='SUB', subproject_name='Another child'), self.lines, 'BOM.xlsx')
        original = next(p for p in pt.list_projects() if p['id'] == child)
        self.assertEqual((original['parent_id'], original['name']), (upper, 'Child'))
        self.assertEqual(len([p for p in pt.list_projects() if p['code'] == 'SUB']), 2)
        other = pt.save_project('SECOND', 'Second parent')
        pt.save_project('SUB', 'Moved child', project_id=child, parent_id=other)
        self.assertEqual(next(p for p in pt.list_projects() if p['id'] == child)['parent_id'], other)
        pt.save_project('SUB', 'Independent project', project_id=child, parent_id=None)
        self.assertIsNone(next(p for p in pt.list_projects() if p['id'] == child)['parent_id'])

    def test_project_codes_are_unique_per_parent_and_can_repeat_across_levels(self):
        existing_root = pt.save_project('SHARED', 'Independent project', folder='root-folder')
        parent = pt.save_project('TOP', 'Parent')
        second_parent = pt.save_project('SECOND', 'Second parent')
        child = pt.save_project('shared', 'First child', parent_id=parent, folder='child-folder')
        second_child = pt.save_project('SHARED', 'Second child', parent_id=second_parent)
        same_as_parent = pt.save_project('TOP', 'Child with parent code', parent_id=parent)
        self.assertEqual(len({existing_root, child, second_child, same_as_parent}), 4)
        before = pt.list_projects()
        for action in (
            lambda: pt.save_project('shared', 'Duplicate root'),
            lambda: pt.save_project('SHARED', 'Duplicate child', parent_id=parent),
            lambda: pt.save_project('shared', 'Moved child', project_id=child, parent_id=second_parent),
            lambda: pt.save_project('shared', 'Moved child', project_id=child, parent_id=None),
        ):
            with self.assertRaisesRegex(ValueError, 'already in use'):
                action()
            self.assertEqual(pt.list_projects(), before)
        pt.save_project('shared', 'Renamed child', project_id=child)
        records = {p['id']: p for p in pt.list_projects()}
        self.assertEqual((records[child]['parent_id'], records[child]['folder']), (parent, 'child-folder'))
        self.assertEqual((records[existing_root]['name'], records[existing_root]['folder']), ('Independent project', 'root-folder'))

    def test_import_resolves_reused_project_codes_by_parent_not_first_matching_row(self):
        top = pt.save_project('TOP', 'Parent')
        child = pt.save_project('REUSE', 'Child first', parent_id=top)
        root = pt.save_project('REUSE', 'Root second')
        child_revision = pt.create_card(child, 'PCB1', 'Card', 'A')
        root_revision = pt.create_card(root, 'PCB2', 'Second card', 'A')
        pt.save_bom(dict(self.meta, project_code='REUSE', project_name='Root second', board_number='PCB2',
                         board_name='Second card'), self.lines, 'Root.xlsx', replace=True, existing_definitions=True)
        pt.save_bom(dict(self.meta, project_code='TOP', project_name='Parent', subproject_code='REUSE',
                         subproject_name='Child first'), [dict(self.lines[0], quantity=8)],
                    'Child.xlsx', replace=True, existing_definitions=True)
        self.assertEqual(pt.bom_edit_snapshot(root, root_revision)['lines'][0]['quantity'], 3)
        self.assertEqual(pt.bom_edit_snapshot(child, child_revision)['lines'][0]['quantity'], 8)
        self.assertEqual({p['id']: p['parent_id'] for p in pt.list_projects()}, {top: None, root: None, child: top})

    def test_legacy_global_project_code_migration_preserves_ids_hierarchy_and_boms(self):
        revision = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'BOM.xlsx')
        project = pt.list_projects()[0]['id']
        parent = pt.save_project('TOP', 'Parent', notes='Parent notes', folder='parent-folder')
        pt.save_project('P1', 'Project', notes='Child notes', project_id=project, parent_id=parent, folder='child-folder')
        projects = pt.list_projects()
        before = pt.bom_edit_snapshot(project, revision)
        with db.connection(commit=True) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''CREATE TABLE PT_Projects_legacy (
                id INTEGER PRIMARY KEY,code TEXT NOT NULL UNIQUE COLLATE NOCASE,name TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',folder TEXT NOT NULL DEFAULT '',parent_id INTEGER REFERENCES PT_Projects(id))''')
            conn.execute('INSERT INTO PT_Projects_legacy SELECT id,code,name,notes,folder,parent_id FROM PT_Projects')
            conn.execute('DROP TABLE PT_Projects')
            conn.execute('ALTER TABLE PT_Projects_legacy RENAME TO PT_Projects')
        def enforced_connection():
            conn = sqlite3.connect(self.path)
            conn.execute('PRAGMA foreign_keys=ON')
            return conn
        with patch.object(db, 'get_connection', enforced_connection):
            pt.ensure_schema()
            pt.ensure_schema()
            self.assertEqual(pt.list_projects(), projects)
            self.assertEqual(pt.bom_edit_snapshot(project, revision), before)
            with db.connection() as conn:
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            new_root = pt.save_project('P1', 'Separate top-level project')
            child = pt.save_project('TOP', 'Reused top-level code', parent_id=parent)
            self.assertNotEqual(new_root, project)
            self.assertNotEqual(child, parent)
            pt.ensure_schema()
            self.assertEqual(pt.bom_edit_snapshot(project, revision), before)

    def test_project_folder_import_edit_and_existing_schema(self):
        with db.connection(commit=True) as conn:
            conn.execute("ALTER TABLE PT_Projects DROP COLUMN folder")
            conn.execute("INSERT INTO PT_Projects(code,name,notes) VALUES ('OLD','Old project','Keep')")
        pt.ensure_schema()
        pt.ensure_schema()
        self.assertEqual(pt.list_projects()[0]["folder"], "")
        self.assertEqual(pt.list_projects()[0]["notes"], "Keep")
        pt.save_bom(dict(self.meta, project_folder=self.temp.name), self.lines, "BOM.xlsx")
        project = next(p for p in pt.list_projects() if p["code"] == "P1")
        self.assertEqual(project["folder"], self.temp.name)
        pt.save_bom(dict(self.meta, revision="B", project_folder=""), self.lines, "BOM.xlsx")
        self.assertEqual(next(p for p in pt.list_projects() if p["id"] == project["id"])["folder"], self.temp.name)
        pt.save_project("P1", "Project", project_id=project["id"], folder="")
        self.assertEqual(next(p for p in pt.list_projects() if p["id"] == project["id"])["folder"], "")

    def test_detach_only_selected_card_or_revision(self):
        a = pt.save_bom(self.meta, self.lines, "A.xlsx")
        b = pt.save_bom(dict(self.meta, revision="B"), self.lines, "B.xlsx")
        other = pt.save_bom(dict(self.meta, board_number="PCB2"), self.lines, "Other.xlsx")
        pt.attach_revision(dict(self.meta, project_code="P2"), b)
        project = next(p for p in pt.list_projects() if p["code"] == "P1")
        pt.detach_project_item(project["id"], revision_id=b)
        remaining = [r for r in pt.project_tree(True) if r["project_id"] == project["id"]]
        self.assertEqual({r["id"] for r in remaining}, {a, other})
        self.assertEqual(next(r for r in remaining if r["id"] == a)["active"], 0)
        pt.detach_project_item(project["id"], board_id=pt.revision_for("PCB1", "A")["board_id"])
        self.assertEqual({r["id"] for r in pt.project_tree(True) if r["project_id"] == project["id"]}, {other})
        self.assertEqual(len(pt.list_projects()), 2)
        self.assertEqual(len(pt.revision_lines(b)), 1)
        self.assertTrue(any(r["code"] == "P2" for r in pt.usage(self.identity)))

    def test_delete_project_preserves_shared_boms_and_components(self):
        revision = pt.save_bom(self.meta, self.lines, "BOM.xlsx")
        pt.attach_revision(dict(self.meta, project_code="P2"), revision)
        projects = pt.list_projects()
        pt.save_project("P1-NEW", "Renamed", project_id=projects[0]["id"])
        self.assertEqual(pt.usage(self.identity)[0]["code"], "P1-NEW")
        pt.delete_project(projects[0]["id"])
        self.assertEqual(len(pt.revision_lines(revision)), 1)
        self.assertEqual([r["code"] for r in pt.usage(self.identity)], ["P2"])
        pt.delete_project(projects[1]["id"])
        self.assertEqual(pt.usage(self.identity), [])
        self.assertEqual(len(pt.revision_lines(revision)), 1)
        self.assertEqual(db.get_total_material_count(), 1)
        pt.attach_revision(dict(self.meta, project_code="P3"), revision)
        self.assertEqual(pt.usage(self.identity)[0]["code"], "P3")

    def test_bom_template_columns_and_grouped_references(self):
        frame = pd.DataFrame([{"Reference": ">  C1, C2", "Company Part Number": "-",
            "Description": "CAP", "Manufacturer": "Yageo", "Manufacturer Part Number": "MPN-1", "Quantity": 2}])
        mapping = pt.template_mapping(frame.columns)
        self.assertTrue(all(mapping.values()))
        with self.assertRaisesRegex(ValueError, "Quantity"):
            pt.template_mapping(frame.drop(columns=["Quantity"]).columns)
        line = pt.prepare_lines(frame, mapping, self.catalog)[0]
        self.assertEqual(line["component_id"], self.identity)
        self.assertEqual(line["references"], "C1, C2")

    def test_approved_revision_and_history(self):
        approved = dict(self.meta, status='Approved')
        revision = pt.save_bom(approved, self.lines, "BOM.xlsx")
        pt.attach_revision(dict(self.meta, project_code="P2"), revision)
        changed = [dict(self.lines[0], quantity=7)]
        with self.assertRaises(ValueError):
            pt.save_bom(approved, changed, "other.xlsx")
        self.assertEqual(pt.save_bom(approved, changed, "other.xlsx", replace=True), revision)
        self.assertEqual(pt.revision_for("PCB1", "A")["status"], 'Approved')
        self.assertEqual(len(pt.revision_lines(revision)), 1)
        self.assertEqual([r["total"] for r in pt.usage(self.identity)], [14, 14])
        pt.save_bom(dict(self.meta, revision="B"), self.lines, "BOM.xlsx")
        self.assertEqual(len(pt.usage(self.identity)), 2)
        self.assertEqual(len(pt.usage(self.identity, True)), 3)

    def test_failed_import_rolls_back_project_and_lines(self):
        with self.assertRaises(ValueError):
            pt.save_bom(self.meta, [dict(self.lines[0], quantity=float('nan'))], "BOM.xlsx")
        self.assertEqual(pt.rows("SELECT * FROM PT_Projects"), [])

    def test_duplicate_import_requires_explicit_replacement(self):
        revision = pt.save_bom(self.meta, self.lines, "BOM.xlsx")
        with self.assertRaises(ValueError):
            pt.save_bom(self.meta, self.lines, "BOM.xlsx")
        changed = [dict(self.lines[0], quantity=7)]
        self.assertEqual(pt.difference(revision, changed), (1, 1))
        pt.save_bom(self.meta, changed, "BOM.xlsx", replace=True)
        self.assertEqual(len(pt.revision_lines(revision)), 1)

    def test_move_and_delete_keep_identity_without_reusing_deleted_key(self):
        pt.save_bom(self.meta, self.lines, "BOM.xlsx")
        new_id = db.move_data_to_table("Resistor", "Other", {"Company_Part_Number": "CMP-1", "Manufacturer": "Yageo", "Manufacturer_Part_Number": "MPN-1"}, "id", 1)
        self.assertEqual(pt.component_catalog()[0]["id"], self.identity)
        self.assertEqual(len(pt.usage(self.identity)), 1)
        db.delete_data("Other", "id", new_id)
        self.assertIsNone(pt.usage(self.identity)[0]["company_stock"])
        db.insert_data("Other", {"Company_Part_Number": "CMP-NEW"})
        self.assertNotEqual(pt.component_catalog()[0]["id"], self.identity)

    def test_failed_move_rolls_back_component_stock_registry_and_audit(self):
        pt.save_bom(self.meta, self.lines, "BOM.xlsx")
        for table in ('Resistor', 'Other'):
            db.ensure_extra_columns_for_table(table)
        with db.connection(commit=True) as conn:
            db.ensure_change_log_table(conn)
        before = pt.rows('SELECT * FROM Resistor')
        registry = pt.rows('SELECT * FROM PT_Components')
        history = pt.rows('SELECT * FROM Change_Log')
        with db.connection(commit=True) as conn:
            conn.execute("CREATE TRIGGER prevent_move BEFORE DELETE ON Resistor "
                         "BEGIN SELECT RAISE(ABORT, 'move blocked'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'move blocked'):
            db.move_data_to_table('Resistor', 'Other', before[0], 'id', 1,
                                  expected_row=before[0])
        self.assertEqual(pt.rows('SELECT * FROM Resistor'), before)
        self.assertEqual(pt.rows('SELECT * FROM Other'), [])
        self.assertEqual(pt.rows('SELECT * FROM PT_Components'), registry)
        self.assertEqual(pt.rows('SELECT * FROM Change_Log'), history)
        self.assertEqual(len(pt.usage(self.identity)), 1)

    def test_matching_does_not_guess_duplicate_or_mpn_only(self):
        frame = pd.DataFrame([{"BR": "CMP-1", "MPN": "MPN-1", "Qty": "3", "Manufacturer": "Yageo"}])
        mapping = pt.suggest_mapping(frame.columns)
        line = pt.prepare_lines(frame, mapping, self.catalog)[0]
        self.assertEqual(line["component_id"], self.identity)
        duplicate = dict(self.catalog[0], id="second")
        self.assertIsNone(pt.prepare_lines(frame, mapping, self.catalog + [duplicate])[0]["component_id"])
        frame.loc[0, "BR"] = ""
        frame.loc[0, "Manufacturer"] = ""
        self.assertIsNone(pt.prepare_lines(frame, mapping, self.catalog)[0]["component_id"])

    def test_excel_sheet_and_header_mapping(self):
        path = os.path.join(self.temp.name, "BOM.xlsx")
        pd.DataFrame([{"BR": "CMP-1", "Qty": 2}]).to_excel(path, sheet_name="BOM", startrow=2, index=False)
        self.assertEqual(pt.workbook_sheets(path), ["BOM"])
        frame = pt.read_sheet(path, "BOM", 3)
        self.assertEqual(pt.prepare_lines(frame, pt.suggest_mapping(frame.columns), self.catalog)[0]["quantity"], 2)

    def test_missing_br_placeholders_fall_back_to_exact_manufacturer_mpn(self):
        catalog = [dict(self.catalog[0], br="-"), dict(self.catalog[0], id="other", br="-", mpn="OTHER-MPN")]
        for placeholder in ("-", "—", "N/A", "  --  ", ""):
            frame = pd.DataFrame([{"BR": placeholder, "MPN": "MPN-1", "Manufacturer": "Yageo", "Qty": 3}])
            line = pt.prepare_lines(frame, pt.suggest_mapping(frame.columns), catalog)[0]
            self.assertEqual(line["component_id"], self.identity)
            self.assertEqual(line["cmp"], "")
            self.assertEqual(line["match"], 'Manufacturer + MPN')
        frame.loc[0, "MPN"] = "UNKNOWN-MPN"
        self.assertIsNone(pt.prepare_lines(frame, pt.suggest_mapping(frame.columns), catalog)[0]["component_id"])
        frame.loc[0, "MPN"] = "-"
        with self.assertRaisesRegex(ValueError, "CMP or MPN is required"):
            pt.prepare_lines(frame, pt.suggest_mapping(frame.columns), catalog)

    def test_unmatched_can_be_linked_later(self):
        revision = pt.save_bom(self.meta, [dict(self.lines[0], component_id=None)], "BOM.xlsx")
        pt.rematch_line(pt.revision_lines(revision)[0]["id"], self.identity)
        self.assertEqual(len(pt.usage(self.identity)), 1)

    def test_existing_approved_match_can_be_updated(self):
        revision = pt.save_bom(dict(self.meta, status='Approved'), self.lines, "BOM.xlsx")
        db.insert_data("Resistor", {"Company_Part_Number": "CMP-2", "Manufacturer_Part_Number": "MPN-2"})
        identity = next(p["id"] for p in pt.component_catalog() if p["cmp"] == "CMP-2")
        before = pt.revision_lines(revision)[0]
        pt.rematch_line(before["id"], identity)
        after = pt.revision_lines(revision)[0]
        self.assertEqual(after, dict(before, component_id=identity))
        self.assertEqual(pt.usage(self.identity), [])
        self.assertEqual(pt.usage(identity)[0]["total"], 6)
        self.assertEqual(pt.revision_for("PCB1", "A")["status"], 'Approved')
        with self.assertRaises(ValueError):
            pt.rematch_line(-1, identity)
        with self.assertRaises(ValueError):
            pt.rematch_line(before["id"], "missing")
        self.assertEqual(pt.revision_lines(revision)[0]["component_id"], identity)

    def test_multiline_description_and_legacy_status_migration(self):
        revision = pt.save_bom(dict(self.meta, status="Approved"), self.lines, "BOM.xlsx")
        description = "Assembly notes\nUse approved alternatives only.\nSecond production run."
        pt.update_bom_description(revision, description)
        self.assertEqual(pt.revision_for("PCB1", "A")["notes"], description)
        self.assertEqual(pt.revision_for("PCB1", "A")["status"], "Approved")
        with db.connection(commit=True) as conn:
            conn.execute("UPDATE PT_Revisions SET status=? WHERE id=?", ("Onaylı", revision))
        pt.ensure_schema()
        self.assertEqual(pt.revision_for("PCB1", "A")["status"], "Approved")
        self.assertEqual(pt.revision_for("PCB1", "A")["notes"], description)

    def test_only_active_admin_can_approve_or_import_approved_boms(self):
        revision = pt.save_bom(self.meta, self.lines, 'BOM.xlsx')
        before = pt.revision_for('PCB1', 'A')
        for actor in ('engineer', 'inactive', '', 'missing'):
            with self.subTest(actor=actor):
                db.set_current_user(actor)
                self.assertFalse(pt.can_approve_bom())
                with self.assertRaises(PermissionError):
                    pt.approve_bom(revision)
                with self.assertRaises(PermissionError):
                    pt.save_bom(dict(self.meta, status='Approved', project_code='NEW', board_number='NEW'),
                                self.lines, 'new.xlsx')
                with self.assertRaises(PermissionError):
                    pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'other.xlsx', replace=True)
                self.assertEqual(pt.revision_for('PCB1', 'A'), before)
                self.assertEqual(len(pt.list_projects()), 1)
                self.assertIsNone(pt.revision_for('NEW', 'A'))
        with db.connection(commit=True) as conn:
            conn.execute('DROP TABLE Users')
        db.set_current_user('admin')
        with self.assertRaises(PermissionError):
            pt.approve_bom(revision)

    def test_bom_approval_records_account_and_time_for_all_project_links(self):
        revision = pt.save_bom(dict(self.meta, approved_by='forged', approved_at='forged'), self.lines, 'BOM.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2'), revision)
        before_lines = pt.revision_lines(revision)
        imported_at = pt.revision_for('PCB1', 'A')['imported_at']
        with patch.object(db, 'now_text', return_value='2026-10-09 12:34:56'):
            pt.approve_bom(revision)
        approved = pt.revision_for('PCB1', 'A')
        self.assertEqual((approved['status'], approved['approved_by'], approved['approved_at']),
                         ('Approved', 'admin', '2026-10-09 12:34:56'))
        self.assertEqual(approved['imported_at'], imported_at)
        self.assertEqual(pt.revision_lines(revision), before_lines)
        self.assertEqual([row['status'] for row in pt.usage(self.identity)], ['Approved', 'Approved'])
        log = pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_APPROVE'")[0]
        self.assertEqual(log['Changed_By'], 'admin')
        self.assertEqual(log['Record_Key'], str(revision))
        other = pt.save_bom(dict(self.meta, revision='B', status='Approved', approved_by='forged'), self.lines, 'B.xlsx')
        self.assertEqual(pt.revision_for('PCB1', 'B')['approved_by'], 'admin')
        db.set_current_user('engineer')
        pt.attach_revision(dict(self.meta, project_code='P3', status='Draft'), other)
        self.assertEqual(pt.revision_for('PCB1', 'B')['status'], 'Approved')

    def test_bom_approval_checks_current_role_and_active_state(self):
        revision = pt.save_bom(self.meta, self.lines, 'BOM.xlsx')
        for role, active in [('user', 1), ('admin', 0)]:
            with db.connection(commit=True) as conn:
                conn.execute("UPDATE Users SET Role=?,Is_Active=? WHERE Username='admin'", (role, active))
            self.assertFalse(pt.can_approve_bom())
            with self.assertRaises(PermissionError):
                pt.approve_bom(revision)
            with self.assertRaises(PermissionError):
                pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'BOM.xlsx', replace=True)
        self.assertEqual(pt.revision_for('PCB1', 'A')['status'], 'Draft')

    def test_bom_content_changes_preserve_approval_and_are_audited(self):
        revision = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'BOM.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2'), revision)
        original = pt.revision_for('PCB1', 'A')
        pt.update_bom_description(revision, original['notes'])
        pt.rematch_line(pt.revision_lines(revision)[0]['id'], self.identity)
        self.assertEqual(pt.revision_for('PCB1', 'A'), original)
        pt.update_bom_description(revision, 'Edited by an administrator')
        edited = pt.revision_for('PCB1', 'A')
        approval = {key: original[key] for key in ('status', 'approved_by', 'approved_at')}
        self.assertEqual({key: edited[key] for key in approval}, approval)
        self.assertEqual([row['status'] for row in pt.usage(self.identity)], ['Approved', 'Approved'])
        log = pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_DESCRIPTION_UPDATE'")[0]
        self.assertEqual(log['Changed_By'], 'admin')
        db.insert_data('Resistor', {'Company_Part_Number': 'CMP-NEW'})
        new_part = next(p for p in pt.component_catalog() if p['cmp'] == 'CMP-NEW')
        pt.rematch_line(pt.revision_lines(revision)[0]['id'], new_part['id'])
        self.assertEqual({key: pt.revision_for('PCB1', 'A')[key] for key in approval}, approval)
        self.assertEqual(len(pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_MATCH_UPDATE'")), 1)
        pt.save_bom(self.meta, [dict(self.lines[0], quantity=9)], 'changed.xlsx', replace=True)
        self.assertEqual({key: pt.revision_for('PCB1', 'A')[key] for key in approval}, approval)
        self.assertEqual(len(pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_REPLACE'")), 1)
        self.assertEqual(pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_APPROVAL_RESET'"), [])
        self.assertEqual(pt.revision_lines(revision)[0]['quantity'], 9)

    def test_user_cannot_modify_approved_bom_through_any_edit_path(self):
        revision = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'BOM.xlsx')
        db.insert_data('Resistor', {'Company_Part_Number': 'CMP-NEW'})
        identity = next(p['id'] for p in pt.component_catalog() if p['cmp'] == 'CMP-NEW')
        line_id = pt.revision_lines(revision)[0]['id']
        def snapshot():
            return {table: pt.rows(f'SELECT * FROM {table}')
                    for table in (*pt.TABLES, 'Change_Log')}
        before = snapshot()
        db.set_current_user('engineer')
        actions = [
            lambda: pt.update_bom_description(revision, 'Changed'),
            lambda: pt.rematch_line(line_id, identity),
            lambda: pt.save_bom(dict(self.meta, project_name='Changed project'),
                                [dict(self.lines[0], quantity=8)], 'changed.xlsx', replace=True),
            lambda: pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'changed.xlsx', replace=True),
            lambda: pt.attach_revision(dict(self.meta, board_quantity=99), revision),
            lambda: pt.save_bom(dict(self.meta, revision='B', board_name='Renamed shared board'), self.lines, 'B.xlsx'),
        ]
        for action in actions:
            with self.assertRaises(PermissionError):
                action()
            self.assertEqual(snapshot(), before)
        self.assertFalse(pt.bom_permissions(revision)['edit'])
        new_revision = pt.save_bom(dict(self.meta, revision='B'), self.lines, 'B.xlsx')
        self.assertEqual(pt.revision_for('PCB1', 'A'), before['PT_Revisions'][0])
        self.assertEqual(pt.revision_for('PCB1', 'B')['status'], 'Draft')
        self.assertTrue(pt.bom_permissions(new_revision)['edit'])
        self.assertEqual(len(pt.project_tree(True)), 2)

    def test_user_can_edit_drafts_but_cannot_delete_approved_or_mixed_bom_links(self):
        approved = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'A.xlsx')
        db.insert_data('Resistor', {'Company_Part_Number': 'CMP-NEW'})
        identity = next(p['id'] for p in pt.component_catalog() if p['cmp'] == 'CMP-NEW')
        db.set_current_user('engineer')
        draft_meta = dict(self.meta, revision='B')
        draft = pt.save_bom(draft_meta, self.lines, 'B.xlsx')
        pt.save_bom(draft_meta, [dict(self.lines[0], quantity=9)], 'B2.xlsx', replace=True)
        pt.update_bom_description(draft, 'Draft notes')
        pt.rematch_line(pt.revision_lines(draft)[0]['id'], identity)
        pt.attach_revision(dict(draft_meta, board_quantity=4), draft)
        self.assertEqual(pt.revision_for('PCB1', 'B')['notes'], 'Draft notes')
        self.assertEqual(pt.revision_lines(draft)[0]['quantity'], 9)
        self.assertEqual(pt.usage(identity)[0]['total'], 36)
        self.assertTrue(pt.bom_permissions(draft)['edit'])
        self.assertTrue(pt.bom_permissions(draft)['delete'])
        project = pt.list_projects()[0]['id']
        board = pt.revision_for('PCB1', 'B')['board_id']
        before = pt.project_tree(True)
        for actor in ('engineer', 'inactive', '', 'missing'):
            db.set_current_user(actor)
            for action in (
                lambda: pt.detach_project_item(project, revision_id=approved),
                lambda: pt.detach_project_item(project, board_id=board),
                lambda: pt.delete_project(project),
            ):
                with self.assertRaises(PermissionError):
                    action()
                self.assertEqual(pt.project_tree(True), before)
            if actor != 'engineer':
                with self.assertRaises(PermissionError):
                    pt.detach_project_item(project, revision_id=draft)
        db.set_current_user('admin')
        pt.detach_project_item(project, revision_id=approved)
        self.assertEqual([r['id'] for r in pt.project_tree(True)], [draft])
        pt.detach_project_item(project, revision_id=draft)
        self.assertEqual(pt.project_tree(True), [])
        self.assertEqual(len(pt.revision_lines(approved)), 1)

    def test_draft_write_permissions_follow_live_account_and_approval_changes(self):
        revision = pt.save_bom(self.meta, self.lines, 'BOM.xlsx')
        line_id = pt.revision_lines(revision)[0]['id']
        for actor in ('inactive', '', 'missing'):
            db.set_current_user(actor)
            self.assertFalse(pt.bom_permissions(revision)['add'])
            self.assertFalse(pt.bom_permissions(revision)['edit'])
            for action in (
                lambda: pt.save_bom(dict(self.meta, revision='NEW'), self.lines, 'new.xlsx'),
                lambda: pt.update_bom_description(revision, 'Changed'),
                lambda: pt.rematch_line(line_id, self.identity),
                lambda: pt.attach_revision(dict(self.meta, project_code='NEW'), revision),
            ):
                with self.assertRaises(PermissionError):
                    action()
        self.assertEqual(len(pt.list_projects()), 1)
        self.assertIsNone(pt.revision_for('PCB1', 'NEW'))
        db.set_current_user('engineer')
        self.assertTrue(pt.bom_permissions(revision)['edit'])
        db.set_current_user('admin')
        pt.approve_bom(revision)
        db.set_current_user('engineer')
        with self.assertRaises(PermissionError):
            pt.update_bom_description(revision, 'Prepared before approval')
        with self.assertRaises(PermissionError):
            pt.save_bom(self.meta, self.lines, 'stale.xlsx', replace=True)
        self.assertEqual(pt.revision_for('PCB1', 'A')['status'], 'Approved')
        self.assertEqual(pt.revision_for('PCB1', 'A')['notes'], 'Note')

    def test_admin_can_return_shared_approved_bom_to_draft_without_content_changes(self):
        revision = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'BOM.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2'), revision)
        before = pt.revision_for('PCB1', 'A')
        lines = pt.revision_lines(revision)
        for actor in ('engineer', 'inactive', '', 'missing'):
            db.set_current_user(actor)
            with self.assertRaises(PermissionError):
                pt.return_bom_to_draft(revision)
            self.assertEqual(pt.revision_for('PCB1', 'A'), before)
        db.set_current_user('admin')
        pt.return_bom_to_draft(revision)
        self.assertEqual(pt.revision_for('PCB1', 'A'), dict(before, status='Draft', approved_by='', approved_at=''))
        self.assertEqual(pt.revision_lines(revision), lines)
        self.assertEqual([r['status'] for r in pt.project_tree()], ['Draft', 'Draft'])
        log = pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_APPROVAL_RESET'")
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]['Changed_By'], 'admin')
        pt.return_bom_to_draft(revision)
        self.assertEqual(pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_APPROVAL_RESET'"), log)
        db.set_current_user('engineer')
        self.assertTrue(pt.bom_permissions(revision)['edit'])
        self.assertTrue(pt.bom_permissions(revision)['delete'])

    def test_user_deletes_draft_bom_with_last_link_only_and_preserves_other_data(self):
        draft = pt.save_bom(self.meta, self.lines, 'A.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2'), draft)
        approved = pt.save_bom(dict(self.meta, revision='B', status='Approved'), self.lines, 'B.xlsx')
        projects = {p['code']: p['id'] for p in pt.list_projects()}
        board = pt.revision_for('PCB1', 'A')['board_id']
        db.set_current_user('engineer')
        self.assertFalse(pt.can_delete_project_item(projects['P1'], board_id=board))
        self.assertTrue(pt.can_delete_project_item(projects['P1'], revision_id=draft))
        before = pt.project_tree(True)
        with self.assertRaises(PermissionError):
            pt.detach_project_item(projects['P1'], board_id=board, purge_unused=True)
        self.assertEqual(pt.project_tree(True), before)
        pt.detach_project_item(projects['P1'], revision_id=draft, purge_unused=True)
        self.assertEqual(len(pt.revision_lines(draft)), 1)
        self.assertEqual([r['code'] for r in pt.project_tree(True) if r['id'] == draft], ['P2'])
        pt.detach_project_item(projects['P2'], board_id=board, purge_unused=True)
        self.assertIsNone(pt.revision_for('PCB1', 'A'))
        self.assertEqual(pt.revision_lines(draft), [])
        self.assertEqual(len(pt.revision_lines(approved)), 1)
        self.assertEqual(len(pt.list_projects()), 2)
        self.assertEqual(db.get_total_material_count(), 1)
        self.assertEqual(len(pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_DELETE' AND Changed_By='engineer'")), 2)

    def test_delete_checks_current_approval_and_account_inside_transaction(self):
        draft = pt.save_bom(self.meta, self.lines, 'A.xlsx')
        project = pt.list_projects()[0]['id']
        db.set_current_user('engineer')
        self.assertTrue(pt.can_delete_project_item(project, revision_id=draft))
        db.set_current_user('admin')
        pt.approve_bom(draft)
        before = pt.project_tree(True)
        for actor in ('engineer', 'inactive', '', 'missing'):
            db.set_current_user(actor)
            self.assertFalse(pt.can_delete_project_item(project, revision_id=draft))
            with self.assertRaises(PermissionError):
                pt.detach_project_item(project, revision_id=draft, purge_unused=True)
            self.assertEqual(pt.project_tree(True), before)

    def test_saved_bom_editor_updates_content_and_selected_quantity_without_reimport(self):
        revision = pt.save_bom(self.meta, self.lines, 'Original.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2', board_quantity=4), revision)
        project = pt.list_projects()[0]['id']
        # Editing a previous revision must not activate it in this project.
        pt.save_bom(dict(self.meta, revision='B'), self.lines, 'B.xlsx')
        snapshot = pt.bom_edit_snapshot(project, revision)
        db.set_current_user('engineer')
        updated = [dict(self.lines[0], quantity=5, references='R1-R5', description='Updated resistor'),
                   dict(self.lines[0], br='NEW', mpn='NEW', component_id=None, quantity=1, references='U1')]
        pt.update_saved_bom(snapshot, updated, 'First line\nSecond line', 3)
        saved = pt.bom_edit_snapshot(project, revision)
        self.assertEqual((saved['active'], saved['board_quantity'], saved['notes']), (0, 3, 'First line\nSecond line'))
        self.assertEqual([r['quantity'] for r in saved['lines']], [5, 1])
        self.assertEqual((saved['source'], saved['imported_by'], saved['imported_at']),
                         (snapshot['source'], snapshot['imported_by'], snapshot['imported_at']))
        self.assertEqual([r['board_quantity'] for r in pt.project_tree(True) if r['id'] == revision], [3, 4])
        self.assertEqual(pt.rows("SELECT Changed_By FROM Change_Log WHERE Action='BOM_EDIT'")[0]['Changed_By'], 'engineer')
        pt.update_saved_bom(saved, [], 'Empty draft', 3)
        self.assertEqual(pt.revision_lines(revision), [])
        db.set_current_user('admin')
        with self.assertRaisesRegex(ValueError, 'empty BOM'):
            pt.approve_bom(revision)

    def test_saved_bom_editor_rejects_stale_invalid_and_unauthorized_updates(self):
        revision = pt.save_bom(self.meta, self.lines, 'Original.xlsx')
        project = pt.list_projects()[0]['id']
        snapshot = pt.bom_edit_snapshot(project, revision)
        pt.update_bom_description(revision, 'Changed elsewhere')
        db.set_current_user('engineer')
        with self.assertRaisesRegex(ValueError, 'changed while'):
            pt.update_saved_bom(snapshot, self.lines, 'Stale', 1)
        snapshot = pt.bom_edit_snapshot(project, revision)
        for lines, quantity in [([dict(self.lines[0], quantity=0)], 1), (self.lines, 'NaN'),
                                ([dict(self.lines[0], br='', mpn='')], 1),
                                ([dict(self.lines[0], component_id='missing')], 1)]:
            with self.assertRaises(ValueError):
                pt.update_saved_bom(snapshot, lines, 'Invalid', quantity)
            self.assertEqual(pt.bom_edit_snapshot(project, revision), snapshot)
        for actor in ('inactive', '', 'missing'):
            db.set_current_user(actor)
            with self.assertRaises(PermissionError):
                pt.update_saved_bom(snapshot, self.lines, 'No', 1)
        db.set_current_user('admin')
        pt.approve_bom(revision)
        approved = pt.bom_edit_snapshot(project, revision)
        db.set_current_user('engineer')
        with self.assertRaises(PermissionError):
            pt.update_saved_bom(snapshot, self.lines, 'Stale approval', 1)
        with self.assertRaises(PermissionError):
            pt.update_saved_bom(approved, self.lines, 'No', 1)
        db.set_current_user('admin')
        pt.update_saved_bom(approved, approved['lines'], approved['notes'], approved['board_quantity'])
        self.assertEqual(pt.bom_edit_snapshot(project, revision), approved)
        pt.update_saved_bom(approved, self.lines, 'Admin edit', 1)
        saved = pt.bom_edit_snapshot(project, revision)
        self.assertEqual(tuple(saved[key] for key in ('status', 'approved_by', 'approved_at')),
                         tuple(approved[key] for key in ('status', 'approved_by', 'approved_at')))

    def test_import_into_selected_revision_preserves_definitions_links_and_notes(self):
        revision = pt.save_bom(self.meta, self.lines, 'Original.xlsx')
        pt.attach_revision(dict(self.meta, project_code='P2', board_quantity=4), revision)
        project = next(p['id'] for p in pt.list_projects() if p['code'] == 'P1')
        current = pt.save_bom(dict(self.meta, revision='B', status='Approved'), self.lines, 'B.xlsx')
        snapshot = pt.bom_edit_snapshot(project, revision)
        definitions = {t: pt.rows(f'SELECT * FROM {t}') for t in ('PT_Projects', 'PT_Boards', 'PT_ProjectBoards')}
        db.set_current_user('engineer')
        self.assertEqual(pt.import_bom_into_revision(snapshot, [dict(self.lines[0], quantity=8)], 'Updated.xlsx'), revision)
        saved = pt.bom_edit_snapshot(project, revision)
        self.assertEqual((saved['notes'], saved['board_quantity'], saved['active']), ('Note', 2, 0))
        self.assertEqual((saved['status'], saved['imported_by'], saved['source']),
                         ('Draft', 'engineer', os.path.abspath('Updated.xlsx')))
        self.assertEqual(pt.revision_lines(revision)[0]['quantity'], 8)
        self.assertEqual(pt.revision_lines(current)[0]['quantity'], 3)
        self.assertEqual({t: pt.rows(f'SELECT * FROM {t}') for t in definitions}, definitions)
        self.assertEqual(pt.rows("SELECT Changed_By FROM Change_Log WHERE Action='BOM_IMPORT'")[0]['Changed_By'], 'engineer')
        db.set_current_user('admin')
        approved = pt.bom_edit_snapshot(project, current)
        pt.import_bom_into_revision(approved, self.lines, 'Replacement.xlsx')
        saved = pt.bom_edit_snapshot(project, current)
        self.assertEqual(tuple(saved[key] for key in ('status', 'approved_by', 'approved_at')),
                         tuple(approved[key] for key in ('status', 'approved_by', 'approved_at')))

    def test_import_into_selected_revision_rechecks_permissions_and_rejects_stale_targets(self):
        revision = pt.save_bom(self.meta, self.lines, 'Original.xlsx')
        project = pt.list_projects()[0]['id']
        snapshot = pt.bom_edit_snapshot(project, revision)
        for actor in ('inactive', '', 'missing'):
            db.set_current_user(actor)
            with self.assertRaises(PermissionError):
                pt.import_bom_into_revision(snapshot, self.lines, 'No.xlsx')
        db.set_current_user('engineer')
        with self.assertRaises(ValueError):
            pt.import_bom_into_revision(snapshot, [], 'No.xlsx')
        with self.assertRaises(ValueError):
            pt.import_bom_into_revision(snapshot, [dict(self.lines[0], component_id='missing')], 'No.xlsx')
        self.assertEqual(pt.bom_edit_snapshot(project, revision), snapshot)
        db.set_current_user('admin')
        pt.approve_bom(revision)
        db.set_current_user('engineer')
        with self.assertRaises(PermissionError):
            pt.import_bom_into_revision(snapshot, self.lines, 'Stale.xlsx')
        db.set_current_user('admin')
        with self.assertRaisesRegex(ValueError, 'changed while'):
            pt.import_bom_into_revision(snapshot, self.lines, 'Stale.xlsx')
        pt.detach_project_item(project, revision_id=revision, purge_unused=True)
        with self.assertRaises(ValueError):
            pt.import_bom_into_revision(snapshot, self.lines, 'Deleted.xlsx')
        self.assertIsNone(pt.revision_for('PCB1', 'A'))

    def test_link_saved_card_to_existing_project_keeps_approval_and_checks_permissions(self):
        revision = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'Original.xlsx')
        project = pt.save_project('P2', 'Second project')
        original = pt.revision_for('PCB1', 'A')
        db.set_current_user('engineer')
        pt.link_card_revision(project, revision, 4)
        self.assertEqual(pt.revision_for('PCB1', 'A'), original)
        self.assertEqual(pt.bom_edit_snapshot(project, revision)['board_quantity'], 4)
        with self.assertRaises(PermissionError):
            pt.link_card_revision(project, revision, 5)
        with self.assertRaises(ValueError):
            pt.link_card_revision(-1, revision)
        for actor in ('inactive', '', 'missing'):
            db.set_current_user(actor)
            with self.assertRaises(PermissionError):
                pt.link_card_revision(project, revision, 4)

    def test_invalid_bom_edits_do_not_change_approval_or_audit(self):
        revision = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'BOM.xlsx')
        before = pt.revision_for('PCB1', 'A')
        before_lines = pt.revision_lines(revision)
        logs = pt.rows('SELECT * FROM Change_Log')
        with self.assertRaises(ValueError):
            pt.save_bom(self.meta, [dict(self.lines[0], quantity=-1)], 'bad.xlsx', replace=True)
        with self.assertRaises(ValueError):
            pt.rematch_line(before_lines[0]['id'], 'missing')
        with self.assertRaises(ValueError):
            pt.approve_bom(-1)
        self.assertEqual(pt.revision_for('PCB1', 'A'), before)
        self.assertEqual(pt.revision_lines(revision), before_lines)
        self.assertEqual(pt.rows('SELECT * FROM Change_Log'), logs)
        with db.connection(commit=True) as conn:
            conn.execute('DELETE FROM PT_Lines WHERE revision_id=?', (revision,))
        with self.assertRaisesRegex(ValueError, 'empty BOM'):
            pt.approve_bom(revision)

    def test_approval_schema_migration_keeps_existing_records_without_inventing_approver(self):
        revision = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'BOM.xlsx')
        with db.connection(commit=True) as conn:
            conn.execute('ALTER TABLE PT_Revisions DROP COLUMN approved_by')
            conn.execute('ALTER TABLE PT_Revisions DROP COLUMN approved_at')
        pt.ensure_schema()
        pt.ensure_schema()
        migrated = pt.revision_for('PCB1', 'A')
        self.assertEqual((migrated['status'], migrated['approved_by'], migrated['approved_at']), ('Approved', '', ''))
        pt.approve_bom(revision)
        self.assertEqual(pt.revision_for('PCB1', 'A')['approved_by'], 'admin')

    def test_compare_revisions_changes_additions_removals_and_metadata(self):
        a_lines = self.lines + [dict(self.lines[0], br="OLD", mpn="OLD", references="U1", component_id=None)]
        a = pt.save_bom(self.meta, a_lines, "A.xlsx")
        b_lines = [dict(self.lines[0], quantity=5, references="R1,R2,R3,R4,R5"),
                   dict(self.lines[0], br="NEW", mpn="NEW", references="C1", component_id=None)]
        b = pt.save_bom(dict(self.meta, revision="B", notes="Changed description"), b_lines, "B.xlsx")
        comparison = pt.compare_revisions(a, b)
        self.assertEqual(sorted(c["kind"] for c in comparison["changes"]), ["Added", "Changed", "Removed"])
        changed = next(c for c in comparison["changes"] if c["kind"] == "Changed")
        self.assertEqual({f[0] for f in changed["fields"]}, {"Quantity", "References"})
        self.assertEqual(comparison["metadata"], [("BOM description", "Note", "Changed description")])
        c = pt.save_bom(dict(self.meta, revision="C", notes="Changed description"), list(reversed(b_lines)), "C.xlsx")
        self.assertEqual(pt.compare_revisions(b, c)["changes"], [])
        other = pt.save_bom(dict(self.meta, board_number="OTHER"), self.lines, "Other.xlsx")
        with self.assertRaisesRegex(ValueError, "same board"):
            pt.compare_revisions(a, other)

    def test_comparison_detects_replacement_at_same_references(self):
        a = pt.save_bom(self.meta, self.lines, "A.xlsx")
        b = pt.save_bom(dict(self.meta, revision="B"), [dict(self.lines[0], mpn="NEW-MPN", component_id=None)], "B.xlsx")
        comparison = pt.compare_revisions(a, b)
        self.assertEqual(len(comparison["changes"]), 1)
        self.assertEqual({f[0] for f in comparison["changes"][0]["fields"]}, {"MPN", "Database match"})

    def test_multiselect_clipboard_preserves_columns_and_display_order(self):
        import csv
        import io
        import tkinter as tk
        from ui.product_tree_window import table, copy_selected_rows
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        try:
            view = table(root, [("code", "Kod", 100), ("qty", 'Quantity', 70)])
            view.insert("", "end", iid="a", values=("CMP-1", 0))
            view.insert("", "end", iid="b", values=("CMP-2", 12))
            view.insert("", "end", iid="c", values=("Not\tçok\nsatırlı", 3))
            view.selection_set(("c", "a"))
            self.assertEqual(str(view.cget("selectmode")), "extended")
            self.assertTrue(view.bind("<Control-c>"))
            self.assertEqual(copy_selected_rows(view), "break")
            copied = list(csv.reader(io.StringIO(root.clipboard_get()), delimiter="\t"))
            self.assertEqual(copied, [["CMP-1", "0"], ["Not\tçok\nsatırlı", "3"]])
            root.deiconify()
            root.update()
            view.focus_force()
            view.event_generate("<Control-a>")
            root.update()
            self.assertEqual(set(view.selection()), {"a", "b", "c"})
        finally:
            root.destroy()

    def test_window_bom_approval_permissions_and_saved_revision_workflow(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        revision = pt.save_bom(self.meta, self.lines, 'BOM.xlsx')
        before = pt.revision_lines(revision)
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 8
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            root.update()
            self.assertFalse(window.busy)
        try:
            wait_job()
            window.tree.selection_set(next(node for node, row in window.tree_rows.items() if row['id'] == revision))
            window.select_revision()
            self.assertEqual(str(window.approve_button.cget('state')), 'normal')
            self.assertEqual(str(window.add_bom_button.cget('state')), 'normal')
            window.bom_description.insert('end', ' unsaved')
            with patch('ui.product_tree_window.messagebox.showinfo') as notice:
                window.approve_bom()
            notice.assert_called_once()
            self.assertEqual(pt.revision_for('PCB1', 'A')['status'], 'Draft')
            window.select_revision()
            with patch('ui.product_tree_window.messagebox.askyesno', return_value=True):
                window.approve_button.invoke()
            wait_job()
            self.assertEqual(pt.revision_for('PCB1', 'A')['status'], 'Approved')
            self.assertEqual(pt.revision_lines(revision), before)
            self.assertIn('admin', window.detail.get())
            self.assertEqual(str(window.approve_button.cget('state')), 'disabled')
            with db.connection(commit=True) as conn:
                conn.execute("UPDATE Users SET Role='user' WHERE Username='admin'")
            window.apply_bom_permissions()
            self.assertEqual(str(window.add_bom_button.cget('state')), 'disabled')
            with patch('ui.product_tree_window.messagebox.showerror') as error:
                window.approve_bom()
            error.assert_called_once()
            for button in (window.description_save, window.repair_match_button, window.delete_button):
                self.assertEqual(str(button.cget('state')), 'disabled')
            self.assertEqual(str(window.bom_description.cget('state')), 'disabled')
            self.assertEqual(str(window.add_revision_button.cget('state')), 'normal')
            with patch('ui.product_tree_window.messagebox.showerror') as error:
                window.save_description()
                window.delete_project()
            self.assertEqual(error.call_count, 2)
            self.assertEqual(pt.revision_for('PCB1', 'A')['status'], 'Approved')
            self.assertEqual(str(window.approve_button.cget('state')), 'disabled')
            with db.connection(commit=True) as conn:
                conn.execute("UPDATE Users SET Role='admin' WHERE Username='admin'")
            window.apply_bom_permissions()
            self.assertEqual(str(window.delete_button.cget('state')), 'normal')
            self.assertEqual(str(window.repair_match_button.cget('state')), 'normal')
            window.bom_description.insert('end', ' changed')
            window.save_description()
            wait_job()
            self.assertEqual(pt.revision_for('PCB1', 'A')['status'], 'Approved')
            self.assertEqual(str(window.approve_button.cget('state')), 'disabled')
            with patch('ui.product_tree_window.messagebox.askyesno', return_value=True):
                window.draft_button.invoke()
            wait_job()
            db.set_current_user('engineer')
            window.apply_bom_permissions()
            self.assertEqual(str(window.description_save.cget('state')), 'normal')
            self.assertEqual(str(window.repair_match_button.cget('state')), 'normal')
            self.assertEqual(str(window.approve_button.cget('state')), 'disabled')
            self.assertEqual(str(window.delete_button.cget('state')), 'normal')
            window.bom_description.insert('end', ' draft edit')
            window.save_description()
            wait_job()
            self.assertTrue(pt.revision_for('PCB1', 'A')['notes'].endswith(' draft edit'))
            # Changing the selection to a project prevents accidental approval.
            window.tree.selection_set(window.tree.get_children()[0])
            window.select_revision()
            self.assertEqual(str(window.approve_button.cget('state')), 'disabled')
        finally:
            wait_job()
            window.close()
            root.destroy()

    def test_window_draft_bom_editor_delete_and_admin_return_to_draft(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        from ui.bom_edit_window import BomEditWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        revision = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'A.xlsx')
        other = pt.save_bom(dict(self.meta, revision='B', status='Approved'), self.lines, 'B.xlsx')
        project = pt.list_projects()[0]['id']
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 20
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            root.update()
            self.assertFalse(window.busy)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        def button(widget, text):
            return next(w for w in descendants(widget) if w.winfo_class() == 'TButton' and w.cget('text') == text)
        def editor():
            window.edit_button.invoke()
            wait_job()
            return next(w for w in window.winfo_children() if isinstance(w, BomEditWindow))
        try:
            with patch('tkinter.messagebox.showerror') as error, patch('tkinter.messagebox.askyesno', return_value=True):
                wait_job()
                window.tree.selection_set(f'p{project}r{revision}')
                window.select_revision()
                self.assertEqual(str(window.draft_button.cget('state')), 'normal')
                before = pt.revision_lines(revision)
                window.draft_button.invoke()
                wait_job()
                self.assertEqual(pt.revision_lines(revision), before)
                self.assertEqual(pt.revision_for('PCB1', 'A')['status'], 'Draft')
                self.assertEqual(str(window.draft_button.cget('state')), 'disabled')
                db.set_current_user('engineer')
                window.apply_bom_permissions()
                for control in (window.edit_button, window.delete_button):
                    self.assertEqual(str(control.cget('state')), 'normal')
                self.assertEqual(window.edit_button.cget('text'), 'Edit BOM')
                self.assertEqual(str(window.approve_button.cget('state')), 'disabled')
                dialog = editor()
                dialog.view.selection_set('0')
                dialog.edit_row()
                row_dialog = next(w for w in dialog.winfo_children() if isinstance(w, tk.Toplevel))
                row_dialog.fields['quantity'].set('5')
                row_dialog.fields['references'].set('R1-R5')
                row_dialog.description.delete('1.0', tk.END)
                row_dialog.description.insert('1.0', 'Edited\nresistor')
                button(row_dialog, 'Apply row').invoke()
                self.assertEqual(dialog.records[0]['component_id'], self.identity)
                dialog.add_row()
                row_dialog = next(w for w in dialog.winfo_children() if isinstance(w, tk.Toplevel))
                row_dialog.fields['cmp'].set('NEW')
                row_dialog.fields['references'].set('U1')
                button(row_dialog, 'Apply row').invoke()
                self.assertEqual(len(dialog.records), 2)
                self.assertIsNone(dialog.records[1]['component_id'])
                self.assertEqual(pt.revision_lines(revision), before)
                dialog.quantity.set('3')
                dialog.notes.insert('end', '\nEdited BOM')
                dialog.save_button.invoke()
                wait_job()
                self.assertEqual([r['quantity'] for r in pt.revision_lines(revision)], [5, 1])
                self.assertEqual(pt.revision_lines(revision)[0]['description'], 'Edited\nresistor')
                self.assertEqual(pt.bom_edit_snapshot(project, revision)['board_quantity'], 3)
                dialog = editor()
                dialog.view.selection_set('1')
                dialog.remove_rows()
                dialog.save_button.invoke()
                wait_job()
                self.assertEqual(len(pt.revision_lines(revision)), 1)
                # Cancelled in-memory edits must not change the saved BOM.
                dialog = editor()
                dialog.view.selection_set('0')
                dialog.remove_rows()
                dialog.cancel()
                self.assertEqual(len(pt.revision_lines(revision)), 1)
                window.tree.selection_set(f'p{project}r{other}')
                window.select_revision()
                for control in (window.edit_button, window.delete_button, window.draft_button):
                    self.assertEqual(str(control.cget('state')), 'disabled')
                window.tree.selection_set(f'p{project}b{pt.revision_for("PCB1", "B")["board_id"]}')
                window.select_revision()
                self.assertEqual(str(window.delete_button.cget('state')), 'disabled')
                window.tree.selection_set(f'p{project}r{revision}')
                window.select_revision()
                window.delete_button.invoke()
                wait_job()
                self.assertIsNone(pt.revision_for('PCB1', 'A'))
                self.assertEqual(pt.revision_lines(revision), [])
                self.assertTrue(window.tree.exists(f'p{project}r{other}'))
                self.assertTrue(window.tree.exists(f'p{project}'))
                error.assert_not_called()
        finally:
            wait_job()
            window.close()
            root.destroy()


    def test_window_add_bom_imports_file_into_captured_card_and_handles_replacement(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        original = pt.save_bom(dict(self.meta, status='Approved'), self.lines, 'Original.xlsx')
        project = pt.list_projects()[0]['id']
        top = pt.save_project('TOP', 'Top project')
        pt.save_project('P1', 'Project', project_id=project, parent_id=top, folder=self.temp.name)
        board = pt.revision_for('PCB1', 'A')['board_id']
        target = pt.add_card_revision(project, board, 'B', 2)
        other = pt.create_card(project, 'OTHER', 'Other card', 'A')
        path = os.path.join(self.temp.name, 'BOM.xlsx')
        frame = pd.DataFrame([{'Company Part Number': 'CMP-1', 'Quantity': 7, 'Manufacturer Part Number': 'MPN-1',
                              'Manufacturer': 'Yageo', 'Description': 'New resistor', 'Reference': 'R1-R7'}])
        frame.to_excel(path, index=False)
        db.set_current_user('engineer')
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 20
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            root.update()
            self.assertFalse(window.busy)
        try:
            with patch('ui.product_tree_window.messagebox.showerror') as error:
                wait_job()
                self.assertEqual([window.tabs.tab(tab, 'text') for tab in window.tabs.tabs()], ['Product tree', 'Where used?'])
                window.tree.selection_set(f'p{project}')
                window.select_revision()
                self.assertEqual(str(window.add_bom_button.cget('state')), 'disabled')
                window.tree.selection_set(f'p{project}b{board}')
                window.select_revision()
                self.assertEqual(str(window.add_bom_button.cget('state')), 'normal')
                def select_file(**kwargs):
                    self.assertEqual(kwargs['initialdir'], self.temp.name)
                    # File parsing must never redirect the import to a later tree selection.
                    window.tree.selection_set(f'p{project}r{other}')
                    window.select_revision()
                    return path
                with patch('ui.product_tree_window.filedialog.askopenfilename', side_effect=select_file) as browser:
                    window.add_bom_button.invoke()
                    wait_job()
                browser.assert_called_once()
                self.assertEqual(window.tree.selection(), (f'p{project}r{target}',))
                self.assertEqual(pt.revision_lines(target)[0]['quantity'], 7)
                self.assertEqual(pt.revision_lines(target)[0]['component_id'], self.identity)
                self.assertEqual(pt.revision_lines(other), [])
                self.assertEqual(pt.revision_lines(original)[0]['quantity'], 3)
                self.assertEqual(pt.bom_edit_snapshot(project, target)['source'], path)
                saved = pt.bom_edit_snapshot(project, target)
                frame.loc[0, 'Quantity'] = 9
                frame.to_excel(path, index=False)
                with patch('ui.product_tree_window.filedialog.askopenfilename', return_value=path), \
                     patch('ui.product_tree_window.messagebox.askyesno', return_value=False) as confirm:
                    window.add_bom_button.invoke()
                    wait_job()
                confirm.assert_called_once()
                self.assertEqual(pt.bom_edit_snapshot(project, target), saved)
                with patch('ui.product_tree_window.filedialog.askopenfilename', return_value=path), \
                     patch('ui.product_tree_window.messagebox.askyesno', return_value=True):
                    window.add_bom_button.invoke()
                    wait_job()
                self.assertEqual(pt.revision_lines(target)[0]['quantity'], 9)
                with patch('ui.product_tree_window.filedialog.askopenfilename', return_value=''):
                    window.add_bom_button.invoke()
                    self.assertFalse(window.busy)
                window.tree.selection_set((f'p{project}r{target}', f'p{project}r{other}'))
                window.select_revision()
                self.assertEqual(str(window.add_bom_button.cget('state')), 'disabled')
                error.assert_not_called()
                window.tree.selection_set(f'p{project}r{original}')
                window.select_revision()
                self.assertEqual(str(window.add_bom_button.cget('state')), 'disabled')
                with patch('ui.product_tree_window.filedialog.askopenfilename') as browser:
                    window.add_bom()
                browser.assert_not_called()
                error.assert_called_once()
        finally:
            wait_job()
            window.close()
            root.destroy()

    def test_window_add_bom_sheet_selection_and_invalid_files_preserve_saved_rows(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        revision = pt.save_bom(self.meta, self.lines, 'Original.xlsx')
        project = pt.list_projects()[0]['id']
        before = pt.bom_edit_snapshot(project, revision)
        path = os.path.join(self.temp.name, 'Multiple.xlsx')
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame([{'Read me': 'Not a BOM'}]).to_excel(writer, sheet_name='Notes', index=False)
            pd.DataFrame([{'Company Part Number': 'UNKNOWN', 'Quantity': 2, 'Manufacturer Part Number': 'X',
                'Manufacturer': 'Vendor', 'Description': 'Unmatched item', 'Reference': 'U1,U2'}]).to_excel(writer, sheet_name='BOM', index=False)
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 20
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            root.update()
            self.assertFalse(window.busy)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        def open_sheet_dialog():
            with patch('ui.product_tree_window.filedialog.askopenfilename', return_value=path):
                window.add_bom_button.invoke()
                wait_job()
            return next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
        try:
            wait_job()
            window.tree.selection_set(f'p{project}r{revision}')
            window.select_revision()
            dialog = open_sheet_dialog()
            next(w for w in descendants(dialog) if w.winfo_class() == 'TButton' and w.cget('text') == 'Cancel').invoke()
            self.assertEqual(pt.bom_edit_snapshot(project, revision), before)
            dialog = open_sheet_dialog()
            with patch('ui.product_tree_window.messagebox.showerror') as error:
                next(w for w in descendants(dialog) if w.winfo_class() == 'TButton' and w.cget('text') == 'Add BOM').invoke()
                wait_job()
            error.assert_called_once()
            self.assertIn('BOM_Template.xlsx', str(error.call_args))
            self.assertEqual(pt.bom_edit_snapshot(project, revision), before)
            dialog = open_sheet_dialog()
            next(w for w in descendants(dialog) if w.winfo_class() == 'TCombobox').set('BOM')
            with patch('ui.product_tree_window.messagebox.askyesno', return_value=True):
                next(w for w in descendants(dialog) if w.winfo_class() == 'TButton' and w.cget('text') == 'Add BOM').invoke()
                wait_job()
            self.assertEqual(pt.revision_lines(revision)[0]['cmp'], 'UNKNOWN')
            self.assertIsNone(pt.revision_lines(revision)[0]['component_id'])
            self.assertIn('1 unmatched', window.status.get())
            saved = pt.bom_edit_snapshot(project, revision)
            with patch('ui.product_tree_window.messagebox.showerror') as error, \
                 patch('ui.product_tree_window.filedialog.askopenfilename', return_value=os.path.join(self.temp.name, 'Missing.xlsx')):
                window.add_bom_button.invoke()
                wait_job()
            error.assert_called_once()
            self.assertEqual(pt.bom_edit_snapshot(project, revision), saved)
        finally:
            wait_job()
            window.close()
            root.destroy()

    def test_window_add_card_revision_from_card_and_revision_selection(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        original = pt.save_bom(dict(self.meta, revision='-X1', status='Approved'), self.lines, 'Original.xlsx')
        project = pt.list_projects()[0]['id']
        board = pt.revision_for('PCB1', '-X1')['board_id']
        db.set_current_user('engineer')
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 15
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            root.update()
            self.assertFalse(window.busy)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        try:
            wait_job()
            window.tree.selection_set(f'p{project}')
            window.select_revision()
            self.assertEqual(str(window.add_revision_button.cget('state')), 'disabled')
            window.tree.selection_set(f'p{project}b{board}')
            window.select_revision()
            self.assertEqual(str(window.add_revision_button.cget('state')), 'normal')
            self.assertEqual(str(window.edit_button.cget('state')), 'disabled')
            for name in ('-X2', '-X3'):
                window.add_revision_button.invoke()
                dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
                self.assertEqual(dialog.title(), 'Add Card Revision')
                controls = list(descendants(dialog))
                entries = [w for w in controls if w.winfo_class() == 'TEntry']
                self.assertEqual([e.get() for e in entries], ['PCB1', 'Card', '', '2', ''])
                self.assertEqual([str(e.cget('state')) for e in entries], ['readonly', 'readonly', 'normal', 'normal', 'readonly'])
                save = next(w for w in controls if w.winfo_class() == 'TButton' and w.cget('text') == 'Add revision')
                if name == '-X2':
                    entries[2].insert(0, '-x1')
                    with patch('ui.product_tree_window.messagebox.showerror') as error:
                        save.invoke()
                    error.assert_called_once()
                    self.assertEqual(len(pt.board_revisions(board)), 1)
                    entries[2].delete(0, tk.END)
                entries[2].insert(0, name)
                save.invoke()
                root.update()
                new = pt.revision_for('PCB1', name)['id']
                self.assertEqual(window.tree.selection(), (f'p{project}r{new}',))
                self.assertEqual(window.tree.parent(f'p{project}r{new}'), f'p{project}b{board}')
                self.assertEqual(window.lines.get_children(), ())
                self.assertTrue(window.tree.exists(f'p{project}r{original}'))
                self.assertEqual(pt.revision_for('PCB1', '-X1')['status'], 'Approved')
                self.assertEqual(str(window.add_revision_button.cget('state')), 'normal')
            self.assertEqual(window.selected_bom_row()['part_number'], 'PCB1')
            self.assertEqual(window.selected_bom_row()['revision'], '-X3')
            self.assertEqual(str(window.add_bom_button.cget('state')), 'normal')
            window.tree.selection_set((f'p{project}r{original}', f'p{project}r{new}'))
            window.select_revision()
            self.assertEqual(str(window.add_revision_button.cget('state')), 'disabled')
            window.tree.selection_set(f'p{project}r{new}')
            db.set_current_user('inactive')
            window.select_revision()
            self.assertEqual(str(window.add_revision_button.cget('state')), 'disabled')
        finally:
            wait_job()
            window.close()
            root.destroy()

    def test_window_card_creation_and_draggable_tree_panel(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        existing = pt.save_bom(self.meta, self.lines, 'Existing.xlsx')
        project = pt.list_projects()[0]['id']
        upper = pt.save_project('TOP', 'Top-level project')
        pt.save_project('P1', 'Project', project_id=project, parent_id=upper)
        db.set_current_user('engineer')
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 8
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            root.update()
            self.assertFalse(window.busy)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        try:
            wait_job()
            window.tree.selection_set(f'p{project}')
            window.select_revision()
            self.assertEqual(str(window.new_card_button.cget('state')), 'normal')
            window.new_card_button.invoke()
            dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
            controls = list(descendants(dialog))
            entries = [w for w in controls if w.winfo_class() == 'TEntry']
            for entry, value in zip(entries, ('PCB2', 'Auxiliary board', 'B', '2', 'Engin Kavak')):
                entry.delete(0, tk.END)
                entry.insert(0, value)
            next(w for w in controls if w.winfo_class() == 'TButton' and w.cget('text') == 'Create card').invoke()
            root.update()
            revision = pt.revision_for('PCB2', 'B')['id']
            node = f'p{project}r{revision}'
            board = window.tree.parent(node)
            self.assertEqual(window.tree.set(board, 'responsible'), 'Engin Kavak')
            self.assertEqual(window.tree.parent(board), f'p{project}')
            self.assertEqual(window.tree.parent(f'p{project}'), f'p{upper}')
            self.assertEqual(len(window.tree.get_children(f'p{project}')), 2)
            self.assertEqual(window.tree.selection(), (node,))
            self.assertEqual(window.lines.get_children(), ())
            self.assertIn('No BOM imported', window.detail.get())
            selected = window.selected_bom_row()
            self.assertEqual([selected[k] for k in
                ('parent_code', 'code', 'part_number', 'board_name', 'revision', 'board_quantity')],
                ['TOP', 'P1', 'PCB2', 'Auxiliary board', 'B', 2])
            root.update()
            split = window.tree_split
            original_width = window.tree.winfo_width()
            x, _ = split.sash_coord(0)
            drag_x, drag_y = x + 4, 100
            self.assertTrue(split.identify(drag_x, drag_y))
            split.event_generate('<ButtonPress-1>', x=drag_x, y=drag_y)
            split.event_generate('<B1-Motion>', x=drag_x + 130, y=drag_y)
            split.event_generate('<ButtonRelease-1>', x=drag_x + 130, y=drag_y)
            root.update()
            self.assertGreater(window.tree.winfo_width(), original_width + 100)
            expanded = split.sash_coord(0)[0]
            window.refresh_tree()
            root.update()
            self.assertEqual(split.sash_coord(0)[0], expanded)
            split.event_generate('<ButtonPress-1>', x=expanded + 4, y=drag_y)
            split.event_generate('<B1-Motion>', x=expanded - 126, y=drag_y)
            split.event_generate('<ButtonRelease-1>', x=expanded - 126, y=drag_y)
            root.update()
            self.assertLess(window.tree.winfo_width(), original_width + 20)
            self.assertEqual(window.tree.selection(), (node,))
            self.assertEqual(len(pt.revision_lines(existing)), 1)
            window.tree.selection_set(board)
            window.select_revision()
            window.edit_project()
            dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
            self.assertEqual(dialog.title(), 'Edit card')
            controls = list(descendants(dialog))
            entries = [w for w in controls if w.winfo_class() == 'TEntry']
            entries[1].delete(0, tk.END)
            entries[1].insert(0, 'Updated auxiliary board')
            next(w for w in controls if w.winfo_class() == 'TButton' and w.cget('text') == 'Save').invoke()
            root.update()
            self.assertIn('Updated auxiliary board', window.tree.item(board, 'text'))
            self.assertEqual(pt.rows('SELECT name FROM PT_Boards WHERE part_number=?', ('PCB2',))[0]['name'], 'Updated auxiliary board')
        finally:
            wait_job()
            window.close()
            root.destroy()

    def test_window_subprojects_edit_import_navigation_and_delete_scope(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        revision = pt.save_bom(self.meta, self.lines, 'BOM.xlsx')
        # Alphabetical order puts the old project before the new parent.
        upper = pt.save_project('TOP', 'Top-level project')
        child = next(p['id'] for p in pt.list_projects() if p['code'] == 'P1')
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 8
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            root.update()
            self.assertFalse(window.busy)
        def descendants(widget):
            for item in widget.winfo_children():
                yield item
                yield from descendants(item)
        def dialog_controls():
            dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
            controls = list(descendants(dialog))
            return controls, next(w for w in controls if w.winfo_class() == 'TButton' and w.cget('text') == 'Save')
        try:
            wait_job()
            window.tree.selection_set(f'p{child}')
            window.edit_project()
            controls, save = dialog_controls()
            parent_choice = next(w for w in controls if w.winfo_class() == 'TCombobox')
            parent_choice.set('TOP — Top-level project')
            save.invoke()
            root.update()
            self.assertEqual(window.tree.parent(f'p{child}'), f'p{upper}')
            board_node = window.tree.get_children(f'p{child}')[0]
            revision_node = window.tree.get_children(board_node)[0]
            window.tree.selection_set(revision_node)
            window.select_revision()
            self.assertEqual(window.selected_project()['id'], child)
            self.assertEqual(window.selected_bom_row()['parent_code'], 'TOP')
            self.assertEqual(window.selected_bom_row()['code'], 'P1')
            root.update()
            self.assertGreater(window.lines.winfo_height(), 100)
            window.tree.item(f'p{child}', open=False)
            window.refresh_tree()
            self.assertFalse(window.tree.item(f'p{child}', 'open'))
            window.tree.item(f'p{child}', open=True)
            window.new_subproject()
            controls, save = dialog_controls()
            entries = [w for w in controls if w.winfo_class() == 'TEntry']
            entries[0].insert(0, 'SUB-B')
            entries[1].insert(0, 'Second subproject')
            save.invoke()
            root.update()
            second = window.selected_project()['id']
            self.assertEqual(window.tree.parent(f'p{second}'), f'p{upper}')
            self.assertEqual(window.selected_project()['code'], 'SUB-B')
            window.new_card()
            dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
            next(w for w in descendants(dialog) if w.winfo_class() == 'TButton' and w.cget('text') == 'Link saved card').invoke()
            dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
            next(w for w in descendants(dialog) if w.winfo_class() == 'TButton' and w.cget('text') == 'Link card').invoke()
            wait_job()
            self.assertEqual(window.selected_project()['id'], second)
            self.assertEqual(window.revision_id, revision)
            window.lines.selection_set(str(pt.revision_lines(revision)[0]['id']))
            window.show_line_usage()
            root.update()
            self.assertEqual(len(window.usages.get_children()), 2)
            index = next(i for i, row in enumerate(window.usage_data) if row['project_id'] == child)
            self.assertEqual(window.usages.item(str(index), 'values')[:2], ('TOP — Top-level project', 'P1 — Project'))
            window.usages.selection_set(str(index))
            window.open_usage_board()
            self.assertEqual(window.selected_project()['id'], child)
            window.tree.selection_set(f'p{upper}')
            with patch('ui.product_tree_window.messagebox.showinfo') as info:
                window.delete_project()
            info.assert_called_once()
            window.tree.selection_set(f'p{child}')
            with patch('ui.product_tree_window.messagebox.askyesno', return_value=True):
                window.delete_project()
            wait_job()
            self.assertFalse(window.tree.exists(f'p{child}'))
            self.assertTrue(window.tree.exists(f'p{upper}'))
            self.assertTrue(window.tree.exists(f'p{second}r{revision}'))
            self.assertEqual([row['code'] for row in pt.usage(self.identity)], ['SUB-B'])
        finally:
            wait_job()
            window.close()
            root.destroy()

    def test_window_starts_collapsed_and_refresh_preserves_expansion(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        revision = pt.save_bom(self.meta, self.lines, 'BOM.xlsx')
        child = pt.list_projects()[0]['id']
        upper = pt.save_project('TOP-A', 'Top-level project')
        pt.save_project('P1-X2', 'Project', project_id=child, parent_id=upper)
        other = pt.save_project('OTHER', 'Other project')
        pt.create_card(other, 'PCB2', 'Other card', 'A')
        window = None
        try:
            # Every new window starts collapsed, regardless of the previous window.
            for _ in range(2):
                window = ProductTreeWindow(root)
                deadline = time.monotonic() + 8
                while window.busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.02)
                root.update()
                self.assertFalse(window.busy)
                roots = window.tree.get_children()
                self.assertEqual(set(roots), {f'p{upper}', f'p{other}'})
                self.assertEqual(window.tree.item(f'p{upper}', 'text'), 'TOP-A · Production — Top-level project')
                self.assertEqual(window.tree.item(f'p{child}', 'text'), 'P1-X2 · Development — Project')
                self.assertEqual(window.tree.item(f'p{other}', 'text'), 'OTHER — Other project')
                revision_node = f'p{child}r{revision}'
                board = window.tree.parent(revision_node)
                for node in (*roots, f'p{child}', board):
                    self.assertFalse(window.tree.item(node, 'open'))
                self.assertEqual(window.tree.selection(), (roots[0],))
                self.assertIsNone(window.revision_id)
                self.assertEqual(window.lines.get_children(), ())
                window.refresh_tree()
                self.assertTrue(all(not window.tree.item(node, 'open') for node in roots))
                for node in (f'p{upper}', f'p{child}', board):
                    window.tree.item(node, open=True)
                window.tree.selection_set(revision_node)
                window.select_revision()
                window.refresh_tree()
                for node in (f'p{upper}', f'p{child}', board):
                    self.assertTrue(window.tree.item(node, 'open'))
                self.assertFalse(window.tree.item(f'p{other}', 'open'))
                self.assertEqual(window.tree.selection(), (revision_node,))
                self.assertEqual(window.revision_id, revision)
                window.close()
                window = None
        finally:
            if window is not None:
                window.close()
            root.destroy()

    def test_window_tree_usage_and_stock(self):
        import time
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 8
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            root.update()
            self.assertFalse(window.busy)
        try:
            wait_job()
            revision = pt.save_bom(self.meta, self.lines, "BOM.xlsx")
            window.refresh_tree()
            project_node = window.tree.get_children()[0]
            board_node = window.tree.get_children(project_node)[0]
            revision_node = window.tree.get_children(board_node)[0]
            self.assertIn("PCB1", window.tree.item(board_node, "text"))
            self.assertIn("Rev. A", window.tree.item(revision_node, "text"))
            window.tree.item(board_node, open=False)
            window.refresh_tree()
            self.assertFalse(window.tree.item(board_node, "open"))
            window.tree.item(board_node, open=True)
            window.tree.selection_set(revision_node)
            window.select_revision()
            line_node = str(pt.revision_lines(revision)[0]['id'])
            self.assertEqual(window.lines.heading('cmp', 'text'), 'Company Part Number')
            self.assertEqual(window.lines.heading('mpn', 'text'), 'Manufacturer Part Number')
            self.assertEqual(window.lines.item(line_node, 'values')[:2], ('CMP-1', 'MPN-1'))
            self.assertNotIn('obsolete', window.lines.item(line_node, 'tags'))
            with db.connection(commit=True) as conn:
                conn.execute('ALTER TABLE Resistor ADD COLUMN "Product Status" TEXT')
            for status, warning in [(' ObSoLeTe ', True), ('Not For New Designs', True),
                                    ('Last Time Buy', True), ('Active', False), (None, False)]:
                with db.connection(commit=True) as conn:
                    conn.execute('UPDATE Resistor SET "Product Status"=?', (status,))
                window.run(pt.component_catalog, window.loaded)
                wait_job()
                self.assertEqual('obsolete' in window.lines.item(line_node, 'tags'), warning)
                self.assertEqual(window.lines.set(line_node, 'product_status'), (status or '').strip())
            window.lines.selection_set(str(pt.revision_lines(revision)[0]["id"]))
            window.show_line_usage()
            root.update()
            self.assertEqual(window.usages.set('0', 'total'), '6')
            self.assertEqual(window.usages.set('0', 'stock'), '25.0')
            window.usages.selection_set("0")
            window.open_usage_board()
            self.assertEqual(window.revision_id, revision)
            pt.save_bom(dict(self.meta, revision="B"), self.lines, "BOM_B.xlsx")
            window.refresh_tree()
            window.bom_description.delete("1.0", tk.END)
            window.bom_description.insert("1.0", "First line\nSecond line")
            window.save_description()
            wait_job()
            self.assertEqual(pt.revision_for("PCB1", "A")["notes"], "First line\nSecond line")
            from ui.revision_compare_window import RevisionCompareWindow
            compare = RevisionCompareWindow(window, pt.revision_for("PCB1", "A")["board_id"], "PCB1", revision)
            wait_job()
            self.assertTrue(compare.records)
            self.assertEqual(compare.records[0]["Field"], "BOM description")
            compare.destroy()
            children = window.tree.get_children(board_node)
            self.assertEqual(len(children), 2)
            self.assertTrue(any("(previous)" in window.tree.item(node, "text") for node in children))
            self.assertTrue(any("Rev. B" in window.tree.item(node, "text") for node in children))
            # Navigating from a historical BOM must show that BOM's usage.
            window.history.set(False)
            window.show_usage(self.identity)
            self.assertEqual(len(window.usage_data), 1)
            self.assertIn('1 previous revision rows hidden', window.usage_title.get())
            old_node = next(k for k, r in window.tree_rows.items() if r['id'] == revision)
            window.tree.selection_set(old_node)
            window.select_revision()
            window.lines.selection_set(str(pt.revision_lines(revision)[0]['id']))
            window.show_line_usage()
            self.assertTrue(window.history.get())
            self.assertEqual(len(window.usage_data), 2)
            self.assertIn(revision, [r['revision_id'] for r in window.usage_data])
            self.assertNotIn('hidden', window.usage_title.get())
            window.refresh_tree()
            self.assertEqual(len(window.tree.get_children(board_node)), 2)
            empty = pt.save_project("EMPTY", "Empty project")
            window.refresh_tree()
            self.assertTrue(window.tree.exists(f"p{empty}"))
            self.assertEqual(window.tree.get_children(f"p{empty}"), ())
            window.tree.selection_set(f"p{empty}")
            window.select_revision()
            self.assertEqual(window.selected_project()["id"], empty)
            with patch("ui.product_tree_window.messagebox.askyesno", return_value=True):
                window.delete_project()
            wait_job()
            self.assertFalse(window.tree.exists(f"p{empty}"))
            window.refresh_tree()
            rev_b = next(k for k, r in window.tree_rows.items() if r["revision"] == "B")
            window.tree.selection_set(rev_b)
            with patch("ui.product_tree_window.messagebox.askyesno", return_value=True):
                window.delete_project()
            wait_job()
            self.assertTrue(window.tree.exists(project_node))
            self.assertEqual(len(window.tree.get_children(board_node)), 1)
            window.tree.selection_set(board_node)
            with patch("ui.product_tree_window.messagebox.askyesno", return_value=True):
                window.delete_project()
            wait_job()
            self.assertTrue(window.tree.exists(project_node))
            self.assertFalse(window.tree.exists(board_node))
        finally:
            wait_job()
            window.close()
            root.destroy()
