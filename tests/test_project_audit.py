"""Project definitions: permission check and audit trail (product_tree.save_project / delete_project)."""
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import db
import product_tree as pt


class ProjectAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "parts.sqlite")
        self.mock = patch.object(db, "get_connection", lambda: sqlite3.connect(self.path))
        self.mock.start()
        with db.connection(commit=True) as conn:
            conn.execute('CREATE TABLE Users (Username TEXT UNIQUE COLLATE NOCASE, Role TEXT, Is_Active INTEGER)')
            conn.executemany('INSERT INTO Users VALUES (?,?,?)',
                             [('admin', 'admin', 1), ('engineer', 'user', 1), ('inactive', 'user', 0)])
            conn.execute('CREATE TABLE Resistor (id INTEGER PRIMARY KEY, Brensan_Part_Number TEXT, '
                         'Manufacturer TEXT, Manufacturer_Part_Number TEXT, Brensan_Stock REAL)')
        db.set_current_user('admin')
        pt.ensure_schema()

    def tearDown(self):
        db.set_current_user('')
        self.mock.stop()
        self.temp.cleanup()

    def log_rows(self, table='PT_Projects'):
        columns, rows = db.get_change_log(table=table)
        rows = [dict(zip(columns, r)) for r in rows]
        for row in rows:
            row['Details'] = json.loads(row['Details']) if row['Details'] else {}
        return list(reversed(rows))  # oldest first

    def test_active_user_can_create_and_edit_a_project(self):
        db.set_current_user('engineer')
        project = pt.save_project('P1', 'Project', 'Note', folder='C:/p1')
        pt.save_project('P1', 'Renamed', 'Note', project_id=project)
        self.assertEqual(pt.list_projects()[0]['name'], 'Renamed')

    def test_inactive_or_unknown_account_cannot_create_or_edit_projects(self):
        project = pt.save_project('P1', 'Project')
        for actor in ('inactive', 'nobody', ''):
            db.set_current_user(actor)
            with self.assertRaises(PermissionError, msg=actor):
                pt.save_project('P2', 'Other')
            with self.assertRaises(PermissionError, msg=actor):
                pt.save_project('P1', 'Renamed', project_id=project)
        db.set_current_user('admin')
        self.assertEqual([p['code'] for p in pt.list_projects()], ['P1'])
        self.assertEqual(pt.list_projects()[0]['name'], 'Project')

    def test_create_and_update_are_recorded_with_old_and_new_values(self):
        db.set_current_user('engineer')
        project = pt.save_project('P1', 'Project', 'Note', folder='C:/p1')
        parent = pt.save_project('TOP', 'Top level')
        pt.save_project('P1', 'Renamed', 'New note', project_id=project, folder='C:/p1-new', parent_id=parent)
        # Saving identical values again must not add a log row.
        pt.save_project('P1', 'Renamed', 'New note', project_id=project, folder='C:/p1-new', parent_id=parent)

        rows = self.log_rows()
        self.assertEqual([r['Action'] for r in rows], ['PROJECT_CREATE', 'PROJECT_CREATE', 'PROJECT_UPDATE'])
        self.assertTrue(all(r['Changed_By'] == 'engineer' for r in rows))
        created = rows[0]
        self.assertEqual(created['Record_Key'], str(project))
        self.assertEqual(created['Part_Number'], 'P1')
        self.assertEqual(created['Details']['name'], 'Project')
        self.assertEqual(created['Details']['folder'], 'C:/p1')
        changes = rows[2]['Details']['changes']
        self.assertEqual(changes['name'], {'old': 'Project', 'new': 'Renamed'})
        self.assertEqual(changes['notes'], {'old': 'Note', 'new': 'New note'})
        self.assertEqual(changes['folder'], {'old': 'C:/p1', 'new': 'C:/p1-new'})
        self.assertEqual(changes['parent_id'], {'old': None, 'new': parent})
        self.assertNotIn('code', changes)

    def test_failed_save_leaves_no_log_row(self):
        pt.save_project('P1', 'Project')
        with self.assertRaises(ValueError):
            pt.save_project('P1', 'Duplicate code')
        with self.assertRaises(ValueError):
            pt.save_project('X', 'Missing', project_id=999)
        self.assertEqual([r['Action'] for r in self.log_rows()], ['PROJECT_CREATE'])

    def test_delete_project_is_admin_only_and_recorded_with_its_links(self):
        project = pt.save_project('P1', 'Project', 'Note')
        revision = pt.create_card(project, 'PCB1', 'Card', 'A', quantity=2)
        db.set_current_user('engineer')
        with self.assertRaises(PermissionError):
            pt.delete_project(project)
        db.set_current_user('admin')
        pt.delete_project(project)
        self.assertEqual(pt.list_projects(), [])
        with self.assertRaises(ValueError):
            pt.delete_project(project)

        rows = [r for r in self.log_rows() if r['Action'] == 'PROJECT_DELETE']
        self.assertEqual(len(rows), 1)
        deleted = rows[0]
        self.assertEqual(deleted['Changed_By'], 'admin')
        self.assertEqual(deleted['Part_Number'], 'P1')
        self.assertEqual(deleted['Details']['project']['name'], 'Project')
        self.assertEqual(deleted['Details']['removed_links'],
                         [{'revision_id': revision, 'quantity': 2, 'active': 1}])

    def test_audit_lines_reach_the_log_file_in_english(self):
        import logging
        records = []

        class Collector(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Collector()
        db.audit_log.addHandler(handler)
        previous_level = db.audit_log.level
        db.audit_log.setLevel(logging.INFO)
        try:
            project = pt.save_project('P1', 'Project')
            pt.delete_project(project)
        finally:
            db.audit_log.removeHandler(handler)
            db.audit_log.setLevel(previous_level)
        self.assertTrue(records[0].startswith('PROJECT_CREATE user=admin table=PT_Projects record='))
        self.assertIn('part=P1', records[0])
        self.assertTrue(records[-1].startswith('PROJECT_DELETE user=admin'))


if __name__ == '__main__':
    unittest.main()
