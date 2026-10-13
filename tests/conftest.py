"""Tests run against a real MySQL/MariaDB with sql/001-003 already loaded (see README).
Set DB_* / TARGET_DB_* env vars if yours differ from the defaults in backend/config.py."""
import os

import pytest

from backend import auth, config, seed


def pytest_sessionstart(session):
    """The suite DELETEs every row in the app database, so refuse to run unless told it's a test DB."""
    if os.getenv("ALLOW_TEST_DB_WIPE") != "1" or "test" not in config.DB_NAME.lower():
        pytest.exit(
            "Refusing to run: these tests erase all data in the app database.\n"
            "Create a separate test database (see README, 'Running the tests'), then run with\n"
            "  DB_NAME=app_db_test ALLOW_TEST_DB_WIPE=1 python -m pytest\n"
            f"(current DB_NAME={config.DB_NAME!r}; its name must contain 'test').",
            returncode=2,
        )
from backend.db import get_conn
from backend.llm import FakeProvider


@pytest.fixture(autouse=True)
def clean_db():
    """Empty every app_db table, then re-seed plans/templates. The target DB is read-only and untouched."""
    with get_conn() as c:
        tables = [r["TABLE_NAME"] for r in c.all(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE'")]
        c.execute("SET FOREIGN_KEY_CHECKS=0")
        for t in tables:
            c.execute(f"DELETE FROM `{t}`")  # TRUNCATE would need DROP, which the app account lacks
        c.execute("SET FOREIGN_KEY_CHECKS=1")
    seed.seed_reference_data()
    yield


@pytest.fixture
def alice():
    uid = auth.signup("alice", "alice@example.com", "password123", True)
    return uid


@pytest.fixture
def admin_id():
    return seed.create_admin("root_admin", "root@example.com", "adminpass1")


def sql_reply(sql: str, why: str = "test") -> str:
    return f"SQL: {sql}\nEXPLANATION: {why}"


@pytest.fixture
def provider():
    return FakeProvider(default=sql_reply("SELECT 1 AS one"))
