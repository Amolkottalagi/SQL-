"""Bell icon / toast backend. Rows live in `notifications`."""
from .db import get_conn

TYPES = ("limit_reached", "payment_success", "payment_failed", "plan_changed", "info")


def notify(conn, user_id: int, type_: str, message: str, once_per_day: bool = False) -> None:
    """Create a notification using an existing transaction (conn from db.get_conn)."""
    assert type_ in TYPES
    if once_per_day and conn.one(
        "SELECT 1 FROM notifications WHERE user_id=%s AND type=%s AND DATE(created_at)=CURDATE() LIMIT 1",
        (user_id, type_),
    ):
        return
    conn.execute(
        "INSERT INTO notifications (user_id, type, message) VALUES (%s,%s,%s)", (user_id, type_, message[:255])
    )


def list_notifications(user_id: int, unread_only: bool = False, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        return conn.all(
            f"""SELECT id, type, message, is_read, created_at FROM notifications
                WHERE user_id=%s {'AND is_read=0' if unread_only else ''} ORDER BY id DESC LIMIT %s""",
            (user_id, limit),
        )


def unread_count(user_id: int) -> int:
    with get_conn() as conn:
        return conn.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id=%s AND is_read=0", (user_id,))["n"]


def mark_read(user_id: int, notification_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE notifications SET is_read=1 WHERE id=%s AND user_id=%s", (notification_id, user_id))


def mark_all_read(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=%s AND is_read=0", (user_id,))
