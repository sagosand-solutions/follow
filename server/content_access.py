# SPDX-License-Identifier: AGPL-3.0-or-later
"""Premium content gate: authenticated principal -> active entitlement -> file.

The access decision happens before any file path is opened. Unknown or
unauthorised ids answer a generic 404; anonymous callers answer 401. No private
path, hash or manifest detail is ever returned to the client.
"""
from __future__ import annotations

import re

from .models import ContentError, EntitlementState

CONTENT_KINDS = frozenset({"question", "exam", "study"})
_CONTENT_ID_RE = re.compile(r"[a-z][a-z0-9.-]{0,79}\Z")  # keep in step with content_catalog._ID_RE

NO_SESSION = "no-session"
NOT_FOUND = "not-found"


class ContentAccessError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def has_entitlement(repositories, user_id: str, product_id: str) -> bool:
    for entitlement in repositories.entitlements.list_active_for_user(user_id):
        if entitlement.product_id == product_id and entitlement.state is EntitlementState.ACTIVE:
            return True
    return False


def resolve_content(catalog, repositories, principal, kind: str, content_id: str, *, gate=None):
    """Return (bytes, media_type) or raise ContentAccessError(401/404)."""
    if principal is None:
        raise ContentAccessError(NO_SESSION)
    if kind not in CONTENT_KINDS or not isinstance(content_id, str) or not _CONTENT_ID_RE.fullmatch(content_id):
        raise ContentAccessError(NOT_FOUND)
    entry = catalog.lookup(kind, content_id) if catalog is not None else None
    if entry is None:
        raise ContentAccessError(NOT_FOUND)
    if not has_entitlement(repositories, principal.user_id, entry.product_id):
        raise ContentAccessError(NOT_FOUND)
    if gate is not None and not gate(entry):
        # Scoring exposure gate: material tied to still-unlocked competition questions is not served.
        raise ContentAccessError(NOT_FOUND)
    try:
        data = catalog.read(entry)
    except ContentError:
        raise ContentAccessError(NOT_FOUND) from None
    return data, "application/json"
