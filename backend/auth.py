import hashlib
import re
import secrets

import bcrypt

from . import config
from .db import get_conn

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CHART_TYPES = ("none", "bar", "line", "pie")
THEMES = ("light", "dark")
IP_FAIL_LIMIT = 20  # failed logins from one IP inside the lockout window


class AuthError(Exception):
    """Message is safe to show to the end user."""


# ---------------------------------------------------------------- passwords
def _check_password_rules(password: str) -> None:
    if len(password) < config.MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {config.MIN_PASSWORD_LEN} characters.")
    if len(password.encode()) > 72:  # bcrypt ignores/rejects anything longer
        raise AuthError("Password is too long (max 72 bytes).")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------- signup
def signup(username: str, email: str, password: str, consent: bool) -> int:
    username, email = username.strip(), email.strip().lower()
    if not consent:
        raise AuthError("You must accept that your questions are stored to register.")
    if not USERNAME_RE.match(username):
        raise AuthError("Username must be 3-30 letters, digits or underscores.")
    if not EMAIL_RE.match(email) or len(email) > 120:
        raise AuthError("Invalid email address.")
    _check_password_rules(password)

    with get_conn() as conn:
        plan = conn.one("SELECT id FROM plans WHERE name='free'")
        if not plan:
            raise RuntimeError("Plans are not seeded. Run: python -m backend.seed")
        if conn.one("SELECT 1 FROM users WHERE username=%s OR email=%s", (username, email)):
            clash = True
        else:
            clash = False
            user_id = conn.insert(
                """INSERT INTO users (username, email, password_hash, plan_id, consent_accepted_at)
                   VALUES (%s,%s,%s,%s,NOW())""",
                (username, email, hash_password(password), plan["id"]),
            )
            conn.execute("INSERT INTO user_settings (user_id) VALUES (%s)", (user_id,))
            conn.execute("INSERT INTO token_balances (user_id, balance) VALUES (%s, 0)", (user_id,))
    if clash:
        raise AuthError("Username or email already registered.")
    return user_id


# -------------------------------------------------------------------- login
def _public_user(row: dict) -> dict:
    return {
        "id": row["id"], "username": row["username"], "email": row["email"],
        "role": row["role"], "plan_id": row["plan_id"], "plan": row["plan_name"],
    }


_USER_SELECT = """SELECT u.id, u.username, u.email, u.role, u.plan_id, u.is_active, u.password_hash,
                         p.name AS plan_name
                  FROM users u JOIN plans p ON p.id = u.plan_id"""


def _locked_out(conn, username: str, ip: str | None) -> bool:
    window = config.LOCKOUT_MINUTES
    n = conn.one(
        """SELECT COUNT(*) AS n FROM login_attempts
           WHERE username_tried=%s AND success=0 AND attempted_at >= NOW() - INTERVAL %s MINUTE
             AND id > COALESCE((SELECT MAX(id) FROM login_attempts WHERE username_tried=%s AND success=1), 0)""",
        (username, window, username),
    )["n"]
    if n >= config.MAX_FAILED_LOGINS:
        return True
    if ip:
        n_ip = conn.one(
            """SELECT COUNT(*) AS n FROM login_attempts
               WHERE ip_address=%s AND success=0 AND attempted_at >= NOW() - INTERVAL %s MINUTE""",
            (ip, window),
        )["n"]
        return n_ip >= IP_FAIL_LIMIT
    return False


def login(username: str, password: str, ip: str | None = None, user_agent: str | None = None) -> dict:
    """Returns {"user": {...}, "token": "..."}. Store the token in st.session_state."""
    username = username.strip()[:120]
    token = None
    user = None
    with get_conn() as conn:
        if _locked_out(conn, username, ip):
            state = "locked"
        else:
            row = conn.one(_USER_SELECT + " WHERE u.username=%s OR u.email=%s", (username, username.lower()))
            ok = bool(row) and row["is_active"] == 1 and verify_password(password, row["password_hash"])
            conn.execute(
                "INSERT INTO login_attempts (username_tried, user_id, ip_address, success) VALUES (%s,%s,%s,%s)",
                (username, row["id"] if row else None, ip, int(ok)),
            )
            if ok:
                state = "ok"
                token = secrets.token_urlsafe(32)
                conn.execute(
                    """INSERT INTO auth_sessions (user_id, session_token_hash, ip_address, user_agent, expires_at)
                       VALUES (%s,%s,%s,%s, NOW() + INTERVAL %s HOUR)""",
                    (row["id"], _token_hash(token), ip, (user_agent or "")[:255] or None, config.SESSION_HOURS),
                )
                conn.execute("UPDATE users SET last_login_at=NOW() WHERE id=%s", (row["id"],))
                user = _public_user(row)
            else:
                state = "bad"
    # Raise after the with-block so the attempt row is committed (get_conn rolls back on exceptions).
    if state == "locked":
        raise AuthError(f"Too many failed attempts. Try again in {config.LOCKOUT_MINUTES} minutes.")
    if state == "bad":
        raise AuthError("Invalid username or password.")  # same text for unknown user / wrong pw / disabled
    return {"user": user, "token": token}


def validate_session(token: str | None) -> dict | None:
    """Return the user for a live session token, else None. Call this on every page."""
    if not token:
        return None
    with get_conn() as conn:
        row = conn.one(
            _USER_SELECT + """ JOIN auth_sessions s ON s.user_id = u.id
               WHERE s.session_token_hash=%s AND s.revoked_at IS NULL AND s.expires_at > NOW()
                 AND u.is_active=1""",
            (_token_hash(token),),
        )
    return _public_user(row) if row else None


def logout(token: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at=NOW() WHERE session_token_hash=%s AND revoked_at IS NULL",
            (_token_hash(token),),
        )


def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.one(_USER_SELECT + " WHERE u.id=%s", (user_id,))
    return _public_user(row) if row else None


# ------------------------------------------------------------ password mgmt
def change_password(user_id: int, old: str, new: str, keep_token: str | None = None) -> None:
    _check_password_rules(new)
    with get_conn() as conn:
        row = conn.one("SELECT password_hash FROM users WHERE id=%s", (user_id,))
        good = bool(row) and verify_password(old, row["password_hash"])
        if good:
            conn.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(new), user_id))
            # Sign out every other device.
            conn.execute(
                """UPDATE auth_sessions SET revoked_at=NOW()
                   WHERE user_id=%s AND revoked_at IS NULL AND session_token_hash <> %s""",
                (user_id, _token_hash(keep_token) if keep_token else ""),
            )
    if not good:
        raise AuthError("Current password is incorrect.")


def request_password_reset(email: str) -> str | None:
    """Returns a reset token to email to the user, or None if the email is unknown.

    The UI must show the same message either way ("if that email exists, we sent a link").
    """
    email = email.strip().lower()
    with get_conn() as conn:
        user = conn.one("SELECT id FROM users WHERE email=%s AND is_active=1", (email,))
        if not user:
            return None
        conn.execute("UPDATE password_reset_tokens SET used_at=NOW() WHERE user_id=%s AND used_at IS NULL", (user["id"],))
        token = secrets.token_urlsafe(32)
        conn.execute(
            """INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
               VALUES (%s,%s, NOW() + INTERVAL %s MINUTE)""",
            (user["id"], _token_hash(token), config.RESET_TOKEN_MINUTES),
        )
    return token


def reset_password(token: str, new_password: str) -> None:
    _check_password_rules(new_password)
    with get_conn() as conn:
        row = conn.one(
            """SELECT id, user_id FROM password_reset_tokens
               WHERE token_hash=%s AND used_at IS NULL AND expires_at > NOW()""",
            (_token_hash(token),),
        )
        if row:
            conn.execute("UPDATE password_reset_tokens SET used_at=NOW() WHERE id=%s", (row["id"],))
            conn.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(new_password), row["user_id"]))
            conn.execute("UPDATE auth_sessions SET revoked_at=NOW() WHERE user_id=%s AND revoked_at IS NULL", (row["user_id"],))
            conn.execute(
                "DELETE FROM login_attempts WHERE username_tried IN (SELECT username FROM users WHERE id=%s)",
                (row["user_id"],),
            )
    if not row:
        raise AuthError("This reset link is invalid or has expired.")


# ------------------------------------------------------------- account/settings
def delete_account(user_id: int, password: str) -> str:
    """Delete (or anonymise) the account. Returns "deleted" or "anonymised".

    Chats/prompts/tokens keep their rows with user_id=NULL (research data, as the consent text says).
    Payments have no cascade (financial records must be kept), so a user with payments is
    anonymised and deactivated instead of removed.
    """
    with get_conn() as conn:
        row = conn.one("SELECT password_hash FROM users WHERE id=%s", (user_id,))
        good = bool(row) and verify_password(password, row["password_hash"])
        result = None
        if good:
            conn.execute("INSERT INTO audit_logs (admin_id, action, target_type, target_id) VALUES (NULL,'self_delete_account','user',%s)", (user_id,))
            if conn.one("SELECT 1 FROM payments WHERE user_id=%s LIMIT 1", (user_id,)):
                conn.execute(
                    """UPDATE users SET username=CONCAT('deleted_', id), email=CONCAT('deleted_', id, '@invalid'),
                              password_hash=%s, is_active=0 WHERE id=%s""",
                    (hash_password(secrets.token_urlsafe(24)), user_id),
                )
                for table in ("auth_sessions", "saved_queries", "notifications", "user_settings", "token_balances"):
                    conn.execute(f"DELETE FROM {table} WHERE user_id=%s", (user_id,))
                result = "anonymised"
            else:
                conn.execute("DELETE FROM users WHERE id=%s", (user_id,))
                result = "deleted"
    if not good:
        raise AuthError("Password is incorrect.")
    return result


def get_settings(user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.one(
            "SELECT default_chart_type, theme, rows_per_page FROM user_settings WHERE user_id=%s", (user_id,)
        )
    return row or {"default_chart_type": "bar", "theme": "light", "rows_per_page": 50}


def update_settings(user_id: int, default_chart_type: str, theme: str, rows_per_page: int = 50) -> None:
    if default_chart_type not in CHART_TYPES:
        raise AuthError("Unsupported chart type.")
    if theme not in THEMES:
        raise AuthError("Unsupported theme.")
    if not 5 <= int(rows_per_page) <= 500:
        raise AuthError("Rows per page must be between 5 and 500.")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO user_settings (user_id, default_chart_type, theme, rows_per_page) VALUES (%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE default_chart_type=VALUES(default_chart_type),
                                       theme=VALUES(theme), rows_per_page=VALUES(rows_per_page)""",
            (user_id, default_chart_type, theme, int(rows_per_page)),
        )
