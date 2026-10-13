import json
from contextlib import contextmanager

import mysql.connector

from . import config

Error = mysql.connector.Error  # re-exported so other modules don't import the driver directly


def _connect(host, port, user, password, database):
    return mysql.connector.connect(
        host=host, port=port, user=user, password=password, database=database,
        charset="utf8mb4", autocommit=False, connection_timeout=10,
    )


class Conn:
    """Thin wrapper so services write `conn.one(sql, args)` instead of juggling cursors."""

    def __init__(self, raw):
        self.raw = raw

    def _cursor(self):
        return self.raw.cursor(dictionary=True, buffered=True)

    def execute(self, sql, args=None) -> int:
        """Run a statement; returns the number of affected rows."""
        cur = self._cursor()
        try:
            cur.execute(sql, args)
            return cur.rowcount
        finally:
            cur.close()

    def insert(self, sql, args=None) -> int:
        cur = self._cursor()
        try:
            cur.execute(sql, args)
            return cur.lastrowid
        finally:
            cur.close()

    def one(self, sql, args=None):
        cur = self._cursor()
        try:
            cur.execute(sql, args)
            return cur.fetchone()
        finally:
            cur.close()

    def all(self, sql, args=None) -> list[dict]:
        cur = self._cursor()
        try:
            cur.execute(sql, args)
            return list(cur.fetchall())
        finally:
            cur.close()


@contextmanager
def get_conn():
    """App DB connection (app_db). Commits on success, rolls back on exception.

    NOTE: anything you want to keep even when you then raise an error (e.g. a failed
    login attempt) must be committed before raising: do the write, leave the `with`
    block, and raise afterwards.
    """
    raw = _connect(config.DB_HOST, config.DB_PORT, config.DB_USER, config.DB_PASSWORD, config.DB_NAME)
    try:
        yield Conn(raw)
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def connect_target():
    """Raw connection to the target DB using the SELECT-only account."""
    return _connect(
        config.TARGET_DB_HOST, config.TARGET_DB_PORT, config.TARGET_DB_USER,
        config.TARGET_DB_PASSWORD, config.TARGET_DB_NAME,
    )


def to_json(obj) -> str:
    return json.dumps(obj, default=str)
