# SPDX-License-Identifier: AGPL-3.0-or-later
"""Purchase-state decision from an authoritative provider snapshot (offline, pure, no side effects).

Rules (protocol §7): only a snapshot can decide; events merely say when to look. A grant needs a ``completed``
transaction, matching proof (environment, price, quantity, currency, amount per the approved policy) and an
adjustment list that contains nothing terminal or unknown. A full approved refund wins over a payment even when
it was observed before the first ``completed`` event. ``cancelled`` needs the authoritative status ``canceled``.
Chargebacks and anything unknown go to manual reconciliation: their final status is undocumented. No state ever
moves backwards and no new enum value exists.
"""
from __future__ import annotations

from .types import AuthoritativeState, Adjustment, Decision, Expected, Item, PaddleMalformed, VerifiedEvent

TERMINAL_STATES = frozenset({"refunded", "chargeback", "cancelled"})
INFORMATIONAL_EVENTS = frozenset({
    "transaction.created", "transaction.updated", "transaction.ready", "transaction.billed", "transaction.paid",
    "transaction.payment_failed", "transaction.past_due",
})
LOOK_EVENTS = frozenset({"transaction.completed", "transaction.canceled", "adjustment.created", "adjustment.updated"})
ADJUSTMENT_ACTIONS = frozenset({"credit", "refund", "chargeback", "chargeback_warning", "chargeback_reverse",
                                "chargeback_warning_reverse", "credit_reverse"})
ADJUSTMENT_STATUSES = frozenset({"pending_approval", "approved", "rejected", "reversed"})
ADJUSTMENT_TYPES = frozenset({"full", "partial"})
TRANSACTION_STATUSES = frozenset({"draft", "ready", "billed", "paid", "completed", "canceled", "past_due"})


def should_look(event: VerifiedEvent) -> bool:
    """Whether this event is a reason to fetch an authoritative snapshot. Informational events never change state."""
    return event.event_type in LOOK_EVENTS


def _is_int(value) -> bool:
    return type(value) is int      # bool is an int subclass and True == 1: never a proof value


def _proof_failures(state: AuthoritativeState, expected: Expected) -> str | None:
    if state.environment != expected.environment:
        return "environment differs"
    if not all(isinstance(i.price_id, str) and _is_int(i.quantity) for i in state.items) or not _is_int(expected.quantity):
        return "item shape differs"
    items = [(i.price_id, i.quantity) for i in state.items]
    if items != [(expected.price_id, expected.quantity)]:
        return "price or quantity differs"
    if state.currency_code != expected.currency_code:
        return "currency differs"
    if expected.total_minor is None:
        return "amount policy is not decided"
    actual = state.totals.get(expected.total_field)
    if not _is_int(expected.total_minor) or not _is_int(actual) or actual != expected.total_minor:
        return "amount differs"
    return None


def decide(state_before: str, snapshot: AuthoritativeState, expected: Expected) -> Decision:
    """Decide the next purchase state from the CURRENT state and a fresh authoritative snapshot."""
    if state_before in TERMINAL_STATES:
        return Decision("none", "already terminal")
    if state_before not in ("pending", "paid"):
        return Decision("reconcile", "unknown current state")
    # 0. The snapshot must be COMPLETE and for exactly the bound transaction, for every kind of transition
    #    (refund and cancel too); anything else is manual reconciliation, never a decision.
    if snapshot.environment != expected.environment:
        return Decision("reconcile", "snapshot is for another environment")
    if snapshot.complete is not True:
        return Decision("reconcile", "snapshot is not complete")
    if not isinstance(expected.transaction_id, str) or snapshot.transaction_id != expected.transaction_id:
        return Decision("reconcile", "snapshot is not for the bound transaction")
    # 1. Validate EVERY adjustment against the documented domains before any rule can skip or accept it.
    for adjustment in snapshot.adjustments:
        if (adjustment.action not in ADJUSTMENT_ACTIONS or adjustment.status not in ADJUSTMENT_STATUSES
                or adjustment.type not in ADJUSTMENT_TYPES):
            return Decision("reconcile", "unknown or unvalidated adjustment")
    if snapshot.status not in TRANSACTION_STATUSES:
        return Decision("reconcile", "unknown transaction status")
    # 2. Terminal adjustments.
    full_refund = False
    for adjustment in snapshot.adjustments:
        if adjustment.action in ("chargeback", "chargeback_reverse"):
            return Decision("reconcile", "chargeback status is undocumented; manual reconciliation")
        if adjustment.action == "refund" and adjustment.type == "full" and adjustment.status == "approved":
            full_refund = True
        # every other in-domain combination (credit, partial refund, pending/rejected refund, warnings, reversals of
        # those) changes no state by policy (P2 open) and is never a reason to grant
    if full_refund:
        if snapshot.status in ("completed", "paid"):
            return Decision("refunded", "full approved refund")   # no grant, even from pending
        return Decision("reconcile", "refund without a paid transaction")
    if snapshot.status == "completed":
        if state_before == "paid":
            return Decision("none", "already paid")
        failure = _proof_failures(snapshot, expected)
        return Decision("reconcile", failure) if failure else Decision("paid", "completed with matching proof")
    if snapshot.status == "canceled":
        if state_before == "pending":
            return Decision("cancelled", "authoritative canceled")
        return Decision("reconcile", "canceled after payment")
    return Decision("none", "not final")   # draft, ready, billed, paid, past_due: information, never a grant


def validate_snapshot(state) -> AuthoritativeState:
    """Typed check of EVERY field a transport returned, so a corrupt payload is a PaddleMalformed, never a stray exception.

    The dataclass annotation alone validates nothing at run time; this is the real gate before ``decide``/digest.
    """
    def bad(message):
        raise PaddleMalformed(message)
    if not isinstance(state, AuthoritativeState):
        bad("snapshot has the wrong type")
    if state.environment not in ("sandbox", "live") or not isinstance(state.transaction_id, str) or not state.transaction_id:
        bad("snapshot environment or transaction id is malformed")
    if not isinstance(state.status, str) or not isinstance(state.complete, bool):
        bad("snapshot status or completeness is malformed")
    if state.currency_code is not None and not isinstance(state.currency_code, str):
        bad("snapshot currency is malformed")
    if not isinstance(state.totals, dict) or any(not isinstance(k, str) or type(v) is not int for k, v in state.totals.items()):
        bad("snapshot totals are malformed")
    if not isinstance(state.items, tuple) or any(not isinstance(i, Item) or not isinstance(i.price_id, str) or type(i.quantity) is not int for i in state.items):
        bad("snapshot items are malformed")
    if not isinstance(state.adjustments, tuple) or any(
            not isinstance(a, Adjustment) or not all(isinstance(x, str) for x in (a.id, a.action, a.status, a.type))
            or (a.total_minor is not None and type(a.total_minor) is not int) or (a.currency_code is not None and not isinstance(a.currency_code, str))
            for a in state.adjustments):
        bad("snapshot adjustments are malformed")
    if len({a.id for a in state.adjustments}) != len(state.adjustments):
        bad("snapshot repeats an adjustment id")       # ambiguous evidence: even identical repeats could double-count a refund
    return state
