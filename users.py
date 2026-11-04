"""User accounts stored in KicadDB.sqlite (table `Users`).

Passwords are never stored, in any reversible form. Each account keeps a
PBKDF2-HMAC-SHA256 digest of its password together with a random per-user
salt and the iteration count, so reading the database file does not reveal a
password and two users with the same password get different digests.

Every account change is written to the Change_Log table and to the
application log — without the password or its digest.
"""
import os
import hmac
import hashlib
import logging
import re

import db

log = logging.getLogger("company.users")

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)
ROLE_LABELS = {ROLE_ADMIN: 'Administrator', ROLE_USER: 'User'}

PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 240_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 6
MAX_USERNAME_LENGTH = 40
DEFAULT_PASSWORD = "123456"

USER_COLUMNS = [
    "id", "Username", "Full_Name", "Role", "Is_Active", "Must_Change_Password",
    "Created_At", "Created_By", "Modified_At", "Modified_By", "Last_Login_At", "Email",
]


class UserError(ValueError):
    """A rule was broken; the message is safe to show to the user."""


# ------------------------------------------------------------------
# Password hashing
# ------------------------------------------------------------------

def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    """Return (salt_hex, digest_hex, iterations) for `password`."""
    if salt is None:
        salt = os.urandom(SALT_BYTES)
    elif isinstance(salt, str):
        salt = bytes.fromhex(salt)
    digest = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM, str(password).encode("utf-8"), salt, int(iterations)
    )
    return salt.hex(), digest.hex(), int(iterations)


def verify_password(password, salt_hex, digest_hex, iterations):
    try:
        _, candidate, _ = hash_password(password, salt_hex, iterations)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, str(digest_hex or ""))


def validate_password(password):
    password = "" if password is None else str(password)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Password must contain at least {MIN_PASSWORD_LENGTH} characters.")
    if password.strip() != password:
        raise UserError('Password cannot start or end with whitespace.')
    return password


def validate_username(username):
    username = str(username or "").strip()
    if not username:
        raise UserError('Username cannot be empty.')
    if len(username) > MAX_USERNAME_LENGTH:
        raise UserError(f"Username cannot exceed {MAX_USERNAME_LENGTH} characters.")
    if any(ch.isspace() for ch in username):
        raise UserError('Username cannot contain spaces.')
    return username


def validate_role(role):
    role = str(role or "").strip().lower()
    if role not in ROLES:
        raise UserError("Role must be 'admin' or 'user'.")
    return role


def validate_email(email):
    """Accept one mailbox, never a display name or a list of recipients."""
    email = str(email or "").strip().lower()
    if not email:
        return ""
    if (len(email) > 254 or not email.isascii()
            or not re.fullmatch(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", email)
            or ".." in email or email.startswith(".") or ".@" in email
            or len(email.split("@")[0]) > 64):
        raise UserError('Enter a valid email address, such as name@company.com.')
    return email


# ------------------------------------------------------------------
# Table
# ------------------------------------------------------------------

def ensure_users_table(conn=None):
    sql = f"""CREATE TABLE IF NOT EXISTS {db.quote_identifier(db.USERS_TABLE)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        Username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        Full_Name TEXT DEFAULT '',
        Role TEXT NOT NULL DEFAULT 'user',
        Password_Hash TEXT NOT NULL,
        Password_Salt TEXT NOT NULL,
        Password_Iterations INTEGER NOT NULL,
        Is_Active INTEGER NOT NULL DEFAULT 1,
        Must_Change_Password INTEGER NOT NULL DEFAULT 0,
        Created_At TEXT DEFAULT '',
        Created_By TEXT DEFAULT '',
        Modified_At TEXT DEFAULT '',
        Modified_By TEXT DEFAULT '',
        Last_Login_At TEXT DEFAULT ''
    )"""
    if conn is not None:
        conn.execute(sql)
        columns = {row[1].lower() for row in conn.execute('PRAGMA table_info("Users")')}
        if "email" not in columns:
            conn.execute('ALTER TABLE "Users" ADD COLUMN Email TEXT NOT NULL DEFAULT \'\'')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS Users_Email_Unique '
                     'ON "Users" (Email COLLATE NOCASE) WHERE Email <> \'\'')
        return
    with db.connection(commit=True) as own:
        ensure_users_table(own)


def _row_to_user(row):
    user = dict(zip(USER_COLUMNS, row))
    user["Is_Active"] = bool(user.get("Is_Active"))
    user["Must_Change_Password"] = bool(user.get("Must_Change_Password"))
    return user


def _select_user(conn, username):
    cols = ", ".join(USER_COLUMNS)
    row = conn.execute(
        f"SELECT {cols} FROM {db.quote_identifier(db.USERS_TABLE)} WHERE Username = ? COLLATE NOCASE",
        (str(username or "").strip(),),
    ).fetchone()
    return _row_to_user(row) if row else None


def _active_admin_count(conn, excluding_id=None):
    sql = (f"SELECT COUNT(*) FROM {db.quote_identifier(db.USERS_TABLE)} "
           "WHERE Role = ? AND Is_Active = 1")
    params = [ROLE_ADMIN]
    if excluding_id is not None:
        sql += " AND id <> ?"
        params.append(excluding_id)
    return int(conn.execute(sql, params).fetchone()[0])


def list_users():
    with db.connection(commit=True) as conn:
        ensure_users_table(conn)
        cols = ", ".join(USER_COLUMNS)
        rows = conn.execute(
            f"SELECT {cols} FROM {db.quote_identifier(db.USERS_TABLE)} ORDER BY Username COLLATE NOCASE"
        ).fetchall()
    return [_row_to_user(r) for r in rows]


def get_user(username):
    with db.connection(commit=True) as conn:
        ensure_users_table(conn)
        return _select_user(conn, username)


def count_users():
    with db.connection(commit=True) as conn:
        ensure_users_table(conn)
        return int(conn.execute(
            f"SELECT COUNT(*) FROM {db.quote_identifier(db.USERS_TABLE)}"
        ).fetchone()[0])


def is_admin(user):
    return bool(user) and str(user.get("Role", "")).lower() == ROLE_ADMIN


# ------------------------------------------------------------------
# Account management
# ------------------------------------------------------------------

def create_user(username, password=DEFAULT_PASSWORD, role=ROLE_USER, full_name="", must_change_password=False, actor=None, email=""):
    username = validate_username(username)
    password = validate_password(password)
    role = validate_role(role)
    email = validate_email(email)
    must_change_password = must_change_password or password == DEFAULT_PASSWORD
    actor = actor or db.get_current_user()
    stamp = db.now_text()
    salt, digest, iterations = hash_password(password)
    with db.connection(commit=True) as conn:
        ensure_users_table(conn)
        if _select_user(conn, username):
            raise UserError(f"'{username}' username is already in use.")
        _check_email_available(conn, email)
        conn.execute(
            f"INSERT INTO {db.quote_identifier(db.USERS_TABLE)} "
            "(Username, Full_Name, Role, Password_Hash, Password_Salt, Password_Iterations, "
            " Is_Active, Must_Change_Password, Created_At, Created_By, Modified_At, Modified_By, Email) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (username, str(full_name or "").strip(), role, digest, salt, iterations,
             1 if must_change_password else 0, stamp, actor, stamp, actor, email),
        )
        line = db.write_change_log(
            conn, "USER_CREATE", db.USERS_TABLE, username,
            details={"role": role, "full_name": str(full_name or "").strip(), "email": email},
            user=actor, stamp=stamp,
        )
    db.audit_log.info(line)
    return get_user(username)


def set_password(username, new_password, must_change_password=False, actor=None):
    new_password = validate_password(new_password)
    must_change_password = must_change_password or new_password == DEFAULT_PASSWORD
    actor = actor or db.get_current_user()
    stamp = db.now_text()
    salt, digest, iterations = hash_password(new_password)
    with db.connection(commit=True) as conn:
        ensure_users_table(conn)
        user = _select_user(conn, username)
        if not user:
            raise UserError(f"User not found: {username}")
        conn.execute(
            f"UPDATE {db.quote_identifier(db.USERS_TABLE)} SET Password_Hash = ?, Password_Salt = ?, "
            "Password_Iterations = ?, Must_Change_Password = ?, Modified_At = ?, Modified_By = ? WHERE id = ?",
            (digest, salt, iterations, 1 if must_change_password else 0, stamp, actor, user["id"]),
        )
        action = "USER_PASSWORD_CHANGE" if actor.casefold() == user["Username"].casefold() else "USER_PASSWORD_RESET"
        line = db.write_change_log(conn, action, db.USERS_TABLE, user["Username"], user=actor, stamp=stamp)
    db.audit_log.info(line)


def change_own_password(username, current_password, new_password):
    """Password change by the account owner; the current password must match."""
    authenticate(username, current_password, record_login=False)
    if str(current_password) == str(new_password):
        raise UserError('New password must differ from the old password.')
    if str(new_password) == DEFAULT_PASSWORD:
        raise UserError('Choose a personal password different from the default password.')
    set_password(username, new_password, must_change_password=False, actor=username)


def _check_email_available(conn, email, excluding_id=-1):
    if email and conn.execute('SELECT 1 FROM "Users" WHERE Email = ? COLLATE NOCASE AND id <> ?',
                              (email, excluding_id)).fetchone():
        raise UserError('This email address is already assigned to another user.')


def update_user(username, role=None, full_name=None, is_active=None, actor=None, email=None):
    actor = actor or db.get_current_user()
    stamp = db.now_text()
    with db.connection(commit=True) as conn:
        ensure_users_table(conn)
        user = _select_user(conn, username)
        if not user:
            raise UserError(f"User not found: {username}")
        new_role = validate_role(role) if role is not None else user["Role"]
        new_active = user["Is_Active"] if is_active is None else bool(is_active)
        new_name = user["Full_Name"] if full_name is None else str(full_name).strip()
        new_email = user["Email"] if email is None else validate_email(email)
        _check_email_available(conn, new_email, user["id"])
        loses_admin = is_admin(user) and user["Is_Active"] and (new_role != ROLE_ADMIN or not new_active)
        if loses_admin and _active_admin_count(conn, excluding_id=user["id"]) == 0:
            raise UserError('The last active administrator cannot be disabled or demoted.')
        changes = {}
        if new_role != user["Role"]:
            changes["Role"] = {"old": user["Role"], "new": new_role}
        if new_active != user["Is_Active"]:
            changes["Is_Active"] = {"old": user["Is_Active"], "new": new_active}
        if new_name != (user["Full_Name"] or ""):
            changes["Full_Name"] = {"old": user["Full_Name"] or "", "new": new_name}
        if new_email != user["Email"]:
            changes["Email"] = {"old": user["Email"], "new": new_email}
        if not changes:
            return user
        conn.execute(
            f"UPDATE {db.quote_identifier(db.USERS_TABLE)} SET Role = ?, Full_Name = ?, Is_Active = ?, "
            "Modified_At = ?, Modified_By = ?, Email = ? WHERE id = ?",
            (new_role, new_name, 1 if new_active else 0, stamp, actor, new_email, user["id"]),
        )
        line = db.write_change_log(
            conn, "USER_UPDATE", db.USERS_TABLE, user["Username"],
            details={"changes": changes}, user=actor, stamp=stamp,
        )
    db.audit_log.info(line)
    return get_user(username)


def delete_user(username, actor=None):
    actor = actor or db.get_current_user()
    stamp = db.now_text()
    with db.connection(commit=True) as conn:
        ensure_users_table(conn)
        user = _select_user(conn, username)
        if not user:
            raise UserError(f"User not found: {username}")
        if is_admin(user) and user["Is_Active"] and _active_admin_count(conn, excluding_id=user["id"]) == 0:
            raise UserError('The last active administrator cannot be deleted.')
        conn.execute(
            f"DELETE FROM {db.quote_identifier(db.USERS_TABLE)} WHERE id = ?", (user["id"],)
        )
        line = db.write_change_log(
            conn, "USER_DELETE", db.USERS_TABLE, user["Username"],
            details={"role": user["Role"], "full_name": user["Full_Name"] or ""},
            user=actor, stamp=stamp,
        )
    db.audit_log.info(line)


# ------------------------------------------------------------------
# Login
# ------------------------------------------------------------------

# Compared against when the user name does not exist, so that an unknown user
# and a wrong password take the same time to reject.
_DUMMY_SALT, _DUMMY_DIGEST, _DUMMY_ITERATIONS = hash_password("company-dummy-password")

LOGIN_FAILED_MESSAGE = 'Incorrect username or password.'


def authenticate(username, password, record_login=True):
    """Return the user dict, or raise UserError with a message for the login form."""
    username = str(username or "").strip()
    password = "" if password is None else str(password)
    with db.connection(commit=True) as conn:
        ensure_users_table(conn)
        user = _select_user(conn, username)
        if user:
            secret = conn.execute(
                f"SELECT Password_Salt, Password_Hash, Password_Iterations "
                f"FROM {db.quote_identifier(db.USERS_TABLE)} WHERE id = ?",
                (user["id"],),
            ).fetchone()
        else:
            secret = (_DUMMY_SALT, _DUMMY_DIGEST, _DUMMY_ITERATIONS)
    matches = verify_password(password, *secret)
    if not user or not matches:
        log.warning("LOGIN_FAILED user=%s", username or "-")
        raise UserError(LOGIN_FAILED_MESSAGE)
    if not user["Is_Active"]:
        log.warning("LOGIN_REJECTED_INACTIVE user=%s", user["Username"])
        raise UserError('This account is disabled. Contact your administrator.')
    if record_login:
        stamp = db.now_text()
        with db.connection(commit=True) as conn:
            conn.execute(
                f"UPDATE {db.quote_identifier(db.USERS_TABLE)} SET Last_Login_At = ? WHERE id = ?",
                (stamp, user["id"]),
            )
        user["Last_Login_At"] = stamp
        log.info("LOGIN user=%s role=%s", user["Username"], user["Role"])
    return user
