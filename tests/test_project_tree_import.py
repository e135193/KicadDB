import gc
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

import db
import product_tree as pt
import project_tree_import as importer


TEMPLATE = Path(__file__).resolve().parents[1] / 'template' / 'Project_Import_Template.xlsx'


class TestProjectTreeImport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / 'parts.sqlite'
        self.path = Path(self.temp.name) / 'project.xlsx'
        shutil.copyfile(TEMPLATE, self.path)
        self.connection_patch = patch.object(db, 'get_connection', lambda: sqlite3.connect(self.database))
        self.connection_patch.start()
        with db.connection(commit=True) as conn:
            conn.execute('CREATE TABLE Users (Username TEXT UNIQUE COLLATE NOCASE, Role TEXT, Is_Active INTEGER)')
            conn.executemany('INSERT INTO Users VALUES (?,?,?)',
                             [('admin', 'admin', 1), ('engineer', 'user', 1), ('inactive', 'admin', 0)])
            conn.execute('CREATE TABLE Parts (id INTEGER PRIMARY KEY, Brensan_Part_Number TEXT, '
                         'Manufacturer_Part_Number TEXT, Manufacturer TEXT, Brensan_Stock REAL)')
            conn.execute("INSERT INTO Parts VALUES (1,'CMP-DEMO','DEMO-R10K-0603','Example Manufacturer',25)")
        db.set_current_user('admin')
        pt.ensure_schema()

    def tearDown(self):
        gc.collect()
        db.set_current_user('')
        self.connection_patch.stop()
        self.temp.cleanup()

    def edit(self, callback):
        book = load_workbook(self.path)
        try:
            callback(book)
            book.save(self.path)
        finally:
            book.close()

    def state(self):
        with db.connection() as conn:
            return list(conn.iterdump())

    def load(self):
        return importer.import_workbook(importer.preview_import(self.path))

    def test_delivered_template_roundtrip_hierarchy_matching_and_repeat(self):
        db.set_current_user('engineer')
        before = self.state()
        preview = importer.preview_import(self.path)
        self.assertEqual(self.state(), before, 'Preview must be read-only, including component identities.')
        self.assertEqual(preview['summary'], dict(projects=2, cards=2, revisions=3, filled=0,
                                                  reused=0, lines=6, approvals=0))
        result = importer.import_workbook(preview)
        self.assertEqual((result['matched'], result['unmatched']), (2, 4))
        projects = {p['code']: p for p in pt.list_projects()}
        self.assertEqual(projects['DEMO-SUB-01']['parent_id'], projects['DEMO-PROJECT-01']['id'])
        self.assertEqual(projects['DEMO-PROJECT-01']['notes'], 'Example top-level project')
        self.assertEqual({r['revision'] for r in pt.project_tree()}, {'-X2', '-A'})
        self.assertEqual(len(pt.project_tree(True)), 3)
        self.assertEqual({r['board_quantity'] for r in pt.project_tree()}, {1, 2})
        revision = pt.revision_for('DEMO-PCB-001', '-X2')
        self.assertEqual((revision['imported_by'], revision['source']), ('engineer', str(self.path)))
        self.assertEqual([r['quantity'] for r in pt.revision_lines(revision['id'])], [3, 2])
        self.assertEqual(pt.rows('SELECT * FROM Parts')[0]['Company_Stock'], 25)
        snapshots = {table: pt.rows(f'SELECT * FROM {table}') for table in pt.TABLES}
        again = self.load()
        self.assertEqual((again['projects'], again['cards'], again['revisions'], again['lines'], again['reused']),
                         (0, 0, 0, 0, 3))
        self.assertEqual({table: pt.rows(f'SELECT * FROM {table}') for table in pt.TABLES}, snapshots)
        self.assertEqual(len(pt.rows("SELECT * FROM Change_Log WHERE Action='BOM_IMPORT'")), 3)

    def test_shared_revision_and_scoped_project_codes(self):
        def change(book):
            book['Projects'].append(['DEMO-SUB-01', 'Another root', '', 'C:/Projects/Other', 'Root notes'])
            book['Projects'].append(['DEMO-PROJECT-01', 'Child reusing root code', 'DEMO-PROJECT-01', '', ''])
            book['Cards'].append(['DEMO-SUB-01', '', 'DEMO-PCB-001', 'Power board', '-X2', 5, 'Yes', 'Draft',
                                  'Updated resistor and capacitor quantities'])
        self.edit(change)
        result = self.load()
        self.assertEqual((result['projects'], result['cards'], result['revisions'], result['lines']), (4, 2, 3, 6))
        self.assertEqual(len(pt.rows('SELECT * FROM PT_ProjectBoards')), 4)
        identity = result['catalog'][0]['id']
        self.assertEqual(sorted(r['total'] for r in pt.usage(identity)), [6, 15])
        self.assertEqual([p['folder'] for p in pt.list_projects() if p['name'] == 'Another root'], ['C:/Projects/Other'])

    def test_existing_empty_draft_can_be_filled_and_blank_optional_details_preserved(self):
        root = pt.save_project('DEMO-PROJECT-01', 'Example power system', notes='Example top-level project', folder='C:/Existing')
        sub = pt.save_project('DEMO-SUB-01', 'Power module', notes='Example subproject', parent_id=root)
        revision = pt.create_card(sub, 'DEMO-PCB-001', 'Power board', '-X2', 2)
        result = self.load()
        self.assertEqual((result['filled'], result['revisions']), (1, 2))
        self.assertEqual(pt.revision_for('DEMO-PCB-001', '-X2')['id'], revision)
        self.assertEqual(len(pt.revision_lines(revision)), 2)
        self.assertEqual(next(p['folder'] for p in pt.list_projects() if p['id'] == root), 'C:/Existing')

    def test_existing_content_and_manual_matches_are_never_overwritten(self):
        self.load()
        with db.connection(commit=True) as conn:
            conn.execute('UPDATE PT_Lines SET component_id=NULL')
        before_lines = pt.rows('SELECT * FROM PT_Lines')
        self.assertEqual(self.load()['reused'], 3)
        self.assertEqual(pt.rows('SELECT * FROM PT_Lines'), before_lines)
        before = self.state()
        self.edit(lambda b: setattr(b['BOM']['H2'], 'value', 9))
        with self.assertRaisesRegex(ValueError, 'saved BOM differs'):
            self.load()
        self.assertEqual(self.state(), before)

    def test_existing_project_card_and_quantity_conflicts_are_rejected(self):
        self.load()
        before = self.state()
        for sheet, cell, value in [('Projects', 'B2', 'Renamed'), ('Projects', 'D2', 'C:/New'),
                                   ('Cards', 'D4', 'Renamed'), ('Cards', 'F4', 4), ('Cards', 'I4', 'New notes')]:
            with self.subTest(sheet=sheet, cell=cell):
                shutil.copyfile(TEMPLATE, self.path)
                self.edit(lambda b: setattr(b[sheet][cell], 'value', value))
                with self.assertRaises(ValueError):
                    self.load()
                self.assertEqual(self.state(), before)

    def test_admin_approval_and_reuse_by_regular_user(self):
        self.edit(lambda b: setattr(b['Cards']['H3'], 'value', 'Approved'))
        db.set_current_user('engineer')
        before = self.state()
        with self.assertRaises(PermissionError):
            self.load()
        self.assertEqual(self.state(), before)
        db.set_current_user('admin')
        self.assertEqual(self.load()['approvals'], 1)
        approved = pt.revision_for('DEMO-PCB-001', '-X2')
        self.assertEqual((approved['status'], approved['approved_by']), ('Approved', 'admin'))
        self.assertTrue(approved['approved_at'])
        db.set_current_user('engineer')
        self.assertEqual(self.load()['reused'], 3)
        self.assertEqual(pt.revision_for('DEMO-PCB-001', '-X2'), approved)

    def test_role_and_activity_are_rechecked_at_commit(self):
        self.edit(lambda b: setattr(b['Cards']['H3'], 'value', 'Approved'))
        preview = importer.preview_import(self.path)
        with db.connection(commit=True) as conn:
            conn.execute("UPDATE Users SET Role='user' WHERE Username='admin'")
        before = self.state()
        with self.assertRaises(PermissionError):
            importer.import_workbook(preview)
        self.assertEqual(self.state(), before)
        for actor in ('inactive', '', 'unknown'):
            db.set_current_user(actor)
            with self.assertRaises(PermissionError):
                importer.preview_import(self.path)

    def test_concurrent_database_and_workbook_changes_reject_stale_preview(self):
        preview = importer.preview_import(self.path)
        pt.save_project('OTHER', 'Other project')
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'Product tree changed'):
            importer.import_workbook(preview)
        self.assertEqual(self.state(), before)
        preview = importer.preview_import(self.path)
        self.edit(lambda b: setattr(b['Projects']['B2'], 'value', 'Updated file'))
        with self.assertRaisesRegex(ValueError, 'Excel file changed'):
            importer.import_workbook(preview)
        self.assertEqual(self.state(), before)

    def test_failure_rolls_back_hierarchy_lines_approval_audit_and_component_registry(self):
        self.edit(lambda b: setattr(b['Cards']['H3'], 'value', 'Approved'))
        preview = importer.preview_import(self.path)
        before = self.state()
        with patch.object(pt, '_link', side_effect=RuntimeError('injected write failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected write failure'):
                importer.import_workbook(preview)
        self.assertEqual(self.state(), before)

    def test_invalid_cells_are_rejected_with_sheet_and_row_without_writes(self):
        before = self.state()
        cases = [('Projects', 'A2', ''), ('Projects', 'C3', 'MISSING'),
                 ('Cards', 'A2', 'MISSING'), ('Cards', 'H2', 'Unknown'), ('Cards', 'G2', 'Maybe'),
                 ('Cards', 'F2', 0), ('Cards', 'F2', 'NaN'), ('Cards', 'E2', '=1+1'),
                 ('BOM', 'B2', 'UNKNOWN'), ('BOM', 'C2', 1.5), ('BOM', 'C3', 1),
                 ('BOM', 'H2', -1), ('BOM', 'H2', '#VALUE!'), ('BOM', 'E2', '-')]
        for sheet, cell, value in cases:
            with self.subTest(sheet=sheet, cell=cell, value=value):
                shutil.copyfile(TEMPLATE, self.path)
                self.edit(lambda b: setattr(b[sheet][cell], 'value', value))
                with self.assertRaisesRegex(ValueError, sheet):
                    self.load()
                self.assertEqual(self.state(), before)

    def test_invalid_headers_duplicates_current_and_empty_approval(self):
        cases = [lambda b: b.remove(b['BOM']),
                 lambda b: setattr(b['BOM']['A1'], 'value', 'Wrong header'),
                 lambda b: setattr(b['BOM']['B1'], 'value', 'Card Part Number'),
                 lambda b: b['Projects'].append(['DEMO-PROJECT-01', 'Duplicate', '', '', '']),
                 lambda b: setattr(b['Cards']['G2'], 'value', 'Yes'),
                 lambda b: setattr(b['Cards']['G3'], 'value', 'No'),
                 lambda b: b['Cards'].append(['DEMO-PROJECT-01', 'DEMO-SUB-01', 'DEMO-PCB-001',
                                             'Power board', '-X2', 2, 'Yes', 'Draft', 'Different notes']),
                 lambda b: (b['BOM'].delete_rows(2, 2), setattr(b['Cards']['H2'], 'value', 'Approved'))]
        before = self.state()
        for index, change in enumerate(cases):
            with self.subTest(case=index):
                shutil.copyfile(TEMPLATE, self.path)
                self.edit(change)
                with self.assertRaises(ValueError):
                    self.load()
                self.assertEqual(self.state(), before)

    def test_empty_draft_and_project_without_cards_are_allowed(self):
        def change(b):
            b['BOM'].delete_rows(2, 2)
            b['Projects'].append(['EMPTY', 'Empty project', '', '', ''])
        self.edit(change)
        self.assertEqual(self.load()['lines'], 4)
        revision = pt.revision_for('DEMO-PCB-001', '-X1')
        self.assertEqual((revision['status'], pt.revision_lines(revision['id'])), ('Draft', []))
        self.assertIn('EMPTY', [p['code'] for p in pt.list_projects()])

    def test_import_button_cancel_confirm_refresh_and_permissions(self):
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        db.set_current_user('engineer')
        window = ProductTreeWindow(root)

        def wait_job():
            deadline = time.monotonic() + 10
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            root.update()
            self.assertFalse(window.busy)

        try:
            with patch('ui.product_tree_window.messagebox.showerror') as error:
                wait_job()
                self.assertEqual(window.tree.selection(), ())
                self.assertEqual(str(window.project_import_button['state']), 'normal')
                with patch('ui.product_tree_window.filedialog.askopenfilename', return_value=''):
                    window.project_import_button.invoke()
                    self.assertFalse(window.busy)
                before = self.state()
                with patch('ui.product_tree_window.filedialog.askopenfilename', return_value=str(self.path)), \
                     patch('ui.product_tree_window.messagebox.askyesno', return_value=False) as confirm:
                    window.project_import_button.invoke()
                    wait_job()
                    confirm.assert_called_once()
                    self.assertEqual(self.state(), before)
                with patch('ui.product_tree_window.filedialog.askopenfilename', return_value=str(self.path)), \
                     patch('ui.product_tree_window.messagebox.askyesno', return_value=True), \
                     patch('ui.product_tree_window.messagebox.showinfo') as info:
                    window.project_import_button.invoke()
                    wait_job()
                    info.assert_called_once()
                self.assertEqual(len(window.tree.get_children()), 1)
                self.assertEqual(len(window.tree_rows), 3)
                self.assertIn('BOM rows added: 6', window.status.get())
                db.set_current_user('inactive')
                window.apply_bom_permissions()
                self.assertEqual(str(window.project_import_button['state']), 'disabled')
                error.assert_not_called()
        finally:
            wait_job()
            window.close()
            root.destroy()


if __name__ == '__main__':
    unittest.main()
