# SPDX-License-Identifier: AGPL-3.0-or-later
"""One atomic effect for a verified payment event.

Event dedup, a fresh locked read of the purchase, the provider binding, the revision fence, the transition policy,
the compare-and-swap, the grant/revoke and the event row are written in ONE ``BEGIN IMMEDIATE`` transaction: all of
it or none of it. A compare-and-swap loser never grants, a stale revision writes nothing (not even the event row) so
the caller can re-fetch and retry, and a transaction that fails to commit leaves no half-applied purchase. No awaits
and no network calls happen inside the transaction; the caller fetches any authoritative snapshot BEFORE calling.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import ConflictError, PaymentEventStatus, PurchaseState, StorageError, UserStatus
from .repositories import new_id

REVOKING_STATES = frozenset({PurchaseState.REFUNDED, PurchaseState.CHARGEBACK, PurchaseState.CANCELLED})

# The ONLY legal status changes. Terminal states never move, nothing goes back to pending, and a payment can only
# be followed by a refund or a chargeback (a cancellation after payment needs manual reconciliation instead).
_LEGAL = {
    PurchaseState.PENDING: frozenset({PurchaseState.PAID, PurchaseState.REFUNDED, PurchaseState.CANCELLED, PurchaseState.CHARGEBACK}),
    PurchaseState.PAID: frozenset({PurchaseState.REFUNDED, PurchaseState.CHARGEBACK}),
    PurchaseState.REFUNDED: frozenset(),
    PurchaseState.CHARGEBACK: frozenset(),
    PurchaseState.CANCELLED: frozenset(),
}


def transition_is_legal(old: PurchaseState, new: PurchaseState) -> bool:
    return new in _LEGAL.get(old, frozenset())


@dataclass(frozen=True)
class ApplyResult:
    outcome: str            # applied | no_change | duplicate | conflict | unknown_purchase | stale | ignored
    purchase_id: str | None = None
    new_state: str | None = None
    granted: bool = False
    revoked: bool = False


def _audit(conn, *, purchase_id, from_state, to_state, from_revision, to_revision, cause, now,
           event_provider=None, event_id=None, snapshot_digest=None) -> None:
    """One row per accepted status change, written in the same transaction as the change itself."""
    conn.execute(
        "INSERT INTO purchase_transitions (id, purchase_id, from_state, to_state, from_revision, to_revision, cause, "
        "event_provider, event_id, snapshot_digest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id(), purchase_id, from_state.value, to_state.value, from_revision, to_revision, cause, event_provider,
         event_id, snapshot_digest, now))


def _valid_revision(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _record(repositories, retry_row, provider, event_id, payload_digest, purchase_id, label, revision, status):
    if retry_row:
        # The binding is immutable once non-NULL: only an unbound row may take this purchase, a bound row must already
        # be this purchase, and exactly one row must change, otherwise the whole transaction rolls back.
        cur = repositories.database.connect().execute(
            "UPDATE payment_events SET purchase_id = ?, normalized_state = ?, revision = ?, status = ? "
            "WHERE provider = ? AND event_id = ? AND payload_digest = ? AND status IN ('received', 'failed') "
            "AND (purchase_id IS NULL OR purchase_id = ?)",
            (purchase_id, label, revision, status.value, provider, event_id, payload_digest, purchase_id))
        if cur.rowcount != 1:
            raise StorageError("payment event could not be finalised")
    else:
        repositories.payment_events.record(provider, event_id, payload_digest, purchase_id=purchase_id,
                                           normalized_state=label, revision=revision, status=status)


def apply_decision(repositories, *, provider: str, event_id: str, payload_digest: str, purchase_id: str,
                   new_state: PurchaseState, event_label: str,
                   expected_revision: int | None = None, new_revision: int | None = None) -> ApplyResult:
    """Apply ``new_state`` to ``purchase_id`` atomically under a mandatory revision fence and transition policy.

    Exactly one fence is required, and a bad value raises ValueError before anything is written:
    * ``expected_revision`` (a snapshot-based adapter): the LOCAL revision read before an authoritative fetch. A
      changed revision means the snapshot is stale: outcome ``stale``, nothing written. The revision becomes
      ``current + 1`` for an accepted change only.
    * ``new_revision`` (an adapter that supplies a VERIFIED, monotonic event revision): must be strictly greater than
      the current one, otherwise the event is ignored without any write; the purchase then takes that revision.
    Neither is a provider freshness proof. The transition policy is not optional: terminal states never move, nothing
    goes back to pending, and a no-op (same state) changes neither revision, grant nor purchase (``no_change``).

    The purchase must belong to ``provider`` (a namespace that includes the environment) and an existing event row
    must already be bound to the same purchase, or the event is never applied. An event row is written only here, as
    APPLIED or IGNORED; a pre-existing RECEIVED/FAILED row with the same digest and binding is finalised, never
    treated as a duplicate, so a rollback cannot block a valid retry. A reused id with another digest or purchase is a
    ``conflict`` that never overwrites earlier evidence.
    """
    if (expected_revision is None) == (new_revision is None):
        raise ValueError("exactly one of expected_revision and new_revision is required")
    if not _valid_revision(expected_revision if expected_revision is not None else new_revision):
        raise ValueError("revision must be a non-negative integer")
    if not isinstance(new_state, PurchaseState):
        raise ValueError("new_state must be a PurchaseState")
    db = repositories.database
    with db.transaction(immediate=True) as conn:
        existing = repositories.payment_events.get(provider, event_id)
        retry_row = False
        if existing is not None:
            if existing.payload_digest != payload_digest or (existing.purchase_id is not None and existing.purchase_id != purchase_id):
                return ApplyResult("conflict", existing.purchase_id)
            if existing.status in (PaymentEventStatus.APPLIED, PaymentEventStatus.IGNORED):
                return ApplyResult("duplicate", existing.purchase_id)
            retry_row = True
        purchase = repositories.purchases.get(purchase_id)
        if purchase is None or purchase.provider != provider:
            return ApplyResult("unknown_purchase")
        if expected_revision is not None and purchase.revision != expected_revision:
            return ApplyResult("stale", purchase.id)
        if new_revision is not None and new_revision <= purchase.revision:
            return ApplyResult("ignored", purchase.id)
        if new_state is purchase.state:
            _record(repositories, retry_row, provider, event_id, payload_digest, purchase.id, event_label,
                    purchase.revision, PaymentEventStatus.IGNORED)
            return ApplyResult("no_change", purchase.id, purchase.state.value)
        if not transition_is_legal(purchase.state, new_state):
            _record(repositories, retry_row, provider, event_id, payload_digest, purchase.id, event_label,
                    purchase.revision, PaymentEventStatus.IGNORED)
            return ApplyResult("ignored", purchase.id, purchase.state.value)
        revision = purchase.revision + 1 if new_revision is None else new_revision
        cur = conn.execute("UPDATE purchases SET state = ?, revision = ? WHERE id = ? AND revision = ?",
                           (new_state.value, revision, purchase.id, purchase.revision))
        if cur.rowcount != 1:                                       # CAS loser: nothing is kept
            raise StorageError("purchase changed during the transaction")
        granted = revoked = False
        if new_state is PurchaseState.PAID:
            owner = repositories.users.get(purchase.user_id)
            # A late event for a known purchase must never grant to a deleted account.
            if owner is not None and owner.status is UserStatus.ACTIVE:
                repositories.entitlements.grant(purchase.user_id, purchase.product_id, purchase.id)
                granted = True
        elif new_state in REVOKING_STATES:
            revoked = repositories.entitlements.revoke(purchase.id, purchase.product_id)
        _record(repositories, retry_row, provider, event_id, payload_digest, purchase.id, event_label, revision,
                PaymentEventStatus.APPLIED)
        _audit(conn, purchase_id=purchase.id, from_state=purchase.state, to_state=new_state, from_revision=purchase.revision,
               to_revision=revision, cause="verified_event", now=db.clock(), event_provider=provider, event_id=event_id)
    return ApplyResult("applied", purchase.id, new_state.value, granted, revoked)


def apply_reconciled(repositories, paddle_repo, *, purchase_id: str, provider: str, owner: str, generation: int,
                     expected_revision: int, new_state: PurchaseState, snapshot_digest: str,
                     snapshot_transaction_id: str) -> ApplyResult:
    """Apply a decision taken from an authoritative snapshot (not from an event) under a fenced reconcile claim.

    Everything below happens in ONE ``BEGIN IMMEDIATE`` transaction with the transition, the grant/revoke and the audit
    row: (1) the worker still holds the claim AND saw all the work (no newer evidence since the claim), (2) the purchase
    belongs to ``provider`` (so to the environment) and its revision is still ``expected_revision`` (r0, read at the
    claim), (3) the purchase has a BOUND Paddle operation in that environment and the snapshot is for exactly that bound
    transaction (every transition, refund and cancel included), (4) the move is legal, and (5) a GRANT additionally
    needs the stored VERIFIED ``transaction.completed`` event for that bound transaction, looked up here in the same
    transaction: there is no optional or injected precondition to leave out. Any failure writes nothing and says why,
    so the caller requeues or re-fetches; a stale decision can never grant or revoke. A reconcile is not a provider
    event: no event row is written and no event id is invented; the audit row carries the snapshot digest instead.
    Outcomes: applied, no_change, lost_claim, stale, unknown_purchase, unbound (no bound operation, wrong environment
    or another transaction), ignored (illegal move), blocked (no verified completed event for a grant).
    """
    if not _valid_revision(expected_revision):
        raise ValueError("expected_revision must be a non-negative integer")
    if not isinstance(new_state, PurchaseState):
        raise ValueError("new_state must be a PurchaseState")
    if not isinstance(snapshot_digest, str) or len(snapshot_digest) != 64:
        raise ValueError("a reconcile transition needs a 64 character snapshot digest")
    if not isinstance(snapshot_transaction_id, str) or not snapshot_transaction_id:
        raise ValueError("a reconcile transition needs the snapshot's transaction id")
    db = repositories.database
    with db.transaction(immediate=True) as conn:
        if not paddle_repo.claim_is_current(purchase_id, owner, generation):
            return ApplyResult("lost_claim", purchase_id)
        purchase = repositories.purchases.get(purchase_id)
        if purchase is None or purchase.provider != provider:
            return ApplyResult("unknown_purchase")
        if purchase.revision != expected_revision:
            return ApplyResult("stale", purchase.id)
        op = paddle_repo.operation_for_purchase(purchase.id)
        if (op is None or op["state"] != "bound" or provider != f"paddle-{op['environment']}"
                or op["provider_txn_id"] != snapshot_transaction_id):
            return ApplyResult("unbound", purchase.id, purchase.state.value)
        if new_state is purchase.state:
            return ApplyResult("no_change", purchase.id, purchase.state.value)
        if not transition_is_legal(purchase.state, new_state):
            return ApplyResult("ignored", purchase.id, purchase.state.value)
        if new_state is PurchaseState.PAID and not paddle_repo.has_completed_evidence(op["environment"], op["provider_txn_id"]):
            return ApplyResult("blocked", purchase.id, purchase.state.value)
        revision = purchase.revision + 1
        cur = conn.execute("UPDATE purchases SET state = ?, revision = ? WHERE id = ? AND revision = ?",
                           (new_state.value, revision, purchase.id, purchase.revision))
        if cur.rowcount != 1:
            raise StorageError("purchase changed during the transaction")
        granted = revoked = False
        if new_state is PurchaseState.PAID:
            owner_user = repositories.users.get(purchase.user_id)
            if owner_user is not None and owner_user.status is UserStatus.ACTIVE:     # never grant to a deleted account
                repositories.entitlements.grant(purchase.user_id, purchase.product_id, purchase.id)
                granted = True
        elif new_state in REVOKING_STATES:
            revoked = repositories.entitlements.revoke(purchase.id, purchase.product_id)
        _audit(conn, purchase_id=purchase.id, from_state=purchase.state, to_state=new_state, from_revision=purchase.revision,
               to_revision=revision, cause="reconcile", now=db.clock(), snapshot_digest=snapshot_digest)
    return ApplyResult("applied", purchase.id, new_state.value, granted, revoked)
