"""Builds the schema text given to the LLM and caches it in app_db.schema_cache."""
import hashlib

from . import config
from .db import Error, connect_target, get_conn
from .executor import ExecutionError


def _introspect() -> tuple[dict, list, dict]:
    """(tables {name: [column dicts]}, foreign keys, sample rows {table: [row dicts]})"""
    db = config.TARGET_DB_NAME
    try:
        conn = connect_target()
    except Error as e:
        raise ExecutionError(f"Could not connect to the target database: {e}")
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute(
            """SELECT TABLE_NAME AS t, COLUMN_NAME AS c, COLUMN_TYPE AS ty, IS_NULLABLE AS nl, COLUMN_KEY AS k
               FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME, ORDINAL_POSITION""",
            (db,),
        )
        cols = cur.fetchall()
        cur.execute(
            """SELECT TABLE_NAME AS t, COLUMN_NAME AS c, REFERENCED_TABLE_NAME AS rt, REFERENCED_COLUMN_NAME AS rc
               FROM information_schema.KEY_COLUMN_USAGE
               WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY TABLE_NAME, COLUMN_NAME""",
            (db,),
        )
        fks = cur.fetchall()
        tables: dict[str, list] = {}
        for r in cols:
            tables.setdefault(r["t"], []).append(r)
        samples = {}
        if config.SCHEMA_SAMPLE_ROWS > 0:
            # Only real tables listed above, names quoted: no user input reaches this query.
            for t in tables:
                cur.execute(f"SELECT * FROM `{t}` LIMIT {int(config.SCHEMA_SAMPLE_ROWS)}")
                samples[t] = cur.fetchall()
        cur.close()
    except Error as e:
        raise ExecutionError(f"Could not read the target database schema: {e}")
    finally:
        conn.close()
    return tables, fks, samples


def _render(tables, fks, samples) -> str:
    out = [f"Database: {config.TARGET_DB_NAME} (MySQL)"]
    for t, cols in tables.items():
        out.append(f"\nTable `{t}`:")
        for c in cols:
            flags = []
            if c["k"] == "PRI":
                flags.append("primary key")
            if c["nl"] == "NO" and c["k"] != "PRI":
                flags.append("not null")
            out.append(f"  - `{c['c']}` {c['ty']}" + (f" ({', '.join(flags)})" if flags else ""))
        for fk in (f for f in fks if f["t"] == t):
            out.append(f"  FK: `{fk['c']}` -> `{fk['rt']}`.`{fk['rc']}`")
        rows = samples.get(t) or []
        if rows:
            out.append("  Sample rows:")
            for r in rows:
                out.append("    " + ", ".join(f"{k}={_short(v)}" for k, v in r.items()))
    return "\n".join(out)


def _short(v) -> str:
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "..."


def _fingerprint(tables, fks) -> str:
    # Structure only (not sample rows) so data changes don't churn the cache.
    parts = [f"{t}.{c['c']}:{c['ty']}:{c['nl']}:{c['k']}" for t, cs in tables.items() for c in cs]
    parts += [f"{f['t']}.{f['c']}->{f['rt']}.{f['rc']}" for f in fks]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def refresh_schema() -> str:
    tables, fks, samples = _introspect()
    if not tables:
        raise ExecutionError(f"No tables found in target database '{config.TARGET_DB_NAME}'.")
    text, h = _render(tables, fks, samples), _fingerprint(tables, fks)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO schema_cache (database_name, schema_text, schema_hash) VALUES (%s,%s,%s)
               ON DUPLICATE KEY UPDATE schema_text=VALUES(schema_text), schema_hash=VALUES(schema_hash)""",
            (config.TARGET_DB_NAME, text, h),
        )
    return text


def get_schema() -> str:
    """Cached schema text; rebuilt automatically when the target DB structure changed."""
    tables, fks, samples = _introspect()
    h = _fingerprint(tables, fks)
    with get_conn() as conn:
        row = conn.one("SELECT schema_text, schema_hash FROM schema_cache WHERE database_name=%s", (config.TARGET_DB_NAME,))
    if row and row["schema_hash"] == h:
        return row["schema_text"]
    return refresh_schema()
