# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite connection, transactions and migration runner.

Short transactions, WAL, ``foreign_keys`` and a busy timeout. ``BEGIN IMMEDIATE``
is available for compare-and-swap writes. A failed commit never reports success.
No network call happens inside a transaction.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager

from .migrations import MIGRATIONS
from .models import StorageError


def connect(db_path: str, *, busy_timeout_ms: int = 5000, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=busy_timeout_ms / 1000.0, isolation_level=None, check_same_thread=False)
    else:
        conn = sqlite3.connect(db_path, timeout=busy_timeout_ms / 1000.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def apply_migrations(conn: sqlite3.Connection, *, migrations=MIGRATIONS, clock=time.time) -> int:
    """Apply pending migrations; return how many were newly applied.

    Repeated calls are a no-op. Each migration runs inside a transaction so a
    failure rolls back the whole step.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    newly_applied = 0
    for version, statements in migrations:
        if version in applied:
            continue
        if conn.in_transaction:
            for statement in statements:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, clock()))
        else:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, clock()))
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        newly_applied += 1
    return newly_applied


class Database:
    """Owns one connection. Tests inject a mock ``clock`` and never sleep."""

    def __init__(self, db_path: str, *, clock=time.time, busy_timeout_ms: int = 5000):
        self.db_path = db_path
        self.clock = clock
        self.busy_timeout_ms = busy_timeout_ms
        self.connection: sqlite3.Connection | None = None
        self._savepoint = 0
        self._owner: int | None = None

    def connect(self) -> sqlite3.Connection:
        """Return this Database's connection, owned by one thread at a time.

        One Database (and its connection) belongs to one request/unit of work.
        A different thread touching it is disallowed sharing, not isolation, so it
        is stopped explicitly rather than silently interleaving transactions.
        """
        current = threading.get_ident()
        if self.connection is None:
            self.connection = connect(self.db_path, busy_timeout_ms=self.busy_timeout_ms)
            self._owner = current
        elif self._owner != current:
            raise StorageError("database connection is owned by another thread; use one Database per request")
        return self.connection

    @contextmanager
    def transaction(self, *, immediate: bool = False):
        """Commit on success, rollback on any exception, raise on failed commit.

        Nested use becomes a savepoint so callers can compose repository methods
        inside one outer transaction.
        """
        conn = self.connect()
        if conn.in_transaction:
            self._savepoint += 1
            name = f"sp_{self._savepoint}"
            conn.execute(f"SAVEPOINT {name}")
            try:
                yield conn
            except BaseException:
                conn.execute(f"ROLLBACK TO {name}")
                conn.execute(f"RELEASE {name}")
                self._savepoint -= 1
                raise
            else:
                conn.execute(f"RELEASE {name}")
                self._savepoint -= 1
            return
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            try:
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise StorageError("commit failed; transaction rolled back") from exc

    def migrate(self) -> int:
        return apply_migrations(self.connect(), clock=self.clock)

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
