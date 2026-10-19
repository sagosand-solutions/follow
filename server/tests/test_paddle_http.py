# SPDX-License-Identifier: AGPL-3.0-or-later
"""Paddle configuration, checkout and webhook routes over a scripted injected transport (sandbox/test only)."""
import asyncio
import dataclasses
import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server import paddle_repo
from server import paddle_checkout
from server.app import Services, create_app
from server.config import load_config
from server.db import Database
from server.models import PolicyActivation, PolicyBundle, Product, SetupError, compute_bundle_digest
from server.paddle.types import Adjustment, AuthoritativeState, Item, PaddleOutcomeUnknown
from server.paddle_service import PaddleService
from server.repositories import Repositories
from server.sessions import SessionService
from server.tests.support import CORE_ACK, ORIGIN, build_fixture, call, json_body

SECRET = "synthetic-endpoint-secret-0123456789"
KEY = "pdl_sdbx_apikey_" + "a" * 30
PRICE = "pri_01abcdefghjkmnpqrstvwxyz00"
PRODUCTS = json.dumps({"python-lifetime": {"price_id": PRICE, "quantity": 1, "currency_code": "USD", "total_minor": 1000}})
NOW = 1_700_000_000.0
BASE_ENV = {"APP_ENV": "test", "PUBLIC_ORIGIN": ORIGIN, "DB_PATH": ":memory:", "CSRF_SECRET_KEY": "a1" * 32, "SESSION_SECRET_KEY": "b2" * 32}
PADDLE_ENV = {"PAYMENTS_PROVIDER": "paddle", "PADDLE_ENV": "sandbox", "PADDLE_API_KEY": KEY, "PADDLE_ENDPOINT_SECRET": SECRET,
              "PADDLE_PRODUCTS": PRODUCTS}


def txn(n):
    return f"txn_01abcdefghjkmnpqrstvwxyz{n:02d}"


def sign(raw: bytes, ts: int = int(NOW), secret: str = SECRET) -> str:
    return f"ts={ts};h1=" + hmac.new(secret.encode(), f"{ts}:".encode() + raw, hashlib.sha256).hexdigest()


def envelope(n=1, txn_n=1, kind="transaction.completed", data=None, **over) -> bytes:
    body = {"event_id": f"evt_{n:026d}", "event_type": kind, "occurred_at": "2026-10-19T01:02:03Z", "notification_id": f"ntf_{n:026d}",
            "data": data if data is not None else {"id": txn(txn_n), "status": "completed", "currency_code": "USD",
                                                   "items": [{"price": {"id": PRICE}, "quantity": 1}],
                                                   "details": {"totals": {"grand_total": "1000", "total": "1000"}}}}
    body.update(over)
    return json.dumps(body, separators=(",", ":")).encode()


def raw_call(app, method, path, headers, body=b""):
    """Like support.call but with a RAW header list, so a header can legitimately appear twice."""
    async def run():
        scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"", "scheme": "http",
                 "server": ("testserver", 80), "client": ("t", 1), "root_path": "", "http_version": "1.1",
                 "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
        inbox = [{"type": "http.request", "body": body, "more_body": False}]
        sent = []

        async def receive():
            return inbox.pop(0) if inbox else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
        await app(scope, receive, send)
        return sent[0]["status"], b"".join(m.get("body", b"") for m in sent[1:])
    return asyncio.run(run())


def snapshot(txn_n=1, status="completed", adjustments=()):
    return AuthoritativeState("sandbox", txn(txn_n), status, "USD", {"grand_total": 1000, "total": 1000}, (Item(PRICE, 1),),
                              tuple(adjustments), 0.0, True)


class Transport:
    def __init__(self):
        self.create_calls, self.get_calls, self.create_results, self.get_results = [], [], [], []

    def create_transaction(self, price_id, quantity, op_id):
        self.create_calls.append((price_id, quantity, op_id))
        out = self.create_results.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out

    def get_transaction(self, txn_id):
        self.get_calls.append(txn_id)
        out = self.get_results.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


class ConfigTests(unittest.TestCase):
    def test_default_is_mock_and_nothing_paddle_is_on(self):
        settings = load_config(BASE_ENV)
        self.assertEqual((settings.payments_provider, settings.paddle_env, settings.paddle_endpoint_secret, settings.paddle_products),
                         ("mock", None, None, {}))

    def test_a_valid_sandbox_configuration_loads_and_the_secret_is_redacted_everywhere(self):
        settings = load_config({**BASE_ENV, **PADDLE_ENV})
        self.assertEqual((settings.payments_provider, settings.paddle_env), ("paddle", "sandbox"))
        self.assertEqual(settings.paddle_products["python-lifetime"].price_id, PRICE)
        for text in (repr(settings), str(settings.redacted()), repr(settings.redacted())):
            self.assertNotIn(SECRET, text)
            self.assertNotIn(KEY, text)
        self.assertFalse(hasattr(settings, "paddle_api_key"))                          # the key is never stored

    def test_every_unsafe_combination_is_a_setup_error_that_never_echoes_a_secret(self):
        bad = {
            "production": {"APP_ENV": "production", "PUBLIC_ORIGIN": "https://x.example", "DB_PATH": "/x/db", "TRUSTED_PROXIES": ""},
            "live_is_not_supported_in_this_step": {"PADDLE_ENV": "live", "PADDLE_API_KEY": "pdl_live_apikey_" + "b" * 30},
            "missing_env": {"PADDLE_ENV": ""}, "unknown_env": {"PADDLE_ENV": "staging"},
            "key_for_the_other_environment": {"PADDLE_API_KEY": "pdl_live_apikey_" + "b" * 30},
            "no_key": {"PADDLE_API_KEY": ""}, "short_secret": {"PADDLE_ENDPOINT_SECRET": "short"},
            "secret_equals_key": {"PADDLE_ENDPOINT_SECRET": KEY},
            "no_products": {"PADDLE_PRODUCTS": ""}, "products_not_json": {"PADDLE_PRODUCTS": "{" + SECRET},
            "products_array": {"PADDLE_PRODUCTS": "[]"}, "products_empty": {"PADDLE_PRODUCTS": "{}"},
            "duplicate_keys": {"PADDLE_PRODUCTS": '{"p": {"price_id": "%s", "quantity": 1, "currency_code": "USD"}, "p": {}}' % PRICE},
            "nan": {"PADDLE_PRODUCTS": '{"p": {"price_id": "%s", "quantity": 1, "currency_code": "USD", "total_minor": NaN}}' % PRICE},
            "extra_field": {"PADDLE_PRODUCTS": '{"p": {"price_id": "%s", "quantity": 1, "currency_code": "USD", "role": "admin"}}' % PRICE},
            "missing_field": {"PADDLE_PRODUCTS": '{"p": {"price_id": "%s", "quantity": 1}}' % PRICE},
            "quantity_two": {"PADDLE_PRODUCTS": '{"p": {"price_id": "%s", "quantity": 2, "currency_code": "USD"}}' % PRICE},
            "quantity_bool": {"PADDLE_PRODUCTS": '{"p": {"price_id": "%s", "quantity": true, "currency_code": "USD"}}' % PRICE},
            "bad_price": {"PADDLE_PRODUCTS": '{"p": {"price_id": "x", "quantity": 1, "currency_code": "USD"}}'},
            "amount_bool": {"PADDLE_PRODUCTS": '{"p": {"price_id": "%s", "quantity": 1, "currency_code": "USD", "total_minor": true}}' % PRICE},
            "unknown_provider": {"PAYMENTS_PROVIDER": "stripe"},
        }
        for name, change in bad.items():
            with self.subTest(case=name), self.assertRaises(SetupError) as caught:
                load_config({**BASE_ENV, **PADDLE_ENV, **change})
            self.assertNotIn(SECRET, str(caught.exception))
            self.assertNotIn(KEY, str(caught.exception))

    def test_paddle_settings_without_the_paddle_provider_are_refused(self):
        for name in ("PADDLE_ENV", "PADDLE_ENDPOINT_SECRET", "PADDLE_API_KEY", "PADDLE_PRODUCTS"):
            with self.subTest(name=name), self.assertRaises(SetupError):
                load_config({**BASE_ENV, name: PADDLE_ENV[name]})

    def test_the_app_needs_a_transport_and_refuses_one_that_is_not_wanted(self):
        paddle = load_config({**BASE_ENV, **PADDLE_ENV})
        mock = load_config(BASE_ENV)
        factory = lambda: None
        with self.assertRaises(SetupError):
            create_app(paddle, Services(open_database=factory))
        with self.assertRaises(SetupError):
            create_app(mock, Services(open_database=factory, paddle_transport=Transport()))
        with self.assertRaises(SetupError):
            create_app(paddle, Services(open_database=factory, paddle_transport=Transport(), payments=object()))
        create_app(paddle, Services(open_database=factory, paddle_transport=Transport()))
        create_app(mock, Services(open_database=factory))

    def test_injected_settings_are_checked_at_app_start_not_only_by_the_env_parser(self):
        paddle = load_config({**BASE_ENV, **PADDLE_ENV})
        mock = load_config(BASE_ENV)
        product = next(iter(paddle.paddle_products.values()))
        bad = {
            "live": dataclasses.replace(paddle, paddle_env="live"), "no_env": dataclasses.replace(paddle, paddle_env=None),
            "no_secret": dataclasses.replace(paddle, paddle_endpoint_secret=None),
            "short_secret": dataclasses.replace(paddle, paddle_endpoint_secret="short"),
            "binary_secret": dataclasses.replace(paddle, paddle_endpoint_secret=b"x" * 32),
            "no_products": dataclasses.replace(paddle, paddle_products={}),
            "not_a_product": dataclasses.replace(paddle, paddle_products={"python-lifetime": {"price_id": PRICE}}),
            "quantity_two": dataclasses.replace(paddle, paddle_products={"python-lifetime": dataclasses.replace(product, quantity=2)}),
            "non_string_key": dataclasses.replace(paddle, paddle_products={1: product}),
        }
        for name, settings in bad.items():
            with self.subTest(case=name), self.assertRaises(SetupError) as caught:
                create_app(settings, Services(open_database=lambda: None, paddle_transport=Transport()))
            self.assertNotIn(SECRET, str(caught.exception))
        leftovers = {
            "env": dataclasses.replace(mock, paddle_env="sandbox"), "secret": dataclasses.replace(mock, paddle_endpoint_secret=SECRET),
            "products": dataclasses.replace(mock, paddle_products=paddle.paddle_products),
            "other_provider": dataclasses.replace(mock, payments_provider="stripe"),
        }
        for name, settings in leftovers.items():
            with self.subTest(case=name), self.assertRaises(SetupError):
                create_app(settings, Services(open_database=lambda: None))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "a.db")
        self.clock = [NOW]
        self.settings = load_config({**BASE_ENV, **PADDLE_ENV, "DB_PATH": self.path})
        db = Database(self.path, clock=lambda: self.clock[0])
        db.migrate()
        repos = Repositories(db)
        repos.products.upsert(Product("python-lifetime", "Python", "USD", 1000, "python", True))
        repos.products.upsert(Product("other", "Other", "USD", 1000, "other", True))
        repos.users.create(user_id="u1")
        digest = compute_bundle_digest(product_id="python-lifetime", access_scope="python", locale="en", version="2026-10-16",
                                       terms_sha256="a" * 64, refunds_sha256="b" * 64, privacy_sha256="c" * 64,
                                       required_acknowledgements=CORE_ACK, effective_at=999_000.0)
        repos.policies.insert(PolicyBundle("pb-1", "python-lifetime", "python", "en", "2026-10-16", "a" * 64, "b" * 64, "c" * 64,
                                           CORE_ACK, digest, 999_000.0))
        credentials = SessionService(repos, self.settings, clock=lambda: self.clock[0]).issue("u1")
        self.cookie, self.csrf = credentials.cookie_value, credentials.csrf_token
        db.close()
        self.transport = Transport()
        self.services = Services(
            open_database=lambda: Database(self.path, clock=lambda: self.clock[0]), paddle_transport=self.transport,
            policy_activations=(PolicyActivation("python-lifetime", "python", "en", "pb-1", approved=True),),
            checkout_policy={"version": "2026-10-16", "documents": ["terms", "refunds", "privacy"], "required_acknowledgements": list(CORE_ACK)},
            clock=lambda: self.clock[0])
        self.app = create_app(self.settings, self.services)

    def db(self):
        return Database(self.path, clock=lambda: self.clock[0])

    def rows(self, sql, args=()):
        db = self.db()
        try:
            return [tuple(r) for r in db.connect().execute(sql, args)]
        finally:
            db.close()

    def headers(self, *, csrf=True, origin=True, cookie=True):
        h = {}
        if cookie:
            h["cookie"] = f"session={self.cookie}"
        if csrf:
            h["x-csrf-token"] = self.csrf
        if origin:
            h["origin"] = ORIGIN
        return h

    def checkout(self, **over):
        body = {"product_id": "python-lifetime", "policy_version": "2026-10-16", "acknowledgements": list(CORE_ACK)}
        body.update(over)
        return call(self.app, "POST", "/checkout", headers=self.headers(), body=json.dumps(body).encode())

    def webhook(self, raw, header=None, path="/payments/paddle-sandbox/webhook"):
        return call(self.app, "POST", path, headers={"paddle-signature": header or sign(raw)}, body=raw)

    def snapshot_of_tables(self):
        return [self.rows(f"SELECT * FROM {t}") for t in ("purchases", "paddle_operations", "paddle_events", "paddle_reconcile",
                                                          "entitlements", "payment_events", "purchase_transitions")]

    def service(self):
        db = self.db()
        self.addCleanup(db.close)
        return PaddleService(Repositories(db), self.transport, self.settings.paddle_products, environment="sandbox",
                             clock=lambda: self.clock[0])


class WebhookTests(Base):
    def bound(self, n=1):
        self.transport.create_results.append(txn(n))
        status, _, body = self.checkout()
        self.assertEqual(status, 200)
        return json_body((status, _, body))["purchase_id"]

    def test_rejections_write_nothing_and_say_little(self):
        raw = envelope()
        good = sign(raw)
        before = self.snapshot_of_tables()
        cases = {
            "no_header": (400, lambda: call(self.app, "POST", "/payments/paddle-sandbox/webhook", headers={}, body=raw)),
            "bad_signature": (400, lambda: self.webhook(raw, sign(raw, secret="another-synthetic-secret-abcdefghij"))),
            "stale": (400, lambda: self.webhook(raw, sign(raw, ts=int(NOW) - 3600))),
            "future": (400, lambda: self.webhook(raw, sign(raw, ts=int(NOW) + 3600))),
            "mutated_body": (400, lambda: self.webhook(raw + b" ", good)),
            "unsigned_garbage": (400, lambda: self.webhook(b"not json", sign(b"not json"))),
            "empty_body": (400, lambda: self.webhook(b"", sign(b""))),
            "malformed_header": (400, lambda: self.webhook(raw, "ts=1;h1=zz")),
            "wrong_environment_path": (404, lambda: self.webhook(raw, path="/payments/paddle-live/webhook")),
            "mock_path": (404, lambda: self.webhook(raw, path="/payments/mock/webhook")),
            "unknown_provider": (404, lambda: self.webhook(raw, path="/payments/paddle/webhook")),
            "substring_provider": (404, lambda: self.webhook(raw, path="/payments/paddle-sandbox-x/webhook")),
        }
        for name, (status, run) in cases.items():
            with self.subTest(case=name):
                response = run()
                self.assertEqual(response[0], status, response[2][:120])
                self.assertNotIn(b"txn_", response[2])
                self.assertNotIn(SECRET.encode(), response[2])
        self.assertEqual(self.snapshot_of_tables(), before)

    def test_two_signature_fields_are_refused_whatever_their_casing(self):
        raw = envelope()
        good = sign(raw)
        before = self.snapshot_of_tables()
        for headers in ([("Paddle-Signature", good), ("paddle-signature", good)], [("Paddle-Signature", good), ("PADDLE-SIGNATURE", "ts=1;h1=" + "0" * 64)],
                        [("paddle-signature", good), ("Paddle-Signature", good)]):
            with self.subTest(headers=[h[0] for h in headers]):
                status, _ = raw_call(self.app, "POST", "/payments/paddle-sandbox/webhook", headers, raw)
                self.assertEqual(status, 400)
        self.assertEqual(raw_call(self.app, "POST", "/payments/paddle-sandbox/webhook", [("Paddle-Signature", good)], raw)[0], 200)
        self.assertNotEqual(self.snapshot_of_tables(), before)

    def test_a_too_large_body_is_413_before_anything_is_parsed(self):
        raw = b"x" * (self.settings.limits.webhook_body_bytes + 1)
        before = self.snapshot_of_tables()
        self.assertEqual(self.webhook(raw, sign(raw))[0], 413)
        self.assertEqual(self.snapshot_of_tables(), before)

    def test_the_mock_route_never_accepts_a_paddle_name_and_paddle_is_off_when_not_configured(self):
        fx, tmp = build_fixture()
        self.addCleanup(tmp.cleanup)
        raw = envelope()
        for path in ("/payments/paddle-sandbox/webhook", "/payments/paddle-live/webhook"):
            self.assertEqual(call(fx.app, "POST", path, headers={"paddle-signature": sign(raw)}, body=raw)[0], 404)

    def test_a_valid_event_stores_evidence_and_queues_a_job_but_never_fetches_or_grants(self):
        purchase_id = self.bound()
        before_calls = len(self.transport.get_calls)
        status, _, body = self.webhook(envelope())
        self.assertEqual((status, json_body((status, _, body))), (200, {"ok": True}))
        self.assertEqual(self.rows("SELECT event_type, state FROM paddle_events"), [("transaction.completed", "pending")])
        self.assertEqual(self.rows("SELECT state FROM paddle_reconcile WHERE purchase_id = ?", (purchase_id,)), [("due",)])
        self.assertEqual(self.rows("SELECT state, revision FROM purchases WHERE id = ?", (purchase_id,)), [("pending", 0)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM entitlements"), [(0,)])
        self.assertEqual(len(self.transport.get_calls), before_calls)                       # the route never fetched

    def test_duplicate_is_200_and_requeues_conflict_is_409_without_mutation_and_info_events_leave_no_row(self):
        purchase_id = self.bound()
        self.assertEqual(self.webhook(envelope())[0], 200)
        row = self.rows("SELECT pending_seq FROM paddle_reconcile")
        self.assertEqual(self.webhook(envelope())[0], 200)                                  # duplicate: acknowledged
        self.assertGreater(self.rows("SELECT pending_seq FROM paddle_reconcile")[0][0], row[0][0])   # and queued again, idempotently
        self.assertEqual(self.rows("SELECT COUNT(*) FROM paddle_events"), [(1,)])
        before = self.snapshot_of_tables()
        changed = envelope(occurred_at="2026-10-19T01:02:04Z")                              # same event id, other body
        status, _, body = self.webhook(changed)
        self.assertEqual((status, json_body((status, _, body))["error"]["code"]), (409, "conflict"))
        self.assertEqual(self.snapshot_of_tables(), before)                                  # evidence, job and purchase untouched
        info = envelope(n=2, kind="transaction.updated")
        self.assertEqual(self.webhook(info)[0], 200)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM paddle_events"), [(1,)])

    def test_an_event_before_the_binding_is_kept_as_an_orphan_and_acknowledged(self):
        self.assertEqual(self.webhook(envelope(txn_n=7))[0], 200)
        self.assertEqual(self.rows("SELECT transaction_id FROM paddle_events"), [(txn(7),)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM paddle_reconcile"), [(0,)])

    def test_a_full_queue_is_503_with_nothing_written(self):
        old = paddle_repo.ORPHAN_PER_TXN_MAX
        paddle_repo.ORPHAN_PER_TXN_MAX = 2
        self.addCleanup(setattr, paddle_repo, "ORPHAN_PER_TXN_MAX", old)
        for n in (1, 2):
            self.assertEqual(self.webhook(envelope(n=n, txn_n=9))[0], 200)
        before = self.snapshot_of_tables()
        status, _, body = self.webhook(envelope(n=3, txn_n=9))
        self.assertEqual((status, json_body((status, _, body))["error"]["code"]), (503, "unavailable"))
        self.assertEqual(self.snapshot_of_tables(), before)

    def test_our_own_operational_failure_is_503_not_a_malformed_request_while_a_bad_caller_stays_400(self):
        raw = envelope()
        good = sign(raw)
        before = self.snapshot_of_tables()
        for bad_clock in (float("nan"), float("inf")):
            with self.subTest(clock=bad_clock):
                self.clock[0] = bad_clock
                status, _, body = self.webhook(raw, good)
                self.assertEqual((status, json_body((status, _, body))["error"]["code"]), (503, "unavailable"))
        self.clock[0] = NOW
        self.assertEqual(self.snapshot_of_tables(), before)                                 # no evidence and no job
        for name, header in (("signature", sign(raw, secret="another-synthetic-secret-abcdefghij")), ("stale", sign(raw, ts=int(NOW) - 3600)),
                             ("malformed", "ts=1;h1=zz")):
            with self.subTest(caller=name):
                self.assertEqual(self.webhook(raw, header)[0], 400)

    def test_a_storage_failure_is_503_and_rolls_back(self):
        self.bound()
        db = self.db()
        db.connect().execute("CREATE TRIGGER boom BEFORE INSERT ON paddle_reconcile BEGIN SELECT RAISE(ABORT, 'x'); END")
        db.close()
        before = self.rows("SELECT COUNT(*) FROM paddle_events")
        self.assertEqual(self.webhook(envelope())[0], 503)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM paddle_events"), before)           # the evidence did not survive alone


class CheckoutTests(Base):
    def test_a_bound_transaction_gives_the_overlay_dto_and_the_server_chose_everything(self):
        self.transport.create_results.append(txn(1))
        status, _, body = self.checkout()
        data = json_body((status, _, body))
        self.assertEqual(status, 200)
        self.assertEqual(set(data), {"purchase_id", "state", "provider", "checkout"})
        self.assertEqual((data["state"], data["provider"], data["checkout"]), ("pending", "paddle", {"mode": "overlay", "transaction_id": txn(1)}))
        self.assertNotIn("redirect_url", data)
        self.assertEqual(self.transport.create_calls[0][:2], (PRICE, 1))                     # the SERVER's price and quantity
        purchase = self.rows("SELECT provider, state, user_id FROM purchases")
        self.assertEqual(purchase, [("paddle-sandbox", "pending", "u1")])
        self.assertEqual(self.rows("SELECT state, environment, provider_txn_id FROM paddle_operations"), [("bound", "sandbox", txn(1))])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM purchase_consents"), [(1,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM entitlements"), [(0,)])             # a checkout never grants

    def test_the_same_guards_as_the_mock_flow_and_nothing_is_created_when_refused(self):
        before = self.snapshot_of_tables()
        cases = {
            "not_signed_in": (401, lambda: call(self.app, "POST", "/checkout", headers=self.headers(cookie=False, csrf=False),
                                                body=json.dumps({"product_id": "python-lifetime", "policy_version": "2026-10-16",
                                                                 "acknowledgements": list(CORE_ACK)}).encode())),
            "no_csrf": (403, lambda: call(self.app, "POST", "/checkout", headers=self.headers(csrf=False), body=b"{}")),
            "no_origin": (403, lambda: call(self.app, "POST", "/checkout", headers=self.headers(origin=False), body=b"{}")),
            "stale_policy": (409, lambda: self.checkout(policy_version="2026-01-01")),
            "wrong_acknowledgements": (400, lambda: self.checkout(acknowledgements=["terms"])),
            "unknown_product": (400, lambda: self.checkout(product_id="nope")),
            "client_price": (400, lambda: self.checkout(price_id="pri_01zzzzzzzzzzzzzzzzzzzzzzzz")),
            "client_amount": (400, lambda: self.checkout(amount_minor=1)),
            "client_transaction": (400, lambda: self.checkout(transaction_id=txn(5))),
            "client_owner": (400, lambda: self.checkout(user_id="u2")),
            "client_environment": (400, lambda: self.checkout(environment="live")),
            "not_sold_by_paddle": (503, lambda: self.checkout(product_id="other")),     # active product, but not in the server's catalog
        }
        for name, (status, run) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(run()[0], status)
        self.assertEqual(self.transport.create_calls, [])
        self.assertEqual(self.snapshot_of_tables(), before)

    def test_a_deleted_or_inactive_user_or_product_is_refused_like_the_mock_flow(self):
        db = self.db()
        repos = Repositories(db)
        repos.products.upsert(Product("python-lifetime", "Python", "USD", 1000, "python", False))
        db.close()
        self.assertEqual(self.checkout()[0], 400)
        db = self.db()
        Repositories(db).products.upsert(Product("python-lifetime", "Python", "USD", 1000, "python", True))
        Repositories(db).users.mark_deleted("u1")
        db.close()
        self.assertEqual(self.checkout()[0], 401)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM purchases"), [(0,)])
        self.assertEqual(self.transport.create_calls, [])

    def test_every_failure_after_the_send_is_a_503_without_provider_ids_and_never_retried(self):
        # This exercises the per-failure send contract, not the checkout cap, so raise the cap for the run: without it a
        # fourth open checkout for the same user would correctly be a 429 (covered by the cap tests).
        failures = [PaddleOutcomeUnknown("timeout"), RuntimeError("secret=abc"), "not-a-txn", None]
        saved_cap = paddle_checkout.MAX_OPEN_CHECKOUTS
        paddle_checkout.MAX_OPEN_CHECKOUTS = len(failures) + 1
        try:
            for index, failure in enumerate(failures, start=1):
                with self.subTest(failure=repr(failure)[:20]):
                    self.transport.create_results.append(failure)
                    status, _, body = self.checkout()
                    self.assertEqual(status, 503)
                    self.assertNotIn(b"txn_", body)
                    self.assertEqual(len(self.transport.create_calls), index)                 # one call per user-started purchase
        finally:
            paddle_checkout.MAX_OPEN_CHECKOUTS = saved_cap
        self.assertEqual(self.rows("SELECT state FROM paddle_operations"), [("unknown",)] * len(failures))
        self.assertEqual(self.rows("SELECT state FROM purchases"), [("pending",)] * len(failures))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM purchase_consents"), [(len(failures),)])   # a new start = a new consent
        self.assertEqual(self.rows("SELECT COUNT(*) FROM entitlements"), [(0,)])

    def test_a_failure_in_the_first_intent_transaction_is_503_with_nothing_written_and_no_remote_call(self):
        db = self.db()
        db.connect().execute("CREATE TRIGGER boom BEFORE INSERT ON paddle_operations BEGIN SELECT RAISE(ABORT, 'x'); END")
        db.close()
        self.transport.create_results.append(txn(1))
        status, _, body = self.checkout()
        self.assertEqual((status, json_body((status, _, body))["error"]["code"]), (503, "unavailable"))
        self.assertNotIn(b"txn_", body)
        self.assertEqual(self.transport.create_calls, [])                                  # the intent never committed: nothing sent
        for table in ("purchases", "purchase_consents", "paddle_operations", "entitlements"):
            self.assertEqual(self.rows(f"SELECT COUNT(*) FROM {table}"), [(0,)], table)    # purchase and consent rolled back too
        db = self.db()
        db.connect().execute("DROP TRIGGER boom")
        db.close()
        self.assertEqual(self.checkout()[0], 200)                                           # and the route recovers afterwards

    def test_a_storage_failure_after_the_intent_commit_is_503_and_makes_no_remote_call(self):
        db = self.db()
        db.connect().execute("CREATE TRIGGER boom BEFORE UPDATE ON paddle_operations BEGIN SELECT RAISE(ABORT, 'x'); END")
        db.close()
        self.transport.create_results.append(txn(1))
        status, _, body = self.checkout()
        self.assertEqual((status, json_body((status, _, body))["error"]["code"]), (503, "unavailable"))
        self.assertNotIn(b"txn_", body)
        self.assertEqual(self.transport.create_calls, [])                                  # the send was never recorded, so never made
        self.assertEqual(self.rows("SELECT state FROM paddle_operations"), [("planned",)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM entitlements"), [(0,)])

    def test_a_storage_failure_after_the_send_is_503_with_one_remote_call_and_the_committed_intent_kept(self):
        db = self.db()
        db.connect().execute("CREATE TRIGGER boom BEFORE UPDATE ON paddle_operations WHEN NEW.state = 'bound' BEGIN SELECT RAISE(ABORT, 'x'); END")
        db.close()
        self.transport.create_results.append(txn(1))
        status, _, body = self.checkout()
        self.assertEqual((status, json_body((status, _, body))["error"]["code"]), (503, "unavailable"))
        self.assertNotIn(b"txn_", body)
        self.assertEqual(len(self.transport.create_calls), 1)                              # sent once, never silently re-sent
        self.assertEqual(self.rows("SELECT state FROM paddle_operations"), [("unknown",)])   # the unknown path: never re-sent, left to reconcile
        self.assertEqual(self.rows("SELECT state FROM purchases"), [("pending",)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM entitlements"), [(0,)])

    def test_an_unopenable_database_is_503_on_the_paddle_path(self):
        def broken():
            raise sqlite3.OperationalError("unable to open database file")
        app = create_app(self.settings, dataclasses.replace(self.services, open_database=broken))
        body = {"product_id": "python-lifetime", "policy_version": "2026-10-16", "acknowledgements": list(CORE_ACK)}
        status, _, raw = call(app, "POST", "/checkout", headers=self.headers(), body=json.dumps(body).encode())
        self.assertEqual((status, json_body((status, _, raw))["error"]["code"]), (503, "unavailable"))
        self.assertEqual(self.transport.create_calls, [])

    def test_the_remote_call_never_runs_inside_a_transaction(self):
        seen = []
        original = self.transport.create_transaction

        def observe(price_id, quantity, op_id):
            other = self.db()
            try:
                seen.append(other.connect().execute("SELECT state FROM paddle_operations WHERE id = ?", (op_id,)).fetchone()[0])
                seen.append(other.connect().execute("SELECT COUNT(*) FROM purchase_consents").fetchone()[0])
            finally:
                other.close()
            return original(price_id, quantity, op_id)
        self.transport.create_transaction = observe
        self.transport.create_results.append(txn(1))
        self.assertEqual(self.checkout()[0], 200)
        self.assertEqual(seen, ["request_sent", 1])                  # purchase, consent and the committed send are visible to others


class EndToEndTests(Base):
    def entitled(self):
        status, _, body = call(self.app, "GET", "/entitlements", headers=self.headers(csrf=False, origin=False))
        self.assertEqual(status, 200)
        return json_body((status, _, body))

    def test_checkout_then_webhook_then_reconcile_grants_and_a_refund_revokes(self):
        self.transport.create_results.append(txn(1))
        status, _, body = self.checkout()
        purchase_id = json_body((status, _, body))["purchase_id"]
        self.assertEqual(self.entitled()["entitlements"], [])
        self.assertEqual(self.webhook(envelope(n=1))[0], 200)
        self.assertEqual(self.entitled()["entitlements"], [])                                # the webhook alone grants nothing
        self.transport.get_results.append(snapshot())
        self.assertEqual(self.service().reconcile_due(5), [(purchase_id, "applied:deleted")])
        self.assertEqual([e["product_id"] for e in self.entitled()["entitlements"]], ["python-lifetime"])
        purchase = call(self.app, "GET", f"/purchases/{purchase_id}", headers=self.headers(csrf=False, origin=False))
        self.assertEqual(json_body(purchase)["state"], "paid")
        refund = Adjustment("adj_" + "1" * 26, "refund", "approved", "full", 1000, "USD")
        adjustment = envelope(n=2, kind="adjustment.created", data={"id": "adj_" + "1" * 26, "transaction_id": txn(1), "action": "refund",
                                                                   "status": "approved", "type": "full", "totals": {"total": "1000", "currency_code": "USD"}})
        self.assertEqual(self.webhook(adjustment)[0], 200)
        self.transport.get_results.append(snapshot(adjustments=[refund]))
        self.assertEqual(self.service().reconcile_due(5), [(purchase_id, "applied:deleted")])
        self.assertEqual(self.entitled()["entitlements"], [])
        self.assertEqual(json_body(call(self.app, "GET", f"/purchases/{purchase_id}", headers=self.headers(csrf=False, origin=False)))["state"], "refunded")

    def test_a_forged_or_replayed_completed_event_without_a_matching_snapshot_never_grants(self):
        self.transport.create_results.append(txn(1))
        self.checkout()
        self.assertEqual(self.webhook(envelope(n=1))[0], 200)
        self.transport.get_results.append(snapshot(status="draft"))                          # the provider says it is NOT completed
        self.service().reconcile_due(5)
        self.assertEqual(self.entitled()["entitlements"], [])
        self.assertEqual(self.rows("SELECT state FROM purchases"), [("pending",)])


if __name__ == "__main__":
    unittest.main()
