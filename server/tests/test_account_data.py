import json
import unittest

from server.db import Database
from server.repositories import Repositories
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
