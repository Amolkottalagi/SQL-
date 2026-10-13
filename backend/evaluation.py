"""Evaluation / Research page: zero-shot vs few-shot accuracy on labelled questions.

A generated query is correct when it returns the same rows as the expected query (execution
accuracy). Column names are ignored; row order is ignored unless the expected SQL has ORDER BY;
numbers are compared to 4 decimal places. Only the first attempt is scored (no self-correction),
so the numbers measure the prompt strategy itself.

Few-shot examples come from `few_shot_examples`, never from `test_questions`, so the model is not
scored on questions it was just shown.
"""
import re

from . import admin, executor, schema_service, sql_guard
from .db import get_conn
from .llm import DEFAULT_TEMPLATE, LLMError, LLMProvider, parse_response, render_template
from .service import select_examples

STRATEGIES = ("zero_shot", "few_shot")
BIG = 10_000


def add_test_question(admin_id: int, question: str, expected_sql: str, category: str | None = None,
                      difficulty: str = "easy") -> int:
    admin.require_admin(admin_id)
    expected_sql = sql_guard.validate(expected_sql)
    executor.run_select(expected_sql, max_rows=1)  # the gold query itself must run
    with get_conn() as conn:
        return conn.insert(
            "INSERT INTO test_questions (question_text, expected_sql, category, difficulty) VALUES (%s,%s,%s,%s)",
            (question.strip(), expected_sql, category, difficulty),
        )


def list_test_questions(admin_id: int) -> list[dict]:
    admin.require_admin(admin_id)
    with get_conn() as conn:
        return conn.all("SELECT * FROM test_questions ORDER BY id")


def delete_test_question(admin_id: int, test_id: int) -> None:
    admin.require_admin(admin_id)
    with get_conn() as conn:
        if conn.one("SELECT 1 FROM experiment_results WHERE test_question_id=%s LIMIT 1", (test_id,)):
            raise ValueError("This question is used by past experiment runs and cannot be deleted.")
        conn.execute("DELETE FROM test_questions WHERE id=%s", (test_id,))


def _norm_cell(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v), 4)
    return "" if v is None else str(v)


def _rows(out: dict, ordered: bool):
    rows = [tuple(_norm_cell(v) for v in r) for r in out["rows"]]
    return rows if ordered else sorted(rows, key=lambda r: tuple(str(x) for x in r))


def same_result(generated: dict, expected: dict, expected_sql: str) -> bool:
    if len(generated["columns"]) != len(expected["columns"]):
        return False
    ordered = bool(re.search(r"\border\s+by\b", expected_sql, re.IGNORECASE))
    return _rows(generated, ordered) == _rows(expected, ordered)


def run_experiment(admin_id: int, provider: LLMProvider, strategy: str, template_id: int | None = None,
                   few_shot_k: int = 3) -> int:
    """Score every test question under `strategy`. Returns the experiment run id."""
    admin.require_admin(admin_id)
    if strategy not in STRATEGIES:
        raise ValueError("strategy must be 'zero_shot' or 'few_shot'")
    with get_conn() as conn:
        tests = conn.all("SELECT * FROM test_questions ORDER BY id")
        template = DEFAULT_TEMPLATE
        if template_id:
            row = conn.one("SELECT template_text FROM prompt_templates WHERE id=%s", (template_id,))
            if not row:
                raise ValueError("Template not found.")
            template = row["template_text"]
    if not tests:
        raise ValueError("Add some test questions first.")
    schema = schema_service.get_schema()

    results = []
    for t in tests:
        examples = None
        if strategy == "few_shot":
            with get_conn() as conn:
                examples = select_examples(conn, t["question_text"], few_shot_k)
        prompt = render_template(template, schema, t["question_text"], examples)
        gen_sql, failure = None, None
        try:
            parsed = parse_response(provider.generate(prompt).text)
            gen_sql = parsed.sql
            if parsed.clarification or not gen_sql:
                failure = "wrong_result"
            else:
                try:
                    gen_sql = sql_guard.validate(gen_sql)
                    got = executor.run_select(gen_sql, max_rows=BIG)
                    want = executor.run_select(t["expected_sql"], max_rows=BIG)
                    if not same_result(got, want, t["expected_sql"]):
                        failure = "wrong_result"
                except sql_guard.UnsafeSQL:
                    failure = "blocked"
                except executor.ExecutionError:
                    failure = "sql_error"
        except LLMError:
            failure = "llm_error"
        results.append((t["id"], gen_sql, failure))

    correct = sum(1 for _, _, f in results if f is None)
    with get_conn() as conn:
        run_id = conn.insert(
            """INSERT INTO experiment_runs (strategy, prompt_template_id, model_name, total, correct)
               VALUES (%s,%s,%s,%s,%s)""",
            (strategy, template_id, getattr(provider, "model", "?"), len(results), correct),
        )
        for tid, sql, f in results:
            conn.execute(
                """INSERT INTO experiment_results (run_id, test_question_id, generated_sql, is_correct, failure_type)
                   VALUES (%s,%s,%s,%s,%s)""",
                (run_id, tid, sql, int(f is None), f),
            )
        conn.execute(
            "INSERT INTO audit_logs (admin_id, action, target_type, target_id, details) VALUES (%s,'run_experiment','experiment_run',%s,%s)",
            (admin_id, run_id, f'{{"strategy": "{strategy}", "correct": {correct}, "total": {len(results)}}}'),
        )
    return run_id


def accuracy_table(admin_id: int) -> list[dict]:
    admin.require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            """SELECT id, strategy, model_name, total, correct,
                      ROUND(100 * correct / NULLIF(total, 0), 1) AS accuracy_pct, started_at
               FROM experiment_runs ORDER BY id DESC"""
        )


def failure_breakdown(admin_id: int, run_id: int) -> list[dict]:
    admin.require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            """SELECT failure_type, COUNT(*) AS n FROM experiment_results
               WHERE run_id=%s AND is_correct=0 GROUP BY failure_type ORDER BY n DESC""",
            (run_id,),
        )


def run_details(admin_id: int, run_id: int) -> list[dict]:
    admin.require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            """SELECT q.question_text, q.category, q.difficulty, q.expected_sql, r.generated_sql,
                      r.is_correct, r.failure_type
               FROM experiment_results r JOIN test_questions q ON q.id=r.test_question_id
               WHERE r.run_id=%s ORDER BY q.id""",
            (run_id,),
        )


def accuracy_by_difficulty(admin_id: int, run_id: int) -> list[dict]:
    admin.require_admin(admin_id)
    with get_conn() as conn:
        return conn.all(
            """SELECT q.difficulty, COUNT(*) AS total, SUM(r.is_correct) AS correct,
                      ROUND(100 * SUM(r.is_correct) / COUNT(*), 1) AS accuracy_pct
               FROM experiment_results r JOIN test_questions q ON q.id=r.test_question_id
               WHERE r.run_id=%s GROUP BY q.difficulty ORDER BY FIELD(q.difficulty,'easy','medium','hard')""",
            (run_id,),
        )
