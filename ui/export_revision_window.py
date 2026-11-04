"""Choose saved card revisions for a read-only BOM export."""
import tkinter as tk
from tkinter import ttk


class ExportRevisionWindow(tk.Toplevel):
    def __init__(self, parent, report, on_export, on_cancel):
        super().__init__(parent)
        self.title('Export BOM — Select revisions')
        self.geometry('1080x500')
        self.minsize(780, 350)
        self.transient(parent)
        self.on_export = on_export
        self.on_cancel = on_cancel
        self.cards = {}
        self.row_ids = {}
        self.checked = set()
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self.bind('<Escape>', lambda event: self.cancel())

        body = ttk.Frame(self, padding=10)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=report['title'], wraplength=1000).pack(anchor=tk.W)
        explanation = ('Project quantities apply to each checked revision.' if report['consolidated']
                       else 'One board is included per checked revision.')
        ttk.Label(body, text='Check rows or select rows and press Space. Multiple revisions add their quantities. ' + explanation,
                  wraplength=1000).pack(anchor=tk.W, pady=(4, 8))
        bar = ttk.Frame(body)
        bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(bar, text='Use current revisions', command=self.select_current).pack(side=tk.LEFT)
        ttk.Button(bar, text='Select all', command=lambda: self.select_all(True)).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text='Clear selection', command=lambda: self.select_all(False)).pack(side=tk.LEFT)

        list_frame = ttk.Frame(body)
        list_frame.pack(fill=tk.BOTH, expand=True)
        columns = [('include', 'Use', 45, 'center'), ('project', 'Project / subproject', 245, 'w'),
                   ('number', 'Card part number', 135, 'w'), ('name', 'Card name', 210, 'w'),
                   ('revision', 'Revision', 65, 'center'), ('current', 'Current', 60, 'center'),
                   ('status', 'Status', 75, 'w'), ('quantity', 'Boards / project', 100, 'e'),
                   ('lines', 'BOM rows', 80, 'e')]
        self.table = ttk.Treeview(list_frame, columns=[c[0] for c in columns], show='headings', selectmode='extended')
        for key, label, width, anchor in columns:
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=45 if key == 'include' else 55,
                              anchor=anchor, stretch=key in ('project', 'name'))
        self.table.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(list_frame, orient=tk.HORIZONTAL, command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.table.tag_configure('alternate', background='#f2f5f8')
        for index, card in enumerate(report['cards']):
            row = str(index)
            self.cards[row] = card
            self.row_ids[(card['project_id'], card['revision_id'])] = row
            if card['selected']:
                self.checked.add(row)
            path = f"{card['parent_code']} / {card['project_code']}" if card['parent_code'] else card['project_code']
            self.table.insert('', 'end', iid=row, values=(
                '☑' if card['selected'] else '☐', f"{path} — {card['project_name']}", card['part_number'],
                card['board_name'], card['revision'], 'Yes' if card['active'] else '', card['status'],
                f"{card['board_quantity']:g}", card['line_count'] if card['line_count'] else 'Empty'),
                tags=('alternate',) if index % 2 else ())
        self.table.bind('<Button-1>', self.click_checkbox)
        self.table.bind('<Double-1>', self.double_click_row)
        self.table.bind('<space>', self.toggle_selected)
        for shortcut in ('<Control-a>', '<Control-A>'):
            self.table.bind(shortcut, self.highlight_all)
        footer = ttk.Frame(body)
        footer.pack(fill=tk.X, pady=(10, 0))
        self.summary = tk.StringVar(self)
        ttk.Label(footer, textvariable=self.summary).pack(side=tk.LEFT)
        self.export_button = ttk.Button(footer, text='Export selected', command=self.export)
        self.export_button.pack(side=tk.RIGHT)
        ttk.Button(footer, text='Cancel', command=self.cancel).pack(side=tk.RIGHT, padx=6)
        self.update_summary()
        self.grab_set()
        self.table.focus_set()
        if self.cards:
            row = next(iter(self.cards))
            self.table.focus(row)
            self.table.selection_set(row)

    def selected_links(self):
        return [(card['project_id'], card['revision_id']) for row, card in self.cards.items() if row in self.checked]

    def update_summary(self):
        selected = [card for row, card in self.cards.items() if row in self.checked]
        empty = sum(card['line_count'] == 0 for card in selected)
        self.summary.set(f'{len(selected)} revisions selected' + (f' · {empty} empty BOMs' if empty else ''))
        self.export_button.configure(state='normal' if selected else 'disabled')

    def select_all(self, selected):
        self.set_checked(self.cards, selected)

    def select_current(self):
        self.checked = {row for row, card in self.cards.items() if card['active']}
        for row in self.cards:
            self.table.set(row, 'include', '☑' if row in self.checked else '☐')
        self.update_summary()

    def set_checked(self, rows, checked):
        for row in rows:
            if checked:
                self.checked.add(row)
            else:
                self.checked.discard(row)
            self.table.set(row, 'include', '☑' if checked else '☐')
        self.update_summary()

    def click_checkbox(self, event):
        if self.table.identify_region(event.x, event.y) == 'cell' and self.table.identify_column(event.x) == '#1':
            row = self.table.identify_row(event.y)
            if row:
                self.table.focus_set()
                self.table.focus(row)
                self.table.selection_set(row)
                self.set_checked([row], row not in self.checked)
                return 'break'

    def double_click_row(self, event):
        if self.table.identify_region(event.x, event.y) == 'cell' and self.table.identify_column(event.x) != '#1':
            row = self.table.identify_row(event.y)
            if row:
                self.set_checked([row], row not in self.checked)
        return 'break'

    def toggle_selected(self, event=None):
        rows = self.table.selection()
        if rows:
            self.set_checked(rows, any(row not in self.checked for row in rows))
        return 'break'

    def highlight_all(self, event=None):
        self.table.selection_set(self.table.get_children())
        return 'break'

    def export(self):
        selected = self.selected_links()
        if selected:
            callback = self.on_export
            self.grab_release()
            self.destroy()
            callback(selected)

    def cancel(self):
        callback = self.on_cancel
        self.grab_release()
        self.destroy()
        callback()

    def destroy(self):
        super().destroy()
        # Release Tk variables on the UI thread before an export worker can collect the dialog.
        self.cards.clear()
        self.row_ids.clear()
        self.checked.clear()
        self.summary = None
        self.on_export = None
        self.on_cancel = None
