# SPDX-License-Identifier: AGPL-3.0-or-later
"""Atomic payment-event apply: dedup, fencing, transition policy, CAS, grant/revoke and event row are one effect."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from server.app import _apply_verified_event
from server.db import Database
from server.models import ConflictError, PaymentEventStatus, Product, PurchaseState, StorageError
from server.payment_apply import apply_decision, apply_reconciled
from server.paddle_repo import PaddleRepository
from server.repositories import Repositories


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "a.db")
        self.clock = [1_000.0]
        self.db = Database(self.path, clock=lambda: self.clock[0])
        self.db.migrate()
        self.addCleanup(self.db.close)
        self.repos = Repositories(self.db)
        self.repos.products.upsert(Product("p1", "P1", "USD", 500, "python", True))
        self.repos.users.create(user_id="u1")
        self.purchase = self.repos.purchases.create("u1", "p1", "paddle-sandbox", "srv-1")

    def apply(self, repos=None, **kw):
        base = dict(provider="paddle-sandbox", event_id="evt-1", payload_digest="d" * 64, purchase_id=self.purchase.id,
                    new_state=PurchaseState.PAID, event_label="transaction.completed")
        base.update(kw)
        if "expected_revision" not in kw and kw.get("new_revision") is None:
            base["expected_revision"] = 0            # the default fence of the snapshot-based tests
        return apply_decision(repos or self.repos, **base)

    def state(self):
        p = self.repos.purchases.get(self.purchase.id)
        return p.state.value, p.revision

    def counts(self):
        c = self.db.connect()
        return (c.execute("SELECT COUNT(*) FROM entitlements").fetchone()[0], c.execute("SELECT COUNT(*) FROM payment_events").fetchone()[0])


class ApplyTests(Base):
    def test_one_effect_state_revision_grant_and_event(self):
        result = self.apply()
        self.assertEqual((result.outcome, result.new_state, result.granted), ("applied", "paid", True))
        self.assertEqual(self.state(), ("paid", 1))
        self.assertTrue(self.repos.purchases.is_entitled(self.repos.purchases.get(self.purchase.id)))
        event = self.repos.payment_events.get("paddle-sandbox", "evt-1")
        self.assertEqual((event.status, event.revision, event.purchase_id), (PaymentEventStatus.APPLIED, 1, self.purchase.id))

    def test_failed_grant_or_failed_event_row_rolls_everything_back(self):
        for table, label in (("entitlements", "grant"), ("payment_events", "event row")):
            with self.subTest(table=table):
                conn = self.db.connect()
                conn.execute(f"CREATE TRIGGER boom BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'x'); END")
                try:
                    with self.assertRaises((sqlite3.Error, StorageError)):
                        self.apply(event_id=f"evt-{label}")
                finally:
                    conn.execute("DROP TRIGGER boom")
                self.assertEqual(self.state(), ("pending", 0))
                self.assertEqual(self.counts(), (0, 0))

    def test_stale_revision_writes_nothing_not_even_the_event(self):
        self.assertEqual(self.apply(expected_revision=7).outcome, "stale")
        self.assertEqual((self.state(), self.counts()), (("pending", 0), (0, 0)))

    def test_cas_loser_never_grants_under_concurrency(self):
        results = []
        errors = []

        def run(i):
            db = Database(self.path, clock=lambda: self.clock[0])
            try:
                repos = Repositories(db)
                results.append(self.apply(repos, event_id=f"evt-race-{i}", payload_digest=f"{i}" * 64).outcome)
            except Exception as exc:                      # a busy lock would surface here, never a double grant
                errors.append(exc)
            finally:
                db.close()
        threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results).count("applied"), 1)
        self.assertEqual(sorted(results).count("stale"), 5)
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(self.state(), ("paid", 1))

    def test_duplicate_is_a_no_op_and_a_changed_body_is_a_conflict_with_no_effect(self):
        self.apply()
        before = (self.state(), self.counts())
        self.assertEqual(self.apply(expected_revision=1).outcome, "duplicate")
        self.assertEqual(self.apply(expected_revision=1, payload_digest="e" * 64).outcome, "conflict")
        self.assertEqual((self.state(), self.counts()), before)

    def test_dedup_key_includes_the_provider_environment(self):
        self.apply()
        other = self.repos.purchases.create("u1", "p1", "paddle-live", "srv-2")
        result = apply_decision(self.repos, provider="paddle-live", event_id="evt-1", payload_digest="d" * 64,
                                purchase_id=other.id, expected_revision=0, new_state=PurchaseState.PAID, event_label="x")
        self.assertEqual(result.outcome, "applied")   # same event id in another environment is a different event

    def test_unknown_purchase_is_a_no_op(self):
        self.assertEqual(self.apply(purchase_id="nope").outcome, "unknown_purchase")
        self.assertEqual(self.counts(), (0, 0))

    def test_deleted_owner_gets_no_entitlement(self):
        self.repos.users.mark_deleted("u1")
        result = self.apply()
        self.assertEqual((result.outcome, result.granted), ("applied", False))
        self.assertEqual(self.counts()[0], 0)

    def test_refund_revokes_and_two_purchases_of_the_same_product_stay_separate(self):
        second = self.repos.purchases.create("u1", "p1", "paddle-sandbox", "srv-2")
        self.apply()
        self.apply(purchase_id=second.id, event_id="evt-2", payload_digest="2" * 64)
        self.assertEqual(self.counts()[0], 2)
        refunded = self.apply(event_id="evt-3", payload_digest="3" * 64, expected_revision=1, new_state=PurchaseState.REFUNDED)
        self.assertEqual((refunded.outcome, refunded.revoked), ("applied", True))
        active = self.repos.entitlements.list_active_for_user("u1")
        self.assertEqual([e.source_purchase_id for e in active], [second.id])

    def test_the_transition_policy_is_mandatory_terminal_states_never_move(self):
        self.apply(new_state=PurchaseState.REFUNDED, event_id="evt-r", payload_digest="r" * 64)
        for i, target in enumerate((PurchaseState.PAID, PurchaseState.PENDING, PurchaseState.CANCELLED, PurchaseState.CHARGEBACK)):
            with self.subTest(target=target):
                result = self.apply(event_id=f"evt-late-{i}", payload_digest=f"{i}" * 64, expected_revision=1, new_state=target)
                self.assertEqual(result.outcome, "ignored")
                self.assertEqual(self.state(), ("refunded", 1))
                self.assertEqual(self.repos.payment_events.get("paddle-sandbox", f"evt-late-{i}").status, PaymentEventStatus.IGNORED)
        self.assertEqual(self.repos.entitlements.list_active_for_user("u1"), [])

    def test_refund_before_the_first_paid_event_never_grants(self):
        self.apply(new_state=PurchaseState.REFUNDED, event_id="evt-r", payload_digest="r" * 64)
        late = self.apply(event_id="evt-paid", payload_digest="p" * 64, expected_revision=1)
        self.assertEqual((late.outcome, late.granted), ("ignored", False))
        self.assertEqual(self.counts()[0], 0)

    def test_paid_never_goes_back_or_to_cancelled(self):
        self.apply()
        for i, target in enumerate((PurchaseState.PENDING, PurchaseState.CANCELLED)):
            with self.subTest(target=target):
                self.assertEqual(self.apply(event_id=f"e{i}", payload_digest=f"{i}" * 64, expected_revision=1, new_state=target).outcome, "ignored")
                self.assertEqual(self.state(), ("paid", 1))

    def test_a_no_op_changes_neither_revision_nor_grant(self):
        self.apply()
        entitlement = self.repos.entitlements.list_active_for_user("u1")[0]
        result = self.apply(event_id="evt-again", payload_digest="a" * 64, expected_revision=1)   # paid -> paid
        self.assertEqual((result.outcome, result.granted), ("no_change", False))
        self.assertEqual(self.state(), ("paid", 1))
        self.assertEqual(self.repos.entitlements.list_active_for_user("u1")[0].id, entitlement.id)
        self.assertEqual(self.repos.payment_events.get("paddle-sandbox", "evt-again").status, PaymentEventStatus.IGNORED)

    def test_exactly_one_valid_fence_is_required_before_any_write(self):
        kw = dict(provider="paddle-sandbox", event_id="x", payload_digest="d" * 64, purchase_id=self.purchase.id,
                  new_state=PurchaseState.PAID, event_label="x")
        for fence in ({}, dict(expected_revision=0, new_revision=1), dict(expected_revision=True), dict(expected_revision=-1),
                      dict(expected_revision="0"), dict(expected_revision=1.0), dict(new_revision=True), dict(new_revision=-3),
                      dict(new_revision=None, expected_revision=None)):
            with self.subTest(fence=fence), self.assertRaises(ValueError):
                apply_decision(self.repos, **kw, **fence)
        with self.assertRaises(ValueError):
            apply_decision(self.repos, **{**kw, "new_state": "paid"}, expected_revision=0)
        self.assertEqual((self.state(), self.counts()), (("pending", 0), (0, 0)))

    def test_a_provider_revision_must_move_forward_and_is_never_written_backwards(self):
        self.assertEqual(self.apply(expected_revision=None, new_revision=5).outcome, "applied")
        self.assertEqual(self.state(), ("paid", 5))
        for i, rev in enumerate((5, 4, 0)):
            self.assertEqual(self.apply(event_id=f"b{i}", payload_digest=f"{i}" * 64, expected_revision=None, new_revision=rev,
                                        new_state=PurchaseState.REFUNDED).outcome, "ignored")
        self.assertEqual((self.state(), self.counts()[1]), (("paid", 5), 1))

    def test_local_revision_grows_only_for_an_accepted_change(self):
        self.apply(event_id="a", payload_digest="a" * 64)
        self.apply(event_id="b", payload_digest="b" * 64, expected_revision=0)          # stale: nothing
        self.assertEqual(self.state(), ("paid", 1))
        self.apply(event_id="c", payload_digest="c" * 64, expected_revision=1, new_state=PurchaseState.REFUNDED)
        self.assertEqual(self.state(), ("refunded", 2))


class RetryAndBindingTests(Base):
    def test_a_received_or_failed_row_is_finalised_by_a_valid_retry_not_blocked(self):
        for status in (PaymentEventStatus.RECEIVED, PaymentEventStatus.FAILED):
            with self.subTest(status=status):
                purchase = self.repos.purchases.create("u1", "p1", "paddle-sandbox", f"srv-{status.value}")
                self.repos.payment_events.record("paddle-sandbox", f"evt-{status.value}", "d" * 64, purchase_id=None,
                                                 normalized_state="seen", revision=0, status=status)
                result = apply_decision(self.repos, provider="paddle-sandbox", event_id=f"evt-{status.value}",
                                        payload_digest="d" * 64, purchase_id=purchase.id, expected_revision=0,
                                        new_state=PurchaseState.PAID, event_label="transaction.completed")
                self.assertEqual(result.outcome, "applied")
                row = self.repos.payment_events.get("paddle-sandbox", f"evt-{status.value}")
                self.assertEqual((row.status, row.purchase_id), (PaymentEventStatus.APPLIED, purchase.id))

    def test_a_received_row_with_another_digest_is_a_conflict_that_keeps_the_original_evidence(self):
        self.repos.payment_events.record("paddle-sandbox", "evt-x", "1" * 64, purchase_id=None, normalized_state="seen", revision=0)
        result = self.apply(event_id="evt-x", payload_digest="2" * 64)
        self.assertEqual(result.outcome, "conflict")
        row = self.repos.payment_events.get("paddle-sandbox", "evt-x")
        self.assertEqual((row.payload_digest, row.status, row.purchase_id), ("1" * 64, PaymentEventStatus.RECEIVED, None))
        self.assertEqual(self.state(), ("pending", 0))

    def test_a_received_or_failed_row_bound_to_purchase_a_can_never_be_finalised_for_purchase_b(self):
        other = self.repos.purchases.create("u1", "p1", "paddle-sandbox", "srv-b")
        for status in (PaymentEventStatus.RECEIVED, PaymentEventStatus.FAILED):
            with self.subTest(status=status):
                event_id = f"evt-{status.value}"
                self.repos.payment_events.record("paddle-sandbox", event_id, "d" * 64, purchase_id=self.purchase.id,
                                                 normalized_state="seen", revision=0, status=status)
                result = self.apply(purchase_id=other.id, event_id=event_id, expected_revision=0)
                self.assertEqual(result.outcome, "conflict")
                row = self.repos.payment_events.get("paddle-sandbox", event_id)
                self.assertEqual((row.purchase_id, row.status), (self.purchase.id, status))     # untouched
                self.assertEqual(self.repos.purchases.get(other.id).state, PurchaseState.PENDING)
                self.assertEqual(self.repos.entitlements.list_active_for_user("u1"), [])

    def test_finalising_checks_the_row_count_and_rolls_back_when_the_row_changed_underneath(self):
        self.repos.payment_events.record("paddle-sandbox", "evt-u", "d" * 64, purchase_id=None, normalized_state="seen", revision=0)
        conn = self.db.connect()
        conn.execute("CREATE TRIGGER swallow BEFORE UPDATE ON payment_events BEGIN SELECT RAISE(IGNORE); END")  # update changes no row
        try:
            with self.assertRaises(StorageError):
                self.apply(event_id="evt-u")
        finally:
            conn.execute("DROP TRIGGER swallow")
        self.assertEqual(self.state(), ("pending", 0))
        self.assertEqual(self.repos.entitlements.list_active_for_user("u1"), [])

    def test_an_unbound_received_row_binds_to_exactly_one_purchase_once(self):
        other = self.repos.purchases.create("u1", "p1", "paddle-sandbox", "srv-b")
        self.repos.payment_events.record("paddle-sandbox", "evt-n", "d" * 64, purchase_id=None, normalized_state="seen", revision=0)
        self.assertEqual(self.apply(event_id="evt-n").outcome, "applied")
        self.assertEqual(self.repos.payment_events.get("paddle-sandbox", "evt-n").purchase_id, self.purchase.id)
        self.assertEqual(self.apply(purchase_id=other.id, event_id="evt-n").outcome, "conflict")     # now final and bound
        self.assertEqual(self.repos.purchases.get(other.id).state, PurchaseState.PENDING)

    def test_an_event_row_bound_to_another_purchase_is_a_conflict_not_a_duplicate(self):
        other = self.repos.purchases.create("u1", "p1", "paddle-sandbox", "srv-other")
        self.apply()
        result = self.apply(purchase_id=other.id, expected_revision=0)       # same id and digest, different purchase
        self.assertEqual(result.outcome, "conflict")
        self.assertEqual(self.repos.purchases.get(other.id).state, PurchaseState.PENDING)

    def test_a_purchase_from_another_provider_namespace_is_never_applied(self):
        live = self.repos.purchases.create("u1", "p1", "paddle-live", "srv-live")
        result = self.apply(purchase_id=live.id)        # event says sandbox, purchase belongs to live
        self.assertEqual(result.outcome, "unknown_purchase")
        self.assertEqual(self.repos.purchases.get(live.id).state, PurchaseState.PENDING)
        self.assertEqual(self.counts(), (0, 0))


TXN1 = "txn_01abcdefghjkmnpqrstvwxyz01"
TXN2 = "txn_01abcdefghjkmnpqrstvwxyz02"


def completed_event(txn=TXN1, env="sandbox", n=1, kind="transaction.completed"):
    from server.paddle.types import VerifiedEvent
    return VerifiedEvent("paddle", env, f"evt_{n:026d}", kind, "2026-10-19T01:02:03Z", f"ntf_{n:026d}", f"{n:064x}", txn, None, {})


class ApplyReconciledTests(Base):
    """The snapshot-based entry: claim, revision, BINDING, legality and verified evidence are checked in the commit."""

    def setUp(self):
        super().setUp()
        self.paddle = PaddleRepository(self.db)
        self.op = self.paddle.plan(self.purchase.id, "sandbox", self.clock[0])
        self.paddle.mark_request_sent(self.op, self.clock[0])
        self.assertEqual(self.paddle.bind(self.op, TXN1, self.clock[0], environment="sandbox"), "bound")
        self.paddle.mark_due(self.purchase.id, self.clock[0])
        self.claim = self.paddle.claim_due(self.clock[0], 1)[0]
        self.digest = "a" * 64

    def evidence(self, **kw):
        self.assertIn(self.paddle.record_verified(completed_event(**kw), self.clock[0]), ("new", "duplicate"))

    def go(self, **kw):
        base = dict(purchase_id=self.purchase.id, provider="paddle-sandbox", owner=self.claim["owner"],
                    generation=self.claim["generation"], expected_revision=self.claim["revision"],
                    new_state=PurchaseState.PAID, snapshot_digest=self.digest, snapshot_transaction_id=TXN1)
        base.update(kw)
        return apply_reconciled(self.repos, self.paddle, **base)

    def audit(self):
        return [tuple(r) for r in self.db.connect().execute(
            "SELECT from_state, to_state, from_revision, to_revision, cause, snapshot_digest, event_id FROM purchase_transitions")]

    def test_applied_in_one_effect_with_an_audit_row_that_carries_the_digest_and_no_event_row(self):
        self.evidence()
        result = self.go()
        self.assertEqual((result.outcome, result.granted), ("applied", True))
        self.assertEqual(self.state(), ("paid", 1))
        self.assertEqual(self.audit(), [("pending", "paid", 0, 1, "reconcile", self.digest, None)])
        self.assertEqual(self.counts(), (1, 0))                                    # no payment_events row: nothing was invented

    def test_a_grant_cannot_be_made_without_the_stored_verified_completed_event_and_there_is_no_bypass(self):
        # no evidence at all
        self.assertEqual(self.go().outcome, "blocked")
        # evidence of another kind, for another transaction, or for another environment is not enough
        self.paddle.record_verified(completed_event(kind="adjustment.created", n=2), self.clock[0])
        self.paddle.record_verified(completed_event(txn=TXN2, n=3), self.clock[0])
        self.paddle.record_verified(completed_event(env="live", n=4), self.clock[0])
        self.assertEqual(self.go().outcome, "blocked")
        self.assertEqual((self.state(), self.counts(), self.audit()), (("pending", 0), (0, 0), []))
        # the old optional/injected precondition is gone: passing one is a TypeError, never a bypass
        for bypass in (None, lambda: True):
            with self.assertRaises(TypeError):
                self.go(grant_precondition=bypass)
        self.evidence()                                                              # the legitimate bound evidence
        self.assertEqual(self.go().outcome, "applied")

    def test_every_transition_needs_the_bound_operation_and_the_snapshot_for_that_transaction(self):
        self.evidence()
        for target in (PurchaseState.PAID, PurchaseState.REFUNDED, PurchaseState.CANCELLED):
            with self.subTest(target=target):
                self.assertEqual(self.go(new_state=target, snapshot_transaction_id=TXN2).outcome, "unbound")   # another transaction
        self.assertEqual(self.go(new_state=PurchaseState.REFUNDED, provider="paddle-live").outcome, "unknown_purchase")
        # an operation that is not bound (here: a fresh purchase with a merely planned one) can never be applied
        other = self.repos.purchases.create("u1", "p1", "paddle-sandbox", "srv-other")
        planned = self.paddle.plan(other.id, "sandbox", self.clock[0])
        self.paddle.mark_due(other.id, self.clock[0])
        claim = [c for c in self.paddle.claim_due(self.clock[0], 5) if c["purchase_id"] == other.id][0]
        for target in (PurchaseState.PAID, PurchaseState.REFUNDED):
            result = apply_reconciled(self.repos, self.paddle, purchase_id=other.id, provider="paddle-sandbox", owner=claim["owner"],
                                      generation=claim["generation"], expected_revision=claim["revision"], new_state=target,
                                      snapshot_digest=self.digest, snapshot_transaction_id=TXN1)
            self.assertEqual(result.outcome, "unbound")
        self.assertEqual(self.repos.purchases.get(other.id).state, PurchaseState.PENDING)
        self.assertEqual(self.paddle.operation(planned)["state"], "planned")
        self.assertEqual((self.state(), self.counts()), (("pending", 0), (0, 0)))

    def test_a_stale_or_foreign_claim_writes_nothing(self):
        self.evidence()
        for name, kw in {"wrong_owner": dict(owner="w-other"), "wrong_generation": dict(generation=self.claim["generation"] + 1)}.items():
            with self.subTest(case=name):
                self.assertEqual(self.go(**kw).outcome, "lost_claim")
        self.paddle.mark_due(self.purchase.id, self.clock[0] + 1)                    # new evidence since the claim
        self.assertEqual(self.go().outcome, "lost_claim")
        self.assertEqual((self.state(), self.counts(), self.audit()), (("pending", 0), (0, 0), []))

    def test_revision_provider_legality_and_no_change(self):
        self.evidence()
        self.assertEqual(self.go(expected_revision=9).outcome, "stale")
        self.assertEqual(self.go(provider="paddle-live").outcome, "unknown_purchase")
        self.assertEqual(self.go(purchase_id="nope").outcome, "lost_claim")      # no claim exists for it: checked first
        self.assertEqual(self.go(new_state=PurchaseState.PENDING).outcome, "no_change")
        self.assertEqual(self.state(), ("pending", 0))
        self.assertEqual(self.go(new_state=PurchaseState.REFUNDED).outcome, "applied")      # pending -> refunded, never granted
        self.assertEqual((self.counts()[0], self.state()), (0, ("refunded", 1)))
        self.assertEqual(self.go(expected_revision=1, new_state=PurchaseState.PAID).outcome, "ignored")   # terminal never moves
        self.assertEqual(len(self.audit()), 1)

    def test_a_deleted_owner_gets_the_state_without_an_entitlement(self):
        self.evidence()
        self.repos.users.mark_deleted("u1")
        result = self.go()
        self.assertEqual((result.outcome, result.granted, self.counts()[0]), ("applied", False, 0))

    def test_a_failure_inside_rolls_everything_back(self):
        self.evidence()
        conn = self.db.connect()
        conn.execute("CREATE TRIGGER boom BEFORE INSERT ON purchase_transitions BEGIN SELECT RAISE(ABORT, 'x'); END")
        try:
            with self.assertRaises((sqlite3.Error, StorageError)):
                self.go()
        finally:
            conn.execute("DROP TRIGGER boom")
        self.assertEqual((self.state(), self.counts()), (("pending", 0), (0, 0)))

    def test_bad_inputs_are_refused_before_any_write(self):
        self.evidence()
        for kw in (dict(expected_revision=True), dict(expected_revision=-1), dict(expected_revision="0"), dict(new_state="paid"),
                   dict(snapshot_digest="short"), dict(snapshot_digest=None), dict(snapshot_transaction_id=""), dict(snapshot_transaction_id=None)):
            with self.subTest(kw=str(kw)[:30]), self.assertRaises(ValueError):
                self.go(**kw)
        self.assertEqual(self.state(), ("pending", 0))

    def test_the_event_based_entry_also_writes_one_audit_row_with_the_event_id(self):
        self.apply()
        rows = [tuple(r) for r in self.db.connect().execute("SELECT cause, event_provider, event_id, snapshot_digest FROM purchase_transitions")]
        self.assertEqual(rows, [("verified_event", "paddle-sandbox", "evt-1", None)])


class ProviderEnumTests(Base):
    def test_only_the_closed_set_is_accepted_and_nothing_parses_substrings(self):
        for ok in ("mock", "test", "paddle-sandbox", "paddle-live"):
            self.repos.payment_events.record(ok, "e-" + ok, "d" * 64, purchase_id=None, normalized_state="x", revision=0)
        for bad in ("paddle", "paddle-", "paddle-sandbox-x", "Paddle-Live", "paddle-staging", "", "stripe", None, 5):
            with self.subTest(provider=bad):
                with self.assertRaises(ValueError):
                    self.repos.purchases.create("u1", "p1", bad, "x-" + str(bad))
                with self.assertRaises(ValueError):
                    self.repos.payment_events.record(bad, "e", "d" * 64, purchase_id=None, normalized_state="x", revision=0)


class MockPathContractTests(Base):
    """The mock webhook path keeps its external contract while now being one atomic effect."""

    def setUp(self):
        super().setUp()
        self.mock = self.repos.purchases.create("u1", "p1", "mock", "prov-1")

    def event(self, **kw):
        base = dict(state="paid", provider_purchase_id="prov-1", revision=1, event_id="m-1", payload_digest="a" * 64)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_newer_revision_applies_older_is_ignored_and_a_changed_body_still_raises(self):
        _apply_verified_event(self.repos, "mock", self.event())
        got = self.repos.purchases.get(self.mock.id)
        self.assertEqual((got.state, got.revision), (PurchaseState.PAID, 1))
        _apply_verified_event(self.repos, "mock", self.event(event_id="m-0", revision=1, state="refunded"))  # not newer
        self.assertEqual(self.repos.purchases.get(self.mock.id).state, PurchaseState.PAID)
        with self.assertRaises(ConflictError):
            _apply_verified_event(self.repos, "mock", self.event(payload_digest="b" * 64))
        with self.assertRaises(ValueError):
            _apply_verified_event(self.repos, "mock", self.event(event_id="m-2", revision=5, state="weird"))
        self.assertEqual(self.repos.purchases.get(self.mock.id).revision, 1)

    def test_the_real_mock_path_also_refuses_terminal_to_paid_and_cross_state_changes(self):
        _apply_verified_event(self.repos, "mock", self.event(state="refunded", revision=1))
        _apply_verified_event(self.repos, "mock", self.event(event_id="m-late", state="paid", revision=9, payload_digest="c" * 64))
        got = self.repos.purchases.get(self.mock.id)
        self.assertEqual((got.state, got.revision), (PurchaseState.REFUNDED, 1))
        self.assertEqual(self.repos.entitlements.list_active_for_user("u1"), [])
        _apply_verified_event(self.repos, "mock", self.event(event_id="m-bad", state="paid", revision="9", payload_digest="d" * 64))
        _apply_verified_event(self.repos, "mock", self.event(event_id="m-bool", state="paid", revision=True, payload_digest="e" * 64))
        self.assertEqual(self.repos.purchases.get(self.mock.id).state, PurchaseState.REFUNDED)

    def test_unknown_purchase_is_a_silent_no_op(self):
        _apply_verified_event(self.repos, "mock", self.event(provider_purchase_id="nope"))
        self.assertEqual(self.counts(), (0, 0))


if __name__ == "__main__":
    unittest.main()
