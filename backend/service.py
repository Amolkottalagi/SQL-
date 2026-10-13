"""The Chat page pipeline: question -> prompt -> LLM -> validate -> run -> log."""
import csv
import io
import re
import time

from . import config, executor, schema_service, sql_guard, usage
from .db import get_conn
from .llm import (DEFAULT_TEMPLATE, LLMError, LLMProvider, build_retry_prompt, parse_response,
                  render_template)

INJECTION_RE = re.compile(
    r"ignore (all |any |the )?(previous|above|prior)|system prompt|you are now|disregard|"
    r"drop\s+table|delete\s+from|truncate\s+table|update\s+\w+\s+set",
    re.IGNORECASE,
)
FEW_SHOT_K = 3


# ------------------------------------------------------------------ helpers
def _active_template(conn) -> tuple[int | None, str, str]:
    row = conn.one("SELECT id, strategy, template_text FROM prompt_templates WHERE is_active=1 ORDER BY id DESC LIMIT 1")
    if row:
        return row["id"], row["strategy"], row["template_text"]
    return None, "zero_shot", DEFAULT_TEMPLATE


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", s.lower()))


def select_examples(conn, question: str, k: int = FEW_SHOT_K) -> list[tuple[str, str]]:
    """Pick the k active few-shot examples whose question shares the most words with this one."""
    rows = conn.all("SELECT question_text, sql_text FROM few_shot_examples WHERE is_active=1")
    qw = _words(question)

    def score(r):
        w = _words(r["question_text"])
        return len(qw & w) / (len(qw | w) or 1)

    rows.sort(key=score, reverse=True)
    return [(r["question_text"], r["sql_text"]) for r in rows[:k]]


def _blocked_reason(question: str, message: str) -> str:
    return "prompt_injection" if INJECTION_RE.search(question) else "non_select"


def _log_error(source: str, user_id: int, message: str, code=None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO error_logs (user_id, source, error_code, message) VALUES (%s,%s,%s,%s)",
            (user_id, source, str(code)[:50] if code is not None else None, message),
        )


def _own_session(conn, user_id: int, session_id: int) -> bool:
    return bool(conn.one("SELECT 1 FROM chat_sessions WHERE id=%s AND user_id=%s", (session_id, user_id)))


# ---------------------------------------------------------------------- ask
def ask(user_id: int, question: str, provider: LLMProvider, chat_session_id: int | None = None) -> dict:
    """Run one question end to end.

    Raises ValueError (bad input) or usage.QuotaExceeded (nothing is logged for those).
    Everything else is returned in the result dict, with `status` one of
    success | error | blocked | clarification.
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("Please enter a question.")
    if len(question) > config.MAX_QUESTION_LEN:
        raise ValueError(f"Question is too long (max {config.MAX_QUESTION_LEN} characters).")
    usage.check_quota(user_id)

    with get_conn() as conn:
        if chat_session_id is not None and not _own_session(conn, user_id, chat_session_id):
            raise ValueError("Chat not found.")
        if chat_session_id is None:
            chat_session_id = conn.insert(
                "INSERT INTO chat_sessions (user_id, title) VALUES (%s,%s)", (user_id, question[:150])
            )
        else:
            conn.execute("UPDATE chat_sessions SET updated_at=NOW() WHERE id=%s", (chat_session_id,))
        prompt_id = conn.insert(
            "INSERT INTO prompts (chat_session_id, user_id, question_text) VALUES (%s,%s,%s)",
            (chat_session_id, user_id, question),
        )
        template_id, strategy, template = _active_template(conn)
        examples = select_examples(conn, question) if strategy == "few_shot" else None

    result = {
        "prompt_id": prompt_id, "chat_session_id": chat_session_id, "response_id": None, "status": None,
        "sql": None, "explanation": None, "clarification": None, "columns": [], "rows": [],
        "row_count": 0, "truncated": False, "error": None, "attempts": 0, "total_tokens": 0,
    }

    try:
        schema = schema_service.get_schema()
    except executor.ExecutionError as e:
        _log_error("database", user_id, str(e), e.code)
        result.update(status="error", error="The data source is unavailable right now.")
        result["response_id"] = _save_response(user_id, prompt_id, template_id, 1, getattr(provider, "model", "?"),
                                               None, None, "error", str(e), None, None, None, queries=1, errors=1)
        return result

    base_prompt = render_template(template, schema, question, examples)
    prompt, last_sql, last_error = base_prompt, None, None

    for attempt in range(1, config.MAX_SQL_RETRIES + 2):
        result["attempts"] = attempt
        t0 = time.monotonic()
        status, sql, explanation, err, row_count, tokens = "error", None, None, None, None, None
        retry = False

        try:
            llm = provider.generate(prompt)
            tokens = llm
        except LLMError as e:
            _log_error("gemini", user_id, str(e), e.code)
            result.update(status="error", error="The AI service is unavailable right now. Please try again.")
            result["response_id"] = _save_response(
                user_id, prompt_id, template_id, attempt, getattr(provider, "model", "?"),
                None, None, "error", str(e), None, int((time.monotonic() - t0) * 1000), None,
                queries=1 if attempt == 1 else 0, errors=1)
            return result

        parsed = parse_response(llm.text)
        sql, explanation = parsed.sql, parsed.explanation

        if parsed.clarification:
            status = "clarification"
            result["clarification"] = parsed.clarification
            explanation = parsed.clarification  # stored in responses.explanation so History can show it again
        elif not sql:
            status, err = "error", "The model did not return a query."
        else:
            try:
                sql = sql_guard.validate(sql)
            except sql_guard.UnsafeSQL as e:
                status, err = "blocked", str(e)
                _log_blocked(user_id, prompt_id, question, sql, _blocked_reason(question, err))
            else:
                try:
                    out = executor.run_select(sql)
                    status, row_count = "success", out["row_count"]
                    result.update(columns=out["columns"], rows=out["rows"], truncated=out["truncated"])
                except executor.ExecutionError as e:
                    status, err = "error", str(e)
                    if e.code == 1146:
                        _log_blocked(user_id, prompt_id, question, sql, "unknown_table")
                    elif "interrupted" in str(e).lower():
                        _log_blocked(user_id, prompt_id, question, sql, "timeout")
                    else:
                        _log_error("database", user_id, str(e), e.code)
                    if attempt <= config.MAX_SQL_RETRIES:
                        retry = True
                        last_sql, last_error = sql, str(e)

        is_final = not retry
        result["response_id"] = _save_response(
            user_id, prompt_id, template_id, attempt, llm.model, sql, explanation, status, err, row_count,
            int((time.monotonic() - t0) * 1000), llm,
            queries=1 if attempt == 1 else 0, errors=1 if is_final and status in ("error", "blocked") else 0,
            explanation_text=explanation,
        )
        result["total_tokens"] += llm.total_tokens
        result.update(status=status, sql=sql, explanation=explanation, error=err)
        if not retry:
            break
        prompt = build_retry_prompt(base_prompt, last_sql, last_error)

    if result["status"] != "success":
        result.update(columns=[], rows=[], row_count=0, truncated=False)
    else:
        result["row_count"] = len(result["rows"])
    return result


def _log_blocked(user_id, prompt_id, question, sql, reason) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO blocked_queries (user_id, prompt_id, question_text, attempted_sql, reason)
               VALUES (%s,%s,%s,%s,%s)""",
            (user_id, prompt_id, question, sql, reason),
        )


def _save_response(user_id, prompt_id, template_id, attempt, model, sql, explanation, status, error,
                   row_count, latency_ms, llm, queries=0, errors=0, explanation_text=None) -> int:
    """Insert the response row and its token usage in one transaction."""
    with get_conn() as conn:
        rid = conn.insert(
            """INSERT INTO responses (prompt_id, prompt_template_id, attempt_no, model_name, generated_sql,
                                      explanation, status, error_message, row_count, latency_ms)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (prompt_id, template_id, attempt, model, sql, explanation_text or explanation, status, error,
             row_count, latency_ms),
        )
        usage.record_usage(
            conn, user_id, rid,
            llm.input_tokens if llm else 0, llm.output_tokens if llm else 0,
            llm.total_tokens if llm else 0, queries=queries, errors=errors,
        )
    return rid


# ----------------------------------------------------------------- feedback
def submit_feedback(user_id: int, response_id: int, rating: int, reason: str | None = None,
                    comment: str | None = None) -> None:
    if rating not in (-1, 1):
        raise ValueError("rating must be 1 or -1")
    if reason not in (None, "wrong_result", "wrong_sql", "slow", "confusing", "other"):
        raise ValueError("Unknown feedback reason.")
    with get_conn() as conn:
        owns = conn.one(
            """SELECT 1 FROM responses r JOIN prompts p ON p.id=r.prompt_id
               WHERE r.id=%s AND p.user_id=%s""",
            (response_id, user_id),
        )
        if owns:
            conn.execute(
                """INSERT INTO feedback (response_id, user_id, rating, reason, comment) VALUES (%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE rating=VALUES(rating), reason=VALUES(reason), comment=VALUES(comment)""",
                (response_id, user_id, rating, reason if rating == -1 else None, (comment or "")[:2000] or None),
            )
    if not owns:
        raise ValueError("Response not found.")


# ------------------------------------------------------------------- export
def export_csv(user_id: int, columns: list[str], rows: list[list]) -> str:
    """CSV text for st.download_button. Only plans with can_export_csv may export."""
    with get_conn() as conn:
        allowed = usage.get_plan(conn, user_id)["can_export_csv"]
    if not allowed:
        raise PermissionError("CSV export is available on the Pro plan.")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        # Neutralise spreadsheet formula injection (=, +, -, @ at the start of a text cell).
        w.writerow(["'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in r])
    return buf.getvalue()
