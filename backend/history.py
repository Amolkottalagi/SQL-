"""History page and Saved Queries page."""
from . import executor, sql_guard
from .db import get_conn

_LATEST = "(SELECT MAX(id) FROM responses WHERE prompt_id = p.id)"


def list_sessions(user_id: int, limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        return conn.all(
            """SELECT s.id, s.title, s.created_at, s.updated_at,
                      (SELECT COUNT(*) FROM prompts p WHERE p.chat_session_id=s.id) AS questions
               FROM chat_sessions s WHERE s.user_id=%s ORDER BY s.updated_at DESC, s.id DESC LIMIT %s""",
            (user_id, limit),
        )


def list_history(user_id: int, limit: int = 50, offset: int = 0, search: str | None = None,
                 status: str | None = None, chat_session_id: int | None = None) -> list[dict]:
    """Past questions, newest first, each with the final response (sql, status, error, rows)."""
    where, args = ["p.user_id=%s"], [user_id]
    if search:
        where.append("p.question_text LIKE %s")
        args.append("%" + search.replace("%", r"\%").replace("_", r"\_") + "%")
    if status:
        where.append("r.status=%s")
        args.append(status)
    if chat_session_id:
        where.append("p.chat_session_id=%s")
        args.append(chat_session_id)
    with get_conn() as conn:
        return conn.all(
            f"""SELECT p.id AS prompt_id, p.chat_session_id, p.question_text, p.created_at,
                       r.id AS response_id, r.generated_sql, r.explanation, r.status,
                       r.error_message, r.row_count, r.attempt_no,
                       (SELECT rating FROM feedback f WHERE f.response_id=r.id AND f.user_id=p.user_id) AS my_rating,
                       EXISTS(SELECT 1 FROM saved_queries sq WHERE sq.user_id=p.user_id
                              AND sq.question_text=p.question_text) AS is_saved
                FROM prompts p LEFT JOIN responses r ON r.id = {_LATEST}
                WHERE {' AND '.join(where)}
                ORDER BY p.id DESC LIMIT %s OFFSET %s""",
            (*args, limit, offset),
        )


def get_session_messages(user_id: int, chat_session_id: int) -> list[dict]:
    return list_history_for_session(user_id, chat_session_id)


def list_history_for_session(user_id: int, chat_session_id: int) -> list[dict]:
    rows = list_history(user_id, limit=500, chat_session_id=chat_session_id)
    return list(reversed(rows))  # oldest first, like a conversation


def delete_prompt(user_id: int, prompt_id: int) -> None:
    """Hard delete. Token usage rows stay (their response_id becomes NULL) so billing stats remain correct."""
    with get_conn() as conn:
        n = conn.execute("DELETE FROM prompts WHERE id=%s AND user_id=%s", (prompt_id, user_id))
    if not n:
        raise ValueError("Question not found.")


def delete_session(user_id: int, chat_session_id: int) -> None:
    with get_conn() as conn:
        n = conn.execute("DELETE FROM chat_sessions WHERE id=%s AND user_id=%s", (chat_session_id, user_id))
    if not n:
        raise ValueError("Chat not found.")


def clear_history(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute("DELETE FROM chat_sessions WHERE user_id=%s", (user_id,))


def _run(sql: str) -> dict:
    sql = sql_guard.validate(sql)  # re-validate: rules may have become stricter since it was stored
    out = executor.run_select(sql)
    out["sql"] = sql
    return out


def rerun(user_id: int, prompt_id: int) -> dict:
    """Re-execute the stored SQL of a past question. No LLM call, so it costs no tokens or quota."""
    with get_conn() as conn:
        row = conn.one(
            """SELECT r.generated_sql FROM responses r JOIN prompts p ON p.id=r.prompt_id
               WHERE p.id=%s AND p.user_id=%s AND r.status='success' ORDER BY r.id DESC LIMIT 1""",
            (prompt_id, user_id),
        )
    if not row:
        raise ValueError("No successful query stored for this question.")
    return _run(row["generated_sql"])


# ------------------------------------------------------------ saved queries
def save_query(user_id: int, title: str, question_text: str, sql_text: str | None = None) -> int:
    title, question_text = title.strip(), question_text.strip()
    if not title or not question_text:
        raise ValueError("Title and question are required.")
    if sql_text:
        sql_text = sql_guard.validate(sql_text)
    with get_conn() as conn:
        return conn.insert(
            "INSERT INTO saved_queries (user_id, title, question_text, sql_text) VALUES (%s,%s,%s,%s)",
            (user_id, title[:150], question_text, sql_text),
        )


def save_from_prompt(user_id: int, prompt_id: int, title: str | None = None) -> int:
    with get_conn() as conn:
        row = conn.one(
            """SELECT p.question_text, (SELECT r.generated_sql FROM responses r WHERE r.prompt_id=p.id
                                        AND r.status='success' ORDER BY r.id DESC LIMIT 1) AS sql_text
               FROM prompts p WHERE p.id=%s AND p.user_id=%s""",
            (prompt_id, user_id),
        )
    if not row:
        raise ValueError("Question not found.")
    return save_query(user_id, title or row["question_text"][:150], row["question_text"], row["sql_text"])


def list_saved(user_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.all(
            "SELECT id, title, question_text, sql_text, created_at FROM saved_queries WHERE user_id=%s ORDER BY id DESC",
            (user_id,),
        )


def delete_saved(user_id: int, saved_id: int) -> None:
    with get_conn() as conn:
        n = conn.execute("DELETE FROM saved_queries WHERE id=%s AND user_id=%s", (saved_id, user_id))
    if not n:
        raise ValueError("Saved query not found.")


def rerun_saved(user_id: int, saved_id: int) -> dict:
    with get_conn() as conn:
        row = conn.one("SELECT sql_text FROM saved_queries WHERE id=%s AND user_id=%s", (saved_id, user_id))
    if not row:
        raise ValueError("Saved query not found.")
    if not row["sql_text"]:
        raise ValueError("This saved query has no SQL. Ask the question again from the Chat page.")
    return _run(row["sql_text"])


def sql_for_response(user_id: int, response_id: int) -> str:
    """The SQL of one of the user's own successful responses (used for CSV export)."""
    with get_conn() as conn:
        row = conn.one(
            """SELECT r.generated_sql FROM responses r JOIN prompts p ON p.id=r.prompt_id
               WHERE r.id=%s AND p.user_id=%s AND r.status='success'""",
            (response_id, user_id),
        )
    if not row:
        raise ValueError("Result not found.")
    return row["generated_sql"]


def rerun_response(user_id: int, response_id: int) -> dict:
    return _run(sql_for_response(user_id, response_id))
