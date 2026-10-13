import mysql.connector
import pytest

from backend import auth, config, executor, schema_service, sql_guard
from backend.db import get_conn


# ------------------------------------------------------------------- auth
def test_signup_login_session_logout(alice):
    res = auth.login("alice", "password123", ip="1.2.3.4", user_agent="pytest")
    assert res["user"]["username"] == "alice" and res["user"]["plan"] == "free" and res["user"]["role"] == "user"
    assert "password_hash" not in res["user"]
    assert auth.validate_session(res["token"])["id"] == alice
    auth.logout(res["token"])
    assert auth.validate_session(res["token"]) is None
    assert auth.validate_session(None) is None and auth.validate_session("garbage") is None
    with get_conn() as c:  # token is stored hashed, never raw
        assert c.one("SELECT COUNT(*) AS n FROM auth_sessions WHERE session_token_hash=%s", (res["token"],))["n"] == 0
        assert c.one("SELECT last_login_at FROM users WHERE id=%s", (alice,))["last_login_at"] is not None


def test_login_with_email_and_case_handling(alice):
    assert auth.login("ALICE@example.com", "password123")["user"]["id"] == alice


def test_signup_validation(alice):
    for args in [
        ("bob", "bob@example.com", "password123", False),   # no consent
        ("bob", "bob@example.com", "short", True),
        ("b!", "bob@example.com", "password123", True),
        ("bob", "not-an-email", "password123", True),
        ("bob", "bob@example.com", "x" * 80, True),         # >72 bytes
        ("alice", "other@example.com", "password123", True),  # duplicate username
        ("bob", "ALICE@example.com", "password123", True),    # duplicate email (case-insensitive)
    ]:
        with pytest.raises(auth.AuthError):
            auth.signup(*args)


def test_signup_creates_settings_and_balance(alice):
    assert auth.get_settings(alice)["default_chart_type"] == "bar"
    with get_conn() as c:
        assert c.one("SELECT balance FROM token_balances WHERE user_id=%s", (alice,))["balance"] == 0


def test_lockout_persists_failed_attempts(alice):
    for _ in range(config.MAX_FAILED_LOGINS):
        with pytest.raises(auth.AuthError, match="Invalid"):
            auth.login("alice", "wrong")
    with pytest.raises(auth.AuthError, match="Too many"):
        auth.login("alice", "password123")  # even the right password is refused while locked
    with get_conn() as c:
        assert c.one("SELECT COUNT(*) AS n FROM login_attempts WHERE success=0")["n"] == config.MAX_FAILED_LOGINS


def test_success_resets_failure_count(alice):
    for _ in range(4):
        with pytest.raises(auth.AuthError):
            auth.login("alice", "wrong")
    auth.login("alice", "password123")
    for _ in range(4):
        with pytest.raises(auth.AuthError):
            auth.login("alice", "wrong")
    auth.login("alice", "password123")  # still allowed


def test_unknown_user_gets_same_message(alice):
    with pytest.raises(auth.AuthError) as a:
        auth.login("nobody", "whatever1")
    with pytest.raises(auth.AuthError) as b:
        auth.login("alice", "wrong")
    assert str(a.value) == str(b.value) == "Invalid username or password."


def test_deactivated_user_cannot_login_or_use_session(alice):
    res = auth.login("alice", "password123")
    with get_conn() as c:
        c.execute("UPDATE users SET is_active=0 WHERE id=%s", (alice,))
    assert auth.validate_session(res["token"]) is None
    with pytest.raises(auth.AuthError):
        auth.login("alice", "password123")


def test_session_expiry(alice):
    tok = auth.login("alice", "password123")["token"]
    with get_conn() as c:
        c.execute("UPDATE auth_sessions SET expires_at = NOW() - INTERVAL 1 MINUTE")
    assert auth.validate_session(tok) is None


def test_change_password_revokes_other_sessions(alice):
    keep = auth.login("alice", "password123")["token"]
    other = auth.login("alice", "password123")["token"]
    with pytest.raises(auth.AuthError):
        auth.change_password(alice, "wrong", "newpassword1")
    auth.change_password(alice, "password123", "newpassword1", keep_token=keep)
    assert auth.validate_session(keep) and auth.validate_session(other) is None
    auth.login("alice", "newpassword1")


def test_password_reset_flow(alice):
    assert auth.request_password_reset("nobody@example.com") is None
    tok = auth.request_password_reset("Alice@Example.com")
    old_session = auth.login("alice", "password123")["token"]
    auth.reset_password(tok, "brandnewpass1")
    assert auth.validate_session(old_session) is None  # sessions revoked
    auth.login("alice", "brandnewpass1")
    with pytest.raises(auth.AuthError):
        auth.login("alice", "password123")
    with pytest.raises(auth.AuthError):
        auth.reset_password(tok, "anotherpass12")  # token is single-use
    with pytest.raises(auth.AuthError):
        auth.reset_password("not-a-token", "anotherpass12")


def test_reset_token_expires(alice):
    tok = auth.request_password_reset("alice@example.com")
    with get_conn() as c:
        c.execute("UPDATE password_reset_tokens SET expires_at = NOW() - INTERVAL 1 MINUTE")
    with pytest.raises(auth.AuthError):
        auth.reset_password(tok, "brandnewpass1")


def test_delete_account_without_payments_removes_user_keeps_anonymous_history(alice, provider):
    from backend import service
    service.ask(alice, "anything", provider)
    with pytest.raises(auth.AuthError):
        auth.delete_account(alice, "bad")
    assert auth.delete_account(alice, "password123") == "deleted"
    assert auth.get_user(alice) is None
    with get_conn() as c:  # research data kept, anonymised
        assert c.one("SELECT COUNT(*) AS n FROM prompts WHERE user_id IS NULL")["n"] == 1
        assert c.one("SELECT COUNT(*) AS n FROM token_usage WHERE user_id IS NULL")["n"] >= 1


def test_settings_validation(alice):
    auth.update_settings(alice, "line", "dark", 25)
    assert auth.get_settings(alice) == {"default_chart_type": "line", "theme": "dark", "rows_per_page": 25}
    for bad in [("donut", "dark", 25), ("bar", "neon", 25), ("bar", "dark", 1)]:
        with pytest.raises(auth.AuthError):
            auth.update_settings(alice, *bad)


# -------------------------------------------------------------- sql guard
@pytest.mark.parametrize("sql", [
    "DROP TABLE customers",
    "SELECT 1; DROP TABLE customers",
    "DELETE FROM orders",
    "WITH x AS (SELECT 1) DELETE FROM orders",
    "UPDATE customers SET name='x'",
    "INSERT INTO customers VALUES (9,'x','y','2025-01-01')",
    "REPLACE INTO customers VALUES (1,'x','y','2025-01-01')",
    "SELECT * FROM customers INTO OUTFILE '/tmp/x'",
    "SELECT SLEEP(10)",
    "SELECT BENCHMARK(1000000, MD5('a'))",
    "SELECT /*!50000 1; DROP TABLE orders */ 1",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM app_db.users",
    "SELECT * FROM `app_db`.`users`",
    "SELECT * FROM mysql.user",
    "SELECT * FROM customers FOR UPDATE",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SHOW TABLES",
    "CALL some_proc()",
    "SELECT 1 # comment\n; DROP TABLE x",
    "",
])
def test_guard_blocks(sql):
    with pytest.raises(sql_guard.UnsafeSQL):
        sql_guard.validate(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers",
    "SELECT * FROM customers;",
    "```sql\nSELECT name FROM customers\n```",
    "WITH t AS (SELECT city FROM customers) SELECT * FROM t",
    "SELECT * FROM customers WHERE name = 'please update me; drop table'",
    'SELECT * FROM customers WHERE name = "delete"',
    "SELECT REPLACE(name, 'a', 'b') FROM customers",
    "SELECT updated_at_not_a_column FROM t",              # 'update' only as part of an identifier
    "SELECT `update` FROM `t`",
    "(SELECT 1) UNION (SELECT 2)",
])
def test_guard_allows(sql):
    assert sql_guard.validate(sql)


def test_guard_strips_fences_and_semicolon():
    assert sql_guard.validate("```sql\nSELECT 1;\n```") == "SELECT 1"


# --------------------------------------------------------------- executor
def test_executor_runs_select_and_converts_types():
    out = executor.run_select("SELECT p.name, p.price, c.signup_date FROM products p JOIN customers c ON c.id=p.id ORDER BY p.id LIMIT 2")
    assert out["columns"] == ["name", "price", "signup_date"]
    assert out["rows"][0] == ["Laptop", 55000, "2025-01-10"] and out["row_count"] == 2 and not out["truncated"]


def test_executor_truncates():
    out = executor.run_select("SELECT * FROM orders", max_rows=3)
    assert out["row_count"] == 3 and out["truncated"]


def test_executor_cannot_write_even_if_guard_is_bypassed():
    for stmt in ["DELETE FROM orders", "DROP TABLE orders", "UPDATE products SET price=1",
                 "INSERT INTO customers VALUES (99,'x','y','2025-01-01')"]:
        with pytest.raises(executor.ExecutionError):
            executor.run_select(stmt)
    assert executor.run_select("SELECT COUNT(*) FROM orders")["rows"] == [[9]]


def test_executor_cannot_read_app_db():
    with pytest.raises(executor.ExecutionError):
        executor.run_select("SELECT * FROM app_db.users")


def test_executor_timeout(monkeypatch):
    monkeypatch.setattr(config, "QUERY_TIMEOUT_SEC", 1)
    with pytest.raises(executor.ExecutionError, match="(?i)interrupted"):
        executor.run_select("SELECT SLEEP(4)")


def test_executor_reports_sql_errors_with_code():
    with pytest.raises(executor.ExecutionError) as e:
        executor.run_select("SELECT * FROM no_such_table")
    assert e.value.code == 1146


# ----------------------------------------------------------- schema cache
def test_schema_text_and_cache():
    text = schema_service.get_schema()
    assert "Table `orders`" in text and "FK: `customer_id` -> `customers`.`id`" in text
    assert "app_db" not in text and "password" not in text
    with get_conn() as c:
        row = c.one("SELECT schema_hash FROM schema_cache WHERE database_name=%s", (config.TARGET_DB_NAME,))
        assert len(row["schema_hash"]) == 64
        c.execute("UPDATE schema_cache SET schema_text='stale', schema_hash='x'")
    assert schema_service.get_schema() == text  # rebuilt because the hash no longer matched


def test_schema_sample_rows_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "SCHEMA_SAMPLE_ROWS", 0)
    assert "Sample rows" not in schema_service.refresh_schema()


def test_reader_account_is_denied_on_app_db():
    conn = mysql.connector.connect(host=config.TARGET_DB_HOST, port=config.TARGET_DB_PORT,
                                   user=config.TARGET_DB_USER, password=config.TARGET_DB_PASSWORD)
    with pytest.raises(mysql.connector.Error):
        conn.cursor().execute(f"SELECT * FROM {config.DB_NAME}.users")
    conn.close()
