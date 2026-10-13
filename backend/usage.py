"""Quota rules and token accounting.

Rules
- plans.daily_query_limit / daily_token_limit: NULL means unlimited.
- A "query" is one question (self-correction retries don't count extra).
- When the daily token allowance is used up, purchased tokens (token_balances) are spent instead.
  Tokens beyond the daily allowance are deducted from the balance; if both are empty the user is blocked.
- daily_usage_summary is updated in the same transaction as token_usage, so quota checks stay accurate.
"""
import datetime

from . import notifications
from .db import get_conn


class QuotaExceeded(Exception):
    """Message is safe to show to the user."""


def get_plan(conn, user_id: int) -> dict:
    row = conn.one(
        """SELECT p.name, p.daily_query_limit, p.daily_token_limit, p.can_export_csv, p.can_use_charts
           FROM users u JOIN plans p ON p.id=u.plan_id WHERE u.id=%s""",
        (user_id,),
    )
    if not row:
        raise ValueError("User not found.")
    return row


def _today_row(conn, user_id: int) -> dict:
    return conn.one(
        "SELECT query_count, error_count, total_tokens FROM daily_usage_summary WHERE user_id=%s AND usage_date=CURDATE()",
        (user_id,),
    ) or {"query_count": 0, "error_count": 0, "total_tokens": 0}


def _balance(conn, user_id: int) -> int:
    row = conn.one("SELECT balance FROM token_balances WHERE user_id=%s", (user_id,))
    return int(row["balance"]) if row else 0


def get_today_usage(user_id: int) -> dict:
    """For the sidebar usage bar and the Usage page. *_limit is None when unlimited."""
    with get_conn() as conn:
        plan, today, bal = get_plan(conn, user_id), _today_row(conn, user_id), _balance(conn, user_id)
    ql, tl = plan["daily_query_limit"], plan["daily_token_limit"]
    return {
        "plan": plan["name"],
        "queries_used": today["query_count"],
        "queries_limit": ql,
        "queries_left": None if ql is None else max(ql - today["query_count"], 0),
        "tokens_used": int(today["total_tokens"]),
        "tokens_limit": tl,
        "tokens_left": None if tl is None else max(tl - int(today["total_tokens"]), 0),
        "token_balance": bal,
        "errors_today": today["error_count"],
        "can_export_csv": bool(plan["can_export_csv"]),
        "can_use_charts": bool(plan["can_use_charts"]),
    }


def check_quota(user_id: int) -> None:
    """Raise QuotaExceeded if the user may not ask another question right now."""
    msg = None
    with get_conn() as conn:
        plan, today, bal = get_plan(conn, user_id), _today_row(conn, user_id), _balance(conn, user_id)
        ql, tl = plan["daily_query_limit"], plan["daily_token_limit"]
        if ql is not None and today["query_count"] >= ql:
            msg = f"You have used all {ql} queries for today. Upgrade your plan or try again tomorrow."
        elif tl is not None and int(today["total_tokens"]) >= tl and bal <= 0:
            msg = "You have used today's token allowance. Buy a token pack, upgrade, or try again tomorrow."
        if msg:
            notifications.notify(conn, user_id, "limit_reached", msg, once_per_day=True)
    if msg:  # raise after commit so the notification is kept
        raise QuotaExceeded(msg)


def record_usage(
    conn, user_id: int, response_id: int | None, prompt_tokens: int, output_tokens: int,
    total_tokens: int | None = None, queries: int = 0, errors: int = 0,
) -> None:
    """Write token_usage + daily summary + balance deduction. Use inside an open transaction."""
    total = total_tokens if total_tokens is not None else prompt_tokens + output_tokens
    plan = get_plan(conn, user_id)
    conn.execute(
        """INSERT INTO daily_usage_summary (user_id, usage_date, query_count, error_count, total_tokens)
           VALUES (%s, CURDATE(), 0, 0, 0) ON DUPLICATE KEY UPDATE user_id=user_id""",
        (user_id,),
    )
    before = conn.one(
        "SELECT total_tokens FROM daily_usage_summary WHERE user_id=%s AND usage_date=CURDATE() FOR UPDATE",
        (user_id,),
    )["total_tokens"]
    if total or prompt_tokens or output_tokens:
        conn.execute(
            """INSERT INTO token_usage (user_id, response_id, prompt_tokens, output_tokens, total_tokens)
               VALUES (%s,%s,%s,%s,%s)""",
            (user_id, response_id, prompt_tokens, output_tokens, total),
        )
    conn.execute(
        """UPDATE daily_usage_summary SET query_count=query_count+%s, error_count=error_count+%s,
                  total_tokens=total_tokens+%s WHERE user_id=%s AND usage_date=CURDATE()""",
        (queries, errors, total, user_id),
    )
    limit = plan["daily_token_limit"]
    if limit is not None and total:
        overflow = max(int(before) + total - max(limit, int(before)), 0)
        if overflow:
            conn.execute(
                "UPDATE token_balances SET balance=GREATEST(balance-%s, 0) WHERE user_id=%s", (overflow, user_id)
            )


def get_daily_history(user_id: int, days: int = 14) -> list[dict]:
    """One row per day (zeros filled in) for the Usage page chart: day, queries, errors, tokens."""
    with get_conn() as conn:
        rows = conn.all(
            """SELECT usage_date, query_count, error_count, total_tokens FROM daily_usage_summary
               WHERE user_id=%s AND usage_date >= CURDATE() - INTERVAL %s DAY""",
            (user_id, days - 1),
        )
        today = conn.one("SELECT CURDATE() AS d")["d"]
    by_day = {r["usage_date"]: r for r in rows}
    out = []
    for i in range(days - 1, -1, -1):
        d = today - datetime.timedelta(days=i)
        r = by_day.get(d)
        out.append({
            "day": d.isoformat(),
            "queries": r["query_count"] if r else 0,
            "errors": r["error_count"] if r else 0,
            "tokens": int(r["total_tokens"]) if r else 0,
        })
    return out


def add_token_balance(conn, user_id: int, tokens: int) -> None:
    conn.execute(
        """INSERT INTO token_balances (user_id, balance) VALUES (%s,%s)
           ON DUPLICATE KEY UPDATE balance=balance+VALUES(balance)""",
        (user_id, tokens),
    )
