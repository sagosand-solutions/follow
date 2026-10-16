import os
import sqlite3
import tempfile
import threading
import unittest

from server.db import Database, apply_migrations, connect
from server.models import StorageError


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "accounts.db")

    def test_migrate_creates_schema_and_repeat_is_noop(self):
        conn = connect(self.path)
        self.addCleanup(conn.close)
        self.assertEqual(apply_migrations(conn), 2)
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in ("users", "identities", "sessions", "auth_transactions", "products", "purchases",
                      "entitlements", "payment_events", "policy_bundles", "purchase_consents", "schema_migrations"):
            self.assertIn(table, names)
        self.assertEqual(apply_migrations(conn), 0)

    def test_wal_and_foreign_keys(self):
        conn = connect(self.path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_foreign_keys_are_enforced(self):
        db = Database(self.path)
        self.addCleanup(db.close)
        db.migrate()
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction(immediate=True) as conn:
                conn.execute("INSERT INTO identities (id, user_id, provider, issuer, subject, created_at)"
                             " VALUES ('i', 'missing', 'p', 'iss', 'sub', 1.0)")

    def test_transaction_rolls_back_on_error(self):
        db = Database(self.path)
        self.addCleanup(db.close)
        db.migrate()
        with self.assertRaises(RuntimeError):
            with db.transaction(immediate=True) as conn:
                conn.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('u1', 1.0, 'active', NULL)")
                raise RuntimeError("boom")
        self.assertIsNone(db.connect().execute("SELECT * FROM users WHERE id = 'u1'").fetchone())

    def test_nested_transaction_uses_savepoint(self):
        db = Database(self.path)
        self.addCleanup(db.close)
        db.migrate()
        with db.transaction(immediate=True) as conn:
            conn.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('outer', 1.0, 'active', NULL)")
            with self.assertRaises(RuntimeError):
                with db.transaction() as inner:
                    inner.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('inner', 1.0, 'active', NULL)")
                    raise RuntimeError("rollback only the savepoint")
            conn.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('after', 1.0, 'active', NULL)")
        ids = {row[0] for row in db.connect().execute("SELECT id FROM users")}
        self.assertEqual(ids, {"outer", "after"})

    def test_unique_constraint_is_enforced(self):
        db = Database(self.path)
        self.addCleanup(db.close)
        db.migrate()

        def add_identity(ident):
            with db.transaction(immediate=True) as conn:
                conn.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('u', 1.0, 'active', NULL)"
                             " ON CONFLICT(id) DO NOTHING")
                conn.execute("INSERT INTO identities (id, user_id, provider, issuer, subject, created_at)"
                             f" VALUES ('{ident}', 'u', 'p', 'iss', 'sub', 1.0)")

        add_identity("i1")
        with self.assertRaises(sqlite3.IntegrityError):
            add_identity("i2")

    def test_connection_cannot_be_shared_across_threads(self):
        db = Database(self.path)
        self.addCleanup(db.close)
        db.migrate()  # connects, so the main thread owns the connection
        errors = []

        def worker():
            try:
                with db.transaction(immediate=True):
                    pass
            except StorageError as exc:
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)

    def test_foreign_thread_cannot_first_use_or_close(self):
        db = Database(self.path)  # owned by this thread; not opened yet
        results = {}

        def worker():
            try:
                db.connect()
                results["connect"] = "ok"
            except StorageError:
                results["connect"] = "error"
            try:
                db.close()
                results["close"] = "ok"
            except StorageError:
                results["close"] = "error"

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(results, {"connect": "error", "close": "error"})
        # The owning thread can still open and close it.
        db.connect()
        db.close()

    def test_clock_is_injected(self):
        clock = [100.0]
        db = Database(self.path, clock=lambda: clock[0])
        self.addCleanup(db.close)
        db.migrate()
        with db.transaction(immediate=True) as conn:
            conn.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('u1', ?, 'active', NULL)",
                         (db.clock(),))
        self.assertEqual(db.connect().execute("SELECT created_at FROM users").fetchone()[0], 100.0)


class StorageErrorTests(unittest.TestCase):
    def test_storage_error_exists(self):
        self.assertTrue(issubclass(StorageError, Exception))


if __name__ == "__main__":
    unittest.main()
