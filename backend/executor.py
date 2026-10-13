"""Runs validated SELECTs on the target database."""
import datetime
import decimal
import time

from . import config
from .db import Error, connect_target


class ExecutionError(Exception):
    """Message is safe to show to the user (and to feed back to the LLM for self-correction)."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code  # MySQL error number, e.g. 1146 = unknown table


def _wrap(e: Error) -> ExecutionError:
    return ExecutionError(getattr(e, "msg", None) or str(e), getattr(e, "errno", None))


def _clean(v):
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, datetime.timedelta):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", errors="replace")
    return v


def _harden_session(cur) -> None:
    cur.execute("SET SESSION TRANSACTION READ ONLY")
    try:  # MySQL (milliseconds)
        cur.execute("SET SESSION MAX_EXECUTION_TIME=%s", (config.QUERY_TIMEOUT_SEC * 1000,))
    except Error:  # MariaDB (seconds)
        cur.execute("SET SESSION max_statement_time=%s", (config.QUERY_TIMEOUT_SEC,))


def run_select(sql: str, max_rows: int | None = None) -> dict:
    """Returns {columns, rows, row_count, truncated, latency_ms}; raises ExecutionError."""
    max_rows = max_rows or config.MAX_ROWS
    try:
        conn = connect_target()
    except Error as e:
        raise ExecutionError(f"Could not connect to the target database: {e}")
    start = time.monotonic()
    drained = False
    try:
        cur = conn.cursor()  # unbuffered: we never load more than max_rows+1 rows
        try:
            _harden_session(cur)
            # Errors can surface on execute() (syntax, unknown table) or while fetching (timeout).
            cur.execute(sql)
            columns = list(cur.column_names) if cur.description else []
            fetched = cur.fetchmany(max_rows + 1)
        except Error as e:
            raise _wrap(e)
        truncated = len(fetched) > max_rows
        rows = [[_clean(v) for v in r] for r in fetched[:max_rows]]
        if not truncated:
            try:
                cur.fetchall()  # make sure the result set is fully consumed
                drained = True
            except Error:
                pass
    finally:
        # A partly-read result can't be closed politely, so drop the socket instead of draining it.
        try:
            if drained:
                conn.close()
            else:
                conn.shutdown()
        except Exception:
            pass
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "latency_ms": int((time.monotonic() - start) * 1000),
    }
