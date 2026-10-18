# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cap on open Paddle checkouts per authenticated user and product, plus narrow reuse.

Negative tests first: the cap must not be bypassable by legacy rows, a full quota must not hide a reusable
checkout, and a refusal must write nothing and make no remote call. Synthetic sandbox configuration, an
injected scripted transport, no network and no secrets.
"""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from server.app import Services, create_app
from server.config import load_config
from server.db import Database
from server.models import PolicyActivation, PolicyBundle, Product, PurchaseState, compute_bundle_digest
from server.paddle_checkout import (
    MAX_OPEN_CHECKOUTS,
    REUSE_WINDOW_SECONDS,
    CheckoutContext,
    _positive_int,
    build_checkout_context,
    reserve_or_reuse_checkout,
)
from server.paddle_repo import PaddleRepository
from server.paddle.types import PaddleOutcomeUnknown
from server.paddle_service import PaddleProduct, PaddleService
from server.repositories import Repositories
from server.sessions import SessionService
from server.tests.support import CORE_ACK, ORIGIN, call, json_body

SECRET = "synthetic-endpoint-secret-0123456789"
KEY = "pdl_sdbx_apikey_" + "a" * 30
PRICE = "pri_01abcdefghjkmnpqrstvwxyz00"
PRICE2 = "pri_01abcdefghjkmnpqrstvwxyz02"
NOW = 1_700_000_000.0
VERSION = "2026-10-16"
PRODUCTS = json.dumps({
    "python-lifetime": {"price_id": PRICE, "quantity": 1, "currency_code": "USD", "total_minor": 1000},
    "other": {"price_id": PRICE2, "quantity": 1, "currency_code": "USD", "total_minor": 2000},
})
BASE_ENV = {"APP_ENV": "test", "PUBLIC_ORIGIN": ORIGIN, "DB_PATH": ":memory:",
            "CSRF_SECRET_KEY": "a1" * 32, "SESSION_SECRET_KEY": "b2" * 32}
PADDLE_ENV = {"PAYMENTS_PROVIDER": "paddle", "PADDLE_ENV": "sandbox", "PADDLE_API_KEY": KEY,
              "PADDLE_ENDPOINT_SECRET": SECRET, "PADDLE_PRODUCTS": PRODUCTS}


def txn(n):
    return f"txn_01abcdefghjkmnpqrstvwxyz{n:02d}"


def bundle_for(product_id, scope, version=VERSION, terms="a", bundle_id=None):
    return PolicyBundle(bundle_id or f"pb-{product_id}", product_id, scope, "en", version, terms * 64, "b" * 64, "c" * 64, CORE_ACK,
                        compute_bundle_digest(product_id=product_id, access_scope=scope, locale="en", version=version,
                                              terms_sha256=terms * 64, refunds_sha256="b" * 64, privacy_sha256="c" * 64,
                                              required_acknowledgements=CORE_ACK, effective_at=999_000.0), 999_000.0)


class Transport:
    """A scripted transport that can also block every create, to hold reservations in flight."""

    def __init__(self):
        self.create_calls, self.create_results, self.get_calls, self.get_results = [], [], [], []
        self.block = False
        self._lock = threading.Lock()
        self._arrived = 0
        self.reached = threading.Event()
        self.gate = threading.Event()

    def create_transaction(self, price_id, quantity, op_id):
        with self._lock:
            self.create_calls.append((price_id, quantity, op_id))
            self._arrived += 1
            arrived = self._arrived
        if self.block:
            if arrived >= 3:
                self.reached.set()
            if not self.gate.wait(20):
                raise AssertionError("transport gate timed out")
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


class CapBase(unittest.TestCase):
    def setUp(self):
        self._reset()

    def _reset(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "a.db")
        self.clock = [NOW]
        self.settings = load_config({**BASE_ENV, **PADDLE_ENV, "DB_PATH": self.path})
        db = Database(self.path, clock=lambda: self.clock[0])
        db.migrate()
        repos = Repositories(db)
        for pid, scope, amount in (("python-lifetime", "python", 1000), ("other", "other", 2000)):
            repos.products.upsert(Product(pid, pid, "USD", amount, scope, True))
        for uid in ("u1", "u2"):
            repos.users.create(user_id=uid)
        self.bundles = {"python-lifetime": bundle_for("python-lifetime", "python"),
                        "other": bundle_for("other", "other", terms="d")}
        for bundle in self.bundles.values():
            repos.policies.insert(bundle)
        self.credentials = {}
        for uid in ("u1", "u2"):
            self.credentials[uid] = SessionService(repos, self.settings, clock=lambda: self.clock[0]).issue(uid)
        db.close()
        self.transport = Transport()
        self.services = self._services(self._activations())
        self.app = create_app(self.settings, self.services)

    def _activations(self, *, bundle_ids=None, version=VERSION):
        bundle_ids = bundle_ids or {"python-lifetime": "pb-python-lifetime", "other": "pb-other"}
        return tuple(PolicyActivation(pid, "python" if pid == "python-lifetime" else "other", "en", bid, approved=True)
                     for pid, bid in bundle_ids.items())

    def _services(self, activations, policy_version=VERSION):
        return Services(open_database=lambda: Database(self.path, clock=lambda: self.clock[0]),
                        paddle_transport=self.transport, policy_activations=activations,
                        checkout_policy={"version": policy_version, "documents": ["terms", "refunds", "privacy"],
                                         "required_acknowledgements": list(CORE_ACK)},
                        clock=lambda: self.clock[0])

    def db(self):
        return Database(self.path, clock=lambda: self.clock[0])

    def rows(self, sql, args=()):
        db = self.db()
        try:
            return [tuple(r) for r in db.connect().execute(sql, args)]
        finally:
            db.close()

    def headers(self, user="u1", *, csrf=True, origin=True, cookie=True):
        creds = self.credentials[user]
        h = {}
        if cookie:
            h["cookie"] = f"session={creds.cookie_value}"
        if csrf:
            h["x-csrf-token"] = creds.csrf_token
        if origin:
            h["origin"] = ORIGIN
        return h

    def checkout(self, user="u1", product="python-lifetime", app=None, **over):
        body = {"product_id": product, "policy_version": VERSION, "acknowledgements": list(CORE_ACK)}
        body.update(over)
        return call(app or self.app, "POST", "/checkout", headers=self.headers(user), body=json.dumps(body).encode())

    def open_states(self):
        return self.rows("SELECT state FROM paddle_operations ORDER BY rowid")

    def http_context(self, user="u1", product_id="python-lifetime"):
        """Exactly the context the route would build for this user/product (for direct-DB setups)."""
        db = self.db()
        try:
            product = Repositories(db).products.get(product_id)
        finally:
            db.close()
        return build_checkout_context(environment="sandbox", user_id=user, product=product,
                                      bundle=self.bundles[product_id], acknowledgements=CORE_ACK,
                                      paddle_product=self.settings.paddle_products[product_id])

    def counts(self):
        return tuple(self.rows(f"SELECT COUNT(*) FROM {t}")[0][0]
                     for t in ("purchases", "purchase_consents", "paddle_operations", "paddle_checkout_context"))

    def set_product(self, product):
        db = self.db()
        try:
            Repositories(db).products.upsert(product)
        finally:
            db.close()

    def replace_consent(self, purchase_id, dimension):
        """Rewrite a purchase's consent so it differs from the stored context in exactly one dimension."""
        bundle = self.bundles["python-lifetime"]
        bundle_id, digest, ack = bundle.id, bundle.bundle_digest, sorted(CORE_ACK)
        if dimension == "bundle_id":
            bundle_id = "pb-other"
        elif dimension == "bundle_digest":
            digest = "d" * 64
        else:
            ack = ["terms"]                                  # a strict subset: set differs from CORE_ACK
        db = self.db()
        try:
            db.connect().execute("DELETE FROM purchase_consents WHERE purchase_id = ?", (purchase_id,))
            db.connect().execute("INSERT INTO purchase_consents (id, purchase_id, user_id, policy_bundle_id, "
                                 "bundle_version, bundle_digest, acknowledgements, accepted_at) "
                                 "VALUES (?, ?, 'u1', ?, ?, ?, ?, 1.0)",
                                 ("c-" + dimension, purchase_id, bundle_id, VERSION, digest, json.dumps(ack)))
        finally:
            db.close()


class TunableAndContextTests(unittest.TestCase):
    def test_the_tunables_are_strictly_positive_ints_never_bools(self):
        for bad in (0, -1, True, False, "3", 1.0, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _positive_int("X", bad)
        self.assertEqual(_positive_int("X", 1), 1)
        for value in (MAX_OPEN_CHECKOUTS, REUSE_WINDOW_SECONDS):
            self.assertIs(type(value), int)
            self.assertGreater(value, 0)

    def _context(self, **over):
        base = dict(environment="sandbox", user_id="u1", product_id="python-lifetime", policy_bundle_id="pb-1",
                    bundle_digest="d" * 64, acknowledgements=("terms", "privacy"), access_scope="python",
                    amount_minor=1000, product_currency="USD", price_id=PRICE, quantity=1, currency_code="USD",
                    total_minor=1000, total_field="grand_total")
        base.update(over)
        return CheckoutContext(**base)

    def test_the_context_digest_is_canonical_and_null_is_never_zero(self):
        a = self._context(total_minor=None)
        self.assertNotEqual(a.digest(), self._context(total_minor=0).digest())
        self.assertEqual(a.digest(), self._context(total_minor=None, acknowledgements=("privacy", "terms")).digest())
        self.assertEqual(a.digest(), self._context(total_minor=None).digest())
        self.assertNotEqual(a.digest(), self._context(total_minor=None, user_id="u2").digest())
        self.assertNotEqual(a.digest(), self._context(total_minor=None, product_id="other").digest())
        self.assertEqual(len(a.digest()), 64)

    def test_a_corrupt_context_is_refused(self):
        for over in ({"environment": "prod"}, {"quantity": 0}, {"quantity": True}, {"amount_minor": -1},
                     {"amount_minor": True}, {"total_minor": -1}, {"total_minor": True}, {"total_field": "x"}):
            with self.subTest(over=over), self.assertRaises(ValueError):
                self._context(**over)

    def test_reserve_refuses_a_non_finite_or_wrong_typed_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "a.db"))
            db.migrate()
            self.addCleanup(db.close)
            repos = Repositories(db)
            with db.transaction(immediate=True):
                for bad in (float("nan"), float("inf"), float("-inf"), True, "1", None):
                    with self.subTest(bad=bad), self.assertRaises(ValueError):
                        reserve_or_reuse_checkout(repos, self._context(), bad)

    def test_reserve_requires_the_callers_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "a.db"))
            db.migrate()
            self.addCleanup(db.close)
            with self.assertRaises(RuntimeError):
                reserve_or_reuse_checkout(Repositories(db), self._context(), NOW)


class ContextSchemaTests(CapBase):
    def _reserved(self):
        db = self.db()
        repos = Repositories(db)
        with db.transaction(immediate=True):
            context = build_checkout_context(environment="sandbox", user_id="u1",
                                             product=repos.products.get("python-lifetime"),
                                             bundle=self.bundles["python-lifetime"], acknowledgements=CORE_ACK,
                                             paddle_product=PaddleProduct(price_id=PRICE, quantity=1,
                                                                          currency_code="USD", total_minor=1000))
            reservation = reserve_or_reuse_checkout(repos, context, self.clock[0])
        db.close()
        return reservation

    def test_the_migration_is_additive_and_the_context_is_immutable_and_unique(self):
        reservation = self._reserved()
        self.assertEqual(reservation.kind, "reserved")
        db = self.db()
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.IntegrityError):     # immutable
            db.connect().execute("UPDATE paddle_checkout_context SET amount_minor = 1")
        with self.assertRaises(sqlite3.IntegrityError):     # one context per operation
            db.connect().execute("INSERT INTO paddle_checkout_context (operation_id, purchase_id, environment, user_id, "
                                 "product_id, policy_bundle_id, bundle_digest, ack_set, access_scope, amount_minor, "
                                 "product_currency, price_id, quantity, currency_code, total_minor, total_field, "
                                 "context_digest, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (reservation.operation_id, reservation.purchase_id, "sandbox", "u1", "python-lifetime",
                                  "pb", "d", "[]", "python", 1, "USD", PRICE, 1, "USD", None, "grand_total", "e" * 64, 1.0))

    def test_the_quota_index_exists(self):
        db = self.db()
        self.addCleanup(db.close)
        names = {row[0] for row in db.connect().execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        self.assertIn("idx_purchases_quota", names)
        self.assertIn("idx_paddle_checkout_context_key", names)

    def test_violating_rows_are_refused(self):
        db = self.db()
        self.addCleanup(db.close)
        conn = db.connect()
        conn.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('u9', 1, 'active', NULL)")
        conn.execute("INSERT INTO products (id, title, currency, amount_minor, access_scope, active)"
                     " VALUES ('p9', 'P', 'USD', 5, 'x', 1)")
        conn.execute("INSERT INTO purchases (id, user_id, product_id, provider, provider_purchase_id, state, revision, created_at)"
                     " VALUES ('pu9', 'u9', 'p9', 'paddle-sandbox', 's', 'pending', 0, 1)")
        conn.execute("INSERT INTO paddle_operations (id, purchase_id, environment, state, created_at)"
                     " VALUES ('op9', 'pu9', 'sandbox', 'planned', 1)")
        columns = ["operation_id", "purchase_id", "environment", "user_id", "product_id", "policy_bundle_id",
                   "bundle_digest", "ack_set", "access_scope", "amount_minor", "product_currency", "price_id",
                   "quantity", "currency_code", "total_minor", "total_field", "context_digest", "created_at"]
        base = ["op9", "pu9", "sandbox", "u9", "p9", "pb", "d", "[]", "x", 5, "USD", PRICE, 1, "USD", None,
                "grand_total", "e" * 64, 1.0]
        for override in ({"environment": "staging"}, {"quantity": 0}, {"amount_minor": -1},
                         {"total_field": "x"}, {"context_digest": "short"}):
            row = list(base)
            key, value = next(iter(override.items()))
            row[columns.index(key)] = value
            with self.subTest(override=override), self.assertRaises(sqlite3.IntegrityError):
                db.connect().execute(f"INSERT INTO paddle_checkout_context ({','.join(columns)})"
                                     f" VALUES ({','.join('?' * len(row))})", row)


class CheckoutCapTests(CapBase):
    def test_three_open_denies_the_fourth_with_a_generic_429_and_no_writes(self):
        self.transport.create_results = [PaddleOutcomeUnknown("t")] * 3
        for _ in range(3):
            self.assertEqual(self.checkout()[0], 503)
        before = self.counts()
        self.transport.create_results.append(txn(9))
        status, headers, body = self.checkout()
        self.assertEqual(status, 429)
        self.assertEqual(json_body((status, headers, body))["error"]["code"], "throttled")
        self.assertNotIn(b"txn_", body)
        for key in (b"purchase_id", b"transaction_id", b"checkout", b"quota"):
            self.assertNotIn(key, body)
        self.assertNotIn("retry-after", {k.lower() for k in headers})
        self.assertEqual(len(self.transport.create_calls), 3)          # the refused request never reached the provider
        self.assertEqual(self.counts(), before)                        # nothing written

    def test_two_to_three_is_allowed(self):
        self.transport.create_results = [PaddleOutcomeUnknown("t"), PaddleOutcomeUnknown("t")]
        self.assertEqual(self.checkout()[0], 503)
        self.assertEqual(self.checkout()[0], 503)
        self.transport.create_results.append(txn(3))
        self.assertEqual(self.checkout()[0], 200)
        self.assertEqual(self.counts()[0], 3)

    def test_a_bound_checkout_is_reused_even_when_the_quota_is_full(self):
        self.transport.create_results = [PaddleOutcomeUnknown("t"), PaddleOutcomeUnknown("t")]
        self.assertEqual(self.checkout()[0], 503)                     # two unreusable opens fill two slots
        self.assertEqual(self.checkout()[0], 503)
        self.transport.create_results.append(txn(1))
        first = json_body(self.checkout())                            # third slot: bound and reusable
        self.assertEqual(self.counts()[0], 3)
        before = self.counts()
        again = json_body(self.checkout())                            # full quota, but the bound one is reusable
        self.assertEqual(again["purchase_id"], first["purchase_id"])
        self.assertEqual(again["checkout"]["transaction_id"], txn(1))
        self.assertEqual(len(self.transport.create_calls), 3)         # no new send
        self.assertEqual(self.counts(), before)                       # no new purchase/consent/operation

    def test_the_reuse_window_is_exactly_the_configured_seconds_and_is_never_extended(self):
        self.transport.create_results.append(txn(1))
        first = json_body(self.checkout())
        self.clock[0] = NOW + REUSE_WINDOW_SECONDS - 1
        second = json_body(self.checkout())
        self.assertEqual(second["purchase_id"], first["purchase_id"])                 # inside the window
        self.assertEqual(self.rows("SELECT created_at FROM paddle_operations"), [(NOW,)])   # never extended
        self.clock[0] = NOW + REUSE_WINDOW_SECONDS                                   # exactly the window: no reuse
        self.transport.create_results.append(txn(2))
        third = json_body(self.checkout())
        self.assertNotEqual(third["purchase_id"], first["purchase_id"])
        self.assertEqual(third["checkout"]["transaction_id"], txn(2))
        self.assertEqual(len(self.transport.create_calls), 2)                         # the reuse made no call

    def test_a_future_operation_is_never_reused(self):
        db = self.db()
        repos = Repositories(db)
        paddle = PaddleRepository(db)
        context = self.http_context()
        with db.transaction(immediate=True):
            purchase = repos.purchases.create("u1", "python-lifetime", "paddle-sandbox", "srv-future")
            repos.consents.record(purchase.id, context.policy_bundle_id, list(context.acknowledgements))
            # created_at in the future: the UPDATE trigger forbids changing it, so insert the bound row directly
            db.connect().execute("INSERT INTO paddle_operations (id, purchase_id, environment, state, provider_txn_id, "
                                 "created_at, bound_at) VALUES (?, ?, 'sandbox', 'bound', ?, ?, ?)",
                                 ("op-future", purchase.id, txn(1), NOW + 10, NOW))
            paddle.record_context("op-future", context, NOW + 10)
        db.close()
        self.transport.create_results.append(txn(2))
        second = json_body(self.checkout())
        self.assertNotEqual(second["purchase_id"], purchase.id)         # a future operation is not reusable
        self.assertEqual(second["checkout"]["transaction_id"], txn(2))

    def test_another_user_or_product_never_borrows_a_checkout(self):
        self.transport.create_results.append(txn(1))
        first = json_body(self.checkout(user="u1"))
        self.transport.create_results.append(txn(2))
        second = json_body(self.checkout(user="u2"))
        self.assertNotEqual(second["purchase_id"], first["purchase_id"])
        self.assertEqual(second["checkout"]["transaction_id"], txn(2))
        self.transport.create_results.append(txn(3))
        other = json_body(self.checkout(user="u1", product="other"))
        self.assertNotEqual(other["purchase_id"], first["purchase_id"])
        self.assertEqual(other["checkout"]["transaction_id"], txn(3))

    def test_a_terminal_purchase_is_never_reused_and_frees_its_slot(self):
        self.transport.create_results.append(txn(1))
        first = json_body(self.checkout())
        db = self.db()
        with db.transaction(immediate=True):
            Repositories(db).purchases.compare_and_swap(first["purchase_id"], 0, state=PurchaseState.PAID, revision=1)
        db.close()
        self.transport.create_results.append(txn(2))
        second = json_body(self.checkout())
        self.assertNotEqual(second["purchase_id"], first["purchase_id"])
        self.assertEqual(second["checkout"]["transaction_id"], txn(2))


class OpenCountTests(CapBase):
    def test_the_quota_is_shared_across_both_environments(self):
        db = self.db()
        repos = Repositories(db)
        paddle = PaddleRepository(db)
        with db.transaction(immediate=True):
            live = repos.purchases.create("u1", "python-lifetime", "paddle-live", "srv-live")
            repos.consents.record(live.id, "pb-python-lifetime", list(CORE_ACK))
            paddle.plan(live.id, "live", self.clock[0])
        db.close()
        self.transport.create_results = [PaddleOutcomeUnknown("t")] * 2
        self.assertEqual(self.checkout()[0], 503)          # the live pending checkout counts: 1 + 1
        self.assertEqual(self.checkout()[0], 503)          # 3 open
        self.transport.create_results.append(txn(9))
        self.assertEqual(self.checkout()[0], 429)          # full across environments

    def test_a_bound_legacy_operation_without_context_counts_but_is_never_reused(self):
        db = self.db()
        repos = Repositories(db)
        paddle = PaddleRepository(db)
        with db.transaction(immediate=True):
            legacy = repos.purchases.create("u1", "python-lifetime", "paddle-sandbox", "srv-legacy")
            repos.consents.record(legacy.id, "pb-python-lifetime", list(CORE_ACK))
            op = paddle.plan(legacy.id, "sandbox", self.clock[0])
            paddle.mark_request_sent(op, self.clock[0])
            paddle.bind(op, txn(1), self.clock[0], environment="sandbox")     # bound, but no stored context
        db.close()
        self.transport.create_results.append(txn(2))
        new = json_body(self.checkout())
        self.assertNotEqual(new["purchase_id"], legacy.id)                    # legacy is never reused
        self.assertEqual(new["checkout"]["transaction_id"], txn(2))
        self.assertEqual(self.counts()[0], 2)                                 # but it still counted

    def test_a_pending_purchase_without_an_operation_still_counts(self):
        db = self.db()
        with db.transaction(immediate=True):
            Repositories(db).purchases.create("u1", "python-lifetime", "paddle-sandbox", "srv-orphan")
        db.close()
        self.transport.create_results = [PaddleOutcomeUnknown("t")] * 2
        self.assertEqual(self.checkout()[0], 503)          # orphan (1) + one
        self.assertEqual(self.checkout()[0], 503)          # three
        self.transport.create_results.append(txn(9))
        self.assertEqual(self.checkout()[0], 429)

    def test_abandoned_before_the_send_frees_the_slot_but_unknown_does_not(self):
        self.transport.create_results.append(PaddleOutcomeUnknown("t"))
        self.assertEqual(self.checkout()[0], 503)          # one unknown holds a slot
        db = self.db()
        repos = Repositories(db)
        paddle = PaddleRepository(db)
        with db.transaction(immediate=True):
            purchase = repos.purchases.create("u1", "python-lifetime", "paddle-sandbox", "srv-abandoned")
            op = paddle.plan(purchase.id, "sandbox", self.clock[0])
        paddle.abandon(op)
        db.close()
        self.transport.create_results.append(txn(2))
        self.assertEqual(self.checkout()[0], 200)          # only the unknown counts: room for a new one

    def test_a_consent_that_differs_in_one_dimension_blocks_reuse(self):
        # Exactly one dimension differs at a time, so removing any single consent check fails a case.
        for dimension in ("bundle_id", "bundle_digest", "ack_set"):
            with self.subTest(dimension=dimension):
                self._reset()
                self.transport.create_results.append(txn(1))
                first = json_body(self.checkout())
                self.replace_consent(first["purchase_id"], dimension)
                self.transport.create_results.append(txn(2))
                second = json_body(self.checkout())
                self.assertNotEqual(second["purchase_id"], first["purchase_id"])   # no reuse
                self.assertEqual(second["checkout"]["transaction_id"], txn(2))     # a fresh send instead

    def test_a_missing_or_changed_consent_blocks_reuse(self):
        self.transport.create_results.append(txn(1))
        first = json_body(self.checkout())
        db = self.db()
        db.connect().execute("DELETE FROM purchase_consents WHERE purchase_id = ?", (first["purchase_id"],))
        db.close()
        self.transport.create_results.append(txn(2))
        second = json_body(self.checkout())
        self.assertNotEqual(second["purchase_id"], first["purchase_id"])

    def test_a_changed_catalog_blocks_reuse_but_still_counts(self):
        self.transport.create_results.append(txn(1))
        first = json_body(self.checkout())
        db = self.db()
        Repositories(db).products.upsert(Product("python-lifetime", "python-lifetime", "USD", 1500, "python", True))
        db.close()
        self.transport.create_results.append(txn(2))
        second = json_body(self.checkout())
        self.assertNotEqual(second["purchase_id"], first["purchase_id"])
        self.assertEqual(self.counts()[0], 2)

    def test_a_new_policy_bundle_blocks_reuse(self):
        self.transport.create_results.append(txn(1))
        first = json_body(self.checkout())
        version2 = "2026-10-17"
        db = self.db()
        Repositories(db).policies.insert(bundle_for("python-lifetime", "python", version=version2, terms="e",
                                                    bundle_id="pb-python-lifetime-v2"))
        db.close()
        services = self._services((PolicyActivation("python-lifetime", "python", "en", "pb-python-lifetime-v2", approved=True),),
                                  policy_version=version2)
        app = create_app(self.settings, services)
        self.transport.create_results.append(txn(2))
        status, _, raw = self.checkout(app=app, policy_version=version2)
        data = json_body((status, _, raw))
        self.assertEqual(status, 200)
        self.assertNotEqual(data["purchase_id"], first["purchase_id"])


class DriftAndIsolationTests(CapBase):
    def _context(self, paddle_product=None):
        db = self.db()
        try:
            product = Repositories(db).products.get("python-lifetime")
        finally:
            db.close()
        return build_checkout_context(environment="sandbox", user_id="u1", product=product,
                                      bundle=self.bundles["python-lifetime"], acknowledgements=CORE_ACK,
                                      paddle_product=paddle_product or PaddleProduct(price_id=PRICE, quantity=1,
                                                                                     currency_code="USD", total_minor=1000))

    def test_a_catalog_drift_before_the_send_fails_closed_without_a_remote_call(self):
        db = self.db()
        repos = Repositories(db)
        with db.transaction(immediate=True):
            reservation = reserve_or_reuse_checkout(repos, self._context(), self.clock[0])
        self.assertEqual(reservation.kind, "reserved")
        changed = dict(self.settings.paddle_products)
        changed["python-lifetime"] = PaddleProduct(price_id=PRICE2, quantity=1, currency_code="USD", total_minor=1000)
        service = PaddleService(repos, self.transport, changed, environment="sandbox", clock=lambda: self.clock[0])
        result = service.start_checkout(repos.purchases.get(reservation.purchase_id))
        self.assertEqual(result, {"result": "abandoned", "transaction_id": None})
        self.assertEqual(self.transport.create_calls, [])
        db.close()
        self.assertEqual(self.open_states(), [("abandoned",)])

    def test_a_product_drift_before_the_send_also_fails_closed(self):
        db = self.db()
        repos = Repositories(db)
        with db.transaction(immediate=True):
            reservation = reserve_or_reuse_checkout(repos, self._context(), self.clock[0])
        Repositories(db).products.upsert(Product("python-lifetime", "python-lifetime", "USD", 1500, "python", True))
        service = PaddleService(repos, self.transport, self.settings.paddle_products, environment="sandbox",
                                clock=lambda: self.clock[0])
        self.assertEqual(service.start_checkout(repos.purchases.get(reservation.purchase_id)),
                         {"result": "abandoned", "transaction_id": None})
        self.assertEqual(self.transport.create_calls, [])
        db.close()
        self.assertEqual(self.open_states(), [("abandoned",)])

    def test_a_mismatch_never_releases_an_operation_that_was_already_sent(self):
        db = self.db()
        repos = Repositories(db)
        paddle = PaddleRepository(db)
        with db.transaction(immediate=True):
            reservation = reserve_or_reuse_checkout(repos, self._context(), self.clock[0])
        self.assertTrue(paddle.mark_request_sent(reservation.operation_id, self.clock[0]))   # a concurrent send won
        Repositories(db).products.upsert(Product("python-lifetime", "python-lifetime", "USD", 1500, "python", True))
        service = PaddleService(repos, self.transport, self.settings.paddle_products, environment="sandbox",
                                clock=lambda: self.clock[0])
        self.assertEqual(service.start_checkout(repos.purchases.get(reservation.purchase_id)),
                         {"result": "not_sent", "transaction_id": None})
        self.assertEqual(self.transport.create_calls, [])
        db.close()
        self.assertEqual(self.open_states(), [("request_sent",)])          # never abandoned from this path

    def test_every_saved_context_field_drift_fails_closed_before_the_send(self):
        baseline = Product("python-lifetime", "python-lifetime", "USD", 1000, "python", True)
        drifts = {
            "amount_minor": (Product("python-lifetime", "python-lifetime", "USD", 1500, "python", True), None),
            "product_currency": (Product("python-lifetime", "python-lifetime", "EUR", 1000, "python", True), None),
            "access_scope": (Product("python-lifetime", "python-lifetime", "USD", 1000, "other", True), None),
            "price_id": (None, PaddleProduct(price_id=PRICE2, quantity=1, currency_code="USD", total_minor=1000)),
            "quantity": (None, PaddleProduct(price_id=PRICE, quantity=2, currency_code="USD", total_minor=1000)),
            "currency_code": (None, PaddleProduct(price_id=PRICE, quantity=1, currency_code="EUR", total_minor=1000)),
            "total_minor_value": (None, PaddleProduct(price_id=PRICE, quantity=1, currency_code="USD", total_minor=999)),
            "total_minor_zero": (None, PaddleProduct(price_id=PRICE, quantity=1, currency_code="USD", total_minor=0)),
            "total_field": (None, PaddleProduct(price_id=PRICE, quantity=1, currency_code="USD", total_minor=1000,
                                                total_field="total")),
        }
        for name, (drifted_product, drifted_catalog) in drifts.items():
            with self.subTest(field=name):
                self.set_product(baseline)
                db = self.db()
                repos = Repositories(db)
                with db.transaction(immediate=True):
                    reservation = reserve_or_reuse_checkout(repos, self._context(), self.clock[0])
                if drifted_product is not None:
                    self.set_product(drifted_product)
                catalog = dict(self.settings.paddle_products)
                if drifted_catalog is not None:
                    catalog["python-lifetime"] = drifted_catalog
                service = PaddleService(repos, self.transport, catalog, environment="sandbox", clock=lambda: self.clock[0])
                self.assertEqual(service.start_checkout(repos.purchases.get(reservation.purchase_id)),
                                 {"result": "abandoned", "transaction_id": None}, name)
                self.assertEqual(self.transport.create_calls, [], name)
                db.close()
        self.set_product(baseline)

    def test_a_session_revoked_between_the_cheap_check_and_the_lock_is_refused(self):
        from unittest import mock
        import server.app as app_module
        real = app_module._principal
        calls = {"n": 0}

        def flaky(request, sessions):
            calls["n"] += 1
            return real(request, sessions) if calls["n"] == 1 else None

        before = self.counts()
        with mock.patch.object(app_module, "_principal", flaky):
            status, _, _ = self.checkout()
        self.assertEqual(status, 401)
        self.assertEqual(self.counts(), before)          # nothing written: the lock-time revalidation refused

    def test_an_activation_removed_between_the_cheap_check_and_the_lock_is_refused(self):
        from unittest import mock
        import server.app as app_module
        real = app_module.resolve_active_bundle
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            return real(*args, **kwargs) if calls["n"] == 1 else None

        before = self.counts()
        with mock.patch.object(app_module, "resolve_active_bundle", flaky):
            status, _, _ = self.checkout()
        self.assertEqual(status, 503)
        self.assertEqual(self.counts(), before)

    def test_a_policy_version_change_under_the_lock_is_refused_as_stale(self):
        from dataclasses import replace as dataclass_replace
        from unittest import mock
        import server.app as app_module
        real = app_module.resolve_active_bundle
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return real(*args, **kwargs)
            return dataclass_replace(self.bundles["python-lifetime"], version="2099-01-01")

        before = self.counts()
        with mock.patch.object(app_module, "resolve_active_bundle", flaky):
            status, _, _ = self.checkout()
        self.assertEqual(status, 409)
        self.assertEqual(self.counts(), before)

    def test_an_environment_drift_before_the_send_fails_closed(self):
        db = self.db()
        repos = Repositories(db)
        with db.transaction(immediate=True):
            reservation = reserve_or_reuse_checkout(repos, self._context(), self.clock[0])
        service = PaddleService(repos, self.transport, self.settings.paddle_products, environment="live",
                                clock=lambda: self.clock[0])
        self.assertEqual(service.start_checkout(repos.purchases.get(reservation.purchase_id)),
                         {"result": "abandoned", "transaction_id": None})
        self.assertEqual(self.transport.create_calls, [])
        db.close()                                      # no send; the mismatched-environment call can never reach the provider

    def test_a_corrupt_product_row_before_the_send_fails_closed(self):
        db = self.db()
        repos = Repositories(db)
        with db.transaction(immediate=True):
            reservation = reserve_or_reuse_checkout(repos, self._context(), self.clock[0])
        db.connect().execute("UPDATE products SET access_scope = '' WHERE id = 'python-lifetime'")
        service = PaddleService(repos, self.transport, self.settings.paddle_products, environment="sandbox",
                                clock=lambda: self.clock[0])
        self.assertEqual(service.start_checkout(repos.purchases.get(reservation.purchase_id)),
                         {"result": "abandoned", "transaction_id": None})
        self.assertEqual(self.transport.create_calls, [])
        db.close()
        self.assertEqual(self.open_states(), [("abandoned",)])

    def test_a_legacy_planned_operation_without_context_keeps_the_original_send_path(self):
        db = self.db()
        repos = Repositories(db)
        paddle = PaddleRepository(db)
        with db.transaction(immediate=True):
            purchase = repos.purchases.create("u1", "python-lifetime", "paddle-sandbox", "srv-legacy-plan")
            op = paddle.plan(purchase.id, "sandbox", self.clock[0])
        self.transport.create_results.append(txn(1))
        service = PaddleService(repos, self.transport, self.settings.paddle_products, environment="sandbox",
                                clock=lambda: self.clock[0])
        self.assertEqual(service.start_checkout(repos.purchases.get(purchase.id))["result"], "bound")
        db.close()
        self.assertEqual(self.open_states(), [("bound",)])


class ConcurrencyTests(CapBase):
    def test_a_barrier_of_starters_creates_at_most_the_cap_and_throttles_the_rest(self):
        self.transport.block = True
        self.transport.create_results = [txn(1), txn(2), txn(3)]
        statuses = []
        lock = threading.Lock()

        def worker():
            status, _, _ = self.checkout()
            with lock:
                statuses.append(status)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        # All three are held after request_sent: the quota must already cover them.
        self.assertTrue(self.transport.reached.wait(20), "the transport never saw three in-flight creates")
        self.transport.create_results.append(txn(9))
        blocked_status, _, body = self.checkout()
        self.assertEqual(blocked_status, 429)                    # the in-flight reservations count
        self.assertNotIn(b"txn_", body)
        self.transport.gate.set()
        for thread in threads:
            thread.join(20)
        self.assertEqual(sorted(statuses), [200, 200, 200])
        self.assertEqual(len(self.transport.create_calls), 3)    # the refused request never sent
        bound = {row[0] for row in self.rows("SELECT provider_txn_id FROM paddle_operations WHERE state = 'bound'")}
        self.assertEqual(bound, {txn(1), txn(2), txn(3)})
        self.assertEqual(self.rows("SELECT COUNT(*) FROM entitlements"), [(0,)])   # a checkout never grants


if __name__ == "__main__":
    unittest.main()
