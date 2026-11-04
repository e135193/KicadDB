import gc
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

import db
import product_tree as model
import product_tree_export as exporter


class TestProductTreeExport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'parts.sqlite')
        self.connection_patch = patch.object(db, 'get_connection', lambda: sqlite3.connect(self.path))
        self.connection_patch.start()
        with db.connection(commit=True) as conn:
            conn.execute('CREATE TABLE Users (Username TEXT UNIQUE,Role TEXT,Is_Active INTEGER)')
            conn.executemany('INSERT INTO Users VALUES (?,?,1)', [('admin', 'admin'), ('engineer', 'user')])
            conn.execute('CREATE TABLE Parts (id INTEGER PRIMARY KEY,Brensan_Part_Number TEXT,Manufacturer_Part_Number TEXT,Manufacturer TEXT,Brensan_Stock REAL)')
            conn.executemany('INSERT INTO Parts VALUES (?,?,?,?,?)',
                [(1, 'CMP-1', '000123', 'Maker', 100.5), (2, 'CMP-2', '=PART', 'Other', 0)])
        db.set_current_user('admin')
        self.catalog = model.component_catalog()
        self.identity = self.catalog[0]['id']
        self.root_project = model.save_project('MAIN', 'Main project')
        self.sub1 = model.save_project('SUB1', 'First subproject', parent_id=self.root_project)
        self.sub2 = model.save_project('SUB2', 'Second subproject', parent_id=self.root_project)
        self.outside = model.save_project('OUT', 'Outside project')

    def tearDown(self):
        gc.collect()
        db.set_current_user('')
        self.connection_patch.stop()
        self.temp.cleanup()

    def line(self, **changes):
        return dict(dict(br='CMP-1', mpn='000123', manufacturer='Maker', description='Resistor',
                         quantity=3, references='R1,R2,R3', component_id=self.identity), **changes)

    def bom(self, project_id, number, revision='A', quantity=1, lines=None):
        projects = {p['id']: p for p in model.list_projects()}
        project = projects[project_id]
        parent = projects.get(project['parent_id'])
        root = parent or project
        rid = model.create_card(project_id, number, number + ' card', revision, quantity)
        if lines is not None:
            metadata = dict(project_code=root['code'], project_name=root['name'],
                subproject_code=project['code'] if parent else '', subproject_name=project['name'] if parent else '',
                board_number=number, board_name=number + ' card', revision=revision, board_quantity=quantity,
                status='Draft', notes='Saved notes')
            model.save_bom(metadata, lines, 'BOM.xlsx', replace=True, existing_definitions=True)
        return rid

    def test_project_combines_current_boms_in_root_and_subprojects_with_board_quantities(self):
        a = self.bom(self.sub1, 'PCB1', quantity=2, lines=[self.line()])
        self.bom(self.sub1, 'PCB1', revision='B', quantity=2, lines=[self.line(quantity=5, references='R1-R5')])
        b = self.bom(self.sub2, 'PCB2', quantity=3, lines=[self.line(quantity=2, references='R8,R9')])
        self.bom(self.root_project, 'DIRECT', quantity=1, lines=[self.line(quantity=1, references='R0')])
        model.link_card_revision(self.sub1, b, 4)
        self.bom(self.outside, 'EXCLUDED', quantity=99, lines=[self.line(quantity=99)])
        self.bom(self.sub2, 'EMPTY')
        db.set_current_user('engineer')
        report = exporter.build_export(self.root_project)
        self.assertEqual(len(report['records']), 1)
        # PCB1 current B: 5*2; PCB2 appears in both subprojects: 2*3 + 2*4; DIRECT: 1.
        self.assertEqual(report['records'][0]['Quantity'], 25)
        self.assertEqual(report['empty_cards'], 1)
        self.assertEqual(len(report['cards']), 5)
        self.assertNotIn('EXCLUDED', report['records'][0]['Reference'])
        self.assertNotIn('PCB1 / Rev. A', report['records'][0]['Reference'])
        self.assertIn('SUB1 / PCB1 / Rev. B: R1-R5', report['records'][0]['Reference'])
        self.assertEqual(exporter.build_export(self.sub1)['records'][0]['Quantity'], 18)
        self.assertEqual(exporter.build_export(self.sub1, revision_id=a)['records'][0]['Quantity'], 3)

    def test_card_export_uses_current_revision_per_board_and_retains_history(self):
        a = self.bom(self.sub1, 'PCB1', quantity=9, lines=[self.line(quantity=7)])
        b = self.bom(self.sub1, 'PCB1', revision='B', quantity=9, lines=[self.line(quantity=4)])
        board_id = model.revision_for('PCB1', 'B')['board_id']
        report = exporter.build_export(self.sub1, board_id=board_id)
        self.assertFalse(report['consolidated'])
        self.assertEqual(report['records'][0]['Quantity'], 4)
        self.assertEqual(report['cards'][0]['revision_id'], b)
        self.assertEqual(report['records'][0]['Reference'], 'R1,R2,R3')
        self.assertEqual(exporter.build_export(self.sub1, revision_id=a)['records'][0]['Quantity'], 7)
        with self.assertRaises(ValueError):
            exporter.build_export(self.sub2, board_id=board_id)
        model.detach_project_item(self.sub1, revision_id=b)
        with self.assertRaisesRegex(ValueError, 'current BOM'):
            exporter.build_export(self.sub1, board_id=board_id)
        self.assertEqual(exporter.build_export(self.sub1)['records'], [])
        self.assertEqual(len(exporter.build_export(self.sub1, revision_id=a)['records']), 1)

    def test_export_keeps_reused_root_and_subproject_codes_in_their_own_scope(self):
        child = model.save_project('MAIN', 'Same code child', parent_id=self.root_project)
        revision = self.bom(self.root_project, 'PCB1', quantity=2, lines=[self.line(quantity=3)])
        model.link_card_revision(child, revision, 4)
        self.bom(self.outside, 'OUTSIDE', quantity=100, lines=[self.line(quantity=100)])
        report = exporter.build_export(self.root_project)
        self.assertEqual(report['records'][0]['Quantity'], 18)
        references = report['records'][0]['Reference'].splitlines()
        self.assertEqual(len(references), 2)
        self.assertTrue(any(ref.startswith('MAIN / PCB1') for ref in references))
        self.assertTrue(any(ref.startswith('MAIN / MAIN / PCB1') for ref in references))
        self.assertEqual(exporter.build_export(child)['records'][0]['Quantity'], 12)

    def test_common_parts_merge_by_match_br_or_complete_manufacturer_mpn(self):
        self.bom(self.sub1, 'PCB1', quantity=2, lines=[self.line(quantity=0.1),
            self.line(br='ONLY-BR', component_id=None, mpn='', manufacturer='', quantity=1),
            self.line(br='', mpn='SAME', manufacturer='Maker A', component_id=None, quantity=1),
            self.line(br='', mpn='UNKNOWN', manufacturer='', component_id=None, quantity=1)])
        self.bom(self.sub2, 'PCB2', quantity=3, lines=[self.line(component_id=None, quantity=0.2),
            self.line(br='only-br', component_id=None, mpn='', manufacturer='', quantity=2),
            self.line(br='', mpn='same', manufacturer='maker a', component_id=None, quantity=2),
            self.line(br='', mpn='SAME', manufacturer='Maker B', component_id=None, quantity=4),
            self.line(br='', mpn='UNKNOWN', manufacturer='', component_id=None, quantity=1)])
        records = exporter.build_export(self.root_project)['records']
        self.assertEqual(len(records), 6)
        common = next(r for r in records if r['Company Part Number'] == 'CMP-1')
        self.assertEqual(common['Quantity'], 0.8)
        self.assertEqual(sorted(r['Quantity'] for r in records), [0.8, 2, 3, 8, 8, 12])

    def test_distinct_component_matches_with_same_br_are_not_combined(self):
        self.bom(self.sub1, 'PCB1', lines=[self.line(quantity=2)])
        self.bom(self.sub2, 'PCB2', lines=[self.line(component_id=self.catalog[1]['id'], quantity=4),
                                         self.line(component_id=None, quantity=8)])
        records = exporter.build_export(self.root_project)['records']
        self.assertEqual(len(records), 3)
        self.assertEqual(sorted(r['Quantity'] for r in records), [2, 4, 8])

    def test_live_stock_is_not_multiplied_or_summed_across_cards_and_revisions(self):
        original = self.bom(self.sub1, 'PCB1', quantity=10, lines=[self.line(quantity=5)])
        current = self.bom(self.sub1, 'PCB1', revision='B', quantity=20, lines=[self.line(quantity=7)])
        other = self.bom(self.sub2, 'PCB2', quantity=30, lines=[self.line(quantity=2)])
        model.link_card_revision(self.sub1, other, 4)
        board = model.revision_for('PCB1', 'B')['board_id']
        scopes = [(self.root_project, {}, 208), (self.sub1, {'board_id': board}, 7),
                  (self.sub1, {'revision_id': original}, 5),
                  (self.sub1, {'board_id': board, 'selections': [(self.sub1, original), (self.sub1, current)]}, 12)]
        for stock in (100.5, 0, 27):
            with db.connection(commit=True) as conn:
                conn.execute('UPDATE Parts SET Brensan_Stock=? WHERE id=1', (stock,))
            for project, options, quantity in scopes:
                with self.subTest(stock=stock, project=project, options=options):
                    record, = exporter.build_export(project, **options)['records']
                    self.assertEqual(record['Quantity'], quantity)
                    self.assertEqual(record['Company Stock'], stock)

    def test_stock_lookup_is_read_only_and_requires_unambiguous_fallback_matches(self):
        with db.connection(commit=True) as conn:
            conn.executemany('INSERT INTO Parts VALUES (?,?,?,?,?)', [
                (3, 'CMP-NEW', 'NEW', 'Maker', 17),
                (4, 'CMP-DUP', 'DUP', 'Maker A', 11),
                (5, 'CMP-DUP', 'DUP', 'Maker B', 42)])
            conn.execute('CREATE TABLE Legacy (id INTEGER PRIMARY KEY,Brensan_Part_Number TEXT)')
            conn.execute("INSERT INTO Legacy VALUES (1,'CMP-NO-STOCK')")
        cases = [(' br-1 ', '', '', 100.5), ('', '000123', 'maker', 100.5),
                 ('CMP-NEW', '', '', 17), ('CMP-DUP', 'DUP', 'Maker A', None),
                 ('', 'DUP', '', None), ('', 'DUP', 'Maker B', 42),
                 ('CMP-NO-STOCK', '', '', None), ('MISSING', '', '', None)]
        revision = self.bom(self.sub1, 'PCB1', lines=[self.line(
            br=br, mpn=mpn, manufacturer=maker, component_id=None) for br, mpn, maker, stock in cases])
        with db.connection() as conn:
            before = list(conn.iterdump())
        def read_only_connection():
            conn = sqlite3.connect(self.path)
            conn.execute('PRAGMA query_only=ON')
            return conn
        with patch.object(db, 'get_connection', read_only_connection):
            report = exporter.build_export(self.sub1, revision_id=revision)
        self.assertEqual([r['Company Stock'] for r in report['records']], [case[3] for case in cases])
        with db.connection() as conn:
            self.assertEqual(list(conn.iterdump()), before)

    def test_stock_follows_component_identity_and_handles_deleted_or_unavailable_values(self):
        revision = self.bom(self.sub1, 'PCB1', lines=[self.line(br='CMP-2', mpn='=PART')])
        # A saved match is authoritative even if the imported identifiers are stale.
        self.assertEqual(exporter.build_export(self.sub1, revision_id=revision)['records'][0]['Company Stock'], 100.5)
        for unavailable in (None, '', 'unknown', float('inf')):
            with db.connection(commit=True) as conn:
                conn.execute('UPDATE Parts SET Brensan_Stock=? WHERE id=1', (unavailable,))
            self.assertIsNone(exporter.build_export(self.sub1, revision_id=revision)['records'][0]['Company Stock'])
        with db.connection(commit=True) as conn:
            conn.execute('DELETE FROM Parts WHERE id=1')
        self.assertIsNone(exporter.build_export(self.sub1, revision_id=revision)['records'][0]['Company Stock'])

    def test_excel_stock_distinguishes_numeric_zero_from_unavailable(self):
        revision = self.bom(self.sub1, 'PCB1', lines=[self.line(),
            self.line(br='CMP-2', mpn='=PART', component_id=self.catalog[1]['id']),
            self.line(br='MISSING', mpn='', component_id=None)])
        path = os.path.join(self.temp.name, 'Stock.xlsx')
        exporter.write_export(path, exporter.build_export(self.sub1, revision_id=revision))
        book = load_workbook(path)
        try:
            self.assertEqual(book['BOM']['F1'].value, 'Company Stock')
            self.assertEqual([book['BOM'].cell(row, 6).value for row in (2, 3, 4)], [100.5, 0, None])
            self.assertEqual([book['BOM'].cell(row, 6).data_type for row in (2, 3)], ['n', 'n'])
            self.assertEqual(book['BOM']['F2'].number_format, '0.######')
            self.assertEqual(book['BOM']['G2'].value, 'R1,R2,R3')
        finally:
            book.close()

    def test_excel_has_numeric_totals_literal_identifiers_and_empty_card_traceability(self):
        self.bom(self.sub1, 'PCB1', quantity=3, lines=[self.line(mpn='=HYPERLINK("bad")', description='First\nSecond')])
        self.bom(self.sub2, 'EMPTY')
        report = exporter.build_export(self.root_project)
        path = os.path.join(self.temp.name, 'Project.xlsx')
        exporter.write_export(path, report)
        book = load_workbook(path)
        try:
            self.assertEqual(book.sheetnames, ['BOM', 'Cards'])
            self.assertEqual(tuple(cell.value for cell in book['BOM'][1]), exporter.BOM_COLUMNS)
            self.assertEqual((book['BOM']['B2'].value, book['BOM']['B2'].data_type), ('=HYPERLINK("bad")', 's'))
            self.assertEqual((book['BOM']['E2'].value, book['BOM']['E2'].data_type), (9, 'n'))
            self.assertEqual((book['BOM']['F2'].value, book['BOM']['F2'].data_type), (100.5, 'n'))
            self.assertEqual(book['BOM']['D2'].value, 'First\nSecond')
            self.assertEqual(book['BOM'].freeze_panes, 'A2')
            self.assertEqual(book['BOM'].auto_filter.ref, 'A1:G2')
            self.assertTrue(book['BOM']['A1'].font.bold)
            self.assertEqual(book['Cards']['I3'].value, 'Empty BOM')
        finally:
            book.close()
        with open(path, 'rb') as saved:
            before = saved.read()
        with patch('product_tree_export.os.replace', side_effect=PermissionError('File is open')):
            with self.assertRaises(PermissionError):
                exporter.write_export(path, report)
        with open(path, 'rb') as saved:
            self.assertEqual(saved.read(), before)
        self.assertFalse(any(name.startswith('.bom-export-') for name in os.listdir(self.temp.name)))

    def test_revision_choices_include_history_in_scope_and_preserve_explicit_selection(self):
        original = self.bom(self.sub1, 'PCB1', quantity=2, lines=[self.line(quantity=5)])
        current = self.bom(self.sub1, 'PCB1', revision='B', quantity=3, lines=[self.line(quantity=7)])
        empty = self.bom(self.sub2, 'EMPTY')
        self.bom(self.outside, 'EXCLUDED', lines=[self.line()])
        before = {table: model.rows(f'SELECT * FROM {table}') for table in model.TABLES}
        choices = exporter.revision_choices(self.root_project)
        self.assertEqual({c['revision_id'] for c in choices['cards']}, {original, current, empty})
        self.assertEqual({c['revision_id'] for c in choices['cards'] if c['selected']}, {current, empty})
        self.assertEqual(next(c['line_count'] for c in choices['cards'] if c['revision_id'] == empty), 0)
        chosen = exporter.revision_choices(self.sub1, revision_id=original)
        self.assertFalse(chosen['consolidated'])
        self.assertEqual({c['revision_id'] for c in chosen['cards']}, {original, current})
        self.assertEqual([c['revision_id'] for c in chosen['cards'] if c['selected']], [original])
        self.assertEqual({table: model.rows(f'SELECT * FROM {table}') for table in model.TABLES}, before)

    def test_explicit_project_revisions_use_selected_links_quantities_and_scope(self):
        original = self.bom(self.sub1, 'PCB1', quantity=2, lines=[self.line(quantity=5)])
        current = self.bom(self.sub1, 'PCB1', revision='B', quantity=3, lines=[self.line(quantity=7)])
        other = self.bom(self.sub2, 'PCB2', quantity=4, lines=[self.line(quantity=2)])
        model.link_card_revision(self.sub2, original, 6)
        selection = [(self.sub1, original), (self.sub2, other)]
        report = exporter.build_export(self.root_project, selections=selection)
        self.assertEqual(report['records'][0]['Quantity'], 18)
        self.assertEqual({(c['project_id'], c['revision_id']) for c in report['cards']}, set(selection))
        self.assertNotIn('PCB1 / Rev. B', report['records'][0]['Reference'])
        self.assertNotIn('SUB2 / PCB1', report['records'][0]['Reference'])
        report = exporter.build_export(self.root_project, selections=[(self.sub1, original), (self.sub1, current)])
        self.assertEqual(report['records'][0]['Quantity'], 31)
        self.assertEqual(model.bom_edit_snapshot(self.sub1, current)['active'], 1)
        self.assertEqual(model.bom_edit_snapshot(self.sub1, original)['active'], 0)

    def test_multiple_card_revisions_export_one_board_each_and_trace_them_in_excel(self):
        original = self.bom(self.sub1, 'PCB1', quantity=10, lines=[self.line(quantity=5)])
        current = self.bom(self.sub1, 'PCB1', revision='B', quantity=20, lines=[self.line(quantity=7)])
        board = model.revision_for('PCB1', 'B')['board_id']
        report = exporter.build_export(self.sub1, board_id=board,
            selections=[(self.sub1, original), (self.sub1, current), (self.sub1, current)])
        self.assertEqual(report['records'][0]['Quantity'], 12)
        self.assertFalse(report['consolidated'])
        self.assertEqual(len(report['cards']), 2)
        self.assertIn('Rev. A', report['records'][0]['Reference'])
        self.assertIn('Rev. B', report['records'][0]['Reference'])
        path = os.path.join(self.temp.name, 'Chosen.xlsx')
        exporter.write_export(path, report)
        book = load_workbook(path)
        try:
            self.assertEqual([book['Cards'].cell(row, 5).value for row in (2, 3)], ['A', 'B'])
            self.assertEqual(book['BOM']['E2'].value, 12)
            self.assertNotIn('current revisions only', book.properties.description)
        finally:
            book.close()

    def test_selected_revisions_cannot_be_empty_stale_or_outside_scope(self):
        original = self.bom(self.sub1, 'PCB1', lines=[self.line()])
        outside = self.bom(self.outside, 'OUTSIDE', lines=[self.line()])
        board = model.revision_for('PCB1', 'A')['board_id']
        for selected in ([], [(self.outside, outside)], [(self.sub2, original)], [(self.sub1, 999)]):
            with self.subTest(selection=selected), self.assertRaises(ValueError):
                exporter.build_export(self.root_project, selections=selected)
        model.detach_project_item(self.sub1, revision_id=original)
        with self.assertRaisesRegex(ValueError, 'no longer linked'):
            exporter.build_export(self.sub1, board_id=board, selections=[(self.sub1, original)])
        with self.assertRaisesRegex(ValueError, 'no longer linked'):
            exporter.revision_choices(self.sub1, revision_id=original)

    def test_window_exports_selected_project_card_and_revision_from_toolbar(self):
        import tkinter as tk
        from ui.product_tree_window import ProductTreeWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        original = self.bom(self.sub1, 'PCB1', quantity=2, lines=[self.line(quantity=5)])
        current = self.bom(self.sub1, 'PCB1', revision='B', quantity=2, lines=[self.line(quantity=3)])
        self.bom(self.sub2, 'PCB2', quantity=4, lines=[self.line(quantity=2)])
        board = model.revision_for('PCB1', 'B')['board_id']
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
            with patch('ui.product_tree_window.messagebox.showerror') as error:
                wait_job()
                self.assertIs(window.export_bom_button.master, window.obsolete_button.master)
                self.assertEqual(window.obsolete_button.cget('text'), 'Obsolete Products')
                self.assertFalse(any(w.winfo_class() == 'TCheckbutton' and w.cget('text') == 'Show previous revisions'
                                     for w in descendants(window)))
                self.assertTrue(window.tree.exists(f'p{self.sub1}r{original}'))
                self.assertTrue(window.tree.exists(f'p{self.sub1}r{current}'))
                for index, (node, quantity) in enumerate([(f'p{self.root_project}', 14),
                    (f'p{self.sub1}b{board}', 3), (f'p{self.sub1}r{original}', 5)]):
                    window.tree.selection_set(node)
                    window.select_revision()
                    self.assertEqual(str(window.export_bom_button.cget('state')), 'normal')
                    path = os.path.join(self.temp.name, f'Export{index}.xlsx')
                    with patch('ui.product_tree_window.filedialog.asksaveasfilename', return_value=path) as save_dialog:
                        window.export_bom_button.invoke()
                        wait_job()
                        save_dialog.assert_not_called()
                        chooser = window.export_revision_window
                        expected = [(self.sub1, current)] if index == 1 else [(self.sub1, original)] if index == 2 else None
                        if expected:
                            self.assertEqual(chooser.selected_links(), expected)
                        self.assertEqual(chooser.table.winfo_class(), 'Treeview')
                        self.assertEqual(len(chooser.table.get_children()), len(chooser.cards))
                        self.assertTrue(all(not chooser.table.get_children(row) for row in chooser.table.get_children()))
                        chooser.export_button.invoke()
                        wait_job()
                    save_dialog.assert_called_once()
                    book = load_workbook(path)
                    try:
                        self.assertEqual(book['BOM']['E2'].value, quantity)
                    finally:
                        book.close()
                with patch('ui.product_tree_window.filedialog.asksaveasfilename', return_value=''), \
                     patch('product_tree_export.write_export') as write:
                    window.export_bom_button.invoke()
                    wait_job()
                    window.export_revision_window.export_button.invoke()
                    wait_job()
                write.assert_not_called()

                # The picker can replace defaults with an old revision and exclude another card.
                window.tree.selection_set(f'p{self.root_project}')
                window.select_revision()
                path = os.path.join(self.temp.name, 'Historical.xlsx')
                with patch('ui.product_tree_window.filedialog.asksaveasfilename', return_value=path) as save_dialog:
                    window.export_bom_button.invoke()
                    wait_job()
                    chooser = window.export_revision_window
                    chooser.select_all(False)
                    self.assertEqual(str(chooser.export_button['state']), 'disabled')
                    chooser.select_all(True)
                    self.assertEqual(len(chooser.selected_links()), 3)
                    chooser.select_current()
                    self.assertEqual(len(chooser.selected_links()), 2)
                    chooser.select_all(False)
                    row = chooser.row_ids[(self.sub1, original)]
                    x, y, width, height = chooser.table.bbox(row, 'include')
                    chooser.table.event_generate('<ButtonPress-1>', x=x + width // 2, y=y + height // 2)
                    chooser.table.event_generate('<ButtonRelease-1>', x=x + width // 2, y=y + height // 2)
                    root.update()
                    self.assertEqual(chooser.selected_links(), [(self.sub1, original)])
                    self.assertEqual(chooser.table.set(row, 'include'), '☑')
                    chooser.export_button.invoke()
                    wait_job()
                book = load_workbook(path)
                try:
                    self.assertEqual(book['BOM']['E2'].value, 10)
                    self.assertEqual(book['Cards'].max_row, 2)
                    self.assertEqual(book['Cards']['E2'].value, 'A')
                finally:
                    book.close()
                with patch('ui.product_tree_window.filedialog.asksaveasfilename') as save_dialog, \
                     patch('product_tree_export.write_export') as write:
                    window.export_bom_button.invoke()
                    wait_job()
                    window.export_revision_window.cancel()
                    save_dialog.assert_not_called()
                    write.assert_not_called()
                window.tree.selection_set(f'p{self.outside}')
                window.select_revision()
                with patch('ui.product_tree_window.messagebox.showinfo') as info, \
                     patch('ui.product_tree_window.filedialog.asksaveasfilename') as save_dialog:
                    window.export_bom_button.invoke()
                    wait_job()
                info.assert_called_once()
                save_dialog.assert_not_called()
                window.tree.selection_set((f'p{self.sub1}', f'p{self.sub2}'))
                window.select_revision()
                self.assertEqual(str(window.export_bom_button.cget('state')), 'disabled')
                error.assert_not_called()
        finally:
            wait_job()
            window.close()
            root.destroy()

    def test_compact_revision_table_keyboard_selection_and_scrolling(self):
        import tkinter as tk
        from unittest.mock import Mock
        from ui.export_revision_window import ExportRevisionWindow
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        cards = [dict(project_id=1, board_id=i // 2, revision_id=i + 1, project_code='P1',
                      parent_code='TOP', project_name='Subproject', part_number=f'PCB-{i // 2:03}',
                      board_name='Example board', revision='A' if i % 2 == 0 else 'B', status='Draft',
                      board_quantity=2, line_count=0 if i == 63 else 10, active=i % 2, selected=bool(i % 2))
                 for i in range(64)]
        exported, cancelled = Mock(), Mock()
        chooser = ExportRevisionWindow(root, {'title': 'Test project', 'cards': cards, 'consolidated': True},
                                       exported, cancelled)
        try:
            root.update()
            self.assertLessEqual(chooser.winfo_height(), 500)
            self.assertEqual(len(chooser.table.get_children()), 64)
            self.assertIn('1 empty BOMs', chooser.summary.get())
            self.assertEqual(chooser.table.set('0', 'project'), 'TOP / P1 — Subproject')
            self.assertEqual(chooser.table.set('1', 'current'), 'Yes')
            self.assertEqual(chooser.table.set('63', 'lines'), 'Empty')
            self.assertLess(chooser.table.yview()[1], 1)
            chooser.select_all(False)
            chooser.table.selection_set(('0', '1'))
            chooser.table.focus_force()
            root.update()
            chooser.table.event_generate('<space>')
            root.update()
            self.assertEqual(chooser.selected_links(), [(1, 1), (1, 2)])
            chooser.table.event_generate('<space>')
            root.update()
            self.assertEqual(chooser.selected_links(), [])
            chooser.table.event_generate('<Control-a>')
            root.update()
            self.assertEqual(len(chooser.table.selection()), 64)
            self.assertEqual(chooser.selected_links(), [])
            chooser.table.event_generate('<space>')
            root.update()
            self.assertEqual(len(chooser.selected_links()), 64)
            chooser.table.see('63')
            root.update()
            self.assertTrue(chooser.table.bbox('63'))
            self.assertGreater(chooser.table.yview()[0], 0)
            chooser.select_current()
            self.assertEqual(len(chooser.selected_links()), 32)
            chooser.cancel()
            cancelled.assert_called_once_with()
            exported.assert_not_called()
        finally:
            if chooser.winfo_exists():
                chooser.destroy()
            root.destroy()
