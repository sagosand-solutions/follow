import hashlib
import json
import unittest
from types import SimpleNamespace

from server.config import AppEnv
from server.db import Database
from server.repositories import Repositories
from server.tests.support import CORE_ACK, FakePayments, build_fixture, call, json_body


class HttpTestCase(unittest.TestCase):
    def make(self, **kwargs):
        fixture, tmp = build_fixture(**kwargs)
        self.addCleanup(tmp.cleanup)
        return fixture


class ImportSmokeTests(unittest.TestCase):
    def test_server_modules_import_cleanly_and_the_dead_body_reader_is_gone(self):
        import importlib
        for name in ("server.http", "server.app", "server.account_data", "server.scoring_http", "server.scoring_service"):
            with self.subTest(module=name):
                self.assertIsNotNone(importlib.import_module(name))
        server_http = importlib.import_module("server.http")
        self.assertFalse(hasattr(server_http, "read_body"))          # REF-05: the unused reader is removed
        self.assertFalse(hasattr(server_http, "BodyLimitExceeded"))


class RedirectAllowlistTests(unittest.TestCase):
    def redirect(self, url, *, hosts=frozenset({"pay.example"}), app_env=AppEnv.TEST):
        from server.app import _allowlisted_redirect
        return _allowlisted_redirect(url, SimpleNamespace(payment_redirect_hosts=hosts), app_env=app_env)

    def test_allowed_https_requires_nonempty_exact_hostname_allowlist(self):
        url = "https://pay.example/checkout/1"
        self.assertEqual(self.redirect(url, app_env=AppEnv.PRODUCTION), url)
        self.assertIsNone(self.redirect(url, hosts=frozenset(), app_env=AppEnv.TEST))
        self.assertIsNone(self.redirect("http://pay.example/checkout/1", hosts=frozenset(), app_env=AppEnv.TEST))

    def test_http_is_allowed_only_in_explicit_test_environment(self):
        url = "http://pay.example/checkout/1"
        self.assertEqual(self.redirect(url, app_env=AppEnv.TEST), url)
        self.assertIsNone(self.redirect(url, app_env=AppEnv.DEVELOPMENT))
        self.assertIsNone(self.redirect(url, app_env=AppEnv.PRODUCTION))

    def test_host_matching_rejects_suffix_and_userinfo_lookalikes(self):
        for url in (
            "https://pay.example.evil.test/checkout",
            "https://evilpay.example/checkout",
            "https://evil.example@pay.example/checkout",
            "https://pay.example@evil.example/checkout",
        ):
            with self.subTest(url=url):
                self.assertIsNone(self.redirect(url, app_env=AppEnv.PRODUCTION))

    def test_malformed_or_unsafe_urls_fail_closed(self):
        for url in (
            None, 7, "", " /relative", "/relative", "//pay.example/checkout", "https:///missing-host",
            "ftp://pay.example/file", "javascript:alert(1)", "https://[::1/", "https://pay.example:99999/",
            "https://pay.example:abc/", "https://pay.example/\r\nHeader: value", "https://pay.example/\tpath",
            "https://pay.example/\x80path",
            "https:// pay.example/path", "https://pay.example /path",
        ):
            with self.subTest(url=url):
                self.assertIsNone(self.redirect(url, app_env=AppEnv.TEST))

    def test_host_allowlist_keeps_existing_hostname_only_port_semantics(self):
        # The allowlist is host-only, as before: URL ports are parsed but not part of the host match.
        url = "https://pay.example:9443/checkout"
        self.assertEqual(self.redirect(url, app_env=AppEnv.PRODUCTION), url)
        self.assertIsNone(self.redirect("https://pay.example.evil.test:9443/checkout", app_env=AppEnv.TEST))


class ClientIpTests(unittest.TestCase):
    def test_forwarded_ip_is_used_only_through_trusted_proxy_chain(self):
        from server.app import _client_ip
        settings = SimpleNamespace(trusted_proxies=("10.0.0.0/8",))
        request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.1"),
                                  headers={"x-forwarded-for": "198.51.100.7, 10.0.0.2"})
        self.assertEqual(_client_ip(request, settings), "198.51.100.7")
        untrusted = SimpleNamespace(client=SimpleNamespace(host="198.51.100.9"),
                                    headers={"x-forwarded-for": "203.0.113.3"})
        self.assertEqual(_client_ip(untrusted, settings), "198.51.100.9")

    def test_ipv4_mapped_peer_is_canonical_before_the_trusted_proxy_check(self):
        from server.app import _client_ip
        settings = SimpleNamespace(trusted_proxies=("10.0.0.0/8",))
        # A trusted proxy that reaches us over a dual-stack socket is still a trusted proxy.
        proxy = SimpleNamespace(client=SimpleNamespace(host="::ffff:10.0.0.1"),
                                headers={"x-forwarded-for": "198.51.100.7"})
        self.assertEqual(_client_ip(proxy, settings), "198.51.100.7")
        proxy_hex = SimpleNamespace(client=SimpleNamespace(host="::ffff:0a00:0001"),
                                    headers={"x-forwarded-for": "198.51.100.8, 10.0.0.2"})
        self.assertEqual(_client_ip(proxy_hex, settings), "198.51.100.8")
        # An untrusted mapped peer is reported as its IPv4 address and cannot spoof X-Forwarded-For.
        client = SimpleNamespace(client=SimpleNamespace(host="::ffff:198.51.100.9"),
                                 headers={"x-forwarded-for": "203.0.113.3"})
        self.assertEqual(_client_ip(client, settings), "198.51.100.9")
        # Mapped entries inside the chain are canonical too (proxy hop and client).
        chain = SimpleNamespace(client=SimpleNamespace(host="10.0.0.1"),
                                headers={"x-forwarded-for": "::ffff:198.51.100.7, ::ffff:10.0.0.2"})
        self.assertEqual(_client_ip(chain, settings), "198.51.100.7")
        # Plain IPv6 is untouched.
        v6 = SimpleNamespace(client=SimpleNamespace(host="2001:db8::5"), headers={})
        self.assertEqual(_client_ip(v6, settings), "2001:db8::5")


class CsrfAndSessionTests(HttpTestCase):
    def test_csrf_issues_preauth_cookie(self):
        fx = self.make()
        status, headers, body = call(fx.app, "GET", "/auth/csrf")
        self.assertEqual(status, 200)
        self.assertIn("csrf_token", json_body((status, headers, body)))
        self.assertIn("preauth=", headers.get("set-cookie", ""))
        self.assertEqual(headers.get("cache-control"), "no-store")

    def test_me_requires_a_session(self):
        fx = self.make()
        self.assertEqual(call(fx.app, "GET", "/me")[0], 401)
        status, headers, body = call(fx.app, "GET", "/me", headers=fx.headers())
        self.assertEqual(status, 200)
        payload = json_body((status, headers, body))
        self.assertEqual(payload["user"]["id"], "u1")
        self.assertIn("csrf_token", payload)
        self.assertEqual(headers.get("cache-control"), "no-store")

    def test_logout_is_csrf_origin_guarded_and_idempotent(self):
        fx = self.make()
        self.assertEqual(call(fx.app, "POST", "/logout", headers={"cookie": f"session={fx.cookie}"})[0], 403)
        self.assertEqual(call(fx.app, "POST", "/logout", headers=fx.headers())[0], 403)  # no csrf
        first = call(fx.app, "POST", "/logout", headers=fx.headers(csrf=True))
        self.assertEqual(first[0], 200)
        self.assertIn("session=", first[1].get("set-cookie", ""))
        self.assertEqual(call(fx.app, "POST", "/logout", headers=fx.headers(csrf=True))[0], 200)

    def test_body_limit_is_enforced_before_routing(self):
        fx = self.make()
        big = b"x" * (17 * 1024)
        status, _, _ = call(fx.app, "POST", "/logout", headers=fx.headers(csrf=True), body=big)
        self.assertEqual(status, 413)


class ProductTests(HttpTestCase):
    def test_products_are_anonymous_and_public(self):
        fx = self.make(payments=FakePayments())
        status, headers, body = call(fx.app, "GET", "/products")
        self.assertEqual(status, 200)
        payload = json_body((status, headers, body))
        self.assertEqual(payload["published_product_count"], 1)
        product = payload["products"][0]
        self.assertEqual(product["id"], "python-lifetime")
        self.assertEqual(product["published_chapter_count"], 1)  # duplicate C01 counted once
        self.assertTrue(payload["checkout_enabled"])
        self.assertEqual(payload["checkout_policy"]["version"], "2026-10-16")
        self.assertNotIn("path", json.dumps(product))

    def test_checkout_disabled_without_approved_policy(self):
        fx = self.make(checkout=False)
        payload = json_body(call(fx.app, "GET", "/products"))
        self.assertFalse(payload["checkout_enabled"])
        self.assertIsNone(payload["checkout_policy"])

    def test_entitlements_require_a_session(self):
        fx = self.make()
        self.assertEqual(call(fx.app, "GET", "/entitlements")[0], 401)
        payload = json_body(call(fx.app, "GET", "/entitlements", headers=fx.headers()))
        self.assertEqual(payload["entitlements"][0]["product_id"], "python-lifetime")


class ProviderTests(HttpTestCase):
    def test_provider_catalog_exposes_only_declared_public_fields(self):
        class Adapter:
            kind = "oauth"
            client_id = "must-not-leak"
            authorize_url = "https://private.example"
        class EmailAdapter:
            kind = "email"
        fx = self.make(providers={"google": Adapter(), "email": EmailAdapter(), "hidden": object()})
        payload = json_body(call(fx.app, "GET", "/auth/providers"))
        self.assertEqual(payload, {"providers": [{"id": "email", "kind": "email"}, {"id": "google", "kind": "oauth"}]})
        self.assertEqual(json_body(call(self.make().app, "GET", "/auth/providers")), {"providers": []})

    def test_provider_start_requires_an_exact_login_contract(self):
        class Recording:
            def __init__(self):
                self.calls = []

            def start(self, **kwargs):
                self.calls.append(kwargs)
                return {"authorize_url": "https://accounts.example/authorize"}

        adapter = Recording()
        fx = self.make(providers={"google": adapter})
        status, headers, body = call(fx.app, "GET", "/auth/csrf")
        csrf = json_body((status, headers, body))["csrf_token"]
        cookie = headers["set-cookie"].split(";", 1)[0]

        def start(payload):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            return call(fx.app, "POST", "/auth/google/start",
                        headers={"origin": "http://testserver", "cookie": cookie, "x-csrf-token": csrf}, body=raw)

        base = {"intent": "login", "return_to": "/me"}
        self.assertEqual(start(base)[0], 200)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual((adapter.calls[0]["intent"], adapter.calls[0]["return_to"]), ("login", "/me"))
        bad = [
            {}, {"intent": "login"}, {"return_to": "/me"}, {**base, "extra": 1},
            {"intent": "link", "return_to": "/me"}, {"intent": "signup", "return_to": "/me"}, {"intent": "", "return_to": "/me"},
            {"intent": ["login"], "return_to": "/me"}, {"intent": True, "return_to": "/me"},
            {"intent": None, "return_to": "/me"}, {"intent": "login", "return_to": "https://evil.example"},
            {"intent": "login", "return_to": "//evil.example"}, {"intent": "login", "return_to": "/a\u200bb"},
            [], None, True, "login", b"{",
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                self.assertEqual(start(payload)[0], 400)
        self.assertEqual(len(adapter.calls), 1)       # nothing but the one valid request reached the adapter

    def test_provider_start_guards_precede_the_adapter(self):
        class Recording:
            calls = []

            def start(self, **kwargs):
                Recording.calls.append(kwargs)
                return {"authorize_url": "https://accounts.example/authorize"}

        fx = self.make(providers={"google": Recording()})
        for headers in ({"origin": "http://testserver"}, {"cookie": "x", "x-csrf-token": "y"}):
            with self.subTest(headers=headers):
                self.assertEqual(call(fx.app, "POST", "/auth/google/start", headers=headers,
                                      body=b'{"intent":"login","return_to":"/me"}')[0], 403)
        self.assertEqual(Recording.calls, [])

    def test_disabled_provider_is_unavailable(self):
        fx = self.make()
        status, _, body = call(fx.app, "POST", "/auth/google/start", headers=fx.headers(csrf=True),
                               body=json.dumps({"intent": "login", "return_to": "/me"}).encode())
        self.assertEqual(status, 503)
        self.assertEqual(json_body((status, {}, body))["error"]["code"], "unavailable")

    def test_enabled_provider_delegates(self):
        class FakeProvider:
            def start(self, **kwargs):
                return {"authorize_url": "https://accounts.example/authorize"}

        fx = self.make(providers={"google": FakeProvider()})
        status, csrf_headers, body = call(fx.app, "GET", "/auth/csrf")
        csrf = json_body((status, csrf_headers, body))["csrf_token"]
        preauth = csrf_headers["set-cookie"].split(";", 1)[0]
        status, _, body = call(fx.app, "POST", "/auth/google/start",
                               headers={"origin": "http://testserver", "cookie": preauth, "x-csrf-token": csrf},
                               body=json.dumps({"intent": "login", "return_to": "/me"}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(json_body((status, {}, body))["authorize_url"], "https://accounts.example/authorize")

    def test_oauth_start_and_callback_remain_unchanged(self):
        from server.auth import AuthSecurity
        from server.models import AuthIntent
        from types import SimpleNamespace

        token_hash = "a" * 64
        class FakeProvider:
            kind = "oauth"
            def start(self, **kwargs):
                return {"authorize_url": "https://accounts.example/authorize"}
            def callback(self, request):
                return SimpleNamespace(user_id="u1", return_to="/me", transaction_token_hash=token_hash)

        fx = self.make(providers={"google": FakeProvider()})
        status, headers, body = call(fx.app, "GET", "/auth/csrf")
        csrf = json_body((status, headers, body))["csrf_token"]
        cookie = headers["set-cookie"].split(";", 1)[0]
        raw_cookie = cookie.split("=", 1)[1]
        db = Database(fx.path, clock=lambda: fx.clock[0])
        Repositories(db).auth_transactions.create(
            token_hash, preauth_binding_hash=AuthSecurity(fx.settings).binding_hash(raw_cookie),
            intent=AuthIntent.LOGIN, provider="google", expires_at=fx.clock[0] + 600)
        db.close()
        started = call(fx.app, "POST", "/auth/google/start",
                       headers={"origin": "http://testserver", "cookie": cookie, "x-csrf-token": csrf},
                       body=b'{"intent":"login","return_to":"/me"}')
        self.assertEqual(started[0], 200)
        callback = call(fx.app, "GET", "/auth/google/callback", headers={"cookie": cookie})
        self.assertEqual(callback[0], 302)
        self.assertEqual(callback[1]["location"], "/me")


class CallbackBindingTests(HttpTestCase):
    """B1: the callback binds to the browser that started the flow, once."""

    def _setup(self, **kw):
        from server.auth import AuthSecurity
        from server.models import AuthIntent
        from types import SimpleNamespace

        state = {"hash": "tx-hash-1"}

        class Provider:
            def callback(self, request):
                return SimpleNamespace(user_id="u1", return_to="/me", transaction_token_hash=state["hash"])

        fx = self.make(providers={"google": Provider()}, **kw)
        status, headers, body = call(fx.app, "GET", "/auth/csrf")
        cookie_a = headers["set-cookie"].split(";", 1)[0]
        raw_a = cookie_a.split("=", 1)[1]
        status, headers, body = call(fx.app, "GET", "/auth/csrf")
        cookie_b = headers["set-cookie"].split(";", 1)[0]
        db = Database(fx.path, clock=lambda: fx.clock[0])
        db.connect()
        Repositories(db).auth_transactions.create(
            "tx-hash-1", preauth_binding_hash=AuthSecurity(fx.settings).binding_hash(raw_a),
            intent=AuthIntent.LOGIN, provider="google", expires_at=fx.clock[0] + 600)
        db.close()
        return fx, cookie_a, cookie_b

    def test_matching_browser_completes_once_and_clears_preauth(self):
        fx, cookie_a, _ = self._setup()
        status, headers, _ = call(fx.app, "GET", "/auth/google/callback", headers={"cookie": cookie_a})
        self.assertEqual(status, 302)
        self.assertIn("session=", headers["set-cookie"])
        # Replay of the same transaction is refused.
        self.assertEqual(call(fx.app, "GET", "/auth/google/callback", headers={"cookie": cookie_a})[0], 400)

    def test_browser_b_cannot_complete_browser_a_transaction(self):
        fx, cookie_a, cookie_b = self._setup()
        for headers in ({"cookie": cookie_b}, {}):
            status, response_headers, _ = call(fx.app, "GET", "/auth/google/callback", headers=headers)
            self.assertEqual(status, 400)
            self.assertNotIn("session=", response_headers.get("set-cookie", ""))
        # A failed binding does not consume the transaction for the rightful browser.
        self.assertEqual(call(fx.app, "GET", "/auth/google/callback", headers={"cookie": cookie_a})[0], 302)

    def test_relogin_with_existing_session_uses_callback_auth_time(self):
        fx, cookie_a, _ = self._setup()
        fx.clock[0] += 500
        headers = {"cookie": f"{cookie_a}; {fx.session_cookie_name}={fx.cookie}"}
        status, response_headers, _ = call(fx.app, "GET", "/auth/google/callback", headers=headers)
        self.assertEqual(status, 302)
        # The old session was rotated away.
        self.assertEqual(call(fx.app, "GET", "/me", headers=fx.headers())[0], 401)

    def test_unknown_or_wrong_provider_transaction_is_refused(self):
        fx, cookie_a, _ = self._setup()
        db = Database(fx.path, clock=lambda: fx.clock[0])
        with db.transaction(immediate=True) as conn:
            conn.execute("UPDATE auth_transactions SET provider = 'apple'")
        db.close()
        self.assertEqual(call(fx.app, "GET", "/auth/google/callback", headers={"cookie": cookie_a})[0], 400)
        # Unknown transaction hash.
        fx2, cookie, _ = self._setup()
        db = Database(fx2.path, clock=lambda: fx2.clock[0])
        with db.transaction(immediate=True) as conn:
            conn.execute("DELETE FROM auth_transactions")
        db.close()
        self.assertEqual(call(fx2.app, "GET", "/auth/google/callback", headers={"cookie": cookie})[0], 400)


class EmailStartTests(HttpTestCase):
    class FakeEmail:
        kind = "email"

        def __init__(self):
            self.outbox = []
            self.users = {"known@example.test": "u1"}

        def start(self, *, email, transaction_token_hash, callback_url, **kwargs):
            self.outbox.append({"email": email, "url": callback_url, "transaction_token_hash": transaction_token_hash})

        def callback(self, request):
            token = request.query_params.get("state", "")
            entry = next((item for item in self.outbox if item["url"].endswith(f"state={token}")), None)
            user_id = self.users.get(entry["email"]) if entry else None
            return SimpleNamespace(user_id=user_id, return_to=None,
                                   transaction_token_hash=entry["transaction_token_hash"] if entry else "")

    def setUp(self):
        self.email_adapter = self.FakeEmail()
        self.fx = self.make(providers={"email": self.email_adapter})

    def start(self, email, return_to="/account"):
        status, headers, body = call(self.fx.app, "GET", "/auth/csrf")
        csrf = json_body((status, headers, body))["csrf_token"]
        cookie = headers["set-cookie"].split(";", 1)[0]
        status, response_headers, response_body = call(
            self.fx.app, "POST", "/auth/email/start",
            headers={"origin": "http://testserver", "cookie": cookie, "x-csrf-token": csrf},
            body=json.dumps({"email": email, "return_to": return_to, "intent": "login"}).encode())
        return status, response_headers, response_body, cookie

    def test_a_swallowed_adapter_failure_logs_only_fixed_fields_and_stays_202(self):
        import logging
        sentinel = "SENTINEL-SECRET-9f3a"

        class Boom(self.FakeEmail):
            def start(self, **kwargs):
                raise RuntimeError(f"adapter failed token={sentinel} for {kwargs.get('email')}")

        fx = self.make(providers={"email": Boom()})
        status, headers, body = call(fx.app, "GET", "/auth/csrf")
        csrf = json_body((status, headers, body))["csrf_token"]
        cookie = headers["set-cookie"].split(";", 1)[0]
        logger = logging.getLogger("follow.server")
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        previous, logger.propagate = logger.propagate, False
        self.addCleanup(setattr, logger, "propagate", previous)
        status, response_headers, raw = call(
            fx.app, "POST", "/auth/email/start",
            headers={"origin": "http://testserver", "cookie": cookie, "x-csrf-token": csrf},
            body=json.dumps({"email": "known@example.test", "return_to": "/account", "intent": "login"}).encode())
        self.assertEqual((status, json_body((status, response_headers, raw))), (202, {"ok": True}))
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual((record.event, record.code), ("provider_start_failed", "provider_error"))
        self.assertEqual(record.request_id, response_headers["x-request-id"])
        standard = set(logging.makeLogRecord({}).__dict__)             # the exact extra-field set: no extra key may slip in
        self.assertEqual(set(record.__dict__) - standard, {"event", "code", "request_id"})
        self.assertEqual(record.getMessage(), "provider start failed")
        text = record.getMessage() + " " + json.dumps(record.__dict__, default=str)
        self.assertNotIn(sentinel, text)
        self.assertNotIn("known@example.test", text)
        self.assertNotIn("token=", text)

    def test_known_and_unknown_addresses_receive_identical_202_and_outbox(self):
        known = self.start("known@EXAMPLE.TEST")
        unknown = self.start("unknown@example.test")
        self.assertEqual((known[0], json_body(known)), (202, {"ok": True}))
        self.assertEqual((unknown[0], json_body(unknown)), (202, {"ok": True}))
        self.assertEqual(len(self.email_adapter.outbox), 2)
        self.assertEqual([item["email"] for item in self.email_adapter.outbox],
                         ["known@example.test", "unknown@example.test"])
        from urllib.parse import parse_qs, urlsplit
        raw_token = parse_qs(urlsplit(self.email_adapter.outbox[0]["url"]).query)["state"][0]
        transaction_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        transaction = self.fx.repos.auth_transactions.get(transaction_hash)
        self.assertIsNotNone(transaction)
        self.assertNotIn("@", transaction.state_data)
        self.assertNotIn(raw_token, repr(transaction))
        self.assertNotIn("known@example.test", json.dumps(json_body(known)))

    def test_email_validation_csrf_and_pre_auth_bound_callback_one_time(self):
        no_csrf = call(self.fx.app, "POST", "/auth/email/start", headers={"origin": "http://testserver"},
                       body=b'{"email":"x@y.test","return_to":"/","intent":"login"}')
        self.assertEqual(no_csrf[0], 403)
        invalid = self.start("bad email@@example.test")
        self.assertEqual(invalid[0], 400)
        for hidden in ("a\u202eb@example.test", "a\u200bb@example.test", "a\u00a0b@example.test"):
            self.assertEqual(self.start(hidden)[0], 400)
        self.assertEqual(self.start("clean@example.test", return_to="/acc\u200bount")[0], 400)
        self.assertEqual(self.fx.repos.auth_transactions.count_active(now=self.fx.clock[0]), 0)
        started = self.start("known@example.test")
        link = self.email_adapter.outbox[-1]["url"]
        from urllib.parse import urlsplit
        path = urlsplit(link).path
        query = urlsplit(link).query.encode()
        _, other_headers, _ = call(self.fx.app, "GET", "/auth/csrf")
        other_cookie = other_headers["set-cookie"].split(";", 1)[0]
        self.assertEqual(call(self.fx.app, "GET", path, headers={"cookie": other_cookie}, query=query)[0], 400)
        success = call(self.fx.app, "GET", path, headers={"cookie": started[3]}, query=query)
        self.assertEqual(success[0], 302)
        self.assertEqual(success[1]["location"], "/account")
        self.assertEqual(call(self.fx.app, "GET", path, headers={"cookie": started[3]}, query=query)[0], 400)

    def test_email_limit_per_address_and_per_ip_sets_retry_after(self):
        for _ in range(5):
            self.assertEqual(self.start("known@example.test")[0], 202)
        limited = self.start("known@example.test")
        self.assertEqual(limited[0], 429)
        self.assertIn("retry-after", limited[1])
        self.assertEqual(json_body(limited)["error"]["code"], "throttled")
        self.assertEqual(self.fx.repos.auth_transactions.count_active(now=self.fx.clock[0]), 5)

        fx2, tmp2 = build_fixture(providers={"email": self.email_adapter})
        self.addCleanup(tmp2.cleanup)
        for i in range(20):
            status, headers, body = call(fx2.app, "GET", "/auth/csrf")
            csrf = json_body((status, headers, body))["csrf_token"]
            cookie = headers["set-cookie"].split(";", 1)[0]
            result = call(fx2.app, "POST", "/auth/email/start",
                          headers={"origin": "http://testserver", "cookie": cookie, "x-csrf-token": csrf},
                          body=json.dumps({"email": f"{i}@example.test", "return_to": "/", "intent": "login"}).encode())
            self.assertEqual(result[0], 202)
        # A deliberately fresh valid token/cookie for the final IP-limited request.
        status, headers, body = call(fx2.app, "GET", "/auth/csrf")
        final = call(fx2.app, "POST", "/auth/email/start",
                     headers={"origin": "http://testserver", "cookie": headers["set-cookie"].split(";", 1)[0],
                              "x-csrf-token": json_body((status, headers, body))["csrf_token"]},
                     body=b'{"email":"over@example.test","return_to":"/","intent":"login"}')
        self.assertEqual(final[0], 429)
        self.assertIn("retry-after", final[1])
        self.assertEqual(fx2.repos.auth_transactions.count_active(now=fx2.clock[0]), 20)


class PurchaseHttpTests(HttpTestCase):
    def test_purchase_list_requires_session_and_details_are_owner_scoped(self):
        fx = self.make()
        self.assertEqual(call(fx.app, "GET", "/purchases")[0], 401)
        own = fx.repos.purchases.list_for_user("u1")[0]
        other = fx.repos.users.create(user_id="u2")
        foreign = fx.repos.purchases.create(other.id, "python-lifetime", "mock", "foreign-p")
        listing = json_body(call(fx.app, "GET", "/purchases", headers=fx.headers()))["purchases"]
        self.assertEqual(listing, [{"id": own.id, "product_id": own.product_id, "state": "pending", "entitled": True}])
        self.assertEqual(json_body(call(fx.app, "GET", f"/purchases/{own.id}", headers=fx.headers())), listing[0])
        self.assertEqual(call(fx.app, "GET", f"/purchases/{foreign.id}", headers=fx.headers())[0], 404)
        self.assertEqual(call(fx.app, "GET", "/purchases/missing", headers=fx.headers())[0], 404)

    def test_purchase_list_is_newest_first_and_capped_at_twenty(self):
        fx = self.make()
        db = Database(fx.path, clock=lambda: fx.clock[0])
        repos = Repositories(db)
        ids = []
        for i in range(22):
            fx.clock[0] += 1
            ids.append(repos.purchases.create("u1", "python-lifetime", "mock", f"cap-{i}").id)
        db.close()
        values = json_body(call(fx.app, "GET", "/purchases", headers=fx.headers()))["purchases"]
        self.assertEqual(len(values), 20)
        self.assertEqual([p["id"] for p in values], list(reversed(ids[-20:])))
        self.assertTrue(all(p["entitled"] is False for p in values))


class ErrorContractTests(HttpTestCase):
    def test_404_and_405_are_json_with_request_id(self):
        fx = self.make()
        for method, path, status in (("GET", "/nope", 404), ("GET", "/logout", 405)):
            code, headers, body = call(fx.app, method, path)
            self.assertEqual(code, status)
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertIn("x-request-id", headers)
            self.assertIn("error", json_body((code, headers, body)))


class CheckoutTests(HttpTestCase):
    def payload(self, **over):
        body = {"product_id": "python-lifetime", "policy_version": "2026-10-16", "acknowledgements": list(CORE_ACK)}
        body.update(over)
        return json.dumps(body).encode()

    def test_checkout_guards(self):
        fx = self.make(payments=FakePayments())
        # no session
        self.assertEqual(call(fx.app, "POST", "/checkout", body=self.payload())[0], 401)
        # no origin
        self.assertEqual(call(fx.app, "POST", "/checkout", headers=fx.headers(origin=False, csrf=True),
                              body=self.payload())[0], 403)
        # no csrf
        self.assertEqual(call(fx.app, "POST", "/checkout", headers=fx.headers(), body=self.payload())[0], 403)
        # client price/time/digest injection is rejected as an unexpected key
        bad = json.dumps({"product_id": "python-lifetime", "policy_version": "2026-10-16",
                          "acknowledgements": list(CORE_ACK), "amount_minor": 1}).encode()
        self.assertEqual(call(fx.app, "POST", "/checkout", headers=fx.headers(csrf=True), body=bad)[0], 400)
        # wrong acknowledgement set
        wrong = self.payload(acknowledgements=["terms"])
        self.assertEqual(call(fx.app, "POST", "/checkout", headers=fx.headers(csrf=True), body=wrong)[0], 400)
        # stale policy version
        stale = self.payload(policy_version="2020-01-01")
        self.assertEqual(call(fx.app, "POST", "/checkout", headers=fx.headers(csrf=True), body=stale)[0], 409)

    def test_checkout_creates_pending_purchase(self):
        payments = FakePayments()
        fx = self.make(payments=payments)
        status, _, body = call(fx.app, "POST", "/checkout", headers=fx.headers(csrf=True), body=self.payload())
        self.assertEqual(status, 200)
        payload = json_body((status, {}, body))
        self.assertEqual(payload["state"], "pending")
        self.assertEqual(payload["redirect_url"], "https://pay.example/checkout/1")
        self.assertEqual(payload["purchase_id"], payments.calls[0][0])
        self.assertTrue(payments.calls)

    def test_checkout_rejects_unsafe_redirect_without_echoing_provider_url(self):
        redirect = "https://pay.example.evil.test/secret?token=do-not-echo"
        fx = self.make(payments=FakePayments(redirect=redirect))
        status, _, body = call(fx.app, "POST", "/checkout", headers=fx.headers(csrf=True), body=self.payload())
        self.assertEqual(status, 500)
        self.assertNotIn(redirect.encode(), body)
        self.assertNotIn(b"do-not-echo", body)
        self.assertIn(b'"code":"server-error"', body)

    def test_checkout_unavailable_without_payments(self):
        fx = self.make()
        status, _, _ = call(fx.app, "POST", "/checkout", headers=fx.headers(csrf=True), body=self.payload())
        self.assertEqual(status, 503)


class WebhookTests(HttpTestCase):
    def test_webhook_disabled_and_invalid(self):
        fx = self.make()
        self.assertEqual(call(fx.app, "POST", "/payments/mock/webhook", body=b"{}")[0], 503)
        fx2 = self.make(payments=FakePayments())
        self.assertEqual(call(fx2.app, "POST", "/payments/mock/webhook", headers={"x-signature": "bad"}, body=b"{}")[0], 400)

    def test_verified_event_grants_entitlement(self):
        class Event:
            state = "paid"
            provider_purchase_id = "prov-webhook"
            revision = 1
            event_id = "evt-1"
            payload_digest = "d" * 64

        payments = FakePayments(webhooks={"good": Event()})
        fx = self.make(payments=payments)
        # A known pending purchase with a matching provider id.
        db = Database(fx.path, clock=lambda: fx.clock[0])
        repos = Repositories(db)
        repos.purchases.create("u1", "python-lifetime", "mock", "prov-webhook")
        db.close()
        status, _, _ = call(fx.app, "POST", "/payments/mock/webhook", headers={"x-signature": "good"}, body=b"raw")
        self.assertEqual(status, 200)
        payload = json_body(call(fx.app, "GET", "/entitlements", headers=fx.headers()))
        self.assertEqual(len(payload["entitlements"]), 2)  # seeded + webhook grant

    def _event(self, **over):
        attrs = dict(state="paid", provider_purchase_id="prov-r", revision=1, event_id="evt-r", payload_digest="f" * 64)
        attrs.update(over)
        return type("Event", (), attrs)()

    def test_unknown_state_is_400_not_500(self):
        fx = self.make(payments=FakePayments(webhooks={"good": self._event(state="weird")}))
        db = Database(fx.path, clock=lambda: fx.clock[0])
        Repositories(db).purchases.create("u1", "python-lifetime", "mock", "prov-r")
        db.close()
        code, _, body = call(fx.app, "POST", "/payments/mock/webhook", headers={"x-signature": "good"}, body=b"raw")
        self.assertEqual(code, 400)

    def test_wrong_provider_path_is_refused(self):
        fx = self.make(payments=FakePayments(webhooks={"good": self._event()}))
        db = Database(fx.path, clock=lambda: fx.clock[0])
        Repositories(db).purchases.create("u1", "python-lifetime", "mock", "prov-r")
        db.close()
        self.assertEqual(call(fx.app, "POST", "/payments/other/webhook", headers={"x-signature": "good"}, body=b"raw")[0], 404)

    def test_late_webhook_for_known_purchase_after_delete_grants_nothing(self):
        fx = self.make(payments=FakePayments(webhooks={"good": self._event()}))
        db = Database(fx.path, clock=lambda: fx.clock[0])
        Repositories(db).purchases.create("u1", "python-lifetime", "mock", "prov-r")
        db.close()
        self.assertEqual(call(fx.app, "POST", "/account/delete", headers=fx.headers(csrf=True))[0], 200)
        self.assertEqual(call(fx.app, "POST", "/payments/mock/webhook", headers={"x-signature": "good"}, body=b"raw")[0], 200)
        db = Database(fx.path, clock=lambda: fx.clock[0])
        repos = Repositories(db)
        self.assertEqual(repos.users.get("u1").status.value, "deleted")
        # Only the seeded grant remains; the late webhook added none.
        self.assertEqual(len(repos.entitlements.list_active_for_user("u1")), 1)
        db.close()


if __name__ == "__main__":
    unittest.main()
