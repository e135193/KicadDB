"""Login, first-administrator and change-password dialogs.

These dialogs are shown while the main window is still withdrawn, so they are
plain top-level windows centred on the screen (a window made transient to a
withdrawn root is not shown on Windows).
"""
import tkinter as tk
from tkinter import ttk, messagebox

import users

MAX_LOGIN_ATTEMPTS = 5


def center_window(window, width, height):
    window.update_idletasks()
    x = max((window.winfo_screenwidth() - width) // 2, 0)
    y = max((window.winfo_screenheight() - height) // 3, 0)
    window.geometry(f"{width}x{height}+{x}+{y}")


class _Dialog:
    """Small modal dialog base.

    Layout: padded form (`self.body`, a two-column grid filled by `build()`).
    The window is as tall as its content needs, so nothing is clipped when a
    message wraps onto a second line.
    """

    title = ""
    width = 400
    show_logo = False

    def __init__(self, parent, icon=None, logo=None):
        self.parent = parent
        self.result = None
        self.win = tk.Toplevel(parent)
        self.win.title(self.title)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)

        # Kept on self: Tk holds no reference, and a collected image shows as
        # an empty label.
        self.logo = (logo or load_logo(self.win)) if self.show_logo else None
        icon = icon or self.logo
        if icon is not None:
            try:
                self.win.iconphoto(False, icon)
            except Exception:
                pass

        self.logo_label = None
        if self.logo is not None:
            # pack() centres a widget horizontally in the space it is given.
            self.logo_label = ttk.Label(self.win, image=self.logo, anchor=tk.CENTER)
            self.logo_label.pack(side=tk.TOP, pady=(18, 0))

        self.body = ttk.Frame(self.win, padding=16)
        self.body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.body.columnconfigure(1, weight=1)
        self.build()
        self.fit(center=True)
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.after(200, lambda: self.win.attributes("-topmost", False))
        self.win.grab_set()
        self.win.focus_force()

    def build(self):
        raise NotImplementedError

    def fit(self, center=False):
        """Size the window to its content (fixed width, height as needed)."""
        self.win.update_idletasks()
        width = max(self.width, self.win.winfo_reqwidth())
        height = self.win.winfo_reqheight()
        if center:
            center_window(self.win, width, height)
        else:
            self.win.geometry(f"{width}x{height}")

    def add_entry(self, row, label, variable, show=None):
        ttk.Label(self.body, text=label).grid(row=row, column=0, sticky=tk.W, pady=4, padx=(0, 10))
        entry = ttk.Entry(self.body, textvariable=variable, show=show or "")
        entry.grid(row=row, column=1, sticky=tk.EW, pady=4)
        return entry

    def add_buttons(self, row, ok_text, ok_command, cancel_text='Cancel'):
        frame = ttk.Frame(self.body)
        frame.grid(row=row, column=0, columnspan=2, sticky=tk.E, pady=(14, 0))
        ttk.Button(frame, text=cancel_text, command=self.cancel).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(frame, text=ok_text, command=ok_command).pack(side=tk.RIGHT)
        self.win.bind("<Return>", lambda event: ok_command())
        self.win.bind("<Escape>", lambda event: self.cancel())

    def warn(self, message, title='Warning'):
        messagebox.showwarning(title, message, parent=self.win)

    def cancel(self):
        self.result = None
        self.close()

    def close(self):
        try:
            self.win.grab_release()
        except Exception:
            pass
        self.win.destroy()

    def wait(self):
        self.parent.wait_window(self.win)
        return self.result


class LoginDialog(_Dialog):
    title = 'Company KiCad DB - Sign in'
    width = 380

    def __init__(self, parent, initial_username="", icon=None, logo=None):
        self.initial_username = initial_username
        self.attempts = 0
        super().__init__(parent, icon=icon, logo=logo)

    def build(self):
        self.username_var = tk.StringVar(value=self.initial_username)
        self.password_var = tk.StringVar()
        self.username_entry = self.add_entry(0, 'Username', self.username_var)
        self.password_entry = self.add_entry(1, 'Password', self.password_var, show="•")
        self.message_var = tk.StringVar(value="")
        ttk.Label(self.body, textvariable=self.message_var, foreground="#C00000", wraplength=340).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
        self.add_buttons(3, 'Sign in', self.submit, cancel_text='Exit')
        self.password_entry.focus_set()

    def show_message(self, text):
        self.message_var.set(text)
        self.fit()

    def submit(self):
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            self.show_message('Enter your username and password.')
            return
        try:
            self.result = users.authenticate(username, password)
        except users.UserError as exc:
            self.attempts += 1
            self.password_var.set("")
            self.password_entry.focus_set()
            remaining = MAX_LOGIN_ATTEMPTS - self.attempts
            if remaining <= 0:
                messagebox.showerror(
                    'Sign in', 'Too many failed attempts. The application will close.', parent=self.win)
                self.cancel()
                return
            self.show_message(f"{exc}  (Remaining attempts: {remaining})")
            return
        except Exception as exc:
            messagebox.showerror('Sign in', f"Sign-in failed:\n{exc}", parent=self.win)
            return
        self.close()


class FirstAdminDialog(_Dialog):
    """Shown once, when the Users table is empty."""

    title = 'First administrator account'
    width = 440

    def build(self):
        ttk.Label(
            self.body, wraplength=400, justify=tk.LEFT,
            text=('No users exist yet. To use the application, first create an '
                  'administrator account. You can add other users under '
                  'Settings > Users.'),
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))
        self.username_var = tk.StringVar(value="admin")
        self.full_name_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.password_var = tk.StringVar(value=users.DEFAULT_PASSWORD)
        self.repeat_var = tk.StringVar(value=users.DEFAULT_PASSWORD)
        self.add_entry(1, 'Username', self.username_var)
        self.add_entry(2, 'Full name', self.full_name_var)
        first = self.add_entry(3, 'Password', self.password_var, show="•")
        self.add_entry(4, 'Confirm password', self.repeat_var, show="•")
        self.add_entry(5, 'Email', self.email_var)
        ttk.Label(self.body, text='Default password: 123456. A change is required at sign-in.',
                  wraplength=400).grid(row=6, column=0, columnspan=2, sticky='w')
        self.add_buttons(7, 'Create', self.submit, cancel_text='Exit')
        first.focus_set()

    def submit(self):
        if self.password_var.get() != self.repeat_var.get():
            self.warn('Passwords do not match.')
            return
        try:
            self.result = users.create_user(
                self.username_var.get(), self.password_var.get(),
                role=users.ROLE_ADMIN, full_name=self.full_name_var.get(),
                email=self.email_var.get(),
                actor=self.username_var.get().strip() or "-",
            )
        except users.UserError as exc:
            self.warn(str(exc))
            return
        except Exception as exc:
            messagebox.showerror('Error', f"Could not create account:\n{exc}", parent=self.win)
            return
        self.close()


class ChangePasswordDialog(_Dialog):
    """The signed-in user changes their own password."""

    title = 'Change password'
    width = 420

    def __init__(self, parent, username, forced=False, icon=None, logo=None):
        self.username = username
        self.forced = forced
        super().__init__(parent, icon=icon, logo=logo)

    def build(self):
        text = f"User: {self.username}"
        if self.forced:
            text += '\nYou must choose a new password to replace your temporary password.'
        ttk.Label(self.body, text=text, wraplength=380, justify=tk.LEFT).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))
        self.current_var = tk.StringVar()
        self.new_var = tk.StringVar()
        self.repeat_var = tk.StringVar()
        first = self.add_entry(1, 'Current password', self.current_var, show="•")
        self.add_entry(2, 'New password', self.new_var, show="•")
        self.add_entry(3, 'Confirm new password', self.repeat_var, show="•")
        self.add_buttons(4, 'Change', self.submit, cancel_text='Exit' if self.forced else 'Cancel')
        first.focus_set()

    def submit(self):
        if self.new_var.get() != self.repeat_var.get():
            self.warn('New passwords do not match.')
            return
        try:
            users.change_own_password(self.username, self.current_var.get(), self.new_var.get())
        except users.UserError as exc:
            message = str(exc)
            if message == users.LOGIN_FAILED_MESSAGE:
                message = 'Incorrect current password.'
            self.warn(message)
            return
        except Exception as exc:
            messagebox.showerror('Error', f"Could not change password:\n{exc}", parent=self.win)
            return
        self.result = True
        messagebox.showinfo('Change password', 'Your password has been changed.', parent=self.win)
        self.close()


def run_login_flow(root, initial_username="", icon=None):
    """Create the first admin if needed, then sign a user in.

    Returns the user dict, or None when the user closed a dialog (the caller
    should then exit the program).
    """
    if users.count_users() == 0:
        created = FirstAdminDialog(root, icon=icon).wait()
        if not created:
            return None
        initial_username = created["Username"]
    user = LoginDialog(root, initial_username=initial_username, icon=icon).wait()
    if not user:
        return None
    if user.get("Must_Change_Password"):
        changed = ChangePasswordDialog(root, user["Username"], forced=True, icon=icon).wait()
        if not changed:
            return None
        user = users.get_user(user["Username"]) or user
    return user