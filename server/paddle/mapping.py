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

from .types import AuthoritativeState, Decision, Expected, VerifiedEvent

TERMINAL_STATES = frozenset({"refunded", "chargeback", "cancelled"})
INFORMATIONAL_EVENTS = frozenset({
    "transaction.created", "transaction.updated", "transaction.ready", "transaction.billed", "transaction.paid",
    "transaction.payment_failed", "transaction.past_due",
})
LOOK_EVENTS = frozenset({"transaction.completed", "transaction.canceled", "adjustment.created", "adjustment.updated"})
_BENIGN = {                         # action -> statuses that never change our state
    "credit": {"approved", "rejected", "reversed", "pending_approval"},
    "credit_reverse": {"approved", "rejected", "reversed", "pending_approval"},
    "refund": {"pending_approval", "rejected", "reversed"},
    "chargeback_warning": {"approved", "rejected", "reversed", "pending_approval"},
    "chargeback_warning_reverse": {"approved", "rejected", "reversed", "pending_approval"},
}


def should_look(event: VerifiedEvent) -> bool:
    """Whether this event is a reason to fetch an authoritative snapshot. Informational events never change state."""
    return event.event_type in LOOK_EVENTS


def _proof_failures(state: AuthoritativeState, expected: Expected) -> str | None:
    if state.environment != expected.environment:
        return "environment differs"
    items = [(i.price_id, i.quantity) for i in state.items]
    if items != [(expected.price_id, expected.quantity)]:
        return "price or quantity differs"
    if state.currency_code != expected.currency_code:
        return "currency differs"
    if expected.total_minor is None:
        return "amount policy is not decided"
    if state.totals.get(expected.total_field) != expected.total_minor:
        return "amount differs"
    return None


def decide(state_before: str, snapshot: AuthoritativeState, expected: Expected) -> Decision:
    """Decide the next purchase state from the CURRENT state and a fresh authoritative snapshot."""
    if state_before in TERMINAL_STATES:
        return Decision("none", "already terminal")
    if state_before not in ("pending", "paid"):
        return Decision("reconcile", "unknown current state")
    full_refund = False
    for adjustment in snapshot.adjustments:
        if adjustment.action == "chargeback" or adjustment.action == "chargeback_reverse":
            return Decision("reconcile", "chargeback status is undocumented; manual reconciliation")
        if adjustment.action == "refund" and adjustment.type == "full" and adjustment.status == "approved":
            full_refund = True
            continue
        if adjustment.action == "refund" and adjustment.type == "partial":
            continue                    # partial refund: no state change by policy (P2 open), never a grant reason
        if adjustment.status not in _BENIGN.get(adjustment.action, set()):
            return Decision("reconcile", "unknown or unvalidated adjustment")
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
