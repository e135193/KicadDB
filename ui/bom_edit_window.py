"""Edit a saved BOM without requiring its source spreadsheet."""
import tkinter as tk
from tkinter import ttk, messagebox

import pandas as pd
import product_tree as model
from ui.product_tree_window import table


class BomEditWindow(tk.Toplevel):
    def __init__(self, parent, snapshot):
        super().__init__(parent)
        self.owner = parent
        self.snapshot = snapshot
        self.records = [dict(row) for row in snapshot['lines']]
        self.title('Edit BOM')
        self.geometry('1080x640')
        self.minsize(850, 480)
        self.transient(parent)
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"{snapshot['part_number']} — {snapshot['board_name']} / Rev. {snapshot['revision']}").pack(anchor=tk.W)
        ttk.Label(body, text='BOM content is shared by every project using this revision. Saving changes preserves its approval status.',
                  wraplength=980).pack(anchor=tk.W, pady=(4, 10))
        bar = ttk.Frame(body)
        bar.pack(fill=tk.X, pady=(0, 8))
        for text, command in [('Add row', self.add_row), ('Edit row', self.edit_row),
                              ('Remove rows', self.remove_rows), ('Update match', self.update_match),
                              ('Clear match', self.clear_match)]:
            ttk.Button(bar, text=text, command=command).pack(side=tk.LEFT, padx=(0, 5))
        self.view = table(body, [('br', 'BR', 120), ('mpn', 'MPN', 140), ('manufacturer', 'Manufacturer', 110),
            ('description', 'Description', 190), ('quantity', 'Quantity / board', 100),
            ('references', 'References', 130), ('match', 'Matched component', 140)])
        self.view.bind('<Double-1>', lambda event: self.edit_row())
        details = ttk.Frame(body)
        details.pack(fill=tk.X, pady=8)
        ttk.Label(details, text='Boards / selected project:').pack(side=tk.LEFT)
        self.quantity = tk.StringVar(value=f"{snapshot['board_quantity']:g}")
        ttk.Entry(details, textvariable=self.quantity, width=12).pack(side=tk.LEFT, padx=8)
        ttk.Label(body, text='BOM description').pack(anchor=tk.W)
        self.notes = tk.Text(body, height=3, wrap=tk.WORD, undo=True)
        self.notes.pack(fill=tk.X)
        self.notes.insert('1.0', snapshot['notes'])
        footer = ttk.Frame(body)
        footer.pack(fill=tk.X, pady=(10, 0))
        self.save_button = ttk.Button(footer, text='Save BOM', command=self.save)
        self.save_button.pack(side=tk.RIGHT)
        ttk.Button(footer, text='Cancel', command=self.cancel).pack(side=tk.RIGHT, padx=8)
        self.render()
        self.grab_set()

    def render(self):
        self.view.delete(*self.view.get_children())
        catalog = {part['id']: part for part in self.owner.catalog}
        for index, row in enumerate(self.records):
            part = catalog.get(row.get('component_id'), {})
            match = part.get('cmp') or part.get('mpn') or ('Deleted component' if row.get('component_id') else 'Unmatched')
            self.view.insert('', 'end', iid=str(index), values=(row.get('cmp') or row.get('br'), row['mpn'], row['manufacturer'],
                row['description'], f"{row['quantity']:g}", row['references'], match))

    def selected_index(self):
        selected = self.view.selection()
        if len(selected) != 1:
            messagebox.showinfo('Edit BOM', 'Select one material row.', parent=self)
            return None
        return int(selected[0])

    def add_row(self):
        if not self.owner.busy:
            self.row_dialog()

    def edit_row(self):
        if self.owner.busy:
            return
        index = self.selected_index()
        if index is not None:
            self.row_dialog(index)

    def row_dialog(self, index=None):
        old = self.records[index] if index is not None else {}
        dialog = tk.Toplevel(self)
        dialog.title('Edit BOM row' if old else 'Add BOM row')
        dialog.transient(self)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        fields = dialog.fields = {}
        for key, label in [('cmp', 'CMP'), ('mpn', 'MPN'), ('manufacturer', 'Manufacturer'),
                           ('quantity', 'Quantity / board'), ('references', 'References')]:
            ttk.Label(body, text=label).pack(anchor=tk.W)
            fields[key] = tk.StringVar(value=str(old.get(key, 1 if key == 'quantity' else '')))
            ttk.Entry(body, textvariable=fields[key], width=60).pack(fill=tk.X, pady=(0, 6))
        ttk.Label(body, text='Description').pack(anchor=tk.W)
        description = dialog.description = tk.Text(body, width=45, height=4, wrap=tk.WORD)
        description.pack(fill=tk.X)
        description.insert('1.0', old.get('description', ''))
        ttk.Label(body, text='Changing BR, MPN or manufacturer recalculates the component match.',
                  wraplength=430).pack(anchor=tk.W, pady=8)
        def close():
            dialog.destroy()
            self.grab_set()
        def apply():
            values = {key: var.get() for key, var in fields.items()}
            values['description'] = description.get('1.0', 'end-1c')
            try:
                row = model.prepare_lines(pd.DataFrame([values]), {key: key for key in values}, self.owner.catalog)[0]
            except ValueError as exc:
                messagebox.showerror('Edit BOM row', str(exc), parent=dialog)
                return
            if old and all(row[key] == old[key] for key in ('cmp', 'mpn', 'manufacturer')):
                row['component_id'] = old.get('component_id')
            if index is None:
                self.records.append(row)
            else:
                self.records[index] = row
            self.render()
            self.view.selection_set(str(index if index is not None else len(self.records) - 1))
            close()
        ttk.Button(body, text='Apply row', command=apply).pack(side=tk.RIGHT, pady=6)
        ttk.Button(body, text='Cancel', command=close).pack(side=tk.RIGHT, padx=8, pady=6)
        dialog.protocol('WM_DELETE_WINDOW', close)
        dialog.grab_set()

    def remove_rows(self):
        if self.owner.busy:
            return
        selected = self.view.selection()
        if selected and messagebox.askyesno('Remove rows', f'Remove {len(selected)} selected material rows from this BOM?', parent=self):
            for index in sorted(map(int, selected), reverse=True):
                del self.records[index]
            self.render()

    def update_match(self):
        if self.owner.busy:
            return
        index = self.selected_index()
        if index is not None:
            def assign(identity):
                self.records[index]['component_id'] = identity
                self.render()
            self.owner.pick_component(assign, owner=self)

    def clear_match(self):
        if not self.owner.busy:
            for item in self.view.selection():
                self.records[int(item)]['component_id'] = None
            self.render()

    def save(self):
        if self.owner.busy or not self.owner.check_bom_edit(self.snapshot['id']):
            return
        notes, quantity = self.notes.get('1.0', 'end-1c'), self.quantity.get()
        try:
            model.positive(quantity)
        except ValueError as exc:
            messagebox.showerror('Save BOM', str(exc), parent=self)
            return
        if not messagebox.askyesno('Save BOM', 'Save this BOM for every project using this revision?\n'
                                  'Boards / project changes apply only to the selected project.', parent=self):
            return
        lines = [dict(row) for row in self.records]
        def done(_):
            self.destroy()
            self.owner.refresh_all()
            self.owner.status.set('BOM changes saved.')
        self.owner.run(lambda: model.update_saved_bom(self.snapshot, lines, notes, quantity), done)

    def cancel(self):
        if self.owner.busy:
            return
        changed = (self.records != self.snapshot['lines'] or self.notes.get('1.0', 'end-1c') != self.snapshot['notes']
                   or self.quantity.get() != f"{self.snapshot['board_quantity']:g}")
        if not changed or messagebox.askyesno('Edit BOM', 'Discard unsaved BOM changes?', parent=self):
            self.destroy()
