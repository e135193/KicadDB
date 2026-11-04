import json
import os
import tkinter as tk
from tkinter import ttk, messagebox

import db
import users
from log_history import read_log_history, filter_history
from config import API_KEYS_FILE, DB_FILE, DB_BACKUP_DIR, DB_BACKUP_KEEP, LOG_FILE


def load_keys(path=API_KEYS_FILE):
    try:
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            keys = data.get('keys') if isinstance(data, dict) else []
            if not isinstance(keys, list):
                keys = []
            return keys
    except Exception:
        pass
    return []


def save_keys(keys, path=API_KEYS_FILE):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'keys': keys}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print('save error', e)
        return False


def mask_secret(value, visible=4):
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= visible:
        return "•" * len(text)
    return "•" * (len(text) - visible) + text[-visible:]


def format_change_details(details_text):
    """Readable multi-line text for one Change_Log `Details` cell."""
    if not details_text:
        return ""
    try:
        data = json.loads(details_text)
    except (TypeError, ValueError):
        return str(details_text)
    if not isinstance(data, dict):
        return str(details_text)
    lines = []
    for key, value in data.items():
        if key == "changes" and isinstance(value, dict):
            lines.append('Changed fields:')
            for column, change in value.items():
                if isinstance(change, dict):
                    lines.append(f"  {column}: {change.get('old', '')!r}  →  {change.get('new', '')!r}")
                else:
                    lines.append(f"  {column}: {change!r}")
        elif isinstance(value, dict):
            title = {"values": 'Saved values:', "deleted_row": 'Deleted record:'}.get(key, f"{key}:")
            lines.append(title)
            for column, item in value.items():
                lines.append(f"  {column}: {item}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


# ======================================================================
# API keys tab
# ======================================================================

class ApiKeysTab:
    """DigiKey API key list.

    Layout note: the bottom widgets (entry rows + buttons) are packed FIRST
    with side=BOTTOM, and the key list is packed last with expand=True. Tk's
    packer gives earlier widgets their requested size first, so the list is
    the one that shrinks on a small window — previously the list was packed
    first and pushed the buttons off-screen until the window was enlarged.
    """

    def __init__(self, parent, app, win):
        self.app = app
        self.win = win
        self.frame = ttk.Frame(parent, padding=8)
        self.path = API_KEYS_FILE
        self.keys = load_keys(self.path)
        self.show_secret_var = tk.BooleanVar(value=False)

        btns = ttk.Frame(self.frame, padding=(0, 6, 0, 0))
        btns.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(btns, text='New', command=self.add_new).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='Update', command=self.update_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='Delete', command=self.delete_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='Refresh usage', command=self.refresh_usage).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='Save', command=self.save).pack(side=tk.RIGHT, padx=4)

        form = ttk.LabelFrame(self.frame, text='Key details', padding=8)
        form.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))

        self.id_var = tk.StringVar()
        self.client_id_var = tk.StringVar()
        self.client_secret_var = tk.StringVar()
        self.key_var = tk.StringVar()
        self.daily_limit_var = tk.StringVar()

        def row_entry(label, var, **entry_kwargs):
            frm = ttk.Frame(form)
            frm.pack(fill=tk.X, pady=2)
            ttk.Label(frm, text=label, width=16).pack(side=tk.LEFT)
            entry = ttk.Entry(frm, textvariable=var, **entry_kwargs)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            return frm, entry

        row_entry('id', self.id_var)
        row_entry('client_id', self.client_id_var)
        secret_row, self.secret_entry = row_entry('client_secret', self.client_secret_var, show='•')
        ttk.Checkbutton(secret_row, text='Show', variable=self.show_secret_var,
                        command=self.toggle_secret_visibility).pack(side=tk.LEFT, padx=(6, 0))
        _, self.key_entry = row_entry('key (cid:csecret)', self.key_var, show='•')
        row_entry('daily_limit', self.daily_limit_var)

        help_text = (
            'DigiKey searches require at least one API key. Enter client_id + client_secret '
            'or use cid:csecret in the key field. daily_limit 0 = unlimited. '
            'The next key is used when the daily limit is reached. '
            'Counters track HTTP requests per UTC day.'
        )
        ttk.Label(self.frame, text=help_text, wraplength=800, foreground='blue').pack(
            side=tk.TOP, anchor=tk.W, padx=2, pady=(0, 6))

        list_frame = ttk.Frame(self.frame)
        list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        cols = ('id', 'client_id', 'client_secret', 'key', 'daily_limit', 'used', 'remaining')
        self.tree = ttk.Treeview(list_frame, columns=cols, show='headings', selectmode='browse', height=6)
        for c in cols:
            self.tree.heading(c, text=c)
            if c in ('used', 'remaining', 'daily_limit'):
                self.tree.column(c, width=80, anchor='center', stretch=False)
            else:
                self.tree.column(c, width=140)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)

        self.refresh()

    def toggle_secret_visibility(self):
        self.secret_entry.configure(show='' if self.show_secret_var.get() else '•')
        self.key_entry.configure(show='' if self.show_secret_var.get() else '•')

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        try:
            from api_manager import get_manager
            mgr = get_manager()
            mgr.refresh_day()
        except Exception:
            mgr = None

        for entry in self.keys:
            used = ''
            remaining = ''
            try:
                if mgr:
                    used = int(mgr.usage.get(entry.get('id'), 0) or 0)
                    limit = int(entry.get('daily_limit') or 0)
                    remaining = '∞' if limit <= 0 else max(0, limit - used)
                    if entry.get('id') in mgr.blocked_keys:
                        remaining = 'Blocked'
            except Exception:
                used = ''
                remaining = ''
            values = (
                entry.get('id', ''),
                entry.get('client_id', ''),
                mask_secret(entry.get('client_secret', '')),
                mask_secret(entry.get('key', '')),
                str(entry.get('daily_limit', '') or ''),
                str(used),
                str(remaining),
            )
            self.tree.insert('', tk.END, values=values)

    def selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        idx = list(self.tree.get_children()).index(sel[0])
        return idx if 0 <= idx < len(self.keys) else None

    def read_daily_limit(self):
        """Parse the daily_limit field, or None if it is not a number."""
        raw = self.daily_limit_var.get().strip()
        if not raw:
            return 0
        try:
            return max(int(float(raw)), 0)
        except ValueError:
            messagebox.showwarning('Invalid value', f'daily_limit must be numeric: {raw!r}', parent=self.win)
            return None

    def read_form_entry(self, fallback_id=None):
        limit = self.read_daily_limit()
        if limit is None:
            return None
        entry = {'id': self.id_var.get().strip() or fallback_id or f'key_{len(self.keys) + 1}',
                 'client_id': self.client_id_var.get().strip(),
                 'client_secret': self.client_secret_var.get().strip(),
                 'key': self.key_var.get().strip(),
                 'daily_limit': limit}
        if not entry['key'] and not (entry['client_id'] and entry['client_secret']):
            if not messagebox.askyesno(
                'Missing credentials',
                'This entry has no client_id + client_secret or key and cannot be used for DigiKey searches.\n\nAdd it anyway?',
                parent=self.win,
            ):
                return None
        return entry

    def add_new(self):
        entry = self.read_form_entry()
        if entry is None:
            return
        if any(str(k.get('id')) == entry['id'] for k in self.keys):
            messagebox.showwarning('Duplicate id', f"'{entry['id']}' is already in use. IDs must be unique because usage counters depend on them.", parent=self.win)
            return
        self.keys.append(entry)
        self.refresh()

    def on_select(self, event=None):
        idx = self.selected_index()
        if idx is None:
            return
        # Read from the real entry, not from the (masked) tree cells.
        entry = self.keys[idx]
        self.id_var.set(str(entry.get('id', '') or ''))
        self.client_id_var.set(str(entry.get('client_id', '') or ''))
        self.client_secret_var.set(str(entry.get('client_secret', '') or ''))
        self.key_var.set(str(entry.get('key', '') or ''))
        self.daily_limit_var.set(str(entry.get('daily_limit', '') or ''))

    def update_selected(self):
        idx = self.selected_index()
        if idx is None:
            messagebox.showwarning('Warning', 'Select a row.', parent=self.win)
            return
        entry = self.read_form_entry(fallback_id=self.keys[idx].get('id'))
        if entry is None:
            return
        if any(i != idx and str(k.get('id')) == entry['id'] for i, k in enumerate(self.keys)):
            messagebox.showwarning('Duplicate id', f"'{entry['id']}' is used by another entry.", parent=self.win)
            return
        self.keys[idx] = entry
        self.refresh()

    def delete_selected(self):
        idx = self.selected_index()
        if idx is None:
            messagebox.showwarning('Warning', 'Select a row.', parent=self.win)
            return
        if not messagebox.askyesno('Delete', f"'{self.keys[idx].get('id')}' key from the list?\n(Changes are written only when you click Save.)", parent=self.win):
            return
        del self.keys[idx]
        self.refresh()

    def save(self):
        if save_keys(self.keys, self.path):
            db.audit_log.info("API_KEYS_SAVE user=%s count=%s", db.get_current_user(), len(self.keys))
            messagebox.showinfo('Saved', f'API keys saved:\n{self.path}', parent=self.win)
            try:
                if hasattr(self.app, 'reload_api_keys'):
                    self.app.reload_api_keys()
                    self.refresh()
            except Exception:
                pass
        else:
            messagebox.showerror('Error', 'Could not save the file.', parent=self.win)

    def refresh_usage(self):
        try:
            from api_manager import reload_manager, get_manager
            reload_manager(self.path)
            _ = get_manager()
        except Exception:
            pass
        self.refresh()


# ======================================================================
# Users tab (administrators only)
# ======================================================================

class UsersTab:
    COLUMNS = [
        ("Username", 'Username', 130),
        ("Full_Name", 'Full name', 160),
        ("Email", 'Email', 210),
        ("Role", 'Role', 90),
        ("Is_Active", 'Status', 70),
        ("Must_Change_Password", 'Password change required', 100),
        ("Last_Login_At", 'Last sign-in', 140),
        ("Created_At", 'Created at', 140),
        ("Created_By", 'Created by', 100),
    ]

    def __init__(self, parent, app, win):
        self.app = app
        self.win = win
        self.frame = ttk.Frame(parent, padding=8)
        self.users = []

        btns = ttk.Frame(self.frame, padding=(0, 6, 0, 0))
        btns.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(btns, text='Add user', command=self.create).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='Update details', command=self.update).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='Set password', command=self.reset_password).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='Delete', command=self.delete).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text='Clear form', command=self.clear_form).pack(side=tk.RIGHT, padx=4)

        form = ttk.LabelFrame(self.frame, text='User details', padding=8)
        form.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        self.username_var = tk.StringVar()
        self.full_name_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.role_var = tk.StringVar(value=users.ROLE_LABELS[users.ROLE_USER])
        self.password_var = tk.StringVar(value=users.DEFAULT_PASSWORD)
        self.repeat_var = tk.StringVar(value=users.DEFAULT_PASSWORD)
        self.active_var = tk.BooleanVar(value=True)
        self.must_change_var = tk.BooleanVar(value=True)

        def cell(row, column, label, widget):
            ttk.Label(form, text=label).grid(row=row, column=column * 2, sticky=tk.W, padx=(0, 6), pady=3)
            widget.grid(row=row, column=column * 2 + 1, sticky=tk.EW, padx=(0, 12), pady=3)

        cell(0, 0, 'Username', ttk.Entry(form, textvariable=self.username_var))
        cell(0, 1, 'Full name', ttk.Entry(form, textvariable=self.full_name_var))
        cell(1, 0, 'Password', ttk.Entry(form, textvariable=self.password_var, show='•'))
        cell(1, 1, 'Confirm password', ttk.Entry(form, textvariable=self.repeat_var, show='•'))
        cell(2, 0, 'Role', ttk.Combobox(form, textvariable=self.role_var, state='readonly',
                                        values=[users.ROLE_LABELS[r] for r in users.ROLES]))
        checks = ttk.Frame(form)
        checks.grid(row=2, column=2, columnspan=2, sticky=tk.W, pady=3)
        ttk.Checkbutton(checks, text='Active', variable=self.active_var).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(checks, text='Require password change at first sign-in',
                        variable=self.must_change_var).pack(side=tk.LEFT)
        cell(3, 0, 'Email', ttk.Entry(form, textvariable=self.email_var))
        ttk.Button(form, text='Use default password (123456)', command=self.use_default_password).grid(
            row=3, column=3, sticky=tk.W, pady=3)

        help_text = (
            'New accounts default to 123456 and must change it at sign-in. '
            'Register a unique email address for password recovery. Configure delivery in Email / Password reset.'
        )
        ttk.Label(self.frame, text=help_text, wraplength=800, foreground='blue').pack(
            side=tk.TOP, anchor=tk.W, padx=2, pady=(0, 6))

        list_frame = ttk.Frame(self.frame)
        list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(list_frame, columns=[c[0] for c in self.COLUMNS],
                                 show='headings', selectmode='browse', height=6)
        for name, title, width in self.COLUMNS:
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, stretch=(name == "Full_Name"))
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        horizontal = ttk.Scrollbar(list_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.bind('<<TreeviewSelect>>', self.on_select)

        self.refresh()

    # -- helpers -------------------------------------------------------
    def role_from_form(self):
        label = self.role_var.get()
        for role, text in users.ROLE_LABELS.items():
            if text == label:
                return role
        return users.ROLE_USER

    def selected_user(self):
        sel = self.tree.selection()
        if not sel:
            return None
        idx = list(self.tree.get_children()).index(sel[0])
        return self.users[idx] if 0 <= idx < len(self.users) else None

    def read_password(self):
        password = self.password_var.get()
        if password != self.repeat_var.get():
            messagebox.showwarning('Password', 'Passwords do not match.', parent=self.win)
            return None
        return password

    def run(self, action, success_message):
        """Run one users.* call, showing rule violations as warnings."""
        try:
            action()
        except users.UserError as exc:
            messagebox.showwarning('Users', str(exc), parent=self.win)
            return False
        except Exception as exc:
            messagebox.showerror('Users', f'Operation failed:\n{exc}', parent=self.win)
            return False
        self.refresh()
        if success_message:
            messagebox.showinfo('Users', success_message, parent=self.win)
        return True

    def refresh(self):
        # Keep the same account selected across a reload, so that several
        # changes can be made to one user without re-selecting it each time.
        selected = self.selected_user()
        selected_name = selected["Username"].casefold() if selected else None
        self.tree.delete(*self.tree.get_children())
        try:
            self.users = users.list_users()
        except Exception as exc:
            self.users = []
            messagebox.showerror('Users', f'Could not load users:\n{exc}', parent=self.win)
        for user in self.users:
            iid = self.tree.insert('', tk.END, values=(
                user["Username"],
                user.get("Full_Name") or "",
                user.get("Email") or "",
                users.ROLE_LABELS.get(user["Role"], user["Role"]),
                'Active' if user["Is_Active"] else 'Inactive',
                'Yes' if user["Must_Change_Password"] else "",
                user.get("Last_Login_At") or "",
                user.get("Created_At") or "",
                user.get("Created_By") or "",
            ))
            if selected_name and user["Username"].casefold() == selected_name:
                self.tree.selection_set(iid)

    def clear_form(self):
        self.tree.selection_remove(self.tree.selection())
        self.username_var.set("")
        self.full_name_var.set("")
        self.email_var.set("")
        self.use_default_password()
        self.role_var.set(users.ROLE_LABELS[users.ROLE_USER])
        self.active_var.set(True)
        self.must_change_var.set(True)

    def on_select(self, event=None):
        user = self.selected_user()
        if not user:
            return
        self.username_var.set(user["Username"])
        self.full_name_var.set(user.get("Full_Name") or "")
        self.email_var.set(user.get("Email") or "")
        self.role_var.set(users.ROLE_LABELS.get(user["Role"], user["Role"]))
        self.active_var.set(user["Is_Active"])
        self.password_var.set("")
        self.repeat_var.set("")

    # -- actions -------------------------------------------------------
    def use_default_password(self):
        self.password_var.set(users.DEFAULT_PASSWORD)
        self.repeat_var.set(users.DEFAULT_PASSWORD)
        self.must_change_var.set(True)

    def create(self):
        password = self.read_password()
        if password is None:
            return
        username = self.username_var.get().strip()
        ok = self.run(
            lambda: users.create_user(
                username, password, role=self.role_from_form(),
                full_name=self.full_name_var.get(),
                email=self.email_var.get(),
                must_change_password=self.must_change_var.get(),
            ),
            f"'{username}' user created.",
        )
        if ok:
            self.clear_form()

    def update(self):
        user = self.selected_user()
        if not user:
            messagebox.showwarning('Warning', 'Select a user from the list.', parent=self.win)
            return
        if self.username_var.get().strip().casefold() != user["Username"].casefold():
            messagebox.showwarning(
                'Users',
                'Usernames cannot be changed because audit records refer to them.\n'
                'Create a new user to use a different name.',
                parent=self.win)
            self.username_var.set(user["Username"])
            return
        self.run(
            lambda: users.update_user(
                user["Username"], role=self.role_from_form(),
                full_name=self.full_name_var.get(), is_active=self.active_var.get(),
                email=self.email_var.get(),
            ),
            f"'{user['Username']}' updated.",
        )

    def reset_password(self):
        user = self.selected_user()
        if not user:
            messagebox.showwarning('Warning', 'Select a user from the list.', parent=self.win)
            return
        password = self.read_password()
        if password is None:
            return
        ok = self.run(
            lambda: users.set_password(
                user["Username"], password, must_change_password=self.must_change_var.get()),
            f"'{user['Username']}' password reset.",
        )
        if ok:
            self.password_var.set("")
            self.repeat_var.set("")

    def delete(self):
        user = self.selected_user()
        if not user:
            messagebox.showwarning('Warning', 'Select a user from the list.', parent=self.win)
            return
        current = (getattr(self.app, 'user', None) or {}).get("Username", "")
        if current and current.casefold() == user["Username"].casefold():
            messagebox.showwarning('Users', 'You cannot delete the account you are signed in with.', parent=self.win)
            return
        if not messagebox.askyesno(
            'Delete',
            f"'{user['Username']}' user?\n\n"
            "Their name is retained in historical records. To block access temporarily, clear 'Active'.",
            parent=self.win,
        ):
            return
        if self.run(lambda: users.delete_user(user["Username"]), None):
            self.clear_form()


# ======================================================================
# Change history tab
# ======================================================================

class HistoryTab:
    COLUMNS = [
        ("Changed_At", 'Date', 140),
        ("Changed_By", 'User', 100),
        ("Action", 'Action', 150),
        ("Table_Name", 'Table', 110),
        ("Record_Key", 'Record', 70),
        ("Part_Number", 'Part number', 200),
        ("Summary", 'Log message', 350),
    ]
    PAGE_SIZE = 250

    def __init__(self, parent, app, win):
        self.app = app
        self.win = win
        self.frame = ttk.Frame(parent, padding=8)
        self.rows = []

        top = ttk.Frame(self.frame)
        top.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        ttk.Label(top, text='Search:').pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.search_var, width=40)
        entry.pack(side=tk.LEFT, padx=5)
        entry.bind('<Return>', lambda event: self.refresh())
        ttk.Button(top, text='Search', command=self.refresh).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text='All', command=self.show_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text='Selected record history', command=self.show_selected_record).pack(side=tk.LEFT, padx=(12, 2))
        self.count_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.count_var).pack(side=tk.RIGHT)

        filters = ttk.Frame(self.frame)
        filters.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self.source_var = tk.StringVar(value='Log files')
        self.day_var = tk.StringVar(value='All days')
        ttk.Label(filters, text='Source:').pack(side=tk.LEFT)
        source = ttk.Combobox(filters, textvariable=self.source_var, state="readonly",
                              values=['Log files', 'Database'], width=18)
        source.pack(side=tk.LEFT, padx=5)
        source.bind('<<ComboboxSelected>>', lambda event: self.refresh())
        ttk.Label(filters, text='Day:').pack(side=tk.LEFT)
        self.day_combo = ttk.Combobox(filters, textvariable=self.day_var, state="readonly", width=15)
        self.day_combo.pack(side=tk.LEFT, padx=5)
        self.day_combo.bind('<<ComboboxSelected>>', lambda event: self.apply_filters())
        ttk.Button(filters, text='Refresh', command=self.refresh).pack(side=tk.LEFT)
        self.next_button = ttk.Button(filters, text='Next ›', command=lambda: self.change_page(1))
        self.next_button.pack(side=tk.RIGHT)
        self.previous_button = ttk.Button(filters, text='‹ Previous', command=lambda: self.change_page(-1))
        self.previous_button.pack(side=tk.RIGHT, padx=5)

        detail_frame = ttk.LabelFrame(self.frame, text='Details', padding=6)
        detail_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        self.detail_text = tk.Text(detail_frame, height=8, wrap=tk.WORD, state=tk.DISABLED)
        detail_scroll = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL, command=self.detail_text.yview)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        self.detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scroll.pack(side=tk.LEFT, fill=tk.Y)

        list_frame = ttk.Frame(self.frame)
        list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(list_frame, columns=[c[0] for c in self.COLUMNS],
                                 show='headings', selectmode='browse', height=8)
        horizontal = ttk.Scrollbar(list_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(xscrollcommand=horizontal.set)
        horizontal.pack(side=tk.BOTTOM, fill=tk.X)
        for name, title, width in self.COLUMNS:
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, stretch=(name == "Part_Number"))
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)

        self.refresh()

    def set_detail(self, text):
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete('1.0', tk.END)
        self.detail_text.insert('1.0', text)
        self.detail_text.configure(state=tk.DISABLED)

    def load(self, **filters):
        self.active_filters = filters
        try:
            if self.source_var.get() == 'Log files':
                self.all_rows = read_log_history(LOG_FILE)
            else:
                columns, rows = db.get_change_log(limit=None)
                self.all_rows = [dict(zip(columns, row)) for row in rows]
        except Exception as exc:
            self.all_rows = []
            messagebox.showerror('Change history', f'Could not read history:\n{exc}', parent=self.win)
        days = sorted({row.get("Changed_At", "")[:10] for row in self.all_rows if row.get("Changed_At")}, reverse=True)
        choices = ['All days'] + days
        if any(not row.get("Changed_At") for row in self.all_rows):
            choices.append('No date')
        self.day_combo.configure(values=choices)
        if self.day_var.get() not in choices:
            self.day_var.set('All days')
        self.apply_filters()

    def apply_filters(self):
        self.filtered_rows = filter_history(self.all_rows, day=self.day_var.get(), **self.active_filters)
        self.page = 0
        self.render_page()

    def change_page(self, offset):
        self.page = max(0, min(self.page + offset, max(0, (len(self.filtered_rows) - 1) // self.PAGE_SIZE)))
        self.render_page()

    def render_page(self):
        self.tree.delete(*self.tree.get_children())
        self.set_detail("")
        start = self.page * self.PAGE_SIZE
        self.rows = self.filtered_rows[start:start + self.PAGE_SIZE]
        for row in self.rows:
            self.tree.insert('', tk.END, values=[row.get(name, "") for name, _, _ in self.COLUMNS])
        total = len(self.filtered_rows)
        self.count_var.set(f"{start + 1 if total else 0}–{start + len(self.rows)} / {total} records")
        self.previous_button.configure(state="normal" if self.page else "disabled")
        self.next_button.configure(state="normal" if start + self.PAGE_SIZE < total else "disabled")

    def refresh(self):
        self.load(text=self.search_var.get())

    def show_all(self):
        self.search_var.set("")
        self.day_var.set('All days')
        self.load()

    def show_selected_record(self):
        table = getattr(self.app, 'current_table', None)
        key = getattr(self.app, 'selected_key_value', None)
        if not table or key is None:
            messagebox.showinfo(
                'Change history',
                'Select a record in the main window first.', parent=self.win)
            return
        self.search_var.set("")
        self.source_var.set('Database')
        self.load(table=table, record_key=key)

    def on_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = list(self.tree.get_children()).index(sel[0])
        if 0 <= idx < len(self.rows):
            detail = self.rows[idx].get("Details", "")
            self.set_detail(detail if self.source_var.get() == 'Log files' else format_change_details(detail))


# ======================================================================
# Database tab / account tab
# ======================================================================

class DatabaseTab:
    def __init__(self, parent, app, win):
        self.app = app
        self.win = win
        self.frame = ttk.Frame(parent, padding=12)
        ttk.Label(self.frame, text=f"Database: {DB_FILE}", wraplength=780, justify=tk.LEFT).pack(anchor=tk.W, pady=2)
        ttk.Label(self.frame, wraplength=780, justify=tk.LEFT,
                  text=f"Backup folder: {DB_BACKUP_DIR}\n(one backup per day, keep the latest {DB_BACKUP_KEEP} backups)").pack(anchor=tk.W, pady=2)
        ttk.Label(self.frame, text=f"Log file: {LOG_FILE}", wraplength=780, justify=tk.LEFT).pack(anchor=tk.W, pady=2)
        self.backup_status_var = tk.StringVar(value="")
        row = ttk.Frame(self.frame)
        row.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(row, text='Back up now', command=self.backup_now).pack(side=tk.LEFT)
        ttk.Label(row, textvariable=self.backup_status_var, wraplength=620).pack(side=tk.LEFT, padx=(8, 0))

    def backup_now(self):
        try:
            from db_backup import backup_database, describe_backup_result
            result = backup_database(force=True)
            message = describe_backup_result(result)
            self.backup_status_var.set(message)
            db.audit_log.info("BACKUP_MANUAL user=%s result=%s file=%s",
                              db.get_current_user(), result.get('status'), result.get('path'))
            if hasattr(self.app, 'status_var'):
                self.app.status_var.set(message)
            if result.get('status') in ('error', 'missing'):
                messagebox.showerror('Backup', message, parent=self.win)
        except Exception as e:
            messagebox.showerror('Backup', f'Backup failed:\n{e}', parent=self.win)


class AccountTab:
    def __init__(self, parent, app, win):
        self.app = app
        self.win = win
        self.frame = ttk.Frame(parent, padding=12)
        user = getattr(app, 'user', None) or {}
        username = user.get("Username") or "-"
        lines = [
            f"Username: {username}",
            f"Full name: {user.get('Full_Name') or '-'}",
            f"Email: {user.get('Email') or '-'}",
            f"Role: {users.ROLE_LABELS.get(user.get('Role'), user.get('Role') or '-')}",
            f"Last sign-in: {user.get('Last_Login_At') or '-'}",
        ]
        ttk.Label(self.frame, text="\n".join(lines), justify=tk.LEFT).pack(anchor=tk.W)
        button = ttk.Button(self.frame, text='Change my password', command=self.change_password)
        button.pack(anchor=tk.W, pady=(12, 0))
        if not user.get("Username"):
            button.configure(state=tk.DISABLED)

    def change_password(self):
        from ui.login_window import ChangePasswordDialog
        username = (getattr(self.app, 'user', None) or {}).get("Username")
        if username:
            ChangePasswordDialog(self.win, username).wait()
            self.win.grab_set()


# ======================================================================
# Window
# ======================================================================

class LcscSettingsTab:
    def __init__(self, parent, win):
        import lcsc_settings
        self.win = win
        self.frame = ttk.Frame(parent, padding=12)
        self.frame.columnconfigure(1, weight=1)
        ttk.Label(self.frame, text='KiCad LCSC Manager integration: JLCPCB / EasyEDA JSON APIs and KiCad library import. No API key is required.',
                  wraplength=700).grid(row=0, column=0, columnspan=2, sticky="w", pady=10)
        self.variables = {}
        values = lcsc_settings.load_settings()
        for row, (key, label) in enumerate((("timeout", 'Timeout (seconds)'),
                                             ("daily_limit", 'Daily request limit (0 = unlimited)'),
                                             ("library_directory", 'Default library folder'),
                                             ("kicad_config_directory", 'KiCad settings folder (optional)')), 1):
            ttk.Label(self.frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            variable = tk.StringVar(master=self.frame, value=str(values[key]))
            self.variables[key] = variable
            ttk.Entry(self.frame, textvariable=variable, width=60).grid(row=row, column=1, sticky="ew")
            if key.endswith('_directory'):
                def browse(var=variable):
                    from tkinter import filedialog
                    selected = filedialog.askdirectory(parent=self.win, initialdir=var.get() or None)
                    if selected:
                        var.set(selected)
                ttk.Button(self.frame, text='Browse…', command=browse).grid(row=row, column=2, padx=5)
        self.usage_var = tk.StringVar(master=self.frame)
        ttk.Label(self.frame, textvariable=self.usage_var).grid(row=5, column=0, columnspan=2, sticky="w", pady=15)
        ttk.Button(self.frame, text='Refresh usage', command=self.refresh).grid(row=6, column=0, sticky="w")
        ttk.Button(self.frame, text='Save', command=self.save).grid(row=6, column=1, sticky="e")
        self.refresh()

    def refresh(self):
        import lcsc_settings
        manager = lcsc_settings.get_usage_manager()
        manager._load_usage_cache()
        used = int(manager.usage.get("lcsc", 0))
        limit = int(lcsc_settings.load_settings()["daily_limit"])
        remaining = max(0, limit - used) if limit else 'Unlimited'
        self.usage_var.set(f"Day (UTC): {manager.usage_day}    Used: {used}    Remaining: {remaining}")

    def save(self):
        import lcsc_settings
        try:
            lcsc_settings.save_settings({key: var.get() for key, var in self.variables.items()})
            self.refresh()
            messagebox.showinfo("LCSC", 'Settings saved. They will be used for the next query.', parent=self.win)
        except (ValueError, OSError) as exc:
            messagebox.showerror("LCSC", str(exc), parent=self.win)


class SettingsWindow:
    """Ayarlar penceresi.

    Tabs for everyone: change history, database/backup, own account.
    Tabs for administrators only: API keys, users.
    """

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title('Settings')
        self.win.geometry('900x640')
        self.win.minsize(760, 560)
        self.win.transient(app.root)
        self.win.grab_set()

        user = getattr(app, 'user', None)
        self.is_admin = users.is_admin(user)

        bottom = ttk.Frame(self.win, padding=(8, 4, 8, 8))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(bottom, text='Close', command=self.win.destroy).pack(side=tk.RIGHT)
        if not self.is_admin:
            ttk.Label(bottom, text='Only administrators can manage API keys and users.',
                      foreground='gray').pack(side=tk.LEFT)

        self.notebook = ttk.Notebook(self.win)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        self.api_tab = self.users_tab = None
        if self.is_admin:
            self.api_tab = ApiKeysTab(self.notebook, app, self.win)
            self.notebook.add(self.api_tab.frame, text='DigiKey API / Daily usage')
            self.lcsc_tab = LcscSettingsTab(self.notebook, self.win)
            self.notebook.add(self.lcsc_tab.frame, text='LCSC / Daily usage')
            self.users_tab = UsersTab(self.notebook, app, self.win)
            self.notebook.add(self.users_tab.frame, text='Users')
        self.history_tab = HistoryTab(self.notebook, app, self.win)
        self.notebook.add(self.history_tab.frame, text='Change history')
        self.database_tab = DatabaseTab(self.notebook, app, self.win)
        self.notebook.add(self.database_tab.frame, text='Database')
        self.account_tab = AccountTab(self.notebook, app, self.win)
        self.notebook.add(self.account_tab.frame, text='My account')


def open_settings(app):
    return SettingsWindow(app)
