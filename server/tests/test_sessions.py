# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic session lifecycle and security-boundary tests for 019-S."""
import base64
import tempfile
import unittest
from pathlib import Path

from server.config import load_config
from server.db import Database
from server.models import UserStatus
from server.repositories import Repositories
from server.sessions import SessionDenied, SessionService


class TokenSequence:
    def __init__(self):
        self.value = 0

    def __call__(self, nbytes):
        self.value += 1
        raw = bytes([self.value]) * nbytes
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def load_settings(db_path, *, idle=100, absolute=200, recent=30):
    return load_config({
        "APP_ENV": "test",
        "PUBLIC_ORIGIN": "https://accounts.example",
        "BASE_PATH": "/api/v1",
        "DB_PATH": db_path,
        "CSRF_SECRET_KEY": "11" * 32,
        "SESSION_SECRET_KEY": "22" * 32,
        "SESSION_IDLE_SECONDS": str(idle),
        "SESSION_ABSOLUTE_SECONDS": str(absolute),
        "RECENT_AUTH_SECONDS": str(recent),
    })


class SessionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [1_000.0]
        self.db = Database(str(Path(self.tmp.name) / "accounts.db"), clock=lambda: self.now[0])
        self.addCleanup(self.db.close)
        self.db.migrate()
        self.repos = Repositories(self.db)
        self.user = self.repos.users.create(user_id="user-1")
        self.settings = load_settings(self.db.db_path)
        self.tokens = TokenSequence()
        self.service = SessionService(self.repos, self.settings, token_factory=self.tokens)

    def test_issue_stores_only_digests_and_credentials_hide_bearers(self):
        issued = self.service.issue(self.user.id)
        self.assertNotIn(issued.cookie_value, repr(issued))
        self.assertNotIn(issued.csrf_token, repr(issued))
        stored = self.repos.sessions.get(issued.session.id_hash)
        self.assertIsNotNone(stored)
        self.assertNotEqual(stored.id_hash, issued.cookie_value)
        self.assertNotEqual(stored.csrf_hash, issued.csrf_token)
        self.assertEqual(stored.auth_time, self.now[0])
        self.assertEqual(stored.expires_at, self.now[0] + self.settings.session_idle_seconds)
        self.assertEqual(stored.absolute_expires_at, self.now[0] + self.settings.session_absolute_seconds)

    def test_authenticate_slides_idle_but_never_past_absolute_expiry(self):
        settings = load_settings(self.db.db_path, idle=100, absolute=10)
        service = SessionService(self.repos, settings, token_factory=self.tokens)
        issued = service.issue(self.user.id)
        self.now[0] = 1_005.0
        principal = service.authenticate(issued.cookie_value)
        self.assertIsNotNone(principal)
        touched = self.repos.sessions.get(issued.session.id_hash)
        self.assertEqual(touched.last_seen, 1_005.0)
        self.assertEqual(touched.expires_at, touched.absolute_expires_at)
        self.now[0] = 1_010.0
        self.assertIsNone(service.authenticate(issued.cookie_value))
        self.assertIsNone(self.repos.sessions.get(issued.session.id_hash))

    def test_expired_idle_or_inactive_user_is_denied(self):
        issued = self.service.issue(self.user.id)
        self.now[0] = issued.session.expires_at
        self.assertIsNone(self.service.authenticate(issued.cookie_value))
        self.assertIsNone(self.repos.sessions.get(issued.session.id_hash))

        self.now[0] = 2_000.0
        self.repos.users.mark_deleted(self.user.id)
        with self.assertRaises(SessionDenied):
            self.service.issue(self.user.id)

    def test_rotation_is_atomic_and_old_cookie_stops_authenticating(self):
        first = self.service.issue(self.user.id)
        second = self.service.issue(self.user.id, old_cookie=first.cookie_value)
        self.assertIsNone(self.service.authenticate(first.cookie_value))
        self.assertFalse(self.service.verify_csrf(first.cookie_value, first.csrf_token))
        self.assertFalse(self.service.verify_csrf(second.cookie_value, first.csrf_token))
        self.assertEqual(self.service.authenticate(second.cookie_value).user_id, self.user.id)
        self.assertIsNone(self.repos.sessions.get(first.session.id_hash))
        self.assertIsNotNone(self.repos.sessions.get(second.session.id_hash))

    def test_rotation_failure_rolls_back_new_session_and_keeps_old(self):
        first = self.service.issue(self.user.id)
        original_revoke = self.repos.sessions.revoke

        def fail_revoke(_id_hash):
            raise RuntimeError("synthetic storage failure")

        self.repos.sessions.revoke = fail_revoke
        try:
            with self.assertRaises(RuntimeError):
                self.service.issue(self.user.id, old_cookie=first.cookie_value)
        finally:
            self.repos.sessions.revoke = original_revoke
        self.assertIsNotNone(self.repos.sessions.get(first.session.id_hash))
        self.assertEqual(len(self.db.connect().execute("SELECT id_hash FROM sessions").fetchall()), 1)

    def test_recent_auth_uses_stored_time_and_revoke_is_idempotent(self):
        issued = self.service.issue(self.user.id)
        principal = self.service.authenticate(issued.cookie_value)
        self.assertTrue(self.service.verify_csrf(issued.cookie_value, issued.csrf_token))
        self.assertFalse(self.service.verify_csrf(issued.cookie_value, "x" * 43))
        self.assertTrue(self.service.require_recent_auth(principal))
        self.now[0] += self.settings.recent_auth_seconds + 1
        self.assertFalse(self.service.require_recent_auth(principal))
        self.assertTrue(self.service.revoke(issued.cookie_value))
        self.assertFalse(self.service.revoke(issued.cookie_value))
        self.assertFalse(self.service.verify_csrf(issued.cookie_value, issued.csrf_token))
        self.assertFalse(self.service.require_recent_auth(principal))

    def test_revoke_all_and_malformed_cookie(self):
        one = self.service.issue(self.user.id)
        two = self.service.issue(self.user.id)
        self.assertIsNone(self.service.authenticate("bad-cookie"))
        self.assertEqual(self.service.revoke_all_for_user(self.user.id), 2)
        self.assertIsNone(self.service.authenticate(one.cookie_value))
        self.assertIsNone(self.service.authenticate(two.cookie_value))

    def test_future_auth_time_is_rejected(self):
        with self.assertRaises(SessionDenied):
            self.service.issue(self.user.id, auth_time=self.now[0] + 1)


if __name__ == "__main__":
    unittest.main()
