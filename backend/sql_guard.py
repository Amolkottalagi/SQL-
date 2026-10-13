"""Static checks on LLM-generated SQL (MySQL dialect) before it reaches the target database.

Defence in depth: the target connection also uses a SELECT-only MySQL account, a READ ONLY
session and an execution time limit (see executor.py), so a bypass here still cannot change data
or read app_db.
"""
import re

from . import config

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|rename|grant|revoke|call|lock|unlock|handler|"
    r"load|outfile|dumpfile|into|prepare|execute|deallocate|sleep|benchmark|get_lock|release_lock|"
    r"load_file|shutdown|kill)\b|\breplace\s+into\b|\bfor\s+(update|share)\b",
    re.IGNORECASE,
)
SYSTEM_SCHEMAS = re.compile(r"\b(information_schema|performance_schema|mysql|sys|app_db)\s*\.", re.IGNORECASE)


class UnsafeSQL(Exception):
    pass


def strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    m = re.match(r"^```(?:sql)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    return (m.group(1) if m else text).strip()


def _mask_literals(sql: str) -> str:
    # MySQL treats both '...' and "..." as strings and allows backslash escapes.
    sql = re.sub(r"'(?:[^'\\]|\\.|'')*'", "''", sql)
    return re.sub(r'"(?:[^"\\]|\\.|"")*"', "''", sql)


def validate(sql: str) -> str:
    """Return the cleaned SQL (no trailing semicolon) or raise UnsafeSQL."""
    sql = strip_code_fences(sql)
    if not sql:
        raise UnsafeSQL("The model returned no SQL.")
    if "/*!" in sql or "/*+" in sql:
        # MySQL executes the body of /*! ... */ comments, so they can hide statements.
        raise UnsafeSQL("Executable comments are not allowed.")

    body = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    body = re.sub(r"(--[ \t]|#)[^\n]*", " ", body)
    body = _mask_literals(body).strip().rstrip(";").strip()

    if ";" in body:
        raise UnsafeSQL("Only a single statement is allowed.")
    if not re.match(r"^\(*\s*(select|with)\b", body, re.IGNORECASE):
        raise UnsafeSQL("Only SELECT queries are allowed.")

    blanked = re.sub(r"`[^`]*`", "`x`", body)  # identifiers named `update` etc. are fine
    hit = FORBIDDEN.search(blanked)
    if hit:
        raise UnsafeSQL(f"Forbidden keyword: {hit.group(0).strip().upper()}.")
    plain = body.replace("`", "")
    if SYSTEM_SCHEMAS.search(plain) or re.search(rf"\b{re.escape(config.DB_NAME)}\s*\.", plain, re.IGNORECASE):
        # The configured app database name is blocked too, in case it isn't called app_db.
        raise UnsafeSQL("Access to system or application schemas is not allowed.")
    return sql.rstrip(";").strip()
