# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed values and errors shared by the offline Paddle modules."""
from __future__ import annotations

from dataclasses import dataclass, field


class PaddleError(Exception):
    """Base class. Messages never carry bodies, secrets, signatures or personal data."""


class PaddleConfigError(PaddleError):
    """Unsafe or inconsistent configuration; fail closed before serving."""


class PaddleMalformed(PaddleError):
    """Header, body or envelope is not in the expected shape."""


class PaddleSignatureInvalid(PaddleError):
    """No signature matched."""


class PaddleStaleTimestamp(PaddleError):
    """The timestamp is outside the tolerance (too old or too far in the future)."""


class PaddleTransportRejected(PaddleError):
    """The transport refused (status, redirect, size or host)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class PaddleOutcomeUnknown(PaddleError):
    """The request may or may not have taken effect (timeout, dropped connection). Never a failed purchase."""


class PaddleUnavailable(PaddleError):
    """The adapter is disabled or not configured."""


@dataclass(frozen=True)
class Item:
    price_id: str
    quantity: int


@dataclass(frozen=True)
class Adjustment:
    id: str
    action: str
    status: str
    type: str
    total_minor: int | None = None
    currency_code: str | None = None


@dataclass(frozen=True)
class VerifiedEvent:
    """A webhook whose signature verified. Authenticity only: not ownership, not amount, not state."""
    provider: str
    environment: str
    event_id: str
    event_type: str
    occurred_at: str
    notification_id: str
    payload_digest: str
    transaction_id: str | None
    adjustment_id: str | None
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AuthoritativeState:
    """A transaction as fetched from the provider API, including ALL its adjustments."""
    environment: str
    transaction_id: str
    status: str
    currency_code: str | None
    totals: dict                       # name -> integer minor units
    items: tuple                       # tuple[Item, ...]
    adjustments: tuple                 # tuple[Adjustment, ...]
    fetched_at: float


@dataclass(frozen=True)
class Expected:
    """What the server itself configured for this purchase (never taken from the client or the event)."""
    environment: str
    price_id: str
    quantity: int
    currency_code: str
    total_minor: int | None = None     # None until the legal amount/tax policy (P1) is decided
    total_field: str = "grand_total"


@dataclass(frozen=True)
class Decision:
    action: str                        # none | paid | refunded | cancelled | reconcile
    reason: str

    @property
    def grants(self) -> bool:
        return self.action == "paid"
