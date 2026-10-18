# SPDX-License-Identifier: AGPL-3.0-or-later
"""A cap on open Paddle checkouts per authenticated user and product, with narrow reuse.

Provisional product limits: confirm before live. The cap and the reuse window
live in ONE place (here). The quota is keyed on the authenticated ``user_id`` + ``product_id`` across BOTH
Paddle environments together. Everything runs inside the caller's request-owned ``BEGIN IMMEDIATE``, so the
quota is shared by separate connections and processes (SQLite serialises writers); there is no process-local
mutex or counter.

This module never calls the provider and never grants access. It decides reuse, throttle or a fresh intent;
``paddle_service.start_checkout`` still owns the remote call after the commit and the existing no-retry rules.
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
from dataclasses import dataclass

from .paddle_repo import PaddleRepository

MAX_OPEN_CHECKOUTS = 3
REUSE_WINDOW_SECONDS = 900
PADDLE_PROVIDERS = ("paddle-sandbox", "paddle-live")
OPEN_OPERATION_STATES = ("planned", "request_sent", "unknown")
TOTAL_FIELDS = ("total", "grand_total", "subtotal")


def _positive_int(name: str, value) -> int:
    """The tunables are strictly positive ints and never bools (a bool would pass an int check)."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a strictly positive int")
    return value


_positive_int("MAX_OPEN_CHECKOUTS", MAX_OPEN_CHECKOUTS)
_positive_int("REUSE_WINDOW_SECONDS", REUSE_WINDOW_SECONDS)


@dataclass(frozen=True)
class CheckoutContext:
    """Everything the SERVER chose for one checkout, in a form that can be canonically hashed.

    Never built from client input: the app derives it from the authenticated principal, the active product, the
    resolved policy bundle and the server's Paddle catalog. ``digest`` is the immutable identity stored with the
    operation; a catalog or policy change produces a different digest, so an old checkout can never be reused.
    """

    environment: str
    user_id: str
    product_id: str
    policy_bundle_id: str
    bundle_digest: str
    acknowledgements: tuple
    access_scope: str
    amount_minor: int
    product_currency: str
    price_id: str
    quantity: int
    currency_code: str
    total_minor: int | None
    total_field: str

    def __post_init__(self):
        if self.environment not in ("sandbox", "live"):
            raise ValueError("unknown Paddle environment")
        if not all(isinstance(v, str) and v for v in (self.user_id, self.product_id, self.policy_bundle_id,
                                                      self.access_scope, self.product_currency, self.price_id, self.currency_code)):
            raise ValueError("context identity is malformed")
        if type(self.quantity) is not int or self.quantity < 1:
            raise ValueError("quantity must be a strictly positive int")
        if type(self.amount_minor) is not int or self.amount_minor < 0:
            raise ValueError("amount_minor must be a non-negative int")
        if self.total_minor is not None and (type(self.total_minor) is not int or self.total_minor < 0):
            raise ValueError("total_minor must be a non-negative int or None")
        if self.total_field not in TOTAL_FIELDS:
            raise ValueError("total_field is not a documented total")
        if not isinstance(self.acknowledgements, tuple) or not all(isinstance(a, str) for a in self.acknowledgements):
            raise ValueError("acknowledgements must be a tuple of strings")

    def ack_set(self) -> str:
        """Canonical (sorted, ascii, no whitespace) JSON list: the stored form of the acknowledgement set."""
        return json.dumps(sorted(self.acknowledgements), separators=(",", ":"), ensure_ascii=True)

    def payload(self) -> dict:
        """Canonical payload: sorted keys, no whitespace, ascii, explicit null (null is never folded into 0)."""
        return {
            "acknowledgements": sorted(self.acknowledgements),
            "access_scope": self.access_scope,
            "amount_minor": self.amount_minor,
            "bundle_digest": self.bundle_digest,
            "currency_code": self.currency_code,
            "environment": self.environment,
            "policy_bundle_id": self.policy_bundle_id,
            "price_id": self.price_id,
            "product_currency": self.product_currency,
            "product_id": self.product_id,
            "quantity": self.quantity,
            "total_field": self.total_field,
            "total_minor": self.total_minor,
            "user_id": self.user_id,
        }

    def digest(self) -> str:
        raw = json.dumps(self.payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Reservation:
    """Outcome of ``reserve_or_reuse_checkout``: ``reused`` | ``reserved`` | ``throttled`` (or ``refused``)."""

    kind: str
    purchase_id: str | None = None
    transaction_id: str | None = None
    operation_id: str | None = None


def build_checkout_context(*, environment: str, user_id, product, bundle, acknowledgements, paddle_product) -> CheckoutContext:
    """Derive the server-chosen context from already-validated values (all arguments come from the server)."""
    return CheckoutContext(
        environment=environment, user_id=user_id, product_id=product.id,
        policy_bundle_id=bundle.id, bundle_digest=bundle.bundle_digest,
        acknowledgements=tuple(sorted(str(a) for a in acknowledgements)),
        access_scope=product.access_scope, amount_minor=product.amount_minor, product_currency=product.currency,
        price_id=paddle_product.price_id, quantity=paddle_product.quantity, currency_code=paddle_product.currency_code,
        total_minor=paddle_product.total_minor, total_field=paddle_product.total_field,
    )


def _require_open_transaction(repositories) -> None:
    if not repositories.database.connect().in_transaction:
        raise RuntimeError("reserve_or_reuse_checkout must run inside the caller's BEGIN IMMEDIATE")


def _find_reusable(repositories, context: CheckoutContext, provider: str, now: float):
    """One bound, pending purchase from the same user/product/provider/environment, still inside the reuse window.

    The stored context digest must match the CURRENT server context, and the existing immutable consent must still
    be for the same bundle and acknowledgement set: a catalog or policy change therefore blocks reuse. The window is
    half-open: ``now - created_at`` must be ``>= 0`` and ``< REUSE_WINDOW_SECONDS``; exactly the window and a future
    ``created_at`` are refused, and the window is never extended.
    """
    row = repositories.database.connect().execute(
        "SELECT o.id, o.purchase_id, o.provider_txn_id FROM paddle_operations o "
        "JOIN purchases p ON p.id = o.purchase_id "
        "WHERE p.user_id = ? AND p.product_id = ? AND p.provider = ? "
        "AND o.environment = ? AND o.state = 'bound' AND o.provider_txn_id IS NOT NULL AND p.state = 'pending' "
        "AND o.created_at <= ? AND (? - o.created_at) < ? "
        "AND EXISTS (SELECT 1 FROM paddle_checkout_context c WHERE c.operation_id = o.id AND c.context_digest = ?) "
        "ORDER BY o.created_at DESC, o.id DESC LIMIT 1",
        (context.user_id, context.product_id, provider, context.environment, now, now, REUSE_WINDOW_SECONDS,
         context.digest())).fetchone()
    if row is None:
        return None
    consent = repositories.consents.get_for_purchase(row["purchase_id"])
    if (consent is None or consent.policy_bundle_id != context.policy_bundle_id
            or consent.bundle_digest != context.bundle_digest
            or set(consent.acknowledgements) != set(context.acknowledgements)):
        return None
    return Reservation("reused", purchase_id=row["purchase_id"], transaction_id=row["provider_txn_id"])


def _open_count(repositories, context: CheckoutContext) -> int:
    """Count every unresolved checkout for the quota key across BOTH environments (not a paged list).

    Counted: planned/request_sent/unknown operations (no age limit) and bound operations whose purchase is still
    pending. Also counted: a pending paddle purchase with NO operation (legacy/inconsistency must not bypass the
    cap). Not counted: an operation abandoned before the send, and a bound purchase that is terminal.
    """
    row = repositories.database.connect().execute(
        "SELECT COUNT(*) FROM purchases p WHERE p.user_id = ? AND p.product_id = ? AND p.provider IN (?, ?) "
        "AND (EXISTS (SELECT 1 FROM paddle_operations o WHERE o.purchase_id = p.id "
        "            AND (o.state IN (?, ?, ?) OR (o.state = 'bound' AND p.state = 'pending'))) "
        "     OR (p.state = 'pending' AND NOT EXISTS (SELECT 1 FROM paddle_operations o WHERE o.purchase_id = p.id)))",
        (context.user_id, context.product_id, *PADDLE_PROVIDERS, *OPEN_OPERATION_STATES)).fetchone()
    return row[0]


def reserve_or_reuse_checkout(repositories, context: CheckoutContext, now: float) -> Reservation:
    """Reuse | reserve | throttle for one authenticated user/product, inside the caller's ``BEGIN IMMEDIATE``.

    Order: reuse (even at full quota) first, then the quota count, then a fresh purchase+consent+planned
    operation+context in the SAME transaction. A throttle writes nothing and makes no remote call.
    """
    _require_open_transaction(repositories)
    if not isinstance(context, CheckoutContext):
        raise ValueError("context must be a CheckoutContext")
    if type(now) not in (int, float) or isinstance(now, bool) or not math.isfinite(now):
        raise ValueError("now must be a finite number")
    provider = f"paddle-{context.environment}"
    paddle = PaddleRepository(repositories.database)
    reuse = _find_reusable(repositories, context, provider, now)
    if reuse is not None:
        return reuse
    if _open_count(repositories, context) >= MAX_OPEN_CHECKOUTS:
        return Reservation("throttled")
    purchase = repositories.purchases.create(context.user_id, context.product_id, provider,
                                             f"srv-{secrets.token_urlsafe(12)}")
    repositories.consents.record(purchase.id, context.policy_bundle_id, list(context.acknowledgements))
    operation_id = paddle.plan(purchase.id, context.environment, now)
    paddle.record_context(operation_id, context, now)
    return Reservation("reserved", purchase_id=purchase.id, operation_id=operation_id)
