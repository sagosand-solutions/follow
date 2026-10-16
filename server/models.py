# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage-neutral records, enums and errors shared by the server modules.

IDs are opaque random strings and timestamps are UTC epoch seconds from an
injected clock. No record carries a password, a bearer secret or an arbitrary
client role.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum


class ServerError(Exception):
    """Base class for every error raised by the server modules."""


class SetupError(ServerError):
    """Configuration or environment is unsafe; fail closed before serving."""


class StorageError(ServerError):
    """A database write failed and was rolled back."""


class ConflictError(ServerError):
    """A unique key was violated (identity, purchase, event)."""


class ContentError(ServerError):
    """A content manifest or file failed validation; fail closed."""


class NotFoundError(ServerError):
    """A requested record does not exist."""


class UserStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class AuthIntent(str, Enum):
    LOGIN = "login"
    LINK = "link"


class PurchaseState(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    REFUNDED = "refunded"
    CHARGEBACK = "chargeback"
    CANCELLED = "cancelled"


class EntitlementState(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class PaymentEventStatus(str, Enum):
    RECEIVED = "received"
    APPLIED = "applied"
    IGNORED = "ignored"
    CONFLICT = "conflict"
    FAILED = "failed"


@dataclass(frozen=True)
class User:
    id: str
    created_at: float
    status: UserStatus
    deletion_at: float | None


@dataclass(frozen=True)
class Identity:
    id: str
    user_id: str
    provider: str
    issuer: str
    subject: str
    created_at: float


@dataclass(frozen=True)
class Session:
    id_hash: str
    user_id: str
    csrf_hash: str
    created_at: float
    last_seen: float
    expires_at: float
    auth_time: float
    absolute_expires_at: float


@dataclass(frozen=True)
class AuthTransaction:
    token_hash: str
    preauth_binding_hash: str
    intent: AuthIntent
    provider: str
    user_id: str | None
    nonce_hash: str | None
    state_data: str | None
    expires_at: float
    consumed_at: float | None
    attempts: int


@dataclass(frozen=True)
class Product:
    id: str
    title: str
    currency: str
    amount_minor: int
    access_scope: str
    active: bool


@dataclass(frozen=True)
class Purchase:
    id: str
    user_id: str
    product_id: str
    provider: str
    provider_purchase_id: str
    state: PurchaseState
    revision: int


@dataclass(frozen=True)
class Entitlement:
    id: str
    user_id: str
    product_id: str
    source_purchase_id: str
    state: EntitlementState
    granted_at: float
    revoked_at: float | None


@dataclass(frozen=True)
class PaymentEvent:
    provider: str
    event_id: str
    payload_digest: str
    purchase_id: str | None
    normalized_state: str
    revision: int
    status: PaymentEventStatus
    received_at: float


# --- Consent / policy (018-C). Technical acknowledgement ids, never legal text. ---

ACKNOWLEDGEMENT_KINDS = (
    "terms",
    "refunds",
    "privacy",
    "immediate_delivery_request",
    "withdrawal_acknowledgement",
)
CORE_ACKNOWLEDGEMENTS = frozenset({"terms", "immediate_delivery_request", "withdrawal_acknowledgement"})
_VALID_APP_ENVS = frozenset({"test", "development", "production"})


def canonical_acknowledgements(items) -> tuple[str, ...]:
    """Validate a raw acknowledgement list and return a sorted, unique tuple.

    Unknown kinds and duplicates are rejected on the raw input, before any set
    conversion, so duplicates can never be hidden by de-duplication.
    """
    if isinstance(items, str) or not isinstance(items, (list, tuple)):
        raise ConflictError("acknowledgements must be a list of strings")
    unknown = [item for item in items if item not in ACKNOWLEDGEMENT_KINDS]
    if unknown:
        raise ConflictError("unknown acknowledgement kind(s): " + ", ".join(sorted(set(unknown))))
    if len(set(items)) != len(items):
        raise ConflictError("duplicate acknowledgement kind")
    return tuple(sorted(items))


def compute_bundle_digest(*, product_id: str, access_scope: str, locale: str, version: str, terms_sha256: str,
                          refunds_sha256: str, privacy_sha256: str, required_acknowledgements, effective_at: float) -> str:
    """Canonical digest over the immutable policy content (ack set plus effective_at)."""
    if not isinstance(effective_at, (int, float)) or isinstance(effective_at, bool) or not math.isfinite(effective_at):
        raise ConflictError("effective_at must be a finite epoch second")
    acknowledgements = canonical_acknowledgements(required_acknowledgements)
    payload = {
        "product_id": product_id,
        "access_scope": access_scope,
        "locale": locale,
        "version": version,
        "terms_sha256": terms_sha256,
        "refunds_sha256": refunds_sha256,
        "privacy_sha256": privacy_sha256,
        "required_acknowledgements": list(acknowledgements),
        "effective_at": float(effective_at),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PolicyBundle:
    id: str
    product_id: str
    access_scope: str
    locale: str
    version: str
    terms_sha256: str
    refunds_sha256: str
    privacy_sha256: str
    required_acknowledgements: tuple[str, ...]
    bundle_digest: str
    effective_at: float


@dataclass(frozen=True)
class PurchaseConsent:
    id: str
    purchase_id: str
    user_id: str
    policy_bundle_id: str
    bundle_version: str
    bundle_digest: str
    acknowledgements: tuple[str, ...]
    accepted_at: float


@dataclass(frozen=True)
class PolicyActivation:
    product_id: str
    access_scope: str
    locale: str
    bundle_id: str
    approved: bool
    test_only: bool = False


def resolve_active_bundle(activations, bundles, *, product_id: str, access_scope: str, locale: str, now: float,
                          app_env: str) -> PolicyBundle | None:
    """Pure, explicit activation resolver. Exactly one matching approved activation.

    No ``latest(effective_at)`` and no id tie-break: duplicated, missing, future,
    mismatched or unapproved activations all return ``None``. ``production``
    refuses ``test_only`` activations and an unknown ``app_env`` is rejected.
    """
    if app_env not in _VALID_APP_ENVS:
        raise SetupError(f"unknown app_env: {app_env!r}")
    if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
        raise SetupError("now must be a finite epoch second")
    matches = [a for a in activations
               if a.product_id == product_id and a.access_scope == access_scope and a.locale == locale]
    if len(matches) != 1:
        return None
    activation = matches[0]
    if not activation.approved:
        return None
    if app_env == "production" and activation.test_only:
        return None
    bundle = next((b for b in bundles if b.id == activation.bundle_id), None)
    if bundle is None:
        return None
    if bundle.product_id != product_id or bundle.access_scope != access_scope or bundle.locale != locale:
        return None
    if bundle.effective_at > now:
        return None
    return bundle
