# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical digest of an authoritative snapshot (the audit identity of a reconcile transition)."""
from __future__ import annotations

import hashlib
import json

from .types import AuthoritativeState


def snapshot_digest(state: AuthoritativeState) -> str:
    """sha256 over canonical JSON (sorted keys, no whitespace, ASCII) of everything a decision can depend on.

    Fields: environment, transaction_id, status, currency_code, totals (sorted by name), items (sorted by price id and
    quantity), adjustments (sorted by id; id, action, status, type, total_minor, currency_code) and ``complete``. The
    fetch time is NOT part of it, so identical content digests identically; a different adjustment list digests
    differently. Never contains payer data: only ids, enums and integer amounts.
    """
    payload = {
        "environment": state.environment, "transaction_id": state.transaction_id, "status": state.status,
        "currency_code": state.currency_code, "totals": {k: state.totals[k] for k in sorted(state.totals)},
        "items": sorted([[i.price_id, i.quantity] for i in state.items]),
        "adjustments": sorted([[a.id, a.action, a.status, a.type, a.total_minor, a.currency_code] for a in state.adjustments]),
        "complete": state.complete is True,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(raw).hexdigest()
