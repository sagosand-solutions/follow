import json
import sqlite3
import unittest

from server.account_data import delete_account
from server.db import Database
from server.repositories import Repositories
from server.scoring_repo import ScoringRepository
from server.tests.support import FakePayments, build_fixture, call, json_body


class AccountDataTests(unittest.TestCase):
    def make(self, **kwargs):
        fixture, tmp = build_fixture(**kwargs)
        self.addCleanup(tmp.cleanup)
        return fixture

    def test_export_requires_session_and_recent_auth(self):
        fx = self.make()
        self.assertEqual(call(fx.app, "GET", "/account/export")[0], 401)
        stale = self.make(auth_time=999_000.0)  # age 1000s > 600s recent-auth window
        self.assertEqual(call(stale.app, "GET", "/account/export", headers=stale.headers())[0], 403)

    def test_export_returns_only_own_sanitized_data(self):
        fx = self.make()
        status, headers, body = call(fx.app, "GET", "/account/export", headers=fx.headers())
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("cache-control"), "no-store")
        payload = json_body((status, headers, body))
        self.assertEqual(payload["user"]["id"], "u1")
        self.assertTrue(payload["identities"] == [])
        self.assertIn("entitlements", payload)
        text = json.dumps(payload).lower()
        for secret in ("csrf", "session", "cookie", "secret"):
            self.assertNotIn(secret, text)

    def test_delete_requires_csrf_and_origin(self):
        fx = self.make()
        self.assertEqual(call(fx.app, "POST", "/account/delete", headers=fx.headers())[0], 403)

    def test_delete_revokes_sessions_and_pseudonymizes(self):
        fx = self.make()
        status, _, _ = call(fx.app, "POST", "/account/delete", headers=fx.headers(csrf=True))
        self.assertEqual(status, 200)
        # The presented session is revoked immediately.
        self.assertEqual(call(fx.app, "GET", "/me", headers=fx.headers())[0], 401)
        db = Database(fx.path, clock=lambda: fx.clock[0])
        repos = Repositories(db)
        self.assertEqual(repos.users.get("u1").status.value, "deleted")
        self.assertEqual(repos.identities.list_for_user("u1"), [])
        db.close()

    def test_delete_purges_scoring_data_including_claimed_sources(self):
        fx = self.make()
        db = Database(fx.path, clock=lambda: fx.clock[0])
        repo = ScoringRepository(db)
        with db.transaction(immediate=True):
            account = repo.ensure_account_principal("u1", fx.clock[0])
            source = repo.create_anonymous("h" * 64, fx.clock[0], fx.clock[0] + 10**6)
            db.connect().execute("UPDATE scoring_principals SET claimed_to = ? WHERE id = ?", (account.id, source.id))
            db.connect().execute("INSERT INTO first_attempts (principal_id, question_id, family, course_id, catalog_version, "
                                 "rules_version, revision, option_id, outcome, score_units, accepted_at) "
                                 "VALUES (?, 'q1', 'free', 'c1', 'v1', 'r2', 'rev', 'o1', 'correct', 2, ?)", (source.id, fx.clock[0]))
            db.connect().execute("INSERT INTO leaderboard_preferences (principal_id, family, alias, moderation_state) "
                                 "VALUES (?, 'free', 'a', 'ok')", (source.id,))
        db.close()
        before = self._counts(fx, ("purchases", "purchase_consents", "entitlements"))
        self.assertEqual(call(fx.app, "POST", "/account/delete", headers=fx.headers(csrf=True))[0], 200)
        db = Database(fx.path, clock=lambda: fx.clock[0])
        conn = db.connect()
        for table in ("scoring_principals", "first_attempts", "leaderboard_preferences"):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        db.close()
        self.assertEqual(self._counts(fx, ("purchases", "purchase_consents", "entitlements")), before)  # tombstones kept

    def test_a_purge_failure_rolls_back_the_whole_delete(self):
        fx = self.make()
        db = Database(fx.path, clock=lambda: fx.clock[0])
        repo = ScoringRepository(db)
        with db.transaction(immediate=True):
            account = repo.ensure_account_principal("u1", fx.clock[0])
            db.connect().execute("INSERT INTO first_attempts (principal_id, question_id, family, course_id, catalog_version, "
                                 "rules_version, revision, option_id, outcome, score_units, accepted_at) "
                                 "VALUES (?, 'q1', 'free', 'c1', 'v1', 'r2', 'rev', 'o1', 'correct', 2, ?)", (account.id, fx.clock[0]))
        db.connect().execute("CREATE TRIGGER boom BEFORE DELETE ON first_attempts BEGIN SELECT RAISE(ABORT, 'x'); END")
        repos = Repositories(db)
        with self.assertRaises(sqlite3.IntegrityError):
            delete_account(repos, "u1")
        # nothing of the deletion survived: the user is still active and no scoring row was removed
        self.assertEqual(repos.users.get("u1").status.value, "active")
        self.assertEqual(db.connect().execute("SELECT COUNT(*) FROM scoring_principals").fetchone()[0], 1)
        self.assertEqual(db.connect().execute("SELECT COUNT(*) FROM first_attempts").fetchone()[0], 1)
        self.assertGreaterEqual(db.connect().execute("SELECT COUNT(*) FROM purchases").fetchone()[0], 1)
        db.close()

    def test_delete_is_idempotent_and_works_without_scoring_data(self):
        fx = self.make()
        self.assertEqual(call(fx.app, "POST", "/account/delete", headers=fx.headers(csrf=True))[0], 200)
        # The session was revoked; a second delete cannot even authenticate.
        self.assertEqual(call(fx.app, "POST", "/account/delete", headers=fx.headers(csrf=True))[0], 401)

    def _counts(self, fx, tables):
        db = Database(fx.path, clock=lambda: fx.clock[0])
        try:
            return tuple(db.connect().execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables)
        finally:
            db.close()

    def test_late_webhook_does_not_recreate_a_user(self):
        class Event:
            state = "paid"
            provider_purchase_id = "unknown-purchase"
            revision = 1
            event_id = "evt-late"
            payload_digest = "e" * 64

        fx = self.make(payments=FakePayments(webhooks={"good": Event()}))
        self.assertEqual(call(fx.app, "POST", "/payments/mock/webhook", headers={"x-signature": "good"}, body=b"raw")[0], 200)
        db = Database(fx.path, clock=lambda: fx.clock[0])
        count = db.connect().execute("SELECT COUNT(*) FROM users").fetchone()[0]
        db.close()
        self.assertEqual(count, 1)  # only the original user


if __name__ == "__main__":
    unittest.main()
