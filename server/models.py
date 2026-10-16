# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storage-neutral records, enums and errors shared by the server modules.

IDs are opaque random strings and timestamps are UTC epoch seconds from an
injected clock. No record carries a password, a bearer secret or an arbitrary
client role.
"""
from __future__ import annotations

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
