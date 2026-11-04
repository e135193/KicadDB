"""Product Tree window: projects, Excel import and reverse component usage."""
import os
import csv
import io
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont

import product_tree as model
import product_tree_export as bom_export
import project_tree_import as project_importer
import project_overview
from ui.project_overview_panel import ProjectOverviewPanel


def project_tree_label(code, name):
    suffix = re.search(r'-(X\d*|[A-WYZ])$', str(code).strip(), re.IGNORECASE)
    stage = ''
    if suffix:
        stage = 'Development' if suffix[1].upper().startswith('X') else 'Production'
    return f"{code}{' · ' + stage if stage else ''} — {name}"


def copy_selected_rows(view, event=None):
    """Copy selected rows in display order as Excel-compatible tabular text."""
    selected = set(view.selection())
    if not selected:
        return "break"
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t", lineterminator="\r\n")
    show_tree = "tree" in map(str, view.tk.splitlist(view.cget("show")))
    def visit(parent=""):
        for item in view.get_children(parent):
            if item in selected:
                values = list(view.item(item, "values"))
                if show_tree:
                    values.insert(0, view.item(item, "text"))
                writer.writerow(values)
            visit(item)
    visit()
    view.clipboard_clear()
    view.clipboard_append(output.getvalue())
    return "break"


def enable_copy(view):
    for shortcut in ("<Control-c>", "<Control-C>"):
        view.bind(shortcut, lambda event: copy_selected_rows(view, event))
    def select_all(event=None):
        if str(view.cget("selectmode")) != "extended":
            return "break"
        items = []
        def visit(parent=""):
            for item in view.get_children(parent):
                items.append(item)
                if view.item(item, "open"):
                    visit(item)
        visit()
        view.selection_set(items)
        return "break"
    for shortcut in ("<Control-a>", "<Control-A>"):
        view.bind(shortcut, select_all)


def table(parent, columns):
    frame = ttk.Frame(parent)
    frame.pack(fill=tk.BOTH, expand=True)
    view = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode="extended")
    enable_copy(view)
    for key, title, width in columns:
        view.heading(key, text=title)
        view.column(key, width=width, minwidth=65)
    view.grid(row=0, column=0, sticky="nsew")
    y = ttk.Scrollbar(frame, orient="vertical", command=view.yview)
    y.grid(row=0, column=1, sticky="ns")
    x = ttk.Scrollbar(frame, orient="horizontal", command=view.xview)
    x.grid(row=1, column=0, sticky="ew")
    view.configure(yscrollcommand=y.set, xscrollcommand=x.set)
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return view


class ProductTreeWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title('Product Tree')
        self.geometry("1180x720")
        self.minsize(850, 560)
        self.catalog = []
        self.revision_id = None
        self.tree_rows = {}
        self.projects = {}
        self.saved_lines = {}
        self.usage_data = []
        self.jobs = queue.Queue()
        self.busy = False
        self.closed = False
        self.overview_target = None
        self.overview_version = 0
        self.overview_pending = None
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value='Loading...')
        self.history = tk.BooleanVar(value=False)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        self.tree_tab = ttk.Frame(self.tabs, padding=10)
        self.usage_tab = ttk.Frame(self.tabs, padding=10)
        for page, label in [(self.tree_tab, 'Product tree'), (self.usage_tab, 'Where used?')]:
            self.tabs.add(page, text=label)
        self.build_tree()
        self.build_usage()
        self.tabs.bind('<<NotebookTabChanged>>', lambda event: self.apply_bom_permissions())
        ttk.Label(self, textvariable=self.status, padding=8).pack(fill=tk.X)
        self.after_id = self.after(100, self.poll)
        self.run(model.component_catalog, self.loaded)

    def close(self):
        self.closed = True
        self.after_cancel(self.after_id)
        self.destroy()

    def run(self, worker, done, *, preserve_status=False):
        if self.busy:
            messagebox.showinfo("Product Tree", 'Wait for the current operation to finish.', parent=self)
            return
        self.busy = True
        if not preserve_status:
            self.status.set('Working...')
        self.apply_bom_permissions()
        def task():
            try:
                self.jobs.put((done, worker(), None, preserve_status))
            except Exception as exc:
                self.jobs.put((done, None, exc, preserve_status))
        threading.Thread(target=task, daemon=True).start()

    def poll(self):
        try:
            done, result, error, preserve_status = self.jobs.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if error:
                self.status.set('Operation failed.')
                messagebox.showerror("Product Tree", str(error), parent=self)
            else:
                if not preserve_status:
                    self.status.set('Ready')
                try:
                    done(result)
                except Exception as exc:
                    messagebox.showerror("Product Tree", str(exc), parent=self)
            self.apply_bom_permissions()
        if not self.closed and not self.busy and self.overview_pending is not None:
            version, project_id = self.overview_pending
            self.overview_pending = None
            def load():
                try:
                    return project_overview.build_overview(project_id), None
                except Exception as exc:
                    return None, str(exc)
            def loaded(result):
                if self.overview_target != project_id or self.overview_version != version:
                    return
                report, error = result
                if error:
                    self.overview.clear(f'Unable to load project overview: {error}')
                else:
                    self.overview.render(report)
            self.run(load, loaded, preserve_status=True)
        if not self.closed:
            self.after_id = self.after(100, self.poll)

    def loaded(self, catalog):
        self.catalog = catalog
        self.refresh_tree()
        self.search_parts()

    def build_tree(self):
        toolbar = ttk.Frame(self.tree_tab)
        toolbar.pack(fill=tk.X, pady=(0, 10))
        viewport = tk.Canvas(toolbar, highlightthickness=0, borderwidth=0)
        viewport.pack(fill=tk.X)
        bar = ttk.Frame(viewport)
        viewport.create_window(0, 0, window=bar, anchor='nw')
        toolbar_scroll = ttk.Scrollbar(toolbar, orient=tk.HORIZONTAL, command=viewport.xview)
        viewport.configure(xscrollcommand=toolbar_scroll.set)
        def resize_toolbar(event=None):
            width, height = bar.winfo_reqwidth(), bar.winfo_reqheight()
            viewport.configure(height=height, scrollregion=(0, 0, width, height))
            if width > viewport.winfo_width():
                toolbar_scroll.pack(fill=tk.X)
            else:
                toolbar_scroll.pack_forget()
                viewport.xview_moveto(0)
        bar.bind('<Configure>', resize_toolbar)
        viewport.bind('<Configure>', resize_toolbar)
        ttk.Button(bar, text='New project', command=self.new_project).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(bar, text='New sub-project', command=self.new_subproject).pack(side=tk.LEFT, padx=(0, 5))
        self.new_card_button = ttk.Button(bar, text='New card', command=self.new_card)
        self.new_card_button.pack(side=tk.LEFT, padx=(0, 5))
        self.add_revision_button = ttk.Button(bar, text='Add Card Revision', command=self.add_card_revision)
        self.add_revision_button.pack(side=tk.LEFT, padx=(0, 5))
        self.edit_button = ttk.Button(bar, text='Edit', command=self.edit_project)
        self.edit_button.pack(side=tk.LEFT, padx=(0, 5))
        self.delete_button = ttk.Button(bar, text='Delete', command=self.delete_project)
        self.delete_button.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(bar, text='Refresh', command=lambda: self.run(model.component_catalog, self.loaded)).pack(side=tk.LEFT)
        ttk.Button(bar, text="Compare revisions", command=self.compare_revisions).pack(side=tk.LEFT, padx=5)
        self.project_import_button = ttk.Button(bar, text='Project Import', command=self.import_project_workbook)
        self.project_import_button.pack(side=tk.LEFT, padx=5)
        self.export_bom_button = ttk.Button(bar, text='Export BOM', command=self.export_bom)
        self.export_bom_button.pack(side=tk.LEFT, padx=5)
        self.obsolete_button = ttk.Button(bar, text='Obsolete Products', command=self.show_obsolete_products)
        self.obsolete_button.pack(side=tk.LEFT, padx=5)
        self.add_bom_button = ttk.Button(bar, text='Add BOM', command=self.add_bom)
        self.add_bom_button.pack(side=tk.LEFT)
        for button in bar.winfo_children():
            button.pack_configure(padx=(0, 5), fill=tk.Y)
        split = self.tree_split = tk.PanedWindow(self.tree_tab, orient=tk.HORIZONTAL,
            sashwidth=10, sashrelief=tk.RAISED, showhandle=True, handlesize=10,
            sashcursor='sb_h_double_arrow', opaqueresize=True, borderwidth=0)
        split.pack(fill=tk.BOTH, expand=True)
        left, right = ttk.Frame(split), ttk.Frame(split)
        split.add(left, width=340, minsize=160, stretch='never')
        split.add(right, minsize=380, stretch='always')
        self.tree = ttk.Treeview(left, columns=('responsible',), show="tree headings", selectmode="extended")
        self.tree.heading('#0', text='Product tree')
        self.tree.heading('responsible', text='Responsible')
        self.tree.column('responsible', width=160, minwidth=120, stretch=False)
        enable_copy(self.tree)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(left, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(left, orient=tk.HORIZONTAL, command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.bind("<<TreeviewSelect>>", self.select_revision)
        self.detail = tk.StringVar(value='Create a project and card, then select the card and use Add BOM.')
        ttk.Label(right, textvariable=self.detail, wraplength=650, padding=10).pack(fill=tk.X)
        folder_row = ttk.Frame(right, padding=(10, 0, 10, 8))
        folder_row.pack(fill=tk.X)
        self.selected_folder = tk.StringVar()
        ttk.Button(folder_row, text="📁", width=3,
                   command=lambda: self.open_folder(self.selected_folder.get(), self)).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(folder_row, textvariable=self.selected_folder, wraplength=600).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.overview = ProjectOverviewPanel(right, table, self.show_saved_revision)
        self.material_panel = ttk.Frame(right)
        self.material_panel.pack(fill=tk.BOTH, expand=True)
        right = self.material_panel
        self.lines = table(right, [("cmp", "Company Part Number", 160),
                                  ("mpn", "Manufacturer Part Number", 210),
                                  ("desc", 'Description', 200),
                                  ("ref", 'References', 120), ("qty", 'Quantity / board', 85),
                                  ("component", 'Matched component', 180), ("match", 'Match', 140),
                                  ("product_status", 'Product Status', 130)])
        self.lines.tag_configure('obsolete', background='#ffe0e0')
        self.lines.bind("<Double-1>", lambda e: self.show_line_usage())
        buttons = ttk.Frame(right)
        buttons.pack(fill=tk.X, pady=8)
        ttk.Button(buttons, text='Where used?', command=self.show_line_usage).pack(side=tk.LEFT)
        self.repair_match_button = ttk.Button(buttons, text='Update match', command=self.repair_match)
        self.repair_match_button.pack(side=tk.LEFT, padx=5)
        self.approve_button = ttk.Button(buttons, text='Approve BOM', command=self.approve_bom, state='disabled')
        self.approve_button.pack(side=tk.LEFT, padx=5)
        self.draft_button = ttk.Button(buttons, text='Return to Draft', command=self.return_to_draft, state='disabled')
        self.draft_button.pack(side=tk.LEFT, padx=5)
        self.sync_database_button = ttk.Button(buttons, text='Sync Database',
                                               command=self.sync_database, state='disabled')
        self.sync_database_button.pack(side=tk.LEFT, padx=5)
        description_frame = ttk.LabelFrame(right, text="Description", padding=6)
        description_frame.pack(fill=tk.X, pady=(6, 0))
        self.bom_description = tk.Text(description_frame, height=4, wrap=tk.WORD, undo=True)
        self.bom_description.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        description_scroll = ttk.Scrollbar(description_frame, command=self.bom_description.yview)
        description_scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.bom_description.configure(yscrollcommand=description_scroll.set)
        self.description_save = ttk.Button(right, text="Save description", command=self.save_description)
        self.description_save.pack(anchor=tk.E, pady=5)
        self.bom_edit_hint = tk.StringVar()
        ttk.Label(right, textvariable=self.bom_edit_hint, wraplength=600).pack(anchor=tk.W)

    def apply_bom_permissions(self):
        selection = self.tree.selection()
        row = self.tree_rows.get(selection[0]) if len(selection) == 1 else None
        permissions = model.bom_permissions(row['id'] if row else None)
        self.project_import_button.configure(state='normal' if permissions['add'] and not self.busy else 'disabled')
        allowed = permissions['approve']
        can_approve = allowed and row and (permissions['status'] != 'Approved' or not permissions['approved_by'])
        self.approve_button.configure(state='normal' if can_approve and not self.busy else 'disabled')
        self.draft_button.configure(state='normal' if allowed and row and permissions['status'] == 'Approved'
                                    and not self.busy else 'disabled')
        editable = permissions['edit'] and not self.busy
        self.bom_description.configure(state='normal' if editable else 'disabled')
        self.description_save.configure(state='normal' if editable else 'disabled')
        self.repair_match_button.configure(state='normal' if editable else 'disabled')
        sync_project = len(selection) == 1 and selection[0] in self.projects and permissions['add']
        self.sync_database_button.configure(state='normal' if (editable or sync_project) and not self.busy else 'disabled')
        self.delete_button.configure(state='normal' if self.can_delete_selection() and not self.busy else 'disabled')
        self.new_card_button.configure(state='normal' if permissions['add'] and len(selection) == 1
                                          and self.selected_project() and not self.busy else 'disabled')
        selected_card = row or (next((r for r in self.tree_rows.values()
            if selection[0] == f"p{r['project_id']}b{r['board_id']}"), None) if len(selection) == 1 else None)
        self.add_revision_button.configure(state='normal' if selected_card and permissions['add']
                                           and not self.busy else 'disabled')
        self.export_bom_button.configure(state='normal' if len(selection) == 1 and
            (selection[0] in self.projects or selected_card) and not self.busy else 'disabled')
        can_edit_definition = permissions['edit'] if row else len(selection) == 1 and permissions['add'] and (
            selection[0] in self.projects or selected_card and model.can_edit_card(selected_card['board_id']))
        self.edit_button.configure(text='Edit BOM' if row else 'Edit card' if selected_card else 'Edit',
                                   state='normal' if can_edit_definition and not self.busy else 'disabled')
        self.bom_edit_hint.set('Select a Draft revision and use Edit BOM or Delete. Approved BOMs require an administrator.'
                              if not allowed else 'Content changes preserve approval. Use Return to Draft to withdraw approval.')
        target = self.selected_bom_row()
        can_import = target and model.bom_permissions(target['id'])['edit']
        self.add_bom_button.configure(state='normal' if can_import and not self.busy else 'disabled')

    def check_bom_edit(self, revision_id):
        try:
            model.require_bom_edit(revision_id)
        except (PermissionError, ValueError) as exc:
            self.apply_bom_permissions()
            messagebox.showerror('Edit BOM', str(exc), parent=self)
            return False
        return True

    def approve_bom(self):
        if self.busy:
            return
        if not model.can_approve_bom():
            self.apply_bom_permissions()
            messagebox.showerror('Approve BOM', 'Only active administrators can approve BOMs.', parent=self)
            return
        selection = self.tree.selection()
        row = self.tree_rows.get(selection[0]) if len(selection) == 1 else None
        if not row:
            messagebox.showinfo('Approve BOM', 'Select one saved BOM revision to approve.', parent=self)
            return
        if self.bom_description.get('1.0', 'end-1c') != row['notes']:
            messagebox.showinfo('Approve BOM', 'Save the edited description before approving this BOM.', parent=self)
            return
        if not messagebox.askyesno('Approve BOM',
                f"Approve {row['part_number']} / Rev. {row['revision']}?\n\n"
                'Approval applies to every project using this saved revision.', parent=self):
            return
        revision_id = row['id']
        def done(_):
            self.refresh_all()
            self.status.set('BOM approved.')
        self.run(lambda: model.approve_bom(revision_id), done)

    def save_description(self):
        if self.busy or self.revision_id is None:
            return
        revision_id = self.revision_id
        if len(self.tree.selection()) != 1 or not self.check_bom_edit(revision_id):
            return
        description = self.bom_description.get("1.0", "end-1c")
        def done(_):
            self.refresh_all()
            self.status.set("BOM description saved for this revision.")
        self.run(lambda: model.update_bom_description(revision_id, description), done)

    def return_to_draft(self):
        if self.busy:
            return
        if not model.can_approve_bom():
            self.apply_bom_permissions()
            messagebox.showerror('Return to Draft', 'Only active administrators can return approved BOMs to Draft.', parent=self)
            return
        selected = self.tree.selection()
        row = self.tree_rows.get(selected[0]) if len(selected) == 1 else None
        if not row:
            messagebox.showinfo('Return to Draft', 'Select one approved BOM revision.', parent=self)
            return
        if self.bom_description.get('1.0', 'end-1c') != row['notes']:
            messagebox.showinfo('Return to Draft', 'Save the edited description before changing BOM status.', parent=self)
            return
        if not messagebox.askyesno('Return to Draft',
                f"Return {row['part_number']} / Rev. {row['revision']} to Draft?\n\n"
                'Approval will be cleared for every project using this revision. BOM content will be retained.\n'
                'Active users will be able to edit or delete this Draft BOM.', parent=self):
            return
        def done(_):
            self.refresh_all()
            self.status.set('BOM returned to Draft.')
        self.run(lambda: model.return_bom_to_draft(row['id']), done)

    def sync_database(self):
        if self.busy:
            return
        selection = self.tree.selection()
        row = self.tree_rows.get(selection[0]) if len(selection) == 1 else None
        project = self.projects.get(selection[0]) if len(selection) == 1 else None
        if not row and not project:
            messagebox.showinfo('Sync Database', 'Select one project or saved BOM revision to synchronize.', parent=self)
            return
        if row and not self.check_bom_edit(row['id']):
            return
        if row and self.bom_description.get('1.0', 'end-1c') != row['notes']:
            messagebox.showinfo('Sync Database', 'Save the edited description before synchronizing this BOM.', parent=self)
            return
        title = f"{project['code']} — {project['name']}" if project else f"{row['part_number']} / Rev. {row['revision']}"
        def done(result):
            self.catalog = result['catalog']
            self.refresh_all()
            selected_parts = self.part_list.selection()
            self.search_parts()
            self.part_list.selection_set([key for key in selected_parts if self.part_list.exists(key)])
            summary = (f"{result['updated']} Company part numbers updated. "
                       f"{result['ambiguous'] + result['not_found'] + result['missing_database_br']} rows skipped.")
            self.status.set(summary)
            details = (f"{title}\n\n{summary}\n"
                       f"Existing Company part numbers preserved: {result['already_present']}\n"
                       f"Multiple database candidates: {result['ambiguous']}\n"
                       f"No matching component: {result['not_found']}\n"
                       f"Component has no Company part number: {result['missing_database_br']}")
            if project:
                details += (f"\n\nBOM revisions checked (including previous revisions): {result['revisions']}\n"
                            f"BOM revisions skipped (administrator required): {result['skipped_revisions']}")
            if result['ambiguous']:
                details += '\n\nUse Update match to resolve ambiguous rows, then synchronize again.'
            messagebox.showinfo('Sync Database', details, parent=self)
        worker = (lambda: model.sync_missing_brensan_numbers(project_id=project['id'])) if project else (
            lambda: model.sync_missing_brensan_numbers(row['id']))
        self.run(worker, done)

    def compare_revisions(self):
        if self.busy:
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Compare revisions", "Select a board or revision.", parent=self)
            return
        node = selection[0]
        row = self.tree_rows.get(node) or next((r for r in self.tree_rows.values()
                if node == f"p{r['project_id']}b{r['board_id']}"), None)
        if not row:
            messagebox.showinfo("Compare revisions", "Select a board or revision, not a project.", parent=self)
            return
        if len(model.board_revisions(row["board_id"])) < 2:
            messagebox.showinfo("Compare revisions", "This board needs at least two saved revisions.", parent=self)
            return
        from ui.revision_compare_window import RevisionCompareWindow
        RevisionCompareWindow(self, row["board_id"], row["part_number"] + " — " + row["board_name"], row["id"])

    def selected_project(self):
        selection = self.tree.selection()
        if not selection:
            return None
        node = selection[0]
        while node and node not in self.projects:
            node = self.tree.parent(node)
        return self.projects.get(node)

    def selected_bom_row(self):
        selected = self.tree.selection()
        if len(selected) != 1:
            return None
        node = selected[0]
        return self.tree_rows.get(node) or next((r for r in self.tree_rows.values()
            if node == f"p{r['project_id']}b{r['board_id']}" and r['active']), None)

    def import_project_workbook(self):
        if self.busy:
            return
        if not model.bom_permissions()['add']:
            messagebox.showerror('Project Import', 'An active user account is required.', parent=self)
            return
        path = filedialog.askopenfilename(parent=self, title='Select Project Import workbook',
                                         filetypes=[('Project Import workbook', '*.xlsx')])
        if not path:
            return
        def preview_ready(preview):
            summary = preview['summary']
            prompt = (f"{os.path.basename(path)}\n\n"
                      f"New projects/subprojects: {summary['projects']}\nNew cards: {summary['cards']}\n"
                      f"New revisions: {summary['revisions']}\nEmpty revisions to fill: {summary['filled']}\n"
                      f"Saved revisions reused: {summary['reused']}\nBOM rows to add: {summary['lines']}\n"
                      f"BOMs to approve: {summary['approvals']}\n\n"
                      'Current revisions will follow the workbook. Existing BOM content will be preserved.\n\n'
                      'Import these projects and BOMs?')
            if not messagebox.askyesno('Project Import', prompt, parent=self):
                self.status.set('Project import cancelled.')
                return
            def imported(result):
                self.loaded(result['catalog'])
                self.show_usage()
                self.tabs.select(self.tree_tab)
                message = (f"Project import completed. New projects: {result['projects']}, cards: {result['cards']}, "
                           f"revisions: {result['revisions']}. BOM rows added: {result['lines']}; "
                           f"matched: {result['matched']}, unmatched: {result['unmatched']}.")
                self.status.set(message)
                messagebox.showinfo('Project Import', message, parent=self)
            self.run(lambda: project_importer.import_workbook(preview), imported)
        self.run(lambda: project_importer.preview_import(path), preview_ready)

    def add_bom(self):
        if self.busy:
            return
        row = self.selected_bom_row()
        if not row:
            messagebox.showinfo('Add BOM', 'Select one card with a current revision, or select a specific revision.', parent=self)
            return
        if not self.check_bom_edit(row['id']):
            return
        if self.revision_id == row['id'] and self.bom_description.get('1.0', 'end-1c') != row['notes']:
            messagebox.showinfo('Add BOM', 'Save the edited description before adding a BOM.', parent=self)
            return
        try:
            snapshot = model.bom_edit_snapshot(row['project_id'], row['id'])
        except ValueError as exc:
            messagebox.showerror('Add BOM', str(exc), parent=self)
            return
        options = {'parent': self, 'title': f"Add BOM — {row['part_number']} / Rev. {row['revision']}",
                   'filetypes': [('Excel BOM', '*.xlsx *.xls')]}
        folder = (self.selected_project() or {}).get('folder', '')
        if os.path.isdir(folder):
            options['initialdir'] = folder
        path = filedialog.askopenfilename(**options)
        if not path:
            return
        def sheets_loaded(sheets):
            if len(sheets) == 1:
                self.read_bom_file(snapshot, path, sheets[0])
            elif sheets:
                self.choose_bom_sheet(snapshot, path, sheets)
            else:
                messagebox.showerror('Add BOM', 'This workbook has no worksheets.', parent=self)
        self.run(lambda: model.workbook_sheets(path), sheets_loaded)

    def choose_bom_sheet(self, snapshot, path, sheets):
        dialog = tk.Toplevel(self)
        dialog.title('Select BOM worksheet')
        dialog.transient(self)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"{snapshot['part_number']} / Rev. {snapshot['revision']}\n{os.path.basename(path)}").pack(anchor=tk.W)
        ttk.Label(body, text='Worksheet (BOM_Template.xlsx headers in row 1):').pack(anchor=tk.W, pady=(10, 4))
        sheet = ttk.Combobox(body, values=sheets, state='readonly', width=45)
        sheet.set(sheets[0])
        sheet.pack(fill=tk.X)
        def load():
            selected = sheet.get()
            dialog.destroy()
            self.read_bom_file(snapshot, path, selected)
        ttk.Button(body, text='Add BOM', command=load).pack(side=tk.RIGHT, pady=(12, 0))
        ttk.Button(body, text='Cancel', command=dialog.destroy).pack(side=tk.RIGHT, padx=8, pady=(12, 0))
        dialog.grab_set()

    def read_bom_file(self, snapshot, path, sheet):
        def worker():
            frame = model.read_sheet(path, sheet)
            mapping = model.template_mapping(frame.columns)
            catalog = model.component_catalog()
            return catalog, model.prepare_lines(frame, mapping, catalog)
        def loaded(result):
            self.catalog, lines = result
            self.search_parts()
            missing = sum(not r['component_id'] for r in lines)
            if snapshot['lines'] or snapshot['status'] == 'Approved':
                message = (f"{snapshot['part_number']} / Rev. {snapshot['revision']}\n"
                           f"Replace {len(snapshot['lines'])} saved rows with {len(lines)} rows from {os.path.basename(path)}?\n"
                           f"Unmatched rows: {missing}. Status after import: {snapshot['status']}.\n\n"
                           'All projects using this revision will receive the new BOM content.')
                if not messagebox.askyesno('Replace BOM', message, parent=self):
                    self.status.set('BOM import cancelled.')
                    return
            def done(revision_id):
                self.show_saved_revision(snapshot['project_id'], revision_id)
                self.status.set(f'BOM added: {len(lines)} rows · {len(lines) - missing} matched · {missing} unmatched. '
                                'Use Update match or Edit BOM to adjust rows.')
            self.run(lambda: model.import_bom_into_revision(snapshot, lines, path), done)
        self.run(worker, loaded)

    def show_saved_revision(self, project_id, revision_id):
        self.revision_id = revision_id
        self.refresh_tree()
        node = f'p{project_id}r{revision_id}'
        if self.tree.exists(node):
            self.tree.selection_set(node)
            self.tree.see(node)
            self.select_revision()
        self.show_usage()
        self.tabs.select(self.tree_tab)

    def link_saved_card(self, project):
        records = model.rows('''SELECT r.*,b.part_number,b.name AS board_name FROM PT_Revisions r
            JOIN PT_Boards b ON b.id=r.board_id ORDER BY b.part_number,r.revision''')
        if not records:
            messagebox.showinfo('Link saved card', 'Create a card first.', parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title('Link saved card')
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"Project: {project['code']} — {project['name']}").pack(anchor=tk.W)
        choices = [f"{r['part_number']} — {r['board_name']} / Rev. {r['revision']} · {r['status']}" for r in records]
        choice = ttk.Combobox(body, values=choices, state='readonly', width=70)
        choice.current(0)
        choice.pack(fill=tk.X, pady=8)
        ttk.Label(body, text='Boards / project').pack(anchor=tk.W)
        quantity = tk.StringVar(value='1')
        ttk.Entry(body, textvariable=quantity).pack(fill=tk.X)
        ttk.Label(body, text='The selected saved revision becomes current in this project.').pack(anchor=tk.W, pady=8)
        def save():
            revision_id = records[choice.current()]['id']
            try:
                model.link_card_revision(project['id'], revision_id, quantity.get())
            except Exception as exc:
                messagebox.showerror('Link saved card', str(exc), parent=dialog)
                return
            dialog.destroy()
            self.show_saved_revision(project['id'], revision_id)
        ttk.Button(body, text='Link card', command=save).pack(side=tk.RIGHT)
        ttk.Button(body, text='Cancel', command=dialog.destroy).pack(side=tk.RIGHT, padx=8)
        dialog.grab_set()

    def new_project(self):
        if not self.busy:
            self.project_dialog()

    def new_subproject(self):
        if self.busy:
            return
        project = self.selected_project()
        if len(self.tree.selection()) != 1 or not project:
            messagebox.showinfo('New subproject', 'Select a parent project first.', parent=self)
            return
        parent_id = project['parent_id'] if project['parent_id'] is not None else project['id']
        self.project_dialog(parent_id=parent_id)


    def new_card(self):
        if self.busy:
            return
        project = self.selected_project()
        if len(self.tree.selection()) != 1 or not project:
            messagebox.showinfo('New card', 'Select a project or sub-project first.', parent=self)
            return
        if not model.bom_permissions()['add']:
            messagebox.showerror('New card', 'An active user account is required to add cards.', parent=self)
            return
        self.card_dialog(project)

    def add_card_revision(self):
        if self.busy:
            return
        selection = self.tree.selection()
        row = self.tree_rows.get(selection[0]) if len(selection) == 1 else None
        if len(selection) == 1 and row is None:
            revisions = [r for r in self.tree_rows.values()
                         if selection[0] == f"p{r['project_id']}b{r['board_id']}"]
            row = next((r for r in revisions if r['active']), revisions[0] if revisions else None)
        if not row:
            messagebox.showinfo('Add Card Revision', 'Select one card or revision first.', parent=self)
            return
        if not model.bom_permissions()['add']:
            messagebox.showerror('Add Card Revision', 'An active user account is required to add revisions.', parent=self)
            return
        self.card_dialog(self.projects[f"p{row['project_id']}"], row)

    def card_dialog(self, project, card=None):
        title = 'Add Card Revision' if card else 'New card'
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        parent = self.projects.get(f"p{project['parent_id']}")
        path = (f"{parent['code']} → " if parent else '') + f"{project['code']} — {project['name']}"
        ttk.Label(body, text=f'Parent project: {path}', wraplength=460).pack(anchor=tk.W, pady=(0, 10))
        fields = {}
        for key, label, default in [('part_number', 'Board part number', card['part_number'] if card else ''),
                                     ('name', 'Board name', card['board_name'] if card else ''),
                                     ('revision', 'Revision', '' if card else 'A'),
                                     ('quantity', 'Boards / project', f"{card['board_quantity']:g}" if card else '1'),
                                     ('responsible', 'Responsible', card.get('responsible', '') if card else '')]:
            ttk.Label(body, text=label).pack(anchor=tk.W, pady=(6, 2))
            fields[key] = tk.StringVar(value=default)
            entry = ttk.Entry(body, textvariable=fields[key], width=55,
                              state='readonly' if card and key in ('part_number', 'name', 'responsible') else 'normal')
            entry.pack(fill=tk.X)
            if card and key == 'revision':
                entry.focus_set()
        ttk.Label(body, text='Creates an empty Draft revision and makes it current in this project. '
                  'Previous revisions are retained. Add material rows with Add BOM or Edit BOM.',
                  wraplength=460).pack(anchor=tk.W, pady=10)
        def save():
            try:
                if card:
                    revision_id = model.add_card_revision(project['id'], card['board_id'],
                                                          fields['revision'].get(), fields['quantity'].get())
                else:
                    revision_id = model.create_card(project['id'], **{key: var.get() for key, var in fields.items()})
            except Exception as exc:
                messagebox.showerror(title, str(exc), parent=dialog)
                return
            dialog.destroy()
            self.refresh_tree()
            node = f"p{project['id']}r{revision_id}"
            self.tree.selection_set(node)
            self.tree.see(node)
            self.select_revision()
            self.status.set('Draft revision added. Use Add BOM or Edit BOM to add its material list.' if card else
                            'Card created. Use Add BOM to add its material list.')
        ttk.Button(body, text='Add revision' if card else 'Create card', command=save).pack(side=tk.RIGHT, pady=(6, 0))
        ttk.Button(body, text='Cancel', command=dialog.destroy).pack(side=tk.RIGHT, padx=8, pady=(6, 0))
        if card is None:
            def link_existing():
                dialog.destroy()
                self.link_saved_card(project)
            ttk.Button(body, text='Link saved card', command=link_existing).pack(side=tk.LEFT, pady=(6, 0))
        dialog.grab_set()


    def edit_project(self):
        if self.busy:
            return
        if len(self.tree.selection()) != 1:
            messagebox.showinfo('Project', 'Select one item to edit.', parent=self)
            return
        node = self.tree.selection()[0]
        row = self.tree_rows.get(node)
        if row:
            if not self.check_bom_edit(row['id']):
                return
            from ui.bom_edit_window import BomEditWindow
            def loaded(result):
                snapshot, self.catalog = result
                BomEditWindow(self, snapshot)
            self.run(lambda: (model.bom_edit_snapshot(row['project_id'], row['id']), model.component_catalog()), loaded)
            return
        row = next((r for r in self.tree_rows.values()
            if node == f"p{r['project_id']}b{r['board_id']}"), None)
        if row:
            self.edit_card(row)
            return
        project = self.selected_project()
        if project:
            self.project_dialog(project)
        else:
            messagebox.showinfo('Project', 'Select a project to edit.', parent=self)

    def edit_card(self, row):
        if not model.can_edit_card(row['board_id']):
            messagebox.showerror('Edit card', 'Only administrators can edit cards with approved BOMs.', parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title('Edit card')
        dialog.transient(self)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        fields = {}
        for key, label, value in [('part_number', 'Card part number', row['part_number']),
                                   ('name', 'Card name', row['board_name']),
                                   ('responsible', 'Responsible', row.get('responsible', ''))]:
            ttk.Label(body, text=label).pack(anchor=tk.W, pady=(6, 2))
            fields[key] = tk.StringVar(value=value)
            ttk.Entry(body, textvariable=fields[key], width=55).pack(fill=tk.X)
        ttk.Label(body, text='These details apply to all projects and revisions using this card.',
                  wraplength=460).pack(anchor=tk.W, pady=10)
        def save():
            try:
                model.update_card(row['board_id'], fields['part_number'].get(), fields['name'].get(),
                                  responsible=fields['responsible'].get())
            except Exception as exc:
                messagebox.showerror('Edit card', str(exc), parent=dialog)
                return
            dialog.destroy()
            self.refresh_all()
            self.status.set('Card details updated.')
        ttk.Button(body, text='Save', command=save).pack(side=tk.RIGHT, pady=(6, 0))
        ttk.Button(body, text='Cancel', command=dialog.destroy).pack(side=tk.RIGHT, padx=8, pady=(6, 0))
        dialog.grab_set()

    def project_dialog(self, project=None, parent_id=None):
        dialog = tk.Toplevel(self)
        dialog.title('Edit project' if project else 'New subproject' if parent_id is not None else 'New project')
        dialog.transient(self)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        if project:
            parent_id = project['parent_id']
        choices = {'(Top-level project)': None}
        choices.update({f"{p['code']} — {p['name']}": p['id'] for p in self.projects.values()
                        if p['parent_id'] is None and p['id'] != (project or {}).get('id')})
        ttk.Label(body, text='Parent project').pack(anchor=tk.W, pady=(6, 2))
        parent_choice = tk.StringVar(value=next((label for label, identity in choices.items()
                                               if identity == parent_id), '(Top-level project)'))
        ttk.Combobox(body, textvariable=parent_choice, values=list(choices), state='readonly').pack(fill=tk.X)
        ttk.Label(body, text='Choose a parent to place this project and its boards under it.\n'
                  'Codes must be unique within the same parent; top-level codes may also be used by subprojects.',
                  wraplength=460).pack(anchor=tk.W, pady=4)
        fields = {}
        for key, label in [("code", 'Project code'), ("name", 'Project name'), ("notes", 'Description')]:
            ttk.Label(body, text=label).pack(anchor=tk.W, pady=(6, 2))
            fields[key] = tk.StringVar(value=(project or {}).get(key, ""))
            widget = self.multiline_entry(body, fields[key], width=55) if key == "notes" else ttk.Entry(body, textvariable=fields[key], width=55)
            widget.pack(fill=tk.X)
        fields["folder"] = tk.StringVar(value=(project or {}).get("folder", ""))
        self.folder_field(body, fields["folder"], dialog)
        def save():
            try:
                identity = model.save_project(fields["code"].get(), fields["name"].get(), fields["notes"].get(),
                                              project["id"] if project else None, folder=fields["folder"].get(),
                                              parent_id=choices[parent_choice.get()])
            except Exception as exc:
                messagebox.showerror('Save project', str(exc), parent=dialog)
                return
            dialog.destroy()
            self.refresh_tree()
            self.tree.selection_set(f"p{identity}")
            self.tree.see(f"p{identity}")
            self.select_revision()
            self.show_usage()
            self.status.set('Project saved.')
        ttk.Button(body, text='Save', command=save).pack(side=tk.RIGHT, pady=(14, 0))
        ttk.Button(body, text='Cancel', command=dialog.destroy).pack(side=tk.RIGHT, padx=8, pady=(14, 0))
        dialog.grab_set()

    def can_delete_selection(self):
        selected = self.tree.selection()
        if len(selected) != 1:
            return False
        node = selected[0]
        if node in self.projects:
            return model.bom_permissions()['delete']
        row = self.tree_rows.get(node)
        if row:
            return model.can_delete_project_item(row['project_id'], revision_id=row['id'])
        row = next((r for r in self.tree_rows.values() if node == f"p{r['project_id']}b{r['board_id']}"), None)
        return bool(row and model.can_delete_project_item(row['project_id'], board_id=row['board_id']))

    def delete_project(self):
        if self.busy:
            return
        if len(self.tree.selection()) != 1:
            messagebox.showinfo('Delete', 'Select one project, subproject, board or revision to delete.', parent=self)
            return
        if not self.can_delete_selection():
            self.apply_bom_permissions()
            messagebox.showerror('Delete', 'Active users can delete Draft BOMs. Only administrators can delete approved BOMs or projects.', parent=self)
            return
        project = self.selected_project()
        if not project:
            messagebox.showinfo('Delete', 'Select a project, board or revision to delete.', parent=self)
            return
        node = self.tree.selection()[0]
        deleting_project = node in self.projects
        if deleting_project:
            if project['subproject_count']:
                messagebox.showinfo('Delete project',
                    'This project contains subprojects. Move or delete them individually before deleting the parent project.',
                    parent=self)
                return
            title = 'Delete subproject' if project['parent_id'] is not None else 'Delete project'
            description = (f"{project['code']} — {project['name']}\n\nThe project and its "
                           f"{project['revision_count']} board/revision links will be deleted.")
            action = lambda: model.delete_project(project["id"])
        elif node in self.tree_rows:
            row = self.tree_rows[node]
            title = 'Delete BOM revision'
            description = (f"{project['code']} → {row['part_number']} → Rev. {row['revision']}\n\n"
                           "This revision's link to the selected project will be removed."
                           '\nOther boards and revisions will be retained.')
            action = lambda: model.detach_project_item(project["id"], revision_id=row["id"], purge_unused=True)
        else:
            row = next((r for r in self.tree_rows.values()
                        if node == f"p{r['project_id']}b{r['board_id']}"), None)
            if row is None:
                return
            title = 'Remove board from project'
            description = (f"{project['code']} → {row['part_number']} — {row['board_name']}\n\n"
                           "Only this board's revision links in the selected project will be removed."
                           '\nThe project and other boards will be retained.')
            action = lambda: model.detach_project_item(project["id"], board_id=row["board_id"], purge_unused=True)
        retention = ('Saved board BOMs will be retained.' if deleting_project else
                     'BOMs still linked to other projects will be retained.\n'
                     'BOMs with no remaining project links and their material rows will be permanently deleted.')
        if not messagebox.askyesno(title, description + '\n\n' + retention +
                '\nOther projects and database components will be retained.'
                '\nContinue?', parent=self):
            return
        def done(_):
            self.refresh_tree()
            self.show_usage()
            self.status.set('Selected item deleted; other project links retained.')
        self.run(action, done)

    def refresh_all(self):
        if self.busy:
            return
        self.refresh_tree()
        self.show_usage()

    def refresh_tree(self):
        if self.busy:
            return
        self.overview_target = None
        self.overview_version += 1
        self.overview_pending = None
        expanded = {}
        def remember(parent=""):
            for child in self.tree.get_children(parent):
                expanded[child] = self.tree.item(child, "open")
                remember(child)
        remember()
        previous = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.tree_rows = {}
        self.projects = {f"p{p['id']}": p for p in model.list_projects()}
        # Insert parents first even when a subproject's code sorts before its parent's.
        for key, project in sorted(self.projects.items(), key=lambda item: item[1]['parent_id'] is not None):
            parent = f"p{project['parent_id']}" if project['parent_id'] is not None else ''
            self.tree.insert(parent, "end", iid=key, text=project_tree_label(project['code'], project['name']),
                             open=expanded.get(key, False))
        for row in model.project_tree(include_history=True):
            project = f"p{row['project_id']}"
            if not self.tree.exists(project):
                self.tree.insert("", "end", iid=project, text=project_tree_label(row['code'], row['project_name']),
                                 open=expanded.get(project, False))
            board = f"{project}b{row['board_id']}"
            if not self.tree.exists(board):
                self.tree.insert(project, "end", iid=board,
                                 text=f"{row['part_number']} — {row['board_name']}",
                                 values=(row.get('responsible', ''),), open=expanded.get(board, False))
            key = f"{project}r{row['id']}"
            self.tree_rows[key] = row
            self.tree.insert(board, "end", iid=key,
                             text=f"Rev. {row['revision']} · {row['status']} · ×{row['board_quantity']:g}" +
                             (' (previous)' if not row["active"] else ' (current)'))
        font = tkfont.Font(font=ttk.Style(self.tree).lookup('Treeview', 'font') or 'TkDefaultFont')
        def text_width(parent='', depth=0):
            return max([200] + [max(font.measure(self.tree.item(item, 'text')) + depth * 20 + 48,
                                    text_width(item, depth + 1)) for item in self.tree.get_children(parent)])
        width = text_width()
        self.tree.column('#0', width=width, minwidth=width, stretch=True)
        children = list(self.tree_rows)
        retained = [node for node in previous if self.tree.exists(node)]
        if retained:
            self.tree.selection_set(retained)
            self.select_revision()
        elif not expanded and self.tree.get_children():
            # Start at a visible top-level project without selecting a hidden BOM.
            self.tree.selection_set(self.tree.get_children()[0])
            self.select_revision()
        elif children:
            selected = next((k for k in children if self.tree_rows[k]["id"] == self.revision_id), children[0])
            self.tree.selection_set(selected)
            self.select_revision()
        else:
            self.revision_id = None
            self.saved_lines = {}
            self.lines.delete(*self.lines.get_children())
            if self.projects:
                self.tree.selection_set(next(iter(self.projects)))
            self.select_revision()

    def select_revision(self, event=None):
        selection = self.tree.selection()
        project_selected = len(selection) == 1 and selection[0] in self.projects
        if project_selected:
            project_id = self.projects[selection[0]]['id']
            self.material_panel.pack_forget()
            self.overview.pack(fill=tk.BOTH, expand=True)
            if self.overview_target != project_id:
                self.overview.filter.set(self.overview.FILTERS[0])
                self.overview.tabs.select(self.overview.materials_page)
                self.overview_version += 1
                self.overview_target = project_id
                self.overview_pending = (self.overview_version, project_id)
                self.overview.clear('Loading project overview...')
        else:
            self.overview_target = None
            self.overview_version += 1
            self.overview_pending = None
            self.overview.pack_forget()
            self.material_panel.pack(fill=tk.BOTH, expand=True)
        project = self.selected_project()
        self.selected_folder.set((project or {}).get("folder", ""))
        project_path = ''
        if project:
            parent = self.projects.get(f"p{project['parent_id']}")
            project_path = (f"{parent['code']} — {parent['name']} → " if parent else '') + f"{project['code']} — {project['name']}"
        row = self.tree_rows.get(selection[0]) if selection else None
        self.bom_description.configure(state="normal")
        self.bom_description.delete("1.0", tk.END)
        if row:
            self.bom_description.insert("1.0", row["notes"])
        else:
            self.bom_description.configure(state="disabled")
        self.description_save.configure(state="normal" if row else "disabled")
        self.apply_bom_permissions()
        if not row:
            self.revision_id = None
            self.saved_lines = {}
            self.lines.delete(*self.lines.get_children())
            project = self.selected_project()
            self.detail.set((f"{project_path}\n{project['notes']}\n" if project else "") +
                            ('Project overview — latest revisions only (finals before prototypes).' if project_selected else
                             'Select a card and use Add BOM; select a revision to view its material list.'))
            return
        self.revision_id = row["id"]
        self.saved_lines = {str(r["id"]): r for r in model.revision_lines(row["id"])}
        approval = ''
        if row['status'] == 'Approved':
            approval = (f"\nApproved: {row['approved_at']} · {row['approved_by']}" if row['approved_by'] else
                        '\nApproval details unavailable for this existing record.')
        self.detail.set(f"{row['board_name']} — {row['part_number']} / {row['revision']}  |  {row['status']}\n"
                        f"{project_path} · Boards / project: {row['board_quantity']:g}\n"
                        + (f"Imported: {row['imported_at']} · {row['imported_by']}\n{row['source']}"
                           if row['source'] else 'BOM created without a source spreadsheet.' if self.saved_lines else
                           'No BOM imported yet. Use Add BOM or Edit BOM to add the material list.') + approval)
        self.lines.delete(*self.lines.get_children())
        live_parts = {p["id"]: p for p in self.catalog}
        for key, line in self.saved_lines.items():
            identity = line["component_id"]
            part = live_parts.get(identity, {})
            self.lines.insert("", "end", iid=key, values=(line["cmp"], line["mpn"], line["description"],
                line["refs"], f"{line['quantity']:g}", part.get("cmp") or part.get("mpn", "—"),
                'Matched' if identity in live_parts else 'Deleted' if identity else 'Unmatched',
                part.get('product_status', '')),
                tags=('obsolete',) if model.is_obsolete_status(part.get('product_status')) else ())

    def show_obsolete_products(self):
        from ui.obsolete_products_window import ObsoleteProductsWindow
        window = getattr(self, 'obsolete_window', None)
        if window is not None and window.winfo_exists():
            window.lift()
            return
        self.obsolete_window = ObsoleteProductsWindow(self)

    def open_folder(self, path, dialog):
        path = path.strip()
        if not path:
            messagebox.showinfo('Project folder', 'Select a project folder first.', parent=dialog)
            return
        if not os.path.isdir(path):
            messagebox.showerror('Project folder', f"Folder not found or inaccessible:\n{path}", parent=dialog)
            return
        try:
            os.startfile(os.path.abspath(path), "explore")
        except OSError as exc:
            messagebox.showerror('Project folder', f"Could not open File Explorer:\n{exc}", parent=dialog)

    def folder_field(self, parent, variable, dialog, readonly=False):
        ttk.Label(parent, text='Project folder').pack(anchor=tk.W, pady=(6, 2))
        row = ttk.Frame(parent)
        row.pack(fill=tk.X)
        ttk.Button(row, text="📁", width=3,
                   command=lambda: self.open_folder(variable.get(), dialog)).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(row, textvariable=variable, state='readonly' if readonly else 'normal').pack(side=tk.LEFT, fill=tk.X, expand=True)
        def browse():
            current = variable.get().strip()
            options = {"parent": dialog, "title": 'Select project folder', "mustexist": True}
            if os.path.isdir(current):
                options["initialdir"] = current
            path = filedialog.askdirectory(**options)
            if path:
                variable.set(path)
        ttk.Button(row, text='Browse…', command=browse, state='disabled' if readonly else 'normal').pack(side=tk.LEFT, padx=(6, 0))

    def multiline_entry(self, parent, variable, width=20):
        widget = tk.Text(parent, height=3, width=width, wrap=tk.WORD, undo=True)
        widget.insert("1.0", variable.get())
        widget.edit_modified(False)
        def from_text(event=None):
            if widget.edit_modified():
                variable.set(widget.get("1.0", "end-1c"))
                widget.edit_modified(False)
        def from_variable(*args):
            if widget.winfo_exists() and widget.get("1.0", "end-1c") != variable.get():
                widget.delete("1.0", tk.END)
                widget.insert("1.0", variable.get())
                widget.edit_modified(False)
        widget.bind("<<Modified>>", from_text)
        variable.trace_add("write", from_variable)
        return widget


    def pick_component(self, callback, owner=None):
        parent = owner or self
        dialog = tk.Toplevel(parent)
        dialog.title('Match component')
        dialog.geometry("760x450")
        dialog.transient(parent)
        dialog.grab_set()
        search = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=search)
        entry.pack(fill=tk.X, padx=10, pady=10)
        view = table(dialog, [("br", "BR", 150), ("mpn", "MPN", 200), ("mfr", 'Manufacturer', 130), ("table", 'Table', 100)])
        view.configure(selectmode="browse")
        def refresh(*args):
            view.delete(*view.get_children())
            query = search.get().casefold()
            for part in self.catalog:
                if query in " ".join([part.get("cmp"), part["mpn"], part["manufacturer"], part["description"]]).casefold():
                    view.insert("", "end", iid=part["id"], values=(part.get("cmp"), part["mpn"], part["manufacturer"], part["table"]))
        def close():
            dialog.destroy()
            if owner and owner.winfo_exists():
                owner.grab_set()
        dialog.protocol('WM_DELETE_WINDOW', close)
        def choose():
            selected = view.selection()
            if selected:
                callback(selected[0])
                close()
        search.trace_add("write", refresh)
        ttk.Button(dialog, text='Match selected component', command=choose).pack(pady=10)
        view.bind("<Double-1>", lambda e: choose())
        refresh()
        entry.focus_set()


    def build_usage(self):
        bar = ttk.Frame(self.usage_tab)
        bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(bar, text='BR / MPN / manufacturer:').pack(side=tk.LEFT)
        self.part_search = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.part_search, width=38)
        entry.pack(side=tk.LEFT, padx=8)
        entry.bind("<Return>", lambda e: self.search_parts())
        ttk.Button(bar, text='Search', command=self.search_parts).pack(side=tk.LEFT)
        ttk.Button(bar, text='Refresh stock / usage', command=self.refresh_usage).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(bar, text='Previous revisions', variable=self.history, command=self.refresh_all).pack(side=tk.RIGHT)
        self.part_list = table(self.usage_tab, [("cmp", "Company Part Number", 160), ("mpn", "MPN", 240),
            ("value", 'Value', 110), ("description", 'Description', 280),
            ("mfr", 'Manufacturer', 150), ("table", 'Table', 120)])
        self.part_list.configure(height=5)
        self.part_list.bind("<<TreeviewSelect>>", lambda e: self.show_usage())
        self.usage_title = tk.StringVar(value='Select a component.')
        ttk.Label(self.usage_tab, textvariable=self.usage_title, padding=10).pack(fill=tk.X)
        self.usages = table(self.usage_tab, [("project", 'Top-level project', 180), ("subproject", 'Subproject', 180),
            ("board", 'Board / revision', 190), ("ref", 'References', 110), ("qty", 'Quantity / board', 90),
            ("boards", 'Boards / project', 90), ("total", 'Quantity / project', 90),
            ("stock", "Company Stock", 110), ("status", 'Status', 90)])
        self.usages.bind("<Double-1>", self.open_usage_board)
        ttk.Button(self.usage_tab, text='Export usage to Excel', command=self.export_usage).pack(anchor=tk.E, pady=8)

    def search_parts(self):
        self.part_list.delete(*self.part_list.get_children())
        query = self.part_search.get().casefold()
        for part in self.catalog:
            if query in " ".join([part["cmp"], part["mpn"], part["manufacturer"]]).casefold():
                self.part_list.insert("", "end", iid=part["id"], values=(part["cmp"], part["mpn"],
                    part.get("value", ""), part.get("description", ""), part["manufacturer"], part["table"]))

    def refresh_usage(self):
        if self.busy:
            return
        selected = self.part_list.selection()
        def done(catalog):
            self.catalog = catalog
            self.search_parts()
            remaining = [key for key in selected if self.part_list.exists(key)]
            self.part_list.selection_set(remaining)
            if remaining:
                self.show_usage(remaining[0])
        self.run(model.component_catalog, done)

    def show_usage(self, identity=None):
        selected = self.part_list.selection()
        identity = identity or (selected[0] if selected else None)
        if not identity:
            return
        all_usage = model.usage(identity, include_history=True)
        self.usage_data = all_usage if self.history.get() else [row for row in all_usage if row['active']]
        self.usages.delete(*self.usages.get_children())
        part = next((p for p in self.catalog if p["id"] == identity), {})
        title = f"{part.get('br', '')} · {part.get('mpn', '')} — {len(self.usage_data)} usage rows"
        hidden = len(all_usage) - len(self.usage_data)
        if hidden:
            title += f"; {hidden} previous revision rows hidden (enable Previous revisions)"
        self.usage_title.set(title)
        for i, row in enumerate(self.usage_data):
            upper = (row['parent_code'] + ' — ' + row['parent_name']) if row['parent_id'] is not None else row['code'] + ' — ' + row['project_name']
            subproject = row['code'] + ' — ' + row['project_name'] if row['parent_id'] is not None else '—'
            self.usages.insert("", "end", iid=str(i), values=(upper, subproject,
                row["part_number"] + " / " + row["revision"], row["refs"], f"{row['quantity']:g}",
                f"{row['board_quantity']:g}", f"{row['total']:g}",
                "—" if row.get("company_stock") is None else row.get("company_stock"),
                row["status"] + (' (previous)' if not row["active"] else "")))

    def show_line_usage(self):
        selected = self.lines.selection()
        if not selected:
            return
        identity = self.saved_lines[selected[0]]["component_id"]
        if not identity:
            messagebox.showinfo('Usage', 'Complete the missing match first.', parent=self)
            return
        row = self.selected_bom_row()
        if row and not row['active']:
            self.history.set(True)
        self.part_search.set("")
        self.search_parts()
        if self.part_list.exists(identity):
            self.part_list.selection_set(identity)
            self.part_list.see(identity)
        self.show_usage(identity)
        self.tabs.select(self.usage_tab)

    def repair_match(self):
        selected = self.lines.selection()
        if self.busy:
            return
        if len(selected) != 1:
            messagebox.showinfo('Match', 'Select one BOM row to update.', parent=self)
            return
        line = self.saved_lines[selected[0]]
        if not self.check_bom_edit(line['revision_id']):
            return
        def assign(identity):
            part = next(p for p in self.catalog if p["id"] == identity)
            if not messagebox.askyesno('Update match',
                    f"BOM row: {line['cmp'] or line['mpn']} ({line['refs']})\n"
                    f"New match: {part['cmp'] or part['mpn']} — {part['manufacturer']} / {part['mpn']}\n\n"
                    'Usage links will be updated for all projects using this revision.\n'
                    'Source BOM values and quantities will be retained.\n'
                    'BOM approval will be preserved. Continue?', parent=self):
                return
            def done(_):
                self.refresh_all()
                self.status.set('BOM match updated.')
            self.run(lambda: model.rematch_line(line["id"], identity), done)
        def ready(catalog):
            self.catalog = catalog
            self.pick_component(assign)
        self.run(model.component_catalog, ready)

    def open_usage_board(self, event=None):
        selected = self.usages.selection()
        if not selected:
            return
        row = self.usage_data[int(selected[0])]
        for key, value in self.tree_rows.items():
            if value["id"] == row["revision_id"] and value["project_id"] == row["project_id"]:
                self.tree.selection_set(key)
                self.tree.see(key)
                self.select_revision()
                self.tabs.select(self.tree_tab)
                break

    def export(self, records):
        if not records or self.busy:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if path:
            def write():
                # Force spreadsheet text, including MPNs starting with '='.
                from openpyxl import Workbook
                book = Workbook()
                sheet = book.active
                sheet.append(list(records[0]))
                for record in records:
                    sheet.append(list(record.values()))
                    for cell in sheet[sheet.max_row]:
                        if isinstance(cell.value, str):
                            cell.data_type = "s"
                book.save(path)
            self.run(write, lambda _: self.status.set('Excel file saved.'))

    def export_bom(self):
        if self.busy:
            return
        selected = self.tree.selection()
        if len(selected) != 1:
            messagebox.showinfo('Export BOM', 'Select one project, card or revision to export.', parent=self)
            return
        node = selected[0]
        project = self.selected_project()
        if not project:
            return
        options = {}
        if node in self.tree_rows:
            options['revision_id'] = self.tree_rows[node]['id']
        elif node not in self.projects:
            card = next((r for r in self.tree_rows.values()
                         if node == f"p{r['project_id']}b{r['board_id']}"), None)
            if not card:
                return
            options['board_id'] = card['board_id']
        def prepared(report):
            if not report['records']:
                messagebox.showinfo('Export BOM', 'The selected item has no BOM rows to export.', parent=self)
                return
            dialog = {'parent': self, 'title': report['title'], 'initialfile': report['filename'],
                      'defaultextension': '.xlsx', 'filetypes': [('Excel', '*.xlsx')]}
            if os.path.isdir(project.get('folder', '')):
                dialog['initialdir'] = project['folder']
            path = filedialog.asksaveasfilename(**dialog)
            if not path:
                self.status.set('Excel export cancelled.')
                return
            def done(_):
                message = f"Excel file saved: {len(report['records'])} material rows."
                if report['empty_cards']:
                    message += f" {report['empty_cards']} cards have empty BOMs; see the Cards worksheet."
                self.status.set(message)
            self.run(lambda: bom_export.write_export(path, report), done)
        def choose_revisions(choices):
            if not choices['cards']:
                messagebox.showinfo('Export BOM', 'The selected item has no linked card revisions.', parent=self)
                return
            from ui.export_revision_window import ExportRevisionWindow
            def export_selected(selections):
                self.run(lambda: bom_export.build_export(project['id'], board_id=choices['board_id'],
                                                        selections=selections), prepared)
            self.export_revision_window = ExportRevisionWindow(self, choices, export_selected,
                lambda: self.status.set('Excel export cancelled.'))
        self.run(lambda: bom_export.revision_choices(project['id'], **options), choose_revisions)

    def export_usage(self):
        self.export(getattr(self, "usage_data", []))
