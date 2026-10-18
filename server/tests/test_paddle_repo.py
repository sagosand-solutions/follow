# SPDX-License-Identifier: MIT
"""Paddle bookkeeping tables: operations, verified-event evidence with bounded orphan queue, reconcile leases."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from server import paddle_repo
from server.db import Database, apply_migrations, connect
from server.migrations import MIGRATIONS
from server.models import Product
from server.paddle.types import VerifiedEvent
from server.paddle_repo import PaddleQueueFull, PaddleRepository
from server.repositories import Repositories

TXN = "txn_01abcdefghjkmnpqrstvwxyz{:02d}"


def event(n=1, txn=1, kind="transaction.completed", env="sandbox", digest=None):
    return VerifiedEvent("paddle", env, f"evt_{n:026d}", kind, "2026-10-19T01:02:03Z", f"ntf_{n:026d}",
                         digest or f"{n:064x}", TXN.format(txn), None, {})


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "a.db")
        self.now = [10_000.0]
        self.db = Database(self.path, clock=lambda: self.now[0])
        self.db.migrate()
        self.addCleanup(self.db.close)
        self.repos = Repositories(self.db)
        self.repos.products.upsert(Product("p1", "P1", "USD", 500, "python", True))
        self.repos.users.create(user_id="u1")
        self.paddle = PaddleRepository(self.db)

    def purchase(self, n=1, provider="paddle-sandbox"):
        return self.repos.purchases.create("u1", "p1", provider, f"srv-{n}")

    def sent_op(self, n=1):
        p = self.purchase(n)
        op = self.paddle.plan(p.id, "sandbox", self.now[0])
        self.assertTrue(self.paddle.mark_request_sent(op, self.now[0]))
        return p, op


class MigrationTests(unittest.TestCase):
    def test_upgrade_from_4_creates_the_tables_and_repeat_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(str(Path(tmp) / "m.db"))
            self.addCleanup(conn.close)
            self.assertEqual(apply_migrations(conn, migrations=MIGRATIONS[:4]), 4)
            conn.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('u', 1, 'active', NULL)")
            self.assertEqual(apply_migrations(conn), 2)     # Paddle bookkeeping (5) and the checkout context (6)
            self.assertEqual(apply_migrations(conn), 0)
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertTrue({"paddle_operations", "paddle_events", "paddle_reconcile", "purchase_transitions",
                             "paddle_checkout_context"} <= names)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)   # earlier data untouched

    def test_constraints(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(str(Path(tmp) / "m.db"))
            self.addCleanup(conn.close)
            apply_migrations(conn)
            conn.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('u', 1, 'active', NULL)")
            conn.execute("INSERT INTO products (id, title, currency, amount_minor, access_scope, active) VALUES ('p','P','USD',5,'x',1)")
            conn.execute("INSERT INTO purchases (id,user_id,product_id,provider,provider_purchase_id,state,revision,created_at) "
                         "VALUES ('pu','u','p','paddle-sandbox','s',  'pending',0,1)")
            bad = [
                "INSERT INTO paddle_operations (id,purchase_id,environment,state,created_at) VALUES ('o','pu','staging','planned',1)",
                "INSERT INTO paddle_operations (id,purchase_id,environment,state,created_at) VALUES ('o','pu','live','weird',1)",
                "INSERT INTO paddle_operations (id,purchase_id,environment,state,provider_txn_id,created_at) VALUES ('o','pu','live','planned','txn_x',1)",
                "INSERT INTO paddle_operations (id,purchase_id,environment,state,created_at) VALUES ('o','pu','live','bound',1)",
                "INSERT INTO paddle_events (environment,event_id,transaction_id,event_type,payload_digest,occurred_at,received_at,state) VALUES ('x','e','t','y','d','o',1,'pending')",
                "INSERT INTO paddle_events (environment,event_id,transaction_id,event_type,payload_digest,occurred_at,received_at,state) VALUES ('live','e','t','y','d','o',1,'weird')",
                "INSERT INTO paddle_reconcile (purchase_id,next_attempt_at,state) VALUES ('pu',1,'weird')",
                "INSERT INTO paddle_reconcile (purchase_id,attempts,next_attempt_at,state) VALUES ('pu',-1,1,'due')",
            ]
            for sql in bad:
                with self.subTest(sql=sql[:70]), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)

    def test_evidence_is_immutable_and_settled_never_goes_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(str(Path(tmp) / "m.db"))
            self.addCleanup(conn.close)
            apply_migrations(conn)
            conn.execute("INSERT INTO paddle_events VALUES ('sandbox','e','t','transaction.completed','d','o',1,'pending')")
            for col, val in (("payload_digest", "x"), ("transaction_id", "u"), ("event_type", "y"), ("event_id", "f"), ("received_at", 9)):
                with self.subTest(col=col), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f"UPDATE paddle_events SET {col} = ?", (val,))
            conn.execute("UPDATE paddle_events SET state = 'settled'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE paddle_events SET state = 'pending'")


class OperationTests(Base):
    def test_request_sent_is_a_compare_and_swap_that_gates_the_remote_call(self):
        p = self.purchase()
        op = self.paddle.plan(p.id, "sandbox", self.now[0])
        self.assertEqual(self.paddle.operation(op)["state"], "planned")
        self.assertTrue(self.paddle.mark_request_sent(op, self.now[0]))
        self.assertFalse(self.paddle.mark_request_sent(op, self.now[0]))          # second caller must NOT call the provider
        self.assertEqual(self.paddle.bind(op, TXN.format(1), 5.0, environment="sandbox"), "bound")
        self.assertEqual(self.paddle.bind(op, TXN.format(2), 6.0, environment="sandbox"), "not_sent")    # bound once only
        self.assertEqual(self.paddle.operation(op)["provider_txn_id"], TXN.format(1))

    def test_bind_before_request_sent_is_refused_and_one_operation_per_purchase(self):
        p = self.purchase()
        op = self.paddle.plan(p.id, "sandbox", self.now[0])
        self.assertEqual(self.paddle.bind(op, TXN.format(1), 1.0, environment="sandbox"), "not_sent")
        with self.assertRaises(sqlite3.IntegrityError):
            self.paddle.plan(p.id, "sandbox", self.now[0])
        with self.assertRaises(ValueError):
            self.paddle.plan(p.id, "staging", self.now[0])

    def test_a_transaction_id_can_never_be_bound_to_two_purchases(self):
        _, a = self.sent_op(1)
        _, b = self.sent_op(2)
        self.assertEqual(self.paddle.bind(a, TXN.format(1), 1.0, environment="sandbox"), "bound")
        self.assertEqual(self.paddle.bind(b, TXN.format(1), 2.0, environment="sandbox"), "conflict")
        row = self.paddle.operation(b)
        self.assertEqual((row["state"], row["provider_txn_id"]), ("unknown", None))      # manual, never shared
        self.assertEqual(self.paddle.bound_operation("sandbox", TXN.format(1))["id"], a)

    def test_unknown_and_abandoned_follow_the_classification_and_never_return(self):
        _, op = self.sent_op(1)
        self.assertTrue(self.paddle.mark_unknown(op))
        self.assertFalse(self.paddle.abandon(op))              # unknown is not later "proved" abandoned
        self.assertFalse(self.paddle.mark_request_sent(op, 1.0))
        p2 = self.purchase(2)
        planned = self.paddle.plan(p2.id, "sandbox", 1.0)
        self.assertTrue(self.paddle.abandon(planned))           # local failure BEFORE any request_sent
        self.assertFalse(self.paddle.mark_request_sent(planned, 1.0))
        _, sent = self.sent_op(3)
        self.assertFalse(self.paddle.abandon(sent))             # once sent, no failure (not even a refusal) is "provably nothing"
        self.assertEqual(self.paddle.operation(sent)["state"], "request_sent")
        self.assertEqual(self.paddle.bind(sent, TXN.format(3), 1.0, environment="sandbox"), "bound")

    def test_an_operation_must_match_its_purchase_provider_and_its_binding_is_immutable(self):
        sandbox, live, mock = self.purchase(1), self.purchase(2, "paddle-live"), self.purchase(3, "mock")
        with self.assertRaises(ValueError):
            self.paddle.plan(sandbox.id, "live", 1.0)             # a sandbox purchase can never get a live operation
        with self.assertRaises(ValueError):
            self.paddle.plan(live.id, "sandbox", 1.0)
        with self.assertRaises(ValueError):
            self.paddle.plan(mock.id, "sandbox", 1.0)             # nor can a mock purchase
        with self.assertRaises(ValueError):
            self.paddle.plan("nope", "sandbox", 1.0)
        op = self.paddle.plan(sandbox.id, "sandbox", 1.0)
        conn = self.db.connect()
        for sql in (f"UPDATE paddle_operations SET environment = 'live' WHERE id = '{op}'",
                    f"UPDATE paddle_operations SET purchase_id = '{live.id}' WHERE id = '{op}'",
                    f"UPDATE paddle_operations SET created_at = 0 WHERE id = '{op}'"):
            with self.subTest(sql=sql[:60]), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql)
        self.paddle.mark_request_sent(op, 1.0)
        self.paddle.bind(op, TXN.format(1), 2.0, environment="sandbox")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE paddle_operations SET provider_txn_id = '{TXN.format(2)}' WHERE id = '{op}'")   # no rebinding

    def test_the_environment_of_the_call_must_match_the_operation(self):
        _, op = self.sent_op(1)
        self.assertEqual(self.paddle.bind(op, TXN.format(1), 1.0, environment="live"), "wrong_env")
        self.assertEqual(self.paddle.operation(op)["state"], "request_sent")
        with self.assertRaises(ValueError):
            self.paddle.bind(op, TXN.format(1), 1.0, environment="staging")

    def test_a_late_authentic_response_after_unknown_is_saved_never_bound_automatically(self):
        _, op = self.sent_op(1)
        self.assertEqual(self.paddle.record_late_response(op, TXN.format(1), environment="sandbox"), "not_unknown")  # still request_sent
        self.paddle.sweep_stale_requests(self.now[0] + 1000, older_than=300)
        self.assertEqual(self.paddle.operation(op)["state"], "unknown")
        self.assertEqual(self.paddle.bind(op, TXN.format(1), 1.0, environment="sandbox"), "not_sent")     # the plain bind refuses
        self.assertEqual(self.paddle.record_late_response(op, TXN.format(1), environment="live"), "wrong_env")
        self.assertEqual(self.paddle.record_late_response(op, TXN.format(1), environment="sandbox"), "saved")
        self.assertEqual(self.paddle.record_late_response(op, TXN.format(2), environment="sandbox"), "not_unknown")  # first one wins
        row = self.paddle.operation(op)
        self.assertEqual((row["state"], row["provider_txn_id"], row["late_txn_id"]), ("unknown", None, TXN.format(1)))
        self.assertIsNone(self.paddle.bound_operation("sandbox", TXN.format(1)))      # nothing bound yet: manual evidence only
        self.assertTrue(self.paddle.adopt_late_response(op, 5.0))                     # the explicit manual decision
        self.assertEqual(self.paddle.bound_operation("sandbox", TXN.format(1))["id"], op)

    def test_a_saved_late_id_can_never_be_bound_by_another_operation_in_either_order(self):
        _, a = self.sent_op(1)
        _, b = self.sent_op(2)
        self.paddle.sweep_stale_requests(self.now[0] + 1000, older_than=300)           # both unknown
        _, c = self.sent_op(3)
        self.assertEqual(self.paddle.record_late_response(a, TXN.format(1), environment="sandbox"), "saved")
        self.assertEqual(self.paddle.bind(c, TXN.format(1), 2.0, environment="sandbox"), "conflict")   # A's evidence kept
        self.assertEqual(self.paddle.operation(a)["late_txn_id"], TXN.format(1))
        self.assertEqual(self.paddle.operation(c)["state"], "unknown")
        _, d = self.sent_op(4)
        self.assertEqual(self.paddle.bind(d, TXN.format(2), 3.0, environment="sandbox"), "bound")
        self.assertEqual(self.paddle.record_late_response(b, TXN.format(2), environment="sandbox"), "conflict")  # other order
        self.assertEqual(self.paddle.record_late_response(b, TXN.format(1), environment="sandbox"), "conflict")  # A's late id
        self.assertTrue(self.paddle.adopt_late_response(a, 4.0))                                              # A can still adopt it
        self.assertEqual(self.paddle.bound_operation("sandbox", TXN.format(1))["id"], a)

    def test_concurrent_bind_and_late_for_the_same_id_has_one_owner(self):
        _, a = self.sent_op(1)
        self.paddle.sweep_stale_requests(self.now[0] + 1000, older_than=300)
        _, c = self.sent_op(2)
        results = []

        def late():
            db = Database(self.path)
            try:
                results.append(("late", PaddleRepository(db).record_late_response(a, TXN.format(1), environment="sandbox")))
            finally:
                db.close()

        def bind():
            db = Database(self.path)
            try:
                results.append(("bind", PaddleRepository(db).bind(c, TXN.format(1), 1.0, environment="sandbox")))
            finally:
                db.close()
        threads = [threading.Thread(target=late), threading.Thread(target=bind)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        outcomes = dict(results)
        self.assertTrue((outcomes["late"], outcomes["bind"]) in (("saved", "conflict"), ("conflict", "bound")), outcomes)
        owners = self.db.connect().execute("SELECT COUNT(*) FROM paddle_txn_reservations WHERE txn_id = ?", (TXN.format(1),)).fetchone()[0]
        self.assertEqual(owners, 1)

    def test_transaction_ids_are_validated_everywhere(self):
        _, op = self.sent_op(1)
        self.paddle.sweep_stale_requests(self.now[0] + 1000, older_than=300)
        for bad in (None, "", "x", "txn_", "TXN_01ABCDEFGHJKMNPQRSTVWXYZ00", 5, "txn_01abc def", "txn_01abcdefghjkmnpqrstvwxyz00\n"):
            with self.subTest(txn=repr(bad)), self.assertRaises(ValueError):
                self.paddle.record_late_response(op, bad, environment="sandbox")
            with self.subTest(txn=repr(bad), api="bind"), self.assertRaises(ValueError):
                self.paddle.bind(op, bad, 1.0, environment="sandbox")
        self.assertIsNone(self.paddle.operation(op)["late_txn_id"])

    def test_a_late_id_that_is_already_in_use_is_a_conflict_and_adoption_refuses(self):
        _, a = self.sent_op(1)
        self.paddle.bind(a, TXN.format(1), 1.0, environment="sandbox")
        _, b = self.sent_op(2)
        self.paddle.sweep_stale_requests(self.now[0] + 1000, older_than=300)
        self.assertEqual(self.paddle.record_late_response(b, TXN.format(2), environment="sandbox"), "saved")
        self.assertTrue(self.paddle.adopt_late_response(b, 5.0))
        _, c = self.sent_op(3)
        self.paddle.sweep_stale_requests(self.now[0] + 2000, older_than=300)
        self.assertEqual(self.paddle.record_late_response(c, TXN.format(1), environment="sandbox"), "conflict")

    def test_a_stale_request_sent_becomes_unknown_never_a_new_request(self):
        _, old = self.sent_op(1)
        self.now[0] += 500
        _, fresh = self.sent_op(2)
        self.assertEqual(self.paddle.sweep_stale_requests(self.now[0], older_than=300), 1)
        self.assertEqual((self.paddle.operation(old)["state"], self.paddle.operation(fresh)["state"]), ("unknown", "request_sent"))

    def test_concurrent_binders_get_exactly_one_winner(self):
        _, op = self.sent_op(1)
        results = []

        def run(i):
            db = Database(self.path)
            try:
                results.append(PaddleRepository(db).bind(op, TXN.format(i), 1.0, environment="sandbox"))
            finally:
                db.close()
        threads = [threading.Thread(target=run, args=(i,)) for i in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), ["bound"] + ["not_sent"] * 4)


class EvidenceTests(Base):
    def test_new_duplicate_and_conflict_never_change_the_first_evidence(self):
        self.assertEqual(self.paddle.record_verified(event(1), 1.0), "new")
        self.assertEqual(self.paddle.record_verified(event(1), 2.0), "duplicate")
        self.assertEqual(self.paddle.record_verified(event(1, digest="f" * 64), 3.0), "conflict")
        self.assertEqual(self.paddle.record_verified(event(1, txn=2), 3.0), "conflict")       # cannot switch transaction
        rows = self.paddle.events_for_transaction("sandbox", TXN.format(1))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["payload_digest"], rows[0]["received_at"]), (f"{1:064x}", 1.0))
        self.assertEqual(self.paddle.events_for_transaction("sandbox", TXN.format(2)), [])

    def test_the_environment_is_part_of_the_key(self):
        self.assertEqual(self.paddle.record_verified(event(1, env="sandbox"), 1.0), "new")
        self.assertEqual(self.paddle.record_verified(event(1, env="live"), 1.0), "new")
        with self.assertRaises(ValueError):
            self.paddle.record_verified(event(2, env="staging"), 1.0)

    def test_completed_evidence_is_per_transaction_and_environment(self):
        self.paddle.record_verified(event(1, txn=1, kind="transaction.updated"), 1.0)
        self.assertFalse(self.paddle.has_completed_evidence("sandbox", TXN.format(1)))
        self.paddle.record_verified(event(2, txn=1), 1.0)
        self.assertTrue(self.paddle.has_completed_evidence("sandbox", TXN.format(1)))
        self.assertFalse(self.paddle.has_completed_evidence("live", TXN.format(1)))
        self.assertFalse(self.paddle.has_completed_evidence("sandbox", TXN.format(2)))

    def test_orphan_caps_are_enforced_per_transaction_and_globally_with_no_eviction(self):
        for n in range(paddle_repo.ORPHAN_PER_TXN_MAX):
            self.assertEqual(self.paddle.record_verified(event(n, txn=1), 1.0), "new")
        with self.assertRaises(PaddleQueueFull):
            self.paddle.record_verified(event(500, txn=1), 1.0)
        self.assertEqual(len(self.paddle.events_for_transaction("sandbox", TXN.format(1))), paddle_repo.ORPHAN_PER_TXN_MAX)
        old = paddle_repo.ORPHAN_GLOBAL_MAX
        paddle_repo.ORPHAN_GLOBAL_MAX = 25
        self.addCleanup(setattr, paddle_repo, "ORPHAN_GLOBAL_MAX", old)
        for n in range(5):
            self.paddle.record_verified(event(600 + n, txn=2), 1.0)
        with self.assertRaises(PaddleQueueFull):
            self.paddle.record_verified(event(700, txn=3), 1.0)
        self.assertEqual(self.paddle.record_verified(event(600, txn=2), 2.0), "duplicate")   # known events never refused

    def test_the_global_orphan_cap_counts_every_environment_together(self):
        old = paddle_repo.ORPHAN_GLOBAL_MAX
        paddle_repo.ORPHAN_GLOBAL_MAX = 6
        self.addCleanup(setattr, paddle_repo, "ORPHAN_GLOBAL_MAX", old)
        for n in range(3):
            self.paddle.record_verified(event(n, txn=n + 1, env="sandbox"), 1.0)
        for n in range(3):
            self.paddle.record_verified(event(10 + n, txn=n + 10, env="live"), 1.0)
        with self.assertRaises(PaddleQueueFull):                   # 3 + 3 = the global cap, in either environment
            self.paddle.record_verified(event(50, txn=50, env="sandbox"), 1.0)
        with self.assertRaises(PaddleQueueFull):
            self.paddle.record_verified(event(51, txn=51, env="live"), 1.0)

    def test_purge_limits_are_validated_like_claim_limits(self):
        for bad in (0, -1, 1001, True, "5", None, 2.0):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                self.paddle.purge_expired_orphans(self.now[0], bad)
        self.assertEqual(self.paddle.purge_expired_orphans(self.now[0], 1000), 0)

    def test_evidence_for_a_bound_transaction_is_never_refused_and_never_purged_by_the_ttl(self):
        _, op = self.sent_op(1)
        self.assertEqual(self.paddle.bind(op, TXN.format(1), 1.0, environment="sandbox"), "bound")
        for n in range(paddle_repo.ORPHAN_PER_TXN_MAX + 5):
            self.assertEqual(self.paddle.record_verified(event(n, txn=1), 1.0), "new")      # bound: not an orphan
        self.paddle.record_verified(event(900, txn=9), 1.0)                                    # a true orphan
        self.now[0] += paddle_repo.ORPHAN_TTL_SECONDS + 1
        self.assertEqual(self.paddle.purge_expired_orphans(self.now[0]), 1)
        self.assertEqual(len(self.paddle.events_for_transaction("sandbox", TXN.format(1))), paddle_repo.ORPHAN_PER_TXN_MAX + 5)
        self.assertTrue(self.paddle.has_completed_evidence("sandbox", TXN.format(1)))

    def test_bound_evidence_has_a_hard_cap_that_applies_backpressure_not_eviction(self):
        _, op = self.sent_op(1)
        self.paddle.bind(op, TXN.format(1), 1.0, environment="sandbox")
        old = paddle_repo.BOUND_EVENTS_PER_TXN_MAX
        paddle_repo.BOUND_EVENTS_PER_TXN_MAX = 30
        self.addCleanup(setattr, paddle_repo, "BOUND_EVENTS_PER_TXN_MAX", old)
        for n in range(30):
            self.assertEqual(self.paddle.record_verified(event(n, txn=1), 1.0), "new")
        with self.assertRaises(PaddleQueueFull):
            self.paddle.record_verified(event(99, txn=1), 1.0)
        self.assertEqual(len(self.paddle.events_for_transaction("sandbox", TXN.format(1))), 30)    # nothing evicted
        self.assertEqual(self.paddle.record_verified(event(3, txn=1), 2.0), "duplicate")

    def test_an_orphan_that_gets_bound_before_the_ttl_survives_it(self):
        self.paddle.record_verified(event(1, txn=1), 1.0)
        _, op = self.sent_op(1)
        self.paddle.bind(op, TXN.format(1), 2.0, environment="sandbox")
        self.now[0] += paddle_repo.ORPHAN_TTL_SECONDS + 1
        self.assertEqual(self.paddle.purge_expired_orphans(self.now[0]), 0)
        self.assertTrue(self.paddle.has_completed_evidence("sandbox", TXN.format(1)))

    def test_no_raw_payload_is_stored(self):
        cols = [r[1] for r in self.db.connect().execute("PRAGMA table_info(paddle_events)")]
        self.assertEqual(set(cols), {"environment", "event_id", "transaction_id", "event_type", "payload_digest",
                                      "occurred_at", "received_at", "state"})

    def test_settling_marks_evidence_without_deleting_it(self):
        self.paddle.record_verified(event(1, txn=1), 1.0)
        self.assertEqual(self.paddle.settle_transaction("sandbox", TXN.format(1)), 1)
        self.assertTrue(self.paddle.has_completed_evidence("sandbox", TXN.format(1)))


class ReconcileTests(Base):
    def due(self, n=1):
        pid = self.purchase(n).id
        self.paddle.mark_due(pid, self.now[0])
        return pid

    def test_claims_are_leased_and_carry_a_token_and_the_revision_read_inside_the_claim(self):
        ids = [self.due(n) for n in range(1, 5)]
        first = self.paddle.claim_due(self.now[0], 3)
        second = self.paddle.claim_due(self.now[0], 3)
        self.assertEqual((len(first), len(second)), (3, 1))
        self.assertEqual({c["purchase_id"] for c in first} & {c["purchase_id"] for c in second}, set())
        self.assertTrue(all(c["generation"] == 1 and c["owner"] and c["revision"] == 0 for c in first + second))
        self.now[0] += paddle_repo.LEASE_SECONDS + 1              # a crashed worker's lease expires
        again = self.paddle.claim_due(self.now[0], 10)
        self.assertEqual((len(again), {c["generation"] for c in again}), (4, {2}))
        self.assertEqual({c["purchase_id"] for c in again}, set(ids))

    def test_a_stale_worker_can_never_clear_back_off_or_overwrite_a_newer_claim(self):
        pid = self.due()
        old = self.paddle.claim_due(self.now[0], 1)[0]
        self.now[0] += paddle_repo.LEASE_SECONDS + 1                                   # the lease expires while worker 1 is slow
        new = self.paddle.claim_due(self.now[0], 1)[0]
        self.assertEqual((new["purchase_id"], new["generation"]), (pid, 2))
        tok = (pid, old["owner"], old["generation"])
        self.assertEqual(self.paddle.finish(*tok), "lost")
        self.assertEqual(self.paddle.fail(*tok, self.now[0], "transport"), "lost")
        self.assertEqual(self.paddle.await_event(*tok, self.now[0], 0.0), "lost")
        self.assertEqual(self.paddle.to_manual(*tok, "proof"), "lost")
        self.assertFalse(self.paddle.retry_now(*tok, self.now[0]))
        self.assertFalse(self.paddle.renew(*tok, self.now[0]))
        row = self.paddle.reconcile_row(pid)
        self.assertEqual((row["owner"], row["generation"], row["state"], row["attempts"]), (new["owner"], 2, "due", 0))   # untouched
        self.assertEqual(self.paddle.finish(pid, new["owner"], new["generation"]), "deleted")

    def test_new_work_during_a_claim_is_never_lost(self):
        pid = self.due()
        claim = self.paddle.claim_due(self.now[0], 1)[0]
        self.paddle.mark_due(pid, self.now[0] + 1)                                      # a new webhook arrives mid-fetch
        row = self.paddle.reconcile_row(pid)
        self.assertEqual((row["pending_seq"], row["claimed_seq"]), (2, 1))
        self.assertFalse(self.paddle.claim_is_current(pid, claim["owner"], claim["generation"]))
        self.assertEqual(self.paddle.finish(pid, claim["owner"], claim["generation"]), "requeued")
        row = self.paddle.reconcile_row(pid)
        self.assertEqual((row["state"], row["owner"]), ("due", None))
        again = self.paddle.claim_due(self.now[0] + 2, 1)
        self.assertEqual(len(again), 1)
        self.assertTrue(self.paddle.claim_is_current(pid, again[0]["owner"], again[0]["generation"]))
        self.assertEqual(self.paddle.finish(pid, again[0]["owner"], again[0]["generation"]), "deleted")   # the new claim saw all work

    def test_new_evidence_after_the_lease_expired_but_before_any_reclaim_still_fences_the_old_worker(self):
        pid = self.due()
        old = self.paddle.claim_due(self.now[0], 1)[0]
        self.now[0] += paddle_repo.LEASE_SECONDS + 5            # the lease clock has run out, nobody reclaimed
        self.paddle.mark_due(pid, self.now[0])                  # NEW evidence now
        self.assertFalse(self.paddle.claim_is_current(pid, old["owner"], old["generation"]))
        self.assertEqual(self.paddle.finish(pid, old["owner"], old["generation"]), "requeued")   # not deleted
        self.assertIsNotNone(self.paddle.reconcile_row(pid))
        self.assertEqual(len(self.paddle.claim_due(self.now[0], 1)), 1)

    def test_every_completion_path_requeues_instead_of_burying_new_evidence(self):
        for index, (name, call) in enumerate((
            ("fail", lambda pid, c: self.paddle.fail(pid, c["owner"], c["generation"], self.now[0], "transport")),
            ("await_event", lambda pid, c: self.paddle.await_event(pid, c["owner"], c["generation"], self.now[0], 0.0)),
            ("await_event_past_deadline", lambda pid, c: self.paddle.await_event(pid, c["owner"], c["generation"],
                                                                                   self.now[0] + paddle_repo.AWAIT_EVENT_WINDOW_SECONDS + 1, 0.0)),
            ("to_manual_reconcile", lambda pid, c: self.paddle.to_manual(pid, c["owner"], c["generation"], "reconcile")),
            ("to_manual_proof", lambda pid, c: self.paddle.to_manual(pid, c["owner"], c["generation"], "proof")),
        ), start=1):
            with self.subTest(path=name):
                pid = self.due(index)
                c = self.paddle.claim_due(self.now[0], 1)[0]
                self.paddle.mark_due(pid, self.now[0] + 1)                              # new evidence during the old fetch
                self.assertEqual(call(pid, c), "requeued")
                row = self.paddle.reconcile_row(pid)
                self.assertEqual((row["state"], row["attempts"], row["last_error"], row["owner"]), ("due", 0, None, None))
                self.assertEqual(len(self.paddle.claim_due(self.now[0] + 5, 1)), 1)     # picked up again, nothing buried
                self.paddle.finish(pid, *[self.paddle.reconcile_row(pid)[k] for k in ("owner", "generation")])

    def test_the_claim_check_works_inside_the_callers_own_transaction(self):
        pid = self.due()
        claim = self.paddle.claim_due(self.now[0], 1)[0]
        with self.db.transaction(immediate=True):
            self.assertTrue(self.paddle.claim_is_current(pid, claim["owner"], claim["generation"]))
            self.assertFalse(self.paddle.claim_is_current(pid, "w-other", claim["generation"]))
            self.assertFalse(self.paddle.claim_is_current(pid, claim["owner"], claim["generation"] + 1))
        self.assertFalse(self.paddle.claim_is_current("nope", "x", 1))

    def test_concurrent_claimers_partition_the_work(self):
        for n in range(1, 9):
            self.due(n)
        got = []

        def run():
            db = Database(self.path)
            try:
                got.extend(c["purchase_id"] for c in PaddleRepository(db).claim_due(self.now[0], 3))
            finally:
                db.close()
        threads = [threading.Thread(target=run) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((len(got), len(set(got))), (8, 8))

    def test_backoff_grows_is_capped_and_ends_in_manual_review(self):
        pid = self.due()
        delays = []
        for _ in range(paddle_repo.MAX_ATTEMPTS - 1):
            c = self.paddle.claim_due(self.now[0], 1)[0]
            self.assertEqual(self.paddle.fail(pid, c["owner"], c["generation"], self.now[0], "transport"), "due")
            delays.append(self.paddle.reconcile_row(pid)["next_attempt_at"] - self.now[0])
            self.assertEqual(self.paddle.claim_due(self.now[0], 1), [])                # not due yet
            self.now[0] += delays[-1]
        self.assertEqual(delays[:3], [60, 120, 240])
        self.assertTrue(all(d <= paddle_repo.BACKOFF_CAP_SECONDS for d in delays))
        c = self.paddle.claim_due(self.now[0], 1)[0]
        self.assertEqual(self.paddle.fail(pid, c["owner"], c["generation"], self.now[0], "timeout"), "manual")
        self.assertEqual(self.paddle.reconcile_row(pid)["last_error"], "timeout")
        self.now[0] += 10 ** 6
        self.assertEqual(self.paddle.claim_due(self.now[0], 1), [])                    # manual is never claimed
        self.paddle.mark_due(pid, self.now[0])                                          # ordinary queueing does not requeue it
        self.assertEqual(self.paddle.reconcile_row(pid)["state"], "manual")

    def test_one_waiting_model_poll_until_the_provider_window_then_manual_and_new_evidence_reawakens(self):
        pid = self.due()
        first_seen = self.now[0]
        c = self.paddle.claim_due(self.now[0], 1)[0]
        self.assertEqual(self.paddle.await_event(pid, c["owner"], c["generation"], self.now[0], first_seen), "due")
        row = self.paddle.reconcile_row(pid)
        self.assertEqual((row["reason"], row["next_attempt_at"] - self.now[0]), ("await_event", paddle_repo.AWAIT_EVENT_POLL_SECONDS))
        self.now[0] = first_seen + paddle_repo.AWAIT_EVENT_WINDOW_SECONDS
        c = self.paddle.claim_due(self.now[0], 1)[0]
        self.assertEqual(self.paddle.await_event(pid, c["owner"], c["generation"], self.now[0], first_seen), "manual")
        self.assertEqual(self.paddle.reconcile_row(pid)["reason"], "await_event")
        self.paddle.mark_due(pid, self.now[0] + 5)                                      # not evidence: stays manual
        self.assertEqual(self.paddle.reconcile_row(pid)["state"], "manual")
        self.paddle.mark_due(pid, self.now[0] + 5, reawaken=True)                       # a NEW verified completed event
        row = self.paddle.reconcile_row(pid)
        self.assertEqual((row["state"], row["attempts"], row["pending_seq"] > row["claimed_seq"]), ("due", 0, True))

    def test_the_decision_asking_for_manual_review_and_bad_inputs(self):
        pid = self.due()
        c = self.paddle.claim_due(self.now[0], 1)[0]
        for bad in ("Traceback (most recent call last): secret=abc", "https://api.paddle.com/x?token=1", "", None, 5, "TIMEOUT"):
            with self.subTest(error=str(bad)[:20]), self.assertRaises(ValueError):
                self.paddle.fail(pid, c["owner"], c["generation"], self.now[0], bad)               # only the closed codes are accepted
        self.assertEqual(self.paddle.reconcile_row(pid)["last_error"], None)
        conn = self.db.connect()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE paddle_reconcile SET last_error = 'free text with a secret' WHERE purchase_id = ?", (pid,))   # the schema agrees
        for bad in ("weird", "transport", "", None):
            with self.assertRaises(ValueError):
                self.paddle.to_manual(pid, c["owner"], c["generation"], bad)
        for bad in (0, 101, True, "3", None):
            with self.assertRaises(ValueError):
                self.paddle.claim_due(self.now[0], bad)
        with self.assertRaises(ValueError):
            self.paddle.mark_due(pid, 1.0, reason="weird")
        self.assertEqual(self.paddle.to_manual(pid, c["owner"], c["generation"], "proof"), "manual")
        self.assertEqual(self.paddle.reconcile_row(pid)["reason"], "proof")

    def test_cleanup_and_binding_orders_are_each_proved_deterministically(self):
        # Order 1: the binding commits first, then the purge: the evidence is bound and survives.
        self.paddle.record_verified(event(1, txn=1), 1.0)
        _, op1 = self.sent_op(1)
        self.now[0] += paddle_repo.ORPHAN_TTL_SECONDS + 1
        self.assertEqual(self.paddle.bind(op1, TXN.format(1), self.now[0], environment="sandbox"), "bound")
        self.assertEqual(self.paddle.purge_expired_orphans(self.now[0]), 0)
        self.assertTrue(self.paddle.has_completed_evidence("sandbox", TXN.format(1)))
        # Order 2: the purge commits first (an expired orphan may legitimately go), then the binding: the evidence is
        # GONE and is not recreated. The absence is visible, so a grant cannot be justified (it waits for an event).
        self.paddle.record_verified(event(2, txn=2), 1.0)
        _, op2 = self.sent_op(2)
        self.assertEqual(self.paddle.purge_expired_orphans(self.now[0]), 1)
        self.assertEqual(self.paddle.bind(op2, TXN.format(2), self.now[0], environment="sandbox"), "bound")
        self.assertFalse(self.paddle.has_completed_evidence("sandbox", TXN.format(2)))
        purchase = self.repos.purchases.get(self.paddle.operation(op2)["purchase_id"])
        self.assertEqual(purchase.state.value, "pending")                               # nothing was granted
        self.assertEqual(self.repos.entitlements.list_active_for_user("u1"), [])
        # A new verified completed event after binding is stored as BOUND evidence and is never refused.
        self.assertEqual(self.paddle.record_verified(event(3, txn=2), self.now[0]), "new")
        self.assertTrue(self.paddle.has_completed_evidence("sandbox", TXN.format(2)))


class TransitionAuditTests(Base):
    def test_a_reconcile_transition_requires_a_snapshot_digest_and_a_verified_one_an_event_id(self):
        p = self.purchase()
        c = self.db.connect()
        ok = [("t1", "reconcile", None, None, "a" * 64), ("t2", "verified_event", "paddle-sandbox", "evt_x", None)]
        for tid, cause, prov, ev, dig in ok:
            c.execute("INSERT INTO purchase_transitions VALUES (?, ?, 'pending', 'paid', 0, 1, ?, ?, ?, ?, 1.0)",
                      (tid, p.id, cause, prov, ev, dig))
        for tid, cause, prov, ev, dig in (("b1", "reconcile", None, None, None), ("b2", "reconcile", None, None, "short"),
                                           ("b3", "reconcile", None, "evt_x", "a" * 64), ("b4", "verified_event", None, None, None),
                                           ("b5", "verified_event", "p", "evt_x", "a" * 64), ("b6", "other", None, None, None)):
            with self.subTest(row=tid), self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO purchase_transitions VALUES (?, ?, 'pending', 'paid', 0, 1, ?, ?, ?, ?, 1.0)",
                          (tid, p.id, cause, prov, ev, dig))


if __name__ == "__main__":
    unittest.main()
