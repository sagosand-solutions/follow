# SPDX-License-Identifier: AGPL-3.0-or-later
"""Account data: a sanitised self-export and a controlled deletion/pseudonymisation.

Deletion revokes every session immediately and removes identity PII; purchase,
consent and entitlement rows stay as minimal tombstones so late payment webhooks
cannot resurrect an account. Legal financial retention and backup purge remain an
explicit production gate, not a promise made here.
"""
from __future__ import annotations

from .dto import export_dto


def _purchases_for_user(repositories, user_id: str):
    rows = repositories.database.connect().execute(
        "SELECT * FROM purchases WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    from .repositories import _purchase
    return [_purchase(row) for row in rows]


def export_account(repositories, user, *, scoring=None) -> dict:
    identities = repositories.identities.list_for_user(user.id)
    purchases = _purchases_for_user(repositories, user.id)
    entitlements = repositories.entitlements.list_active_for_user(user.id)
    data = export_dto(user, identities, purchases, entitlements)
    if scoring is not None:
        data["scoring"] = scoring
    return data


def delete_account(repositories, user_id: str) -> bool:
    """Revoke access and pseudonymise the user in one transaction.

    Returns True when an active account was deleted, False when already gone.
    """
    with repositories.database.transaction(immediate=True) as conn:
        user = repositories.users.get(user_id)
        if user is None or user.status.value != "active":
            return False
        repositories.sessions.revoke_all_for_user(user_id)
        conn.execute("DELETE FROM identities WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM auth_transactions WHERE user_id = ? AND consumed_at IS NULL", (user_id,))
        repositories.users.mark_deleted(user_id)
        # Scoring: unpublish at once; the principal row and first-attempt locks stay (specs: lifetime lock).
        from .scoring_repo import ScoringRepository
        principal = ScoringRepository(repositories.database).principal_for_user(user_id)
        if principal is not None:
            ScoringRepository(repositories.database).unpublish_all(principal.id)
    return True
