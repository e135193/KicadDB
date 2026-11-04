import gc
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import db
import product_tree as model
import project_overview as overview


class TestProjectOverview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'overview.sqlite')
        self.connection_patch = patch.object(db, 'get_connection', lambda: sqlite3.connect(self.path))
        self.connection_patch.start()
        with db.connection(commit=True) as conn:
            conn.execute('CREATE TABLE Users (Username TEXT,Role TEXT,Is_Active INTEGER)')
            conn.execute("INSERT INTO Users VALUES ('admin','admin',1)")
            for category, br, stock, status, alternate in [
                ('Resistor', 'CMP-R', 100, 'Active', ''), ('Inductor', 'CMP-L', None, 'Active', ''),
                ('Capacitor', 'CMP-C', 1000, 'Active', ''), ('IC', 'CMP-IC', 5, ' ObSoLeTe ', ''),
                ('Mosfet', 'CMP-MOS', 0, 'Not For New Designs', 'ALT-MOS'),
                ('MCU', 'CMP-MCU', 1000, 'Active', ''), ('Connector', 'CMP-CONN', 1000, 'Active', ''),
                ('Transistor', 'CMP-BJT', 1000, 'Active', '')]:
                conn.execute(f'''CREATE TABLE {category}(id INTEGER PRIMARY KEY,Brensan_Part_Number TEXT,
                    Manufacturer_Part_Number TEXT,Manufacturer TEXT,Brensan_Stock REAL,
                    Product_Status TEXT,Alternate_Part_Number TEXT)''')
                conn.execute(f'INSERT INTO {category} VALUES (1,?,?,?,?,?,?)', (br, br + '-MPN', 'Maker', stock, status, alternate))
        db.set_current_user('admin')
        self.parts = {p['cmp']: p for p in model.component_catalog()}
        self.root = model.save_project('ROOT', 'Root project')
        self.first = model.save_project('SUB1', 'First', parent_id=self.root)
        self.second = model.save_project('SUB2', 'Second', parent_id=self.root)
        self.outside = model.save_project('OUT', 'Outside')

    def tearDown(self):
        gc.collect()
        db.set_current_user('')
        self.connection_patch.stop()
        self.temp.cleanup()

    def line(self, br, quantity=1, **changes):
        part = self.parts[br]
        return dict(dict(br=br, mpn=part['mpn'], manufacturer='Maker', description='Example',
            quantity=quantity, references='U1', component_id=part['id']), **changes)

    def bom(self, project, board, lines=(), revision='A', quantity=1):
        rid = model.create_card(project, board, board + ' name', revision, quantity)
        if lines:
            model.import_bom_into_revision(model.bom_edit_snapshot(project, rid), list(lines), 'BOM.xlsx')
        return rid

    def test_latest_scope_deduplicates_materials_and_preserves_project_quantities(self):
        direct = self.bom(self.root, 'DIRECT', [self.line('CMP-IC', 2), self.line('CMP-R')])
        model.approve_bom(direct)
        self.bom(self.first, 'PCB1', [self.line('CMP-MOS', 100)], quantity=2)
        self.bom(self.first, 'PCB1', [self.line('CMP-IC'), self.line('CMP-R', 2),
            self.line('CMP-L'), self.line('CMP-C'), self.line('CMP-MCU'),
            self.line('CMP-CONN'), self.line('CMP-BJT')], revision='B', quantity=3)
        shared = self.bom(self.second, 'PCB2', [self.line('CMP-IC', 2), self.line('CMP-MOS')], quantity=2)
        model.link_card_revision(self.first, shared, 4)
        self.bom(self.second, 'EMPTY')
        self.bom(self.outside, 'EXCLUDED', [self.line('CMP-IC', 999)])
        report = overview.build_overview(self.root)
        summary = report['summary']
        expected = dict(subprojects=2, cards=4, card_links=5, latest_boms=4, approved_boms=1,
            draft_boms=3, empty_boms=1, materials=8, obsolete=1, lifecycle_risk=2, critical=3,
            shortages=2, unknown_stock=1, unmatched_parts=0, missing_alternatives=1)
        for key, value in expected.items():
            self.assertEqual(summary[key], value, key)
        parts = {p['cmp']: p for p in report['materials']}
        self.assertEqual(parts['CMP-IC']['quantity'], 17)
        self.assertEqual(parts['CMP-IC']['stock'], 5)
        self.assertEqual(parts['CMP-IC']['shortage'], 12)
        self.assertEqual(parts['CMP-MOS']['quantity'], 6)
        self.assertEqual(parts['CMP-MOS']['shortage'], 6)
        self.assertEqual(parts['CMP-R']['quantity'], 7)
        self.assertIsNone(parts['CMP-L']['shortage'])
        self.assertFalse(parts['CMP-C']['critical'])
        self.assertEqual({p['cmp'] for p in report['materials'] if p['critical']},
                         {'CMP-IC', 'CMP-MCU', 'CMP-MOS'})
        self.assertNotIn('EXCLUDED', parts['CMP-IC']['uses'])
        leaf = {p['cmp']: p for p in overview.build_overview(self.second)['materials']}
        self.assertEqual(leaf['CMP-IC']['quantity'], 4)
        self.assertEqual(leaf['CMP-MOS']['quantity'], 2)

    def test_final_priority_numeric_order_and_project_local_links(self):
        self.bom(self.first, 'SHARED', [self.line('CMP-MOS', 100)], revision='-X1')
        prototype = self.bom(self.first, 'SHARED', [self.line('CMP-MCU')], revision='-X10')
        final = self.bom(self.first, 'SHARED', [self.line('CMP-IC', 7)], revision='-B', quantity=3)
        self.bom(self.first, 'SHARED', [self.line('CMP-R', 100)], revision='-A')
        self.bom(self.first, 'SHARED', [self.line('CMP-MOS', 100)], revision='-X11')
        # Each placement uses its own linked revisions and quantity, not the global newest.
        model.link_card_revision(self.second, prototype, 2)
        self.bom(self.outside, 'SHARED', [self.line('CMP-BJT', 999)], revision='-Z')
        numeric = self.bom(self.root, 'PROTO', [self.line('CMP-C', 2)], revision='-X10', quantity=3)
        self.bom(self.root, 'PROTO', [self.line('CMP-MOS', 100)], revision='-X2')
        # A newer final with an empty BOM must not reuse an older revision's materials.
        self.bom(self.root, 'EMPTY', [self.line('CMP-MOS', 100)], revision='-Z')
        empty = self.bom(self.root, 'EMPTY', revision='-AA')
        self.bom(self.root, 'EMPTY', [self.line('CMP-MOS', 100)], revision='-X99')
        with db.connection() as conn:
            before = list(conn.iterdump())
        report = overview.build_overview(self.root)
        self.assertEqual({(c['project_id'], c['revision_id']) for c in report['cards']},
                         {(self.first, final), (self.second, prototype), (self.root, numeric), (self.root, empty)})
        parts = {part['cmp']: part for part in report['materials']}
        self.assertEqual({br: part['quantity'] for br, part in parts.items()},
                         {'CMP-IC': 21, 'CMP-MCU': 2, 'CMP-C': 6})
        self.assertEqual(parts['CMP-IC']['shortage'], 16)
        self.assertIn('SHARED / Rev. -B', parts['CMP-IC']['uses'])
        self.assertIn('SHARED / Rev. -X10', parts['CMP-MCU']['uses'])
        self.assertEqual(report['summary']['materials'], 3)
        self.assertEqual(report['summary']['latest_boms'], 4)
        self.assertEqual(report['summary']['critical'], 2)
        self.assertEqual(report['summary']['lifecycle_risk'], 1)
        self.assertEqual(report['summary']['empty_boms'], 1)
        with db.connection() as conn:
            self.assertEqual(list(conn.iterdump()), before)

    def test_live_read_only_summary_does_not_register_or_change_matches(self):
        self.bom(self.root, 'PCB1', [self.line('CMP-IC', 2, component_id=None)])
        with db.connection(commit=True) as conn:
            conn.execute('DELETE FROM PT_Components')
            conn.execute("UPDATE IC SET Brensan_Stock=0,Product_Status='Last Time Buy',Alternate_Part_Number='—'")
        with db.connection() as conn:
            before = list(conn.iterdump())
        def read_only():
            conn = sqlite3.connect(self.path)
            conn.execute('PRAGMA query_only=ON')
            return conn
        with patch.object(db, 'get_connection', read_only):
            report = overview.build_overview(self.root)
        part, = report['materials']
        self.assertEqual((part['stock'], part['shortage']), (0, 2))
        self.assertTrue(part['unmatched'])
        self.assertTrue(part['critical'])
        self.assertTrue(part['missing_alternative'])
        self.assertEqual(report['summary']['obsolete'], 0)
        self.assertEqual(report['summary']['lifecycle_risk'], 1)
        with db.connection() as conn:
            self.assertEqual(list(conn.iterdump()), before)

    def test_ambiguous_and_deleted_matches_are_unknown_not_zero_or_critical(self):
        self.bom(self.root, 'PCB1', [self.line('CMP-IC', component_id=None), self.line('CMP-MOS')])
        with db.connection(commit=True) as conn:
            conn.execute("INSERT INTO IC VALUES (2,'CMP-IC','DIFFERENT','Maker',10,'Active','')")
            conn.execute('DELETE FROM Mosfet')
        report = overview.build_overview(self.root)
        self.assertEqual(report['summary']['unknown_stock'], 2)
        self.assertEqual(report['summary']['unmatched_lines'], 2)
        self.assertEqual(report['summary']['critical'], 0)
        self.assertEqual(report['summary']['obsolete'], 0)
        self.assertEqual(report['summary']['shortages'], 0)
        self.assertTrue(all(p['category'] == 'Unknown' for p in report['materials']))

    def test_empty_missing_project_and_decimal_quantities(self):
        self.assertEqual(overview.build_overview(self.root)['materials'], [])
        self.assertIn('No linked cards', overview.build_overview(self.root)['suggestions'][0])
        with self.assertRaisesRegex(ValueError, 'Project not found'):
            overview.build_overview(999)
        self.bom(self.root, 'PCB1', [self.line('CMP-IC', .1)], quantity=3)
        self.bom(self.first, 'PCB2', [self.line('CMP-IC', .2)], quantity=3)
        part, = overview.build_overview(self.root)['materials']
        self.assertEqual(part['quantity'], .9)
        self.assertEqual(part['stock'], 5)

    def test_window_overview_filters_refresh_and_revision_navigation(self):
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        revision = self.bom(self.first, 'PCB1', [self.line('CMP-IC', 7), self.line('CMP-R'), self.line('CMP-CONN')])
        self.bom(self.first, 'PCB1', [self.line('CMP-MCU', 99)], revision='-X100')
        window = ProductTreeWindow(root)
        def wait_job():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                root.update()
                if not window.busy and window.overview_pending is None:
                    return
                time.sleep(.02)
            self.fail('Overview did not finish')
        try:
            with patch('tkinter.messagebox.showerror') as error:
                wait_job()
                window.tree.selection_set(f'p{self.root}')
                window.select_revision()
                wait_job()
                panel = window.overview
                self.assertTrue(panel.winfo_manager())
                self.assertFalse(window.material_panel.winfo_manager())
                self.assertEqual(panel.report['summary']['obsolete'], 1)
                self.assertEqual(panel.report['cards'][0]['revision_id'], revision)
                panel.filter_materials('Critical (IC / MOSFET)')
                self.assertEqual(len(panel.materials.get_children()), 1)
                self.assertEqual(panel.materials.set(panel.materials.get_children()[0], 'br'), 'CMP-IC')
                panel.filter_materials('Stock shortages')
                self.assertEqual(panel.materials.set(panel.materials.get_children()[0], 'shortage'), '2')
                self.assertTrue(panel.advice.get_children())
                with db.connection(commit=True) as conn:
                    conn.execute("UPDATE IC SET Product_Status='Active',Brensan_Stock=20")
                window.run(model.component_catalog, window.loaded)
                wait_job()
                self.assertEqual(panel.report['summary']['obsolete'], 0)
                self.assertEqual(panel.report['summary']['shortages'], 0)
                panel.cards.selection_set('0')
                panel.open_selected_card()
                root.update()
                self.assertEqual(window.revision_id, revision)
                self.assertFalse(panel.winfo_manager())
                self.assertTrue(window.material_panel.winfo_manager())
                self.assertEqual(len(window.lines.get_children()), 3)
                window.tree.selection_set(f'p{self.root}')
                window.select_revision()
                wait_job()
                self.assertEqual(panel.filter.get(), 'All materials')
                self.assertEqual(panel.tabs.select(), str(panel.materials_page))
                self.assertEqual(len(panel.materials.get_children()), 3)
                error.assert_not_called()
        finally:
            wait_job()
            window.close()
            root.destroy()

    def test_window_discards_late_project_results_and_shows_load_errors(self):
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        window = ProductTreeWindow(root)
        release = threading.Event()
        entered = threading.Event()
        def wait_for(condition):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                root.update()
                if condition():
                    return
                time.sleep(.02)
            self.fail('Pending overview did not settle')
        try:
            wait_for(lambda: not window.busy and window.overview_pending is None)
            original = overview.build_overview
            def slow(project_id):
                report = original(project_id)
                entered.set()
                release.wait(8)
                return report
            with patch.object(overview, 'build_overview', side_effect=slow):
                window.tree.selection_set(f'p{self.first}')
                window.select_revision()
                wait_for(entered.is_set)
                window.tree.selection_set(f'p{self.second}')
                window.select_revision()
                release.set()
                wait_for(lambda: not window.busy and window.overview_pending is None)
            self.assertEqual(window.overview.report['project']['id'], self.second)
            with patch.object(overview, 'build_overview', side_effect=RuntimeError('Test unavailable')):
                window.tree.selection_set(f'p{self.root}')
                window.select_revision()
                wait_for(lambda: not window.busy and window.overview_pending is None)
            self.assertIsNone(window.overview.report)
            self.assertIn('Test unavailable', window.overview.summary.get())
        finally:
            release.set()
            wait_for(lambda: not window.busy and window.overview_pending is None)
            window.close()
            root.destroy()
