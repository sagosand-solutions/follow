# SPDX-License-Identifier: AGPL-3.0-or-later
"""Paddle-Signature verification over the ORIGINAL request bytes (offline, pure).

Documented format (developer.paddle.com/webhooks/signature-verification, read 2026-10-18): header
``ts=<unix>;h1=<hex>``, signed string ``ts + ":" + raw body``, HMAC-SHA256 with the destination secret; more than
one ``h1`` can appear during secret rotation. No official test vector exists, so the tests build their own
synthetic vectors; they prove self-consistency, not network compatibility.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from datetime import datetime
from typing import Mapping

from .types import (PaddleConfigError, PaddleMalformed, PaddleSignatureInvalid, PaddleStaleTimestamp, VerifiedEvent)

MAX_HEADER_BYTES = 1024
MAX_H1 = 4
DEFAULT_TOLERANCE_SECONDS = 5
MAX_BODY_BYTES = 256 * 1024
_H1_RE = re.compile(r"[0-9a-f]{64}\Z")
_TS_RE = re.compile(r"(?:0|[1-9][0-9]{0,11})\Z")  # canonical: no leading zeros, so the signed bytes equal int(ts)
_RFC3339_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?([Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z")
_EVENT_ID_RE = re.compile(r"evt_[a-z0-9]{10,40}\Z")
_NOTIFICATION_RE = re.compile(r"ntf_[a-z0-9]{10,40}\Z")
_TXN_RE = re.compile(r"txn_[a-z0-9]{10,40}\Z")
_ADJ_RE = re.compile(r"adj_[a-z0-9]{10,40}\Z")
_PRICE_RE = re.compile(r"pri_[a-z0-9]{10,40}\Z")
_EVENT_TYPE_RE = re.compile(r"[a-z_]{1,40}\.[a-z_]{1,60}\Z")
_CURRENCY_RE = re.compile(r"[A-Z]{3}\Z")
_MINOR_RE = re.compile(r"-?[0-9]{1,15}\Z")


def parse_signature_header(value) -> tuple[int, tuple[str, ...]]:
    """Strict parse. Exactly one ``ts``, one to four lowercase 64-hex ``h1``, no unknown keys, no repeats of ts."""
    if not isinstance(value, str) or not value or len(value.encode("utf-8", "replace")) > MAX_HEADER_BYTES or not value.isascii():
        raise PaddleMalformed("signature header missing or too large")
    ts_values: list[str] = []
    h1_values: list[str] = []
    for part in value.split(";"):
        key, sep, val = part.partition("=")
        if not sep or key not in ("ts", "h1") or not val:
            raise PaddleMalformed("signature header part is not ts= or h1=")
        (ts_values if key == "ts" else h1_values).append(val)
    if len(ts_values) != 1 or not _TS_RE.fullmatch(ts_values[0]):
        raise PaddleMalformed("signature header needs exactly one numeric ts")
    if not 1 <= len(h1_values) <= MAX_H1 or not all(_H1_RE.fullmatch(h) for h in h1_values):
        raise PaddleMalformed("signature header needs one to four hex h1 values")
    return int(ts_values[0]), tuple(h1_values)


def _secret_bytes(secret) -> bytes:
    raw = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 16:
        raise PaddleMalformed("endpoint secret is not configured")
    return bytes(raw)


def verify_signature(raw_body: bytes, header, secret, now: float, *, tolerance: float = DEFAULT_TOLERANCE_SECONDS) -> int:
    """Return the verified ``ts``; raise a typed error otherwise. All candidate h1 values are compared."""
    if not isinstance(raw_body, (bytes, bytearray)) or len(raw_body) > MAX_BODY_BYTES:
        raise PaddleMalformed("body missing or too large")
    if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
        raise PaddleMalformed("server clock unavailable")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
        raise PaddleConfigError("tolerance must be a finite, non-negative number")
    ts, candidates = parse_signature_header(header)
    expected = hmac.new(_secret_bytes(secret), f"{ts}:".encode("ascii") + bytes(raw_body), hashlib.sha256).hexdigest()
    matched = False
    for candidate in candidates:               # no early exit: every candidate is compared
        matched |= hmac.compare_digest(expected, candidate)
    if not matched:
        raise PaddleSignatureInvalid("no signature matched")
    if abs(now - ts) > tolerance:
        raise PaddleStaleTimestamp("timestamp outside the tolerance")
    return ts


def _single_header(headers: Mapping, name: str):
    """Case-insensitive lookup; two values for the header are ambiguous and refused."""
    found = [v for k, v in headers.items() if isinstance(k, str) and k.lower() == name]
    if len(found) != 1 or isinstance(found[0], (list, tuple)):
        raise PaddleMalformed("signature header missing or repeated")
    return found[0]


def _minor(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, str) or not _MINOR_RE.fullmatch(value):
        raise PaddleMalformed(f"{label} is not an integer amount in minor units")
    return int(value)


def _rfc3339(value) -> str:
    """A full RFC 3339 timestamp WITH a zone (date-only or naive times are refused)."""
    if not isinstance(value, str) or len(value) > 40:
        raise PaddleMalformed("occurred_at is not RFC 3339")
    match = _RFC3339_RE.fullmatch(value)
    if match is None:
        raise PaddleMalformed("occurred_at is not RFC 3339")
    normalised = value.replace("z", "Z").replace("t", "T")
    if match.group(1):
        normalised = normalised.replace(match.group(1), match.group(1)[:7])  # validate with microsecond precision
    try:
        datetime.fromisoformat(normalised.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PaddleMalformed("occurred_at is not RFC 3339") from exc
    return value


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(_name):
    raise ValueError("non-finite number")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _strict_json(raw: bytes):
    """JSON with duplicate keys, NaN/Infinity and overflowing numbers refused; parser failures are PaddleMalformed."""
    try:
        return json.loads(bytes(raw).decode("utf-8"), object_pairs_hook=_no_duplicates,
                          parse_constant=_reject_constant, parse_float=_finite_float)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise PaddleMalformed("body is not valid strict JSON") from exc


def _normalise_transaction(data: dict) -> dict:
    txn = data.get("id")
    if not isinstance(txn, str) or not _TXN_RE.fullmatch(txn):
        raise PaddleMalformed("transaction id missing")
    raw_items = data.get("items")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise PaddleMalformed("transaction items are not a list")
    items = []
    for item in raw_items:
        price = item.get("price") if isinstance(item, dict) else None
        price_id = price.get("id") if isinstance(price, dict) else None
        quantity = item.get("quantity") if isinstance(item, dict) else None
        if not isinstance(price_id, str) or not _PRICE_RE.fullmatch(price_id) or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise PaddleMalformed("transaction item is malformed")
        items.append({"price_id": price_id, "quantity": quantity})
    status = data.get("status")
    currency = data.get("currency_code")
    if not isinstance(status, str) or len(status) > 20 or (currency is not None and not (isinstance(currency, str) and _CURRENCY_RE.fullmatch(currency))):
        raise PaddleMalformed("transaction status/currency is malformed")
    totals = {}
    details = data.get("details")
    if details is not None and not isinstance(details, dict):
        raise PaddleMalformed("transaction details are not an object")
    raw_totals = details.get("totals") if details else None
    if raw_totals is not None and not isinstance(raw_totals, dict):
        raise PaddleMalformed("transaction totals are not an object")
    if raw_totals:
        for name in ("subtotal", "tax", "total", "grand_total"):
            if name in raw_totals:
                totals[name] = _minor(raw_totals[name], f"totals.{name}")
    return {"transaction_id": txn, "status": status, "currency_code": currency, "items": items, "totals": totals}


def _normalise_adjustment(data: dict) -> dict:
    adj, txn = data.get("id"), data.get("transaction_id")
    action, status, kind = data.get("action"), data.get("status"), data.get("type")
    if (not isinstance(adj, str) or not _ADJ_RE.fullmatch(adj) or not isinstance(txn, str) or not _TXN_RE.fullmatch(txn)
            or not all(isinstance(v, str) and 0 < len(v) <= 40 for v in (action, status, kind))):
        raise PaddleMalformed("adjustment is malformed")
    totals = data.get("totals")
    if totals is None:
        totals = {}
    if not isinstance(totals, dict):
        raise PaddleMalformed("adjustment totals are not an object")
    return {"adjustment_id": adj, "transaction_id": txn, "action": action, "status": status, "type": kind,
            "total_minor": _minor(totals["total"], "totals.total") if "total" in totals else None,
            "currency_code": totals.get("currency_code") if isinstance(totals.get("currency_code"), str) else None}


def verify_webhook(raw_body: bytes, headers: Mapping, now: float, *, secret, environment: str,
                   tolerance: float = DEFAULT_TOLERANCE_SECONDS) -> VerifiedEvent:
    """Verify the signature over the raw bytes first, then parse. Returns a VerifiedEvent or raises a typed error.

    The envelope is minimal and strictly shaped; ``environment`` comes from our own configuration (the event does
    not carry one). Nothing here decides ownership, amount or state.
    """
    if environment not in ("sandbox", "live"):
        raise PaddleMalformed("environment is not configured")
    verify_signature(raw_body, _single_header(headers, "paddle-signature"), secret, now, tolerance=tolerance)
    envelope = _strict_json(raw_body)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), dict):
        raise PaddleMalformed("envelope is malformed")
    event_id, event_type, notification_id = envelope.get("event_id"), envelope.get("event_type"), envelope.get("notification_id")
    if (not isinstance(event_id, str) or not _EVENT_ID_RE.fullmatch(event_id)
            or not isinstance(notification_id, str) or not _NOTIFICATION_RE.fullmatch(notification_id)
            or not isinstance(event_type, str) or not _EVENT_TYPE_RE.fullmatch(event_type)):
        raise PaddleMalformed("envelope ids are malformed")
    occurred_at = _rfc3339(envelope.get("occurred_at"))
    entity = event_type.split(".", 1)[0]
    transaction_id = adjustment_id = None
    data: dict = {}
    if entity == "transaction":
        data = _normalise_transaction(envelope["data"])
        transaction_id = data["transaction_id"]
    elif entity == "adjustment":
        data = _normalise_adjustment(envelope["data"])
        transaction_id, adjustment_id = data["transaction_id"], data["adjustment_id"]
    return VerifiedEvent("paddle", environment, event_id, event_type, occurred_at, notification_id,
                         hashlib.sha256(bytes(raw_body)).hexdigest(), transaction_id, adjustment_id, data)


def classify_dedup(existing_digest: str | None, new_digest: str) -> str:
    """Dedup is keyed on (provider, environment, event_id); the digest is compared SEPARATELY.

    ``new``: first sight. ``duplicate``: same id, same body (no-op 2xx). ``conflict``: same id, changed body
    (audit, no state change). The digest is never part of the key.
    """
    if existing_digest is None:
        return "new"
    return "duplicate" if hmac.compare_digest(existing_digest, new_digest) else "conflict"
