# SPDX-License-Identifier: AGPL-3.0-or-later
import unittest

from server.config import AppEnv, load_config
from server.models import SetupError

SECRET = "a1" * 32  # 64 hex characters = 32 bytes
SECRET2 = "b2" * 32


def env(**over):
    base = {
        "APP_ENV": "development",
        "PUBLIC_ORIGIN": "http://localhost:8000",
        "DB_PATH": "/tmp/accounts.db",
        "CSRF_SECRET_KEY": SECRET,
        "SESSION_SECRET_KEY": SECRET2,
    }
    base.update(over)
    return base


class ConfigTests(unittest.TestCase):
    def test_valid_development_config(self):
        settings = load_config(env())
        self.assertEqual(settings.app_env, AppEnv.DEVELOPMENT)
        self.assertEqual(settings.public_origin, "http://localhost:8000")
        self.assertEqual(settings.base_path, "/")
        self.assertEqual(settings.enabled_adapters, frozenset())
        self.assertEqual(settings.limits.auth_body_bytes, 16 * 1024)
        self.assertEqual(len(settings.csrf_key), 32)

    def test_origin_is_normalized(self):
        settings = load_config(env(PUBLIC_ORIGIN="https://follow.example/"))
        self.assertEqual(settings.public_origin, "https://follow.example")

    def test_unknown_environment_fails_closed(self):
        with self.assertRaises(SetupError):
            load_config(env(APP_ENV="staging"))

    def test_missing_origin_and_db_fail_closed(self):
        for key in ("PUBLIC_ORIGIN", "DB_PATH"):
            with self.subTest(key=key):
                bad = env()
                del bad[key]
                with self.assertRaises(SetupError):
                    load_config(bad)

    def test_origin_shape_is_checked(self):
        for origin in ("ftp://follow.example", "https://follow.example/path", "https://user:pw@follow.example",
                       "https://", "not a url"):
            with self.subTest(origin=origin):
                with self.assertRaises(SetupError):
                    load_config(env(PUBLIC_ORIGIN=origin))

    def test_secrets_are_required_and_strong(self):
        for key in ("CSRF_SECRET_KEY", "SESSION_SECRET_KEY"):
            with self.subTest(key=key):
                bad = env()
                del bad[key]
                with self.assertRaises(SetupError):
                    load_config(bad)
        with self.assertRaises(SetupError):
            load_config(env(CSRF_SECRET_KEY="abcd"))  # too short
        with self.assertRaises(SetupError):
            load_config(env(SESSION_SECRET_KEY="zz"))

    def test_production_requires_https_and_explicit_proxies(self):
        with self.assertRaises(SetupError):
            load_config(env(APP_ENV="production", PUBLIC_ORIGIN="http://follow.example", TRUSTED_PROXIES=""))
        # Explicit (even empty) trusted proxies are accepted with https.
        settings = load_config(env(APP_ENV="production", PUBLIC_ORIGIN="https://follow.example", TRUSTED_PROXIES=""))
        self.assertEqual(settings.trusted_proxies, ())
        with self.assertRaises(SetupError):
            load_config(env(APP_ENV="production", PUBLIC_ORIGIN="https://follow.example"))

    def test_production_rejects_mock_adapters_and_memory_db(self):
        with self.assertRaises(SetupError):
            load_config(env(APP_ENV="production", PUBLIC_ORIGIN="https://follow.example",
                            TRUSTED_PROXIES="", AUTH_ADAPTERS="mock"))
        with self.assertRaises(SetupError):
            load_config(env(APP_ENV="production", PUBLIC_ORIGIN="https://follow.example",
                            TRUSTED_PROXIES="", DB_PATH=":memory:"))

    def test_unknown_adapter_fails_closed(self):
        with self.assertRaises(SetupError):
            load_config(env(AUTH_ADAPTERS="google,nope"))

    def test_secrets_never_appear_in_repr_or_redacted(self):
        settings = load_config(env())
        self.assertNotIn(SECRET, repr(settings))
        self.assertNotIn(SECRET2, repr(settings))
        redacted = settings.redacted()
        self.assertEqual(redacted["csrf_key"], "<redacted>")
        self.assertNotIn(SECRET, str(redacted))

    def test_base_path_is_validated(self):
        self.assertEqual(load_config(env(BASE_PATH="/api/v1/")).base_path, "/api/v1")
        for bad in ("api", "/a/../b", "/a b"):
            with self.subTest(base=bad):
                with self.assertRaises(SetupError):
                    load_config(env(BASE_PATH=bad))

    def test_limit_overrides(self):
        settings = load_config(env(LIMIT_AUTH_BODY_BYTES="2048"))
        self.assertEqual(settings.limits.auth_body_bytes, 2048)
        with self.assertRaises(SetupError):
            load_config(env(LIMIT_AUTH_BODY_BYTES="0"))
        with self.assertRaises(SetupError):
            load_config(env(LIMIT_AUTH_BODY_BYTES="many"))


if __name__ == "__main__":
    unittest.main()
