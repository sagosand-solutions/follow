# SPDX-License-Identifier: MIT
"""Paddle orchestration over a scripted injected transport: checkout creation, webhook evidence and fenced reconcile."""
import tempfile
import unittest
from pathlib import Path

from server import paddle_repo
from server.db import Database
from server.models import Product, PurchaseState
from server.paddle.digest import snapshot_digest
from server.paddle.types import (Adjustment, AuthoritativeState, Item, PaddleMalformed, PaddleOutcomeUnknown,
                                 PaddleTransportRejected, VerifiedEvent)
from server.paddle_repo import PaddleQueueFull, PaddleRepository
from server.paddle_service import PaddleProduct, PaddleService
from server.repositories import Repositories

PRICE = "pri_01abcdefghjkmnpqrstvwxyz00"
TXN = "txn_01abcdefghjkmnpqrstvwxyz{:02d}"


def event(n, txn=1, kind="transaction.completed", env="sandbox", digest=None):
    return VerifiedEvent("paddle", env, f"evt_{n:026d}", kind, "2026-10-19T01:02:03Z", f"ntf_{n:026d}", digest or f"{n:064x}",
                         TXN.format(txn), None, {})


def snapshot(txn=1, status="completed", adjustments=(), **over):
    base = dict(environment="sandbox", transaction_id=TXN.format(txn), status=status, currency_code="USD",
                totals={"grand_total": 1000, "total": 1000}, items=(Item(PRICE, 1),), adjustments=tuple(adjustments),
                fetched_at=0.0, complete=True)
    base.update(over)
    return AuthoritativeState(**base)


def refund(i=1):
    return Adjustment(f"adj_{i:026d}", "refund", "approved", "full", 1000, "USD")


class Transport:
    """Scripted: ``create`` and ``get`` are queues of results or exceptions; hooks run while a call is in flight."""

    def __init__(self):
        self.create_calls, self.get_calls = [], []
        self.create_results, self.get_results = [], []
        self.on_create = self.on_get = None
        self.get_by_txn = {}                                        # snapshots keyed by transaction (order independent)

    def create_transaction(self, price_id, quantity, op_id):
        self.create_calls.append((price_id, quantity, op_id))
        if self.on_create:
            self.on_create(op_id)
        out = self.create_results.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out

    def get_transaction(self, txn_id):
        self.get_calls.append(txn_id)
        if self.on_get:
            self.on_get(txn_id)
        out = self.get_by_txn[txn_id] if txn_id in self.get_by_txn else self.get_results.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "a.db")
        self.now = [100_000.0]
        self.db = Database(self.path, clock=lambda: self.now[0])
        self.db.migrate()
        self.addCleanup(self.db.close)
        self.repos = Repositories(self.db)
        self.repos.products.upsert(Product("p1", "P1", "USD", 500, "python", True))
        self.repos.users.create(user_id="u1")
        self.transport = Transport()
        self.decisions = {}                                          # injected observer target (the service itself stores nothing)
        self.catalog = {"p1": PaddleProduct(PRICE, 1, "USD", total_minor=1000)}
        self.service = self.make_service()
        self.paddle = self.service.paddle

    def make_service(self, env="sandbox", catalog=None, repos=None):
        return PaddleService(repos or self.repos, self.transport, catalog if catalog is not None else self.catalog,
                             environment=env, clock=lambda: self.now[0], on_decision=self.decisions.__setitem__)

    def purchase_with_op(self, n=1, provider="paddle-sandbox", env="sandbox"):
        with self.db.transaction(immediate=True):
            purchase = self.repos.purchases.create("u1", "p1", provider, f"srv-{n}")
            op = self.paddle.plan(purchase.id, env, self.now[0])
        return purchase, op

    def bound_purchase(self, n=1):
        purchase, op = self.purchase_with_op(n)
        self.transport.create_results.append(TXN.format(n))
        self.assertEqual(self.service.start_checkout(purchase)["result"], "bound")
        return purchase, op

    def state(self, purchase):
        p = self.repos.purchases.get(purchase.id)
        return p.state.value, p.revision

    def entitled(self):
        return len(self.repos.entitlements.list_active_for_user("u1"))

    def transitions(self):
        return [tuple(r) for r in self.db.connect().execute(
            "SELECT from_state, to_state, cause, snapshot_digest IS NOT NULL FROM purchase_transitions ORDER BY created_at, rowid")]


class CheckoutTests(Base):
    def test_request_sent_is_committed_before_the_remote_call_and_a_success_binds(self):
        purchase, op = self.purchase_with_op()
        seen = []

        def observe(op_id):                      # a SEPARATE connection: only a real commit is visible to it
            other = Database(self.path)
            try:
                seen.append(PaddleRepository(other).operation(op_id)["state"])
            finally:
                other.close()
        self.transport.on_create = observe
        self.transport.create_results.append(TXN.format(1))
        result = self.service.start_checkout(purchase)
        self.assertEqual((result["result"], result["transaction_id"]), ("bound", TXN.format(1)))
        self.assertEqual(seen, ["request_sent"])                                     # visible to everyone BEFORE the call
        self.assertEqual(self.transport.create_calls, [(PRICE, 1, op)])
        self.assertEqual(self.paddle.operation(op)["state"], "bound")
        self.assertEqual(self.state(purchase), ("pending", 0))                       # binding grants nothing

    def test_a_local_failure_before_send_is_abandoned_and_nothing_is_called(self):
        purchase, op = self.purchase_with_op()
        service = self.make_service(catalog={})                                      # product not configured
        self.assertEqual(service.start_checkout(purchase)["result"], "abandoned")
        self.assertEqual((self.transport.create_calls, self.paddle.operation(op)["state"]), ([], "abandoned"))

    def test_a_second_caller_never_sends_a_second_request(self):
        purchase, _ = self.purchase_with_op()
        self.transport.create_results.append(TXN.format(1))
        self.service.start_checkout(purchase)
        self.assertEqual(self.service.start_checkout(purchase)["result"], "not_sent")
        self.assertEqual(len(self.transport.create_calls), 1)

    def test_every_failure_after_the_send_is_unknown_and_never_retried(self):
        failures = [PaddleOutcomeUnknown("timeout"), PaddleTransportRejected("bad request", 400), PaddleTransportRejected("limit", 429),
                    PaddleMalformed("x"), RuntimeError("secret token abc"), "not-a-txn-id", None, "txn_SHORT", 5]
        for n, failure in enumerate(failures, start=1):
            with self.subTest(failure=repr(failure)[:30]):
                purchase, op = self.purchase_with_op(n)
                self.transport.create_results.append(failure)
                self.assertEqual(self.service.start_checkout(purchase)["result"], "unknown")
                self.assertEqual(self.paddle.operation(op)["state"], "unknown")
                calls = len(self.transport.create_calls)
                self.assertEqual(self.service.start_checkout(purchase)["result"], "not_sent")   # no automatic re-create
                self.assertEqual(len(self.transport.create_calls), calls)
                self.assertEqual(self.state(purchase), ("pending", 0))

    def test_a_late_authentic_response_after_the_sweep_is_kept_as_evidence_not_bound(self):
        purchase, op = self.purchase_with_op()
        self.transport.on_create = lambda op_id: self.paddle.sweep_stale_requests(self.now[0] + 10_000, older_than=60)
        self.transport.create_results.append(TXN.format(1))
        result = self.service.start_checkout(purchase)
        self.assertEqual((result["result"], result["transaction_id"]), ("late", None))
        row = self.paddle.operation(op)
        self.assertEqual((row["state"], row["provider_txn_id"], row["late_txn_id"]), ("unknown", None, TXN.format(1)))
        self.assertIsNone(self.paddle.bound_operation("sandbox", TXN.format(1)))

    def test_a_transaction_id_already_bound_to_another_purchase_is_never_shared(self):
        first, _ = self.bound_purchase(1)
        second, op2 = self.purchase_with_op(2)
        self.transport.create_results.append(TXN.format(1))                          # the provider returns the SAME id
        self.assertEqual(self.service.start_checkout(second)["result"], "unknown")
        self.assertEqual(self.paddle.operation(op2)["state"], "unknown")
        self.assertEqual(self.paddle.bound_operation("sandbox", TXN.format(1))["purchase_id"], first.id)

    def test_the_wrong_environment_or_provider_never_calls_the_transport(self):
        purchase, op = self.purchase_with_op()
        self.assertEqual(self.make_service(env="live").start_checkout(purchase)["result"], "abandoned")
        mock, _ = self.purchase_with_op(2)
        self.assertEqual(self.transport.create_calls, [])
        self.assertEqual(self.paddle.operation(op)["state"], "planned")              # untouched by the wrong service

    def test_operations_refuse_to_run_inside_an_open_transaction_and_make_no_remote_call(self):
        purchase, op = self.purchase_with_op()
        self.transport.create_results.append(TXN.format(1))
        with self.db.transaction(immediate=True):
            with self.assertRaises(RuntimeError):
                self.service.start_checkout(purchase)
            with self.assertRaises(RuntimeError):
                self.service.reconcile_due(5)
            with self.assertRaises(RuntimeError):
                self.service.adopt_late(op)
        self.assertEqual((self.transport.create_calls, self.transport.get_calls), ([], []))
        self.assertEqual(self.paddle.operation(op)["state"], "planned")                  # no send was recorded either
        self.assertEqual(self.service.start_checkout(purchase)["result"], "bound")        # and the normal path still works

    def test_a_failure_while_queueing_rolls_the_binding_back_never_leaving_bound_evidence_without_a_job(self):
        self.service.handle_event(event(1, txn=1))                                        # already acknowledged (2xx) as an orphan
        purchase, op = self.purchase_with_op()
        conn = self.db.connect()
        conn.execute("CREATE TRIGGER boom BEFORE INSERT ON paddle_reconcile BEGIN SELECT RAISE(ABORT, 'x'); END")
        self.transport.create_results.append(TXN.format(1))
        try:
            with self.assertRaises(Exception):
                self.service.start_checkout(purchase)
        finally:
            conn.execute("DROP TRIGGER boom")
        row = self.paddle.operation(op)
        self.assertEqual((row["state"], row["provider_txn_id"]), ("request_sent", None))   # the binding did not survive alone
        self.assertEqual(len(self.paddle.events_for_transaction("sandbox", TXN.format(1))), 1)
        self.assertEqual(self.service.start_checkout(purchase)["result"], "not_sent")      # and no second create
        self.assertEqual(len(self.transport.create_calls), 1)
        self.paddle.sweep_stale_requests(self.now[0] + 10_000, older_than=60)              # restart: manual, never re-created
        self.assertEqual(self.paddle.operation(op)["state"], "unknown")
        self.assertEqual(self.service.start_checkout(purchase)["result"], "not_sent")
        self.assertEqual(len(self.transport.create_calls), 1)

    def test_a_late_response_that_could_not_be_saved_is_not_reported_as_late(self):
        first, _ = self.bound_purchase(1)
        second, op2 = self.purchase_with_op(2)
        self.transport.on_create = lambda op_id: self.paddle.sweep_stale_requests(self.now[0] + 10_000, older_than=60)
        self.transport.create_results.append(TXN.format(1))                                # the id belongs to another purchase
        self.assertEqual(self.service.start_checkout(second)["result"], "unknown")        # conflict: nothing saved, so not "late"
        self.assertIsNone(self.paddle.operation(op2)["late_txn_id"])

    def test_adopting_a_late_id_queues_a_reconcile_in_the_same_transaction(self):
        purchase, op = self.purchase_with_op()
        self.service.handle_event(event(1, txn=1))
        self.transport.on_create = lambda op_id: self.paddle.sweep_stale_requests(self.now[0] + 10_000, older_than=60)
        self.transport.create_results.append(TXN.format(1))
        self.assertEqual(self.service.start_checkout(purchase)["result"], "late")
        self.transport.on_create = None
        self.assertIsNone(self.paddle.reconcile_row(purchase.id))
        self.assertTrue(self.service.adopt_late(op))
        self.assertEqual((self.paddle.operation(op)["state"], self.paddle.reconcile_row(purchase.id)["state"]), ("bound", "due"))
        self.assertFalse(self.service.adopt_late(op))
        self.assertFalse(self.make_service(env="live").adopt_late(op))

    def test_the_product_configuration_is_validated_before_anything_is_sent(self):
        for bad in (dict(price_id="price_1"), dict(price_id=5), dict(quantity=0), dict(quantity=True), dict(quantity=1.0),
                    dict(currency_code="usd"), dict(currency_code="USDD"), dict(total_minor=True), dict(total_minor=-1),
                    dict(total_minor="1000"), dict(total_field="weird")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PaddleProduct(**{**dict(price_id=PRICE, quantity=1, currency_code="USD", total_minor=1000), **bad})
        with self.assertRaises(ValueError):
            self.make_service(catalog={"p1": {"price_id": PRICE}})                          # not a PaddleProduct

    def test_events_that_arrived_before_the_binding_queue_a_reconcile_once_bound(self):
        self.assertEqual(self.service.handle_event(event(1, txn=1)), "orphan")
        purchase, _ = self.purchase_with_op()
        self.transport.create_results.append(TXN.format(1))
        self.assertEqual(self.service.start_checkout(purchase)["result"], "bound")
        self.assertEqual(self.paddle.reconcile_row(purchase.id)["state"], "due")


class EventTests(Base):
    def test_informational_events_are_ignored_and_conflicts_change_nothing(self):
        self.assertEqual(self.service.handle_event(event(1, kind="transaction.updated")), "ignored")
        self.assertEqual(self.paddle.events_for_transaction("sandbox", TXN.format(1)), [])
        self.assertEqual(self.service.handle_event(event(2)), "orphan")
        self.assertEqual(self.service.handle_event(event(2, digest="f" * 64)), "conflict")
        self.assertEqual(self.paddle.events_for_transaction("sandbox", TXN.format(1))[0]["payload_digest"], f"{2:064x}")

    def test_bound_events_queue_a_job_and_a_duplicate_queues_again_idempotently(self):
        purchase, _ = self.bound_purchase()
        self.assertEqual(self.service.handle_event(event(1)), "queued")
        self.assertEqual(self.service.handle_event(event(1)), "queued")           # duplicate after a failed fetch: a job still exists
        row = self.paddle.reconcile_row(purchase.id)
        self.assertEqual((row["state"], row["pending_seq"]), ("due", 2))
        self.assertEqual(len(self.paddle.events_for_transaction("sandbox", TXN.format(1))), 1)

    def test_a_full_queue_propagates_and_writes_nothing(self):
        old = paddle_repo.ORPHAN_PER_TXN_MAX
        paddle_repo.ORPHAN_PER_TXN_MAX = 2
        self.addCleanup(setattr, paddle_repo, "ORPHAN_PER_TXN_MAX", old)
        self.service.handle_event(event(1))
        self.service.handle_event(event(2))
        with self.assertRaises(PaddleQueueFull):
            self.service.handle_event(event(3))
        self.assertEqual(len(self.paddle.events_for_transaction("sandbox", TXN.format(1))), 2)

    def test_an_event_for_another_environment_is_refused(self):
        with self.assertRaises(ValueError):
            self.service.handle_event(event(1, env="live"))


class ReconcileTests(Base):
    def run_reconcile(self):
        return self.service.reconcile_due(10)

    def test_a_grant_needs_a_stored_completed_event_and_a_complete_bound_snapshot(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1))
        self.transport.get_results.append(snapshot())
        self.assertEqual(self.run_reconcile(), [(purchase.id, "applied:deleted")])
        self.assertEqual((self.state(purchase), self.entitled()), (("paid", 1), 1))
        self.assertEqual(self.transitions(), [("pending", "paid", "reconcile", 1)])
        digest = self.db.connect().execute("SELECT snapshot_digest FROM purchase_transitions").fetchone()[0]
        self.assertEqual(digest, snapshot_digest(snapshot()))

    def test_a_completed_snapshot_without_the_event_never_grants_it_waits_then_the_event_arrives(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1, kind="adjustment.created"))             # evidence exists, but NOT a completed event
        self.transport.get_results.append(snapshot())
        self.assertEqual(self.run_reconcile(), [(purchase.id, "await:due")])
        self.assertEqual((self.state(purchase), self.entitled()), (("pending", 0), 0))
        self.assertEqual(self.paddle.reconcile_row(purchase.id)["reason"], "await_event")
        self.assertEqual(self.run_reconcile(), [])                                    # polled gently, not hammered
        self.assertEqual(self.service.handle_event(event(2)), "queued")               # the verified completed event
        self.transport.get_results.append(snapshot())
        self.assertEqual(self.run_reconcile(), [(purchase.id, "applied:deleted")])
        self.assertEqual((self.state(purchase), self.entitled()), (("paid", 1), 1))

    def test_no_event_inside_the_providers_window_ends_in_manual_and_new_evidence_reawakens_it(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1, kind="adjustment.created"))
        self.transport.get_results.append(snapshot())
        self.run_reconcile()
        self.now[0] += paddle_repo.AWAIT_EVENT_WINDOW_SECONDS + 10
        self.transport.get_results.append(snapshot())
        self.assertEqual(self.run_reconcile(), [(purchase.id, "await:manual")])
        self.assertEqual(self.paddle.reconcile_row(purchase.id)["state"], "manual")
        self.assertEqual(self.run_reconcile(), [])
        self.service.handle_event(event(2))                                            # a NEW verified completed event
        self.assertEqual(self.paddle.reconcile_row(purchase.id)["state"], "due")
        self.transport.get_results.append(snapshot())
        self.assertEqual(self.run_reconcile(), [(purchase.id, "applied:deleted")])
        self.assertEqual(self.entitled(), 1)

    def test_each_proof_dimension_is_tested_alone_against_a_snapshot_for_the_right_transaction(self):
        dims = {   # name -> (change applied to an otherwise valid snapshot for THIS purchase, expected decision reason, manual reason)
            "price": (dict(items=(Item("pri_01zzzzzzzzzzzzzzzzzzzzzzzz", 1),)), "price or quantity differs", "proof"),
            "quantity": (dict(items=(Item(PRICE, 3),)), "price or quantity differs", "proof"),
            "currency": (dict(currency_code="EUR"), "currency differs", "proof"),
            "amount": (dict(totals={"grand_total": 1, "total": 1}), "amount differs", "proof"),
            "environment": (dict(environment="live"), "snapshot is for another environment", "reconcile"),
            "incomplete": (dict(complete=False), "snapshot is not complete", "reconcile"),
            "unknown_adjustment": (dict(adjustments=(Adjustment("adj_" + "1" * 26, "mystery", "approved", "full", 1, "USD"),)),
                                   "unknown or unvalidated adjustment", "reconcile"),
            "chargeback": (dict(adjustments=(Adjustment("adj_" + "1" * 26, "chargeback", "approved", "full", 1000, "USD"),)),
                           "chargeback status is undocumented; manual reconciliation", "reconcile"),
        }
        for n, (name, (change, reason, manual_reason)) in enumerate(dims.items(), start=1):
            with self.subTest(dimension=name):
                purchase, _ = self.bound_purchase(n)
                self.service.handle_event(event(100 + n, txn=n))                         # the evidence exists: only ONE dimension is wrong
                self.transport.get_results.append(snapshot(txn=n, **change))             # for the purchase's OWN transaction
                out = self.run_reconcile()
                self.assertEqual(out, [(purchase.id, "manual:manual")])
                self.assertEqual(self.transport.get_calls[-1], TXN.format(n))            # the right transaction was fetched
                self.assertEqual(self.decisions[purchase.id], reason)             # and it failed on exactly this dimension
                row = self.paddle.reconcile_row(purchase.id)
                self.assertEqual((row["state"], row["reason"]), ("manual", manual_reason))
                self.assertEqual(self.state(purchase), ("pending", 0))
        self.assertEqual(self.entitled(), 0)

    def test_an_observer_failure_never_steers_a_payment_and_nothing_is_stored_without_one(self):
        def broken(pid, reason):
            raise RuntimeError("secret=abc")
        service = PaddleService(self.repos, self.transport, self.catalog, environment="sandbox", clock=lambda: self.now[0], on_decision=broken)
        purchase, _ = self.bound_purchase()
        service.handle_event(event(1))
        self.transport.get_results.append(snapshot())
        self.assertEqual(service.reconcile_due(10), [(purchase.id, "applied:deleted")])      # still a normal grant
        self.assertEqual((self.state(purchase), self.entitled()), (("paid", 1), 1))
        self.assertIsNone(self.paddle.reconcile_row(purchase.id))                             # no back-off, no manual row
        silent = PaddleService(self.repos, self.transport, self.catalog, environment="sandbox", clock=lambda: self.now[0])
        self.assertFalse(hasattr(silent, "decisions"))                                          # no hidden growing history

    def test_a_snapshot_for_another_transaction_is_its_own_isolated_case(self):
        purchase, _ = self.bound_purchase(1)
        self.service.handle_event(event(1, txn=1))
        self.transport.get_results.append(snapshot(txn=2))                                # valid in every other respect
        self.assertEqual(self.run_reconcile(), [(purchase.id, "manual:manual")])
        self.assertEqual(self.decisions[purchase.id], "snapshot is not for the bound transaction")
        self.assertEqual(self.entitled(), 0)

    def test_an_undecided_amount_policy_blocks_every_grant(self):
        undecided = self.make_service(catalog={"p1": PaddleProduct(PRICE, 1, "USD", total_minor=None)})
        purchase, _ = self.bound_purchase(1)
        undecided.handle_event(event(1, txn=1))
        self.transport.get_results.append(snapshot(txn=1))
        self.assertEqual(undecided.reconcile_due(10), [(purchase.id, "manual:manual")])
        self.assertEqual(self.decisions[purchase.id], "amount policy is not decided")
        self.assertEqual((self.state(purchase), self.paddle.reconcile_row(purchase.id)["reason"], self.entitled()), (("pending", 0), "proof", 0))

    def test_a_corrupt_snapshot_is_malformed_and_never_aborts_the_batch(self):
        corrupt = [("totals_text", lambda t: snapshot(t, totals={"grand_total": "1000"})), ("items_list", lambda t: snapshot(t, items=[Item(PRICE, 1)])),
                   ("status_none", lambda t: snapshot(t, status=None)), ("adjustment_object", lambda t: snapshot(t, adjustments=(object(),))),
                   ("complete_text", lambda t: snapshot(t, complete="yes")), ("quantity_bool", lambda t: snapshot(t, items=(Item(PRICE, True),))),
                   ("currency_int", lambda t: snapshot(t, currency_code=5)),
                   ("repeated_adjustment_id_identical", lambda t: snapshot(t, adjustments=(refund(1), refund(1)))),
                   ("repeated_adjustment_id_none_vs_int", lambda t: snapshot(t, adjustments=(
                       Adjustment(f"adj_{1:026d}", "refund", "approved", "full", None, "USD"), refund(1)))), ("none", lambda t: None), ("dict", lambda t: {"x": 1})]
        for index, (name, make) in enumerate(corrupt):
            with self.subTest(snapshot=name):
                a, _ = self.bound_purchase(2 * index + 1)
                b, _ = self.bound_purchase(2 * index + 2)
                ta, tb = 2 * index + 1, 2 * index + 2
                self.service.handle_event(event(10 * index + 1, txn=ta))
                self.service.handle_event(event(10 * index + 2, txn=tb))
                self.transport.get_by_txn = {TXN.format(ta): make(ta), TXN.format(tb): snapshot(tb)}
                out = dict(self.run_reconcile())
                self.assertEqual(out[b.id], "applied:deleted")                             # the healthy purchase still completes
                self.assertEqual(out[a.id], "fail:due")                                    # the corrupt one is typed, not fatal
                self.assertEqual(self.paddle.reconcile_row(a.id)["last_error"], "malformed")
                self.assertEqual((self.state(a), self.state(b)), (("pending", 0), ("paid", 1)))
        self.assertEqual(self.entitled(), len(corrupt))

    def test_an_unexpected_exception_while_deciding_is_a_closed_error_code_not_a_lost_batch(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1))
        self.transport.get_results.append(snapshot())
        import server.paddle.mapping as mapping
        original = mapping.decide
        mapping.decide = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret=abc"))
        try:
            self.assertEqual(self.run_reconcile(), [(purchase.id, "fail:due")])
        finally:
            mapping.decide = original
        self.assertEqual(self.paddle.reconcile_row(purchase.id)["last_error"], "unexpected")
        self.assertNotIn("secret", str(tuple(self.db.connect().execute("SELECT * FROM paddle_reconcile").fetchone())))

    def test_a_refund_seen_before_the_payment_never_grants(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1, kind="adjustment.created"))
        self.service.handle_event(event(2))
        self.transport.get_results.append(snapshot(adjustments=[refund()]))
        self.assertEqual(self.run_reconcile(), [(purchase.id, "applied:deleted")])
        self.assertEqual((self.state(purchase), self.entitled()), (("refunded", 1), 0))
        self.assertEqual(self.transitions(), [("pending", "refunded", "reconcile", 1)])
        self.assertEqual(self.db.connect().execute("SELECT COUNT(*) FROM entitlements").fetchone()[0], 0)

    def test_a_stale_completed_snapshot_after_a_refund_cannot_reactivate(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1))
        self.transport.get_results.append(snapshot())
        self.run_reconcile()
        self.service.handle_event(event(2, kind="adjustment.created"))
        self.transport.get_results.append(snapshot(adjustments=[refund()]))
        self.run_reconcile()
        self.assertEqual((self.state(purchase), self.entitled()), (("refunded", 2), 0))
        self.service.handle_event(event(3))                                              # old completed delivered again
        self.transport.get_results.append(snapshot())
        self.run_reconcile()
        self.assertEqual((self.state(purchase), self.entitled()), (("refunded", 2), 0))

    def test_a_concurrent_change_between_claim_and_apply_is_stale_and_looks_again(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1))
        def bump(_txn):
            self.repos.purchases.compare_and_swap(purchase.id, 0, state=PurchaseState.PENDING, revision=1)   # another writer moved r0
        self.transport.on_get = bump
        self.transport.get_results.append(snapshot())
        self.assertEqual(self.run_reconcile(), [(purchase.id, "stale")])
        self.assertEqual((self.state(purchase), self.entitled()), (("pending", 1), 0))
        self.transport.on_get = None
        self.transport.get_results.append(snapshot())
        self.assertEqual(self.run_reconcile(), [(purchase.id, "applied:deleted")])    # a FRESH fetch and a fresh r0
        self.assertEqual(self.state(purchase), ("paid", 2))

    def test_a_worker_that_lost_its_claim_applies_nothing(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1))
        stolen = []
        def steal(_txn):
            self.now[0] += paddle_repo.LEASE_SECONDS + 1
            stolen.extend(self.paddle.claim_due(self.now[0], 1))                       # the lease expired: a newer claim took over
        self.transport.on_get = steal
        self.transport.get_results.append(snapshot())
        self.assertEqual(self.run_reconcile(), [(purchase.id, "lost:lost")])
        self.assertEqual((self.state(purchase), self.entitled(), len(stolen)), (("pending", 0), 0, 1))
        row = self.paddle.reconcile_row(purchase.id)
        self.assertEqual((row["owner"], row["generation"]), (stolen[0]["owner"], 2))   # the newer claim is untouched

    def test_new_evidence_during_the_fetch_defeats_the_old_decision_and_is_never_buried(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1))
        self.transport.on_get = lambda _txn: self.service.handle_event(event(2, kind="adjustment.created"))   # a refund event arrives mid-fetch
        self.transport.get_results.append(snapshot())                                  # the OLD view: completed, no refund yet
        self.assertEqual(self.run_reconcile(), [(purchase.id, "lost:requeued")])
        self.assertEqual((self.state(purchase), self.entitled()), (("pending", 0), 0))   # the old snapshot never granted
        self.assertEqual(self.paddle.reconcile_row(purchase.id)["state"], "due")
        self.transport.on_get = None
        self.transport.get_results.append(snapshot(adjustments=[refund()]))            # the fresh view includes the refund
        self.assertEqual(self.run_reconcile(), [(purchase.id, "applied:deleted")])
        self.assertEqual((self.state(purchase), self.entitled()), (("refunded", 1), 0))

    def test_transport_failures_back_off_with_a_closed_error_code_and_end_in_manual(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1))
        expected = {"timeout": PaddleOutcomeUnknown("t"), "rejected": PaddleTransportRejected("r", 500),
                    "malformed": PaddleMalformed("m"), "unexpected": RuntimeError("password=hunter2 https://x/?token=1")}
        for code, failure in expected.items():
            self.transport.get_results.append(failure)
            self.assertEqual(self.run_reconcile(), [(purchase.id, "fail:due")])
            self.assertEqual(self.paddle.reconcile_row(purchase.id)["last_error"], code)
            self.now[0] += paddle_repo.BACKOFF_CAP_SECONDS + 1
        self.transport.get_results.append("not a snapshot")
        self.assertEqual(self.run_reconcile(), [(purchase.id, "fail:due")])             # wrong type is malformed
        self.assertEqual(self.paddle.reconcile_row(purchase.id)["last_error"], "malformed")
        stored = str(tuple(self.db.connect().execute("SELECT * FROM paddle_reconcile").fetchone()))
        self.assertNotIn("hunter2", stored)
        for _ in range(paddle_repo.MAX_ATTEMPTS):
            self.now[0] += paddle_repo.BACKOFF_CAP_SECONDS + 1
            self.transport.get_results.append(RuntimeError("x"))
            out = self.run_reconcile()
            if out and out[0][1] == "fail:manual":
                break
        self.assertEqual(self.paddle.reconcile_row(purchase.id)["state"], "manual")
        self.assertEqual((self.state(purchase), self.entitled()), (("pending", 0), 0))

    def test_a_deleted_owner_gets_the_state_but_no_entitlement(self):
        purchase, _ = self.bound_purchase()
        self.repos.users.mark_deleted("u1")
        self.service.handle_event(event(1))
        self.transport.get_results.append(snapshot())
        self.run_reconcile()
        self.assertEqual((self.state(purchase), self.entitled()), (("paid", 1), 0))

    def test_two_purchases_of_the_same_product_stay_separate_and_a_job_for_a_non_paddle_purchase_goes_to_manual(self):
        a, _ = self.bound_purchase(1)
        b, _ = self.bound_purchase(2)
        for n in (1, 2):
            self.service.handle_event(event(n, txn=n))
        self.transport.get_by_txn = {TXN.format(1): snapshot(1), TXN.format(2): snapshot(2)}   # claims come in any order
        self.assertEqual(sorted(o for _, o in self.run_reconcile()), ["applied:deleted", "applied:deleted"])
        self.assertEqual(self.entitled(), 2)
        mock = self.repos.purchases.create("u1", "p1", "mock", "m-1")
        self.paddle.mark_due(mock.id, self.now[0])
        self.assertEqual(self.run_reconcile(), [(mock.id, "manual:manual")])
        self.assertEqual(self.state(mock), ("pending", 0))

    def test_every_status_change_has_exactly_one_audit_row_with_its_cause(self):
        purchase, _ = self.bound_purchase()
        self.service.handle_event(event(1))
        self.transport.get_results.append(snapshot())
        self.run_reconcile()
        self.service.handle_event(event(2, kind="adjustment.created"))
        self.transport.get_results.append(snapshot(adjustments=[refund()]))
        self.run_reconcile()
        self.assertEqual(self.transitions(), [("pending", "paid", "reconcile", 1), ("paid", "refunded", "reconcile", 1)])


if __name__ == "__main__":
    unittest.main()
