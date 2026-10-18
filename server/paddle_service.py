# SPDX-License-Identifier: AGPL-3.0-or-later
"""Orchestration of the Paddle adapter over an INJECTED transport (checkout creation, webhook evidence, reconcile).

This module owns the order of operations; the rules live elsewhere: ``paddle.mapping.decide`` (what a snapshot
means), ``payment_apply.apply_reconciled`` (the one atomic, fenced effect) and ``paddle_repo`` (bookkeeping). The
transport is a plain object supplied by the caller, so no network is touched here and a test can script every
failure. Nothing is wired into the HTTP app yet and nothing grants access except ``reconcile_due``.

Provisional values (decision 2026-10-18): confirm before live. This covers the limits and timings in ``paddle_repo``
(orphan caps and TTL, bound-event cap, lease, backoff, attempts, wait window).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .models import PurchaseState
from .paddle import mapping
from .paddle.digest import snapshot_digest
from .paddle.types import (AuthoritativeState, Expected, PaddleMalformed, PaddleOutcomeUnknown, PaddleTransportRejected,
                           VerifiedEvent)
from .payment_apply import apply_reconciled
from .paddle_repo import PaddleQueueFull, PaddleRepository

_TXN_RE = re.compile(r"txn_[a-z0-9]{10,40}\Z")


_PRICE_RE = re.compile(r"pri_[a-z0-9]{10,40}\Z")
_CURRENCY_RE = re.compile(r"[A-Z]{3}\Z")


@dataclass(frozen=True)
class PaddleProduct:
    """What the SERVER configured for a product: never taken from the client or from an event. Validated on creation."""
    price_id: str
    quantity: int
    currency_code: str
    total_minor: int | None = None      # None until the legal amount/tax policy is decided: then nothing is ever granted
    total_field: str = "grand_total"

    def __post_init__(self):
        if (not isinstance(self.price_id, str) or not _PRICE_RE.fullmatch(self.price_id) or type(self.quantity) is not int
                or self.quantity < 1 or not isinstance(self.currency_code, str) or not _CURRENCY_RE.fullmatch(self.currency_code)
                or (self.total_minor is not None and (type(self.total_minor) is not int or self.total_minor < 0))
                or self.total_field not in ("total", "grand_total", "subtotal")):
            raise ValueError("invalid Paddle product configuration")


class PaddleTransport(Protocol):
    def create_transaction(self, price_id: str, quantity: int, op_id: str) -> str: ...
    def get_transaction(self, txn_id: str) -> AuthoritativeState: ...


def error_class(exc: BaseException) -> str:
    """A closed code for a transport failure: the exception text is never stored."""
    if isinstance(exc, PaddleOutcomeUnknown):
        return "timeout"
    if isinstance(exc, PaddleTransportRejected):
        return "rejected"
    if isinstance(exc, PaddleMalformed):
        return "malformed"
    return "unexpected"


class PaddleService:
    def __init__(self, repositories, transport: PaddleTransport, catalog: dict, *, environment: str, clock):
        if environment not in ("sandbox", "live"):
            raise ValueError("unknown Paddle environment")
        self.repositories = repositories
        self.db = repositories.database
        self.paddle = PaddleRepository(self.db)
        self.transport = transport
        if not all(isinstance(v, PaddleProduct) for v in dict(catalog).values()):
            raise ValueError("catalog values must be PaddleProduct")
        self.catalog = dict(catalog)
        self.decisions = {}                  # purchase id -> reason of the last decision (observability for tests and ops)
        self.environment = environment
        self.provider = f"paddle-{environment}"
        self.clock = clock

    def _require_no_open_transaction(self) -> None:
        """Remote calls must never happen inside the caller's transaction: it could still roll back (a send that
        was 'committed' in a nested savepoint would reopen) or hold the write lock across the network."""
        if self.db.connect().in_transaction:
            raise RuntimeError("Paddle operations must not run inside an open database transaction")

    # ---- checkout creation: planned -> request_sent (committed) -> remote call -> bound ----------------------
    def start_checkout(self, purchase) -> dict:
        """Create the remote transaction for a purchase that already has a ``planned`` operation.

        Returns ``{"result": ..., "transaction_id": ...}`` where result is ``bound`` (the only success), ``abandoned``
        (a local failure BEFORE anything could be sent), ``not_sent`` (another caller owns the send), ``unknown`` (the
        outcome cannot be told: never retried automatically) or ``late`` (an authentic id arrived after the
        operation had already become unknown: kept as evidence, not bound).
        """
        self._require_no_open_transaction()
        op = self.paddle.operation_for_purchase(purchase.id)
        product = self.catalog.get(purchase.product_id)
        if op is None or op["environment"] != self.environment or purchase.provider != self.provider:
            return {"result": "abandoned", "transaction_id": None}
        if product is None or purchase.state is not PurchaseState.PENDING or op["state"] != "planned":
            if op["state"] == "planned":
                self.paddle.abandon(op["id"])                       # local failure before send
            return {"result": "abandoned" if op["state"] == "planned" else "not_sent", "transaction_id": None}
        if not self.paddle.mark_request_sent(op["id"], self.clock()):   # committed BEFORE the call; False => do not call
            return {"result": "not_sent", "transaction_id": None}
        try:
            txn_id = self.transport.create_transaction(product.price_id, product.quantity, op["id"])
            if not isinstance(txn_id, str) or not _TXN_RE.fullmatch(txn_id):
                raise PaddleMalformed("transaction id missing or malformed")
        except Exception:                                           # any failure after the send is `unknown`, whatever its status
            self.paddle.mark_unknown(op["id"])
            return {"result": "unknown", "transaction_id": None}
        return self._bind_and_queue(op, purchase.id, txn_id)

    def _bind_and_queue(self, op, purchase_id: str, txn_id: str) -> dict:
        """Bind and queue in ONE transaction: a crash or error can never leave a bound transaction whose earlier,
        already acknowledged evidence has no job. If the queueing fails, the binding rolls back with it (the operation
        stays request_sent and ends up unknown: manual, never a second create)."""
        with self.db.transaction(immediate=True):
            outcome = self.paddle.bind(op["id"], txn_id, self.clock(), environment=self.environment)
            if outcome == "bound":
                if self.paddle.events_for_transaction(self.environment, txn_id):     # events that arrived before the binding
                    self.paddle.mark_due(purchase_id, self.clock())
                return {"result": "bound", "transaction_id": txn_id}
        if outcome == "not_sent":                                   # the sweep made it unknown while the call was alive
            saved = self.paddle.record_late_response(op["id"], txn_id, environment=self.environment)
            return {"result": "late" if saved == "saved" else "unknown", "transaction_id": None}
        return {"result": "unknown", "transaction_id": None}       # conflict / wrong_env: manual review, nothing shared

    def adopt_late(self, op_id: str) -> bool:
        """The explicit manual decision to bind a saved late id, queued in the same transaction."""
        self._require_no_open_transaction()
        with self.db.transaction(immediate=True):
            op = self.paddle.operation(op_id)
            if op is None or op["environment"] != self.environment or not self.paddle.adopt_late_response(op_id, self.clock()):
                return False
            self.paddle.mark_due(op["purchase_id"], self.clock())
        return True

    # ---- webhook evidence ---------------------------------------------------------------------------------
    def handle_event(self, event: VerifiedEvent) -> str:
        """Store a VERIFIED event as evidence and queue a reconcile. Never fetches and never grants.

        Returns ``ignored`` (informational), ``conflict`` (same id, other body), ``orphan`` (transaction not bound yet),
        or ``queued``. ``PaddleQueueFull`` propagates so the caller can answer 503 and the provider retries.
        """
        if event.environment != self.environment:
            raise ValueError("event is for another environment")
        if not mapping.should_look(event) or not event.transaction_id:
            return "ignored"
        with self.db.transaction(immediate=True):
            stored = self.paddle.record_verified(event, self.clock())
            if stored == "conflict":
                return "conflict"
            op = self.paddle.bound_operation(self.environment, event.transaction_id)
            if op is None:
                return "orphan"
            self.paddle.mark_due(op["purchase_id"], self.clock(), reawaken=(stored == "new"))
        return "queued"

    # ---- reconcile -----------------------------------------------------------------------------------------
    def reconcile_due(self, limit: int = 10) -> list:
        """Process up to ``limit`` leased purchases. Callable by a scheduler later; there is no scheduler here."""
        self._require_no_open_transaction()
        results = []
        for claim in self.paddle.claim_due(self.clock(), limit):
            try:
                outcome = self._reconcile_one(claim)
            except Exception as exc:                 # one corrupt job can never abort the batch or lose the claim
                outcome = "fail:" + self.paddle.fail(*self._token(claim), self.clock(), error_class(exc))
            results.append((claim["purchase_id"], outcome))
        return results

    def _token(self, claim) -> tuple:
        return claim["purchase_id"], claim["owner"], claim["generation"]

    def _reconcile_one(self, claim) -> str:
        pid = claim["purchase_id"]
        token = self._token(claim)
        purchase = self.repositories.purchases.get(pid)
        op = self.paddle.operation_for_purchase(pid)
        product = self.catalog.get(purchase.product_id) if purchase else None
        if purchase is None or purchase.provider != self.provider or op is None or op["state"] != "bound":
            return "manual:" + self.paddle.to_manual(*token, "reconcile")
        if product is None:
            return "manual:" + self.paddle.to_manual(*token, "proof")
        txn_id = op["provider_txn_id"]
        try:
            snapshot = mapping.validate_snapshot(self.transport.get_transaction(txn_id))   # OUTSIDE any lock, after the claim
        except Exception as exc:
            return "fail:" + self.paddle.fail(*token, self.clock(), error_class(exc))
        expected = Expected(self.environment, product.price_id, product.quantity, product.currency_code,
                            transaction_id=txn_id, total_minor=product.total_minor, total_field=product.total_field)
        decision = mapping.decide(purchase.state.value, snapshot, expected)
        self.decisions[pid] = decision.reason
        if decision.action == "none":
            return "finish:" + self.paddle.finish(*token)
        if decision.action == "reconcile":
            reason = "proof" if ("differs" in decision.reason or "policy" in decision.reason) else "reconcile"
            return "manual:" + self.paddle.to_manual(*token, reason)
        first_seen = self._first_seen(txn_id, op)
        if decision.action == "paid" and not self.paddle.has_completed_evidence(self.environment, txn_id):
            return "await:" + self.paddle.await_event(*token, self.clock(), first_seen)
        target = {"paid": PurchaseState.PAID, "refunded": PurchaseState.REFUNDED, "cancelled": PurchaseState.CANCELLED}[decision.action]
        result = apply_reconciled(
            self.repositories, self.paddle, purchase_id=pid, provider=self.provider, owner=claim["owner"],
            generation=claim["generation"], expected_revision=claim["revision"], new_state=target,
            snapshot_digest=snapshot_digest(snapshot), snapshot_transaction_id=snapshot.transaction_id)
        if result.outcome in ("applied", "no_change"):
            if result.outcome == "applied" and target is not PurchaseState.PAID:
                self.paddle.settle_transaction(self.environment, txn_id)
            return result.outcome + ":" + self.paddle.finish(*token)
        if result.outcome == "stale":
            self.paddle.retry_now(*token, self.clock())
            return "stale"
        if result.outcome == "lost_claim":
            return "lost:" + self.paddle.finish(*token)                  # requeues if new work arrived, 'lost' if not ours
        if result.outcome == "blocked":
            return "await:" + self.paddle.await_event(*token, self.clock(), first_seen)
        return "manual:" + self.paddle.to_manual(*token, "reconcile")    # ignored / unknown_purchase / unbound

    def _first_seen(self, txn_id: str, op) -> float:
        rows = self.paddle.events_for_transaction(self.environment, txn_id)
        return min([r["received_at"] for r in rows] + [op["bound_at"] or self.clock()])
