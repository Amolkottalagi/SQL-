"""Admin Dashboard backend. Every function re-checks the admin role itself, so a missed
page guard can never expose data. Every change is written to audit_logs."""
from .db import get_conn, to_json


def require_admin(user_id: int) -> None:
    with get_conn() as conn:
        row = conn.one("SELECT role FROM users WHERE id=%s AND is_active=1", (user_id,))
    if not row or row["role"] != "admin":
        raise PermissionError("Admin access required.")


def _audit(conn, admin_id: int, action: str, target_type: str | None = None, target_id: int | None = None,
           details: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_logs (admin_id, action, target_type, target_id, details) VALUES (%s,%s,%s,%s,%s)",
        (admin_id, action, target_type, target_id, to_json(details) if details else None),
    )


# ---------------------------------------------------------------- dashboard
def overview(admin_id: int) -> dict:
    require_admin(admin_id)
    with get_conn() as conn:
        users = conn.one("SELECT COUNT(*) AS n, SUM(is_active) AS active FROM users")
        tok = conn.one("SELECT COALESCE(SUM(prompt_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o, "
                       "COALESCE(SUM(total_tokens),0) AS t FROM token_usage")
        q = conn.one("SELECT COUNT(*) AS n FROM prompts")
        r = conn.one("SELECT COUNT(*) AS n, COALESCE(SUM(status IN ('error','blocked')),0) AS bad FROM responses "
                     "WHERE id IN (SELECT MAX(id) FROM responses GROUP BY prompt_id)")
        fb = conn.one("SELECT COALESCE(SUM(rating=1),0) AS up, COALESCE(SUM(rating=-1),0) AS down FROM feedback")
        rev = conn.one("SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE status='success'")
        today = conn.one("SELECT COALESCE(SUM(query_count),0) AS q, COALESCE(SUM(total_tokens),0) AS t "
                         "FROM daily_usage_summary WHERE usage_date=CURDATE()")
    final = r["n"] or 0
    return {
        "total_users": users["n"], "active_users": int(users["active"] or 0),
        "total_questions": q["n"],
        "input_tokens": int(tok["i"]), "output_tokens": int(tok["o"]), "total_tokens": int(tok["t"]),
        "error_rate": (int(r["bad"]) / final) if final else 0.0,  # share of questions whose final answer failed
        "thumbs_up": int(fb["up"]), "thumbs_down": int(fb["down"]),
        "revenue": rev["total"], "queries_today": int(today["q"]), "tokens_today": int(today["t"]),
    }


def most_active_users(admin_id: int, limit: int = 10) -> list[dict]:
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            """SELECT u.id, u.username, pl.name AS plan,
                      (SELECT COUNT(*) FROM prompts p WHERE p.user_id=u.id) AS questions,
                      (SELECT COALESCE(SUM(total_tokens),0) FROM token_usage t WHERE t.user_id=u.id) AS tokens
               FROM users u JOIN plans pl ON pl.id=u.plan_id
               ORDER BY questions DESC, tokens DESC LIMIT %s""",
            (limit,),
        )


def status_breakdown(admin_id: int) -> list[dict]:
    """Final status of every question: success / error / blocked / clarification."""
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            """SELECT status, COUNT(*) AS n FROM responses
               WHERE id IN (SELECT MAX(id) FROM responses GROUP BY prompt_id) GROUP BY status ORDER BY n DESC"""
        )


def daily_totals(admin_id: int, days: int = 14) -> list[dict]:
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            """SELECT usage_date AS day, SUM(query_count) AS queries, SUM(error_count) AS errors,
                      SUM(total_tokens) AS tokens, COUNT(DISTINCT user_id) AS active_users
               FROM daily_usage_summary WHERE usage_date >= CURDATE() - INTERVAL %s DAY
               GROUP BY usage_date ORDER BY usage_date""",
            (days - 1,),
        )


def feedback_reasons(admin_id: int) -> list[dict]:
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all("SELECT COALESCE(reason,'(none)') AS reason, COUNT(*) AS n FROM feedback "
                        "WHERE rating=-1 GROUP BY reason ORDER BY n DESC")


def recent_errors(admin_id: int, limit: int = 50, source: str | None = None) -> list[dict]:
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            f"""SELECT e.id, e.created_at, u.username, e.source, e.error_code, e.message
                FROM error_logs e LEFT JOIN users u ON u.id=e.user_id
                {'WHERE e.source=%s' if source else ''} ORDER BY e.id DESC LIMIT %s""",
            ((source, limit) if source else (limit,)),
        )


def recent_blocked(admin_id: int, limit: int = 50) -> list[dict]:
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            """SELECT b.id, b.created_at, u.username, b.reason, b.question_text, b.attempted_sql
               FROM blocked_queries b LEFT JOIN users u ON u.id=b.user_id ORDER BY b.id DESC LIMIT %s""",
            (limit,),
        )


def recent_audit_logs(admin_id: int, limit: int = 100) -> list[dict]:
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            """SELECT a.id, a.created_at, u.username AS admin, a.action, a.target_type, a.target_id, a.details
               FROM audit_logs a LEFT JOIN users u ON u.id=a.admin_id ORDER BY a.id DESC LIMIT %s""",
            (limit,),
        )


def recent_payments(admin_id: int, limit: int = 50) -> list[dict]:
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            """SELECT p.id, p.created_at, u.username, pl.name AS plan, p.tokens_purchased, p.amount, p.currency, p.status
               FROM payments p JOIN users u ON u.id=p.user_id LEFT JOIN plans pl ON pl.id=p.plan_id
               ORDER BY p.id DESC LIMIT %s""",
            (limit,),
        )


# -------------------------------------------------------------- user management
def list_users(admin_id: int, search: str | None = None, limit: int = 100) -> list[dict]:
    require_admin(admin_id)
    where, args = "", []
    if search:
        where = "WHERE u.username LIKE %s OR u.email LIKE %s"
        like = "%" + search.replace("%", r"\%").replace("_", r"\_") + "%"
        args = [like, like]
    with get_conn() as conn:
        return conn.all(
            f"""SELECT u.id, u.username, u.email, u.role, u.is_active, u.created_at, u.last_login_at,
                       pl.name AS plan
                FROM users u JOIN plans pl ON pl.id=u.plan_id {where} ORDER BY u.id DESC LIMIT %s""",
            (*args, limit),
        )


def set_user_plan(admin_id: int, user_id: int, plan_name: str) -> None:
    require_admin(admin_id)
    with get_conn() as conn:
        plan = conn.one("SELECT id FROM plans WHERE name=%s AND is_active=1", (plan_name,))
        if not plan:
            raise ValueError(f"Unknown plan: {plan_name}")
        if not conn.execute("UPDATE users SET plan_id=%s WHERE id=%s", (plan["id"], user_id)):
            if not conn.one("SELECT 1 FROM users WHERE id=%s", (user_id,)):
                raise ValueError("User not found.")
        _audit(conn, admin_id, "change_plan", "user", user_id, {"plan": plan_name})
        from . import notifications
        notifications.notify(conn, user_id, "plan_changed", f"Your plan was changed to {plan_name}.")


def set_user_active(admin_id: int, user_id: int, active: bool) -> None:
    require_admin(admin_id)
    if admin_id == user_id and not active:
        raise ValueError("You cannot deactivate your own account.")
    with get_conn() as conn:
        if not conn.one("SELECT 1 FROM users WHERE id=%s", (user_id,)):
            raise ValueError("User not found.")
        conn.execute("UPDATE users SET is_active=%s WHERE id=%s", (int(active), user_id))
        if not active:
            conn.execute("UPDATE auth_sessions SET revoked_at=NOW() WHERE user_id=%s AND revoked_at IS NULL", (user_id,))
        _audit(conn, admin_id, "activate_user" if active else "deactivate_user", "user", user_id)


def set_user_role(admin_id: int, user_id: int, role: str) -> None:
    require_admin(admin_id)
    if role not in ("user", "admin"):
        raise ValueError("Role must be 'user' or 'admin'.")
    if admin_id == user_id and role != "admin":
        raise ValueError("You cannot remove your own admin role.")
    with get_conn() as conn:
        if not conn.execute("UPDATE users SET role=%s WHERE id=%s", (role, user_id)):
            if not conn.one("SELECT 1 FROM users WHERE id=%s", (user_id,)):
                raise ValueError("User not found.")
        _audit(conn, admin_id, "change_role", "user", user_id, {"role": role})


def grant_tokens(admin_id: int, user_id: int, tokens: int) -> None:
    require_admin(admin_id)
    if tokens <= 0:
        raise ValueError("Tokens must be positive.")
    from . import usage
    with get_conn() as conn:
        if not conn.one("SELECT 1 FROM users WHERE id=%s", (user_id,)):
            raise ValueError("User not found.")
        usage.add_token_balance(conn, user_id, tokens)
        _audit(conn, admin_id, "grant_tokens", "user", user_id, {"tokens": tokens})


def refund_payment(admin_id: int, payment_id: int) -> None:
    """Marks a successful payment refunded and takes back what it granted (record-keeping only:
    refund the money in the gateway dashboard)."""
    require_admin(admin_id)
    with get_conn() as conn:
        pay = conn.one("SELECT * FROM payments WHERE id=%s FOR UPDATE", (payment_id,))
        if not pay or pay["status"] != "success":
            raise ValueError("Only successful payments can be refunded.")
        conn.execute("UPDATE payments SET status='refunded' WHERE id=%s", (payment_id,))
        if pay["tokens_purchased"]:
            conn.execute("UPDATE token_balances SET balance=GREATEST(balance-%s,0) WHERE user_id=%s",
                         (pay["tokens_purchased"], pay["user_id"]))
        if pay["plan_id"]:
            free = conn.one("SELECT id FROM plans WHERE name='free'")
            conn.execute("UPDATE users SET plan_id=%s WHERE id=%s AND plan_id=%s",
                         (free["id"], pay["user_id"], pay["plan_id"]))
        _audit(conn, admin_id, "refund", "payment", payment_id)


# ------------------------------------------------- prompt config (research)
def list_templates(admin_id: int) -> list[dict]:
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all("SELECT id, name, version, strategy, is_active, created_at, template_text "
                        "FROM prompt_templates ORDER BY name, version DESC")


def create_template(admin_id: int, name: str, strategy: str, template_text: str) -> int:
    require_admin(admin_id)
    if strategy not in ("zero_shot", "few_shot", "chain_of_thought"):
        raise ValueError("Unknown strategy.")
    if "{question}" not in template_text or "{schema}" not in template_text:
        raise ValueError("Template must contain {schema} and {question} placeholders.")
    with get_conn() as conn:
        ver = conn.one("SELECT COALESCE(MAX(version),0)+1 AS v FROM prompt_templates WHERE name=%s", (name,))["v"]
        tid = conn.insert("INSERT INTO prompt_templates (name, version, strategy, template_text) VALUES (%s,%s,%s,%s)",
                          (name, ver, strategy, template_text))
        _audit(conn, admin_id, "create_template", "prompt_template", tid, {"name": name, "version": ver})
    return tid


def activate_template(admin_id: int, template_id: int) -> None:
    """Exactly one template is active at a time."""
    require_admin(admin_id)
    with get_conn() as conn:
        if not conn.one("SELECT 1 FROM prompt_templates WHERE id=%s", (template_id,)):
            raise ValueError("Template not found.")
        conn.execute("UPDATE prompt_templates SET is_active=0")
        conn.execute("UPDATE prompt_templates SET is_active=1 WHERE id=%s", (template_id,))
        _audit(conn, admin_id, "activate_template", "prompt_template", template_id)


def add_few_shot_example(admin_id: int, question: str, sql_text: str, difficulty: str = "easy") -> int:
    require_admin(admin_id)
    from . import sql_guard
    sql_text = sql_guard.validate(sql_text)
    with get_conn() as conn:
        eid = conn.insert("INSERT INTO few_shot_examples (question_text, sql_text, difficulty) VALUES (%s,%s,%s)",
                          (question.strip(), sql_text, difficulty))
        _audit(conn, admin_id, "add_few_shot_example", "few_shot_example", eid)
    return eid


def set_few_shot_active(admin_id: int, example_id: int, active: bool) -> None:
    require_admin(admin_id)
    with get_conn() as conn:
        conn.execute("UPDATE few_shot_examples SET is_active=%s WHERE id=%s", (int(active), example_id))
        _audit(conn, admin_id, "toggle_few_shot_example", "few_shot_example", example_id, {"active": active})


def list_few_shot_examples(admin_id: int) -> list[dict]:
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all("SELECT * FROM few_shot_examples ORDER BY id")


def list_plans(admin_id: int) -> list[dict]:
    """All active plans (including the internal 'admin' plan) for the user-management dropdown."""
    require_admin(admin_id)
    with get_conn() as conn:
        return conn.all("SELECT id, name FROM plans WHERE is_active=1 ORDER BY price_monthly, id")
