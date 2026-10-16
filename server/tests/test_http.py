import json
import unittest

from server.db import Database
from server.repositories import Repositories
from server.tests.support import CORE_ACK, FakePayments, build_fixture, call, json_body


class HttpTestCase(unittest.TestCase):
    def make(self, **kwargs):
        fixture, tmp = build_fixture(**kwargs)
        self.addCleanup(tmp.cleanup)
        return fixture


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
        self.assertTrue(payments.calls)

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


if __name__ == "__main__":
    unittest.main()
