# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic tests for purpose-bound CSRF and process-local PKCE state."""
import base64
import threading
import unittest

from server.auth import AuthCapacityError, AuthRestartRequired, AuthSecurity, BoundedPKCECache
from server.config import load_config


class TokenSequence:
    def __init__(self):
        self.value = 0

    def __call__(self, nbytes):
        self.value += 1
        raw = bytes([self.value]) * nbytes
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def settings():
    return load_config({
        "APP_ENV": "test",
        "PUBLIC_ORIGIN": "https://accounts.example",
        "BASE_PATH": "/api/v1",
        "DB_PATH": ":memory:",
        "CSRF_SECRET_KEY": "11" * 32,
        "SESSION_SECRET_KEY": "22" * 32,
    })


class AuthSecurityTests(unittest.TestCase):
    def setUp(self):
        self.security = AuthSecurity(settings(), token_factory=TokenSequence())

    def test_preauth_csrf_is_bound_to_cookie_and_purpose(self):
        first = self.security.new_preauth()
        second = self.security.new_preauth()
        self.assertNotEqual(first.binding_cookie, second.binding_cookie)
        self.assertTrue(self.security.verify_csrf(
            first.binding_cookie, first.csrf_token, purpose="preauth"))
        self.assertFalse(self.security.verify_csrf(
            first.binding_cookie, first.csrf_token, purpose="session"))
        self.assertFalse(self.security.verify_csrf(
            second.binding_cookie, first.csrf_token, purpose="preauth"))
        self.assertTrue(self.security.verify_binding(
            first.binding_cookie, self.security.binding_hash(first.binding_cookie)))
        self.assertFalse(self.security.verify_binding(
            second.binding_cookie, self.security.binding_hash(first.binding_cookie)))

    def test_session_csrf_requires_stored_digest_and_is_purpose_separated(self):
        token = self.security.new_token()
        csrf = self.security.csrf_for(token, purpose="session")
        stored = self.security.csrf_digest(csrf)
        self.assertTrue(self.security.verify_csrf(token, csrf, purpose="session", stored_hash=stored))
        self.assertFalse(self.security.verify_csrf(token, csrf, purpose="session"))
        self.assertFalse(self.security.verify_csrf(token, csrf, purpose="preauth", stored_hash=stored))
        self.assertFalse(self.security.verify_csrf(token, "x" * 43, purpose="session", stored_hash=stored))
        self.assertFalse(self.security.verify_csrf(token, "é" * 43, purpose="session", stored_hash=stored))

    def test_invalid_purpose_and_token_fail_closed(self):
        token = self.security.new_token()
        with self.assertRaises(ValueError):
            self.security.csrf_for(token, purpose="payment")
        with self.assertRaises(ValueError):
            self.security.session_id_hash("not-a-token")
        self.assertFalse(self.security.verify_binding(token, "bad-hash"))


class BoundedPKCECacheTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.cache = BoundedPKCECache(max_entries=2, clock=lambda: self.now[0])

    def test_take_is_single_use_and_restart_has_no_fallback(self):
        key = "a" * 64
        self.cache.put(key, "v" * 43, expires_at=200.0)
        self.assertEqual(self.cache.take(key), "v" * 43)
        with self.assertRaises(AuthRestartRequired):
            self.cache.take(key)
        restarted = BoundedPKCECache(max_entries=2, clock=lambda: self.now[0])
        with self.assertRaises(AuthRestartRequired):
            restarted.take(key)

    def test_expiry_and_capacity_are_bounded(self):
        self.cache.put("a" * 64, "a" * 43, expires_at=101.0)
        self.cache.put("b" * 64, "b" * 43, expires_at=200.0)
        with self.assertRaises(AuthCapacityError):
            self.cache.put("c" * 64, "c" * 43, expires_at=300.0)
        self.now[0] = 101.0
        self.assertEqual(self.cache.purge(), 1)
        self.cache.put("c" * 64, "c" * 43, expires_at=300.0)
        with self.assertRaises(AuthRestartRequired):
            self.cache.take("a" * 64)
        self.assertEqual(self.cache.take("c" * 64), "c" * 43)
        self.assertEqual(len(self.cache), 1)

    def test_verifier_size_and_expiry_are_bounded(self):
        for verifier in ("short", "v" * 129, "v" * 42 + "!"):
            with self.subTest(verifier_length=len(verifier)):
                with self.assertRaises(ValueError):
                    self.cache.put("d" * 64, verifier, expires_at=200.0)
        with self.assertRaises(ValueError):
            self.cache.put("d" * 64, "d" * 43, expires_at=float("inf"))

    def test_concurrent_take_has_exactly_one_winner(self):
        key = "c" * 64
        self.cache.put(key, "o" * 43, expires_at=200.0)
        barrier = threading.Barrier(2)
        results = []

        def take():
            barrier.wait()
            try:
                results.append(self.cache.take(key))
            except AuthRestartRequired:
                results.append("missing")

        threads = [threading.Thread(target=take) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(results, ["o" * 43, "missing"])


if __name__ == "__main__":
    unittest.main()
