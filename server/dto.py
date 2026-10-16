# SPDX-License-Identifier: AGPL-3.0-or-later
"""DTO projections. Public JSON never carries secrets, session/CSRF tokens,
private paths, document bytes or other users' data."""
from __future__ import annotations

from .models import EntitlementState, UserStatus


def product_dto(release) -> dict:
    return {
        "id": release.id,
        "title": release.title,
        "currency": release.currency,
        "amount_minor": release.amount_minor,
        "access_scope": release.access_scope,
        "published_chapter_count": release.published_chapter_count,
    }


def product_list_dto(summary, *, checkout_enabled: bool, checkout_policy) -> dict:
    return {
        "products": [product_dto(release) for release in summary.products],
        "published_product_count": summary.published_product_count,
        "checkout_enabled": bool(checkout_enabled),
        "checkout_policy": checkout_policy,
    }


def me_dto(user, identities, principal, *, csrf_token: str) -> dict:
    return {
        "user": {"id": user.id, "status": user.status.value},
        "providers": [identity.provider for identity in identities],
        "session": {
            "auth_time": principal.auth_time,
            "expires_at": principal.expires_at,
            "absolute_expires_at": principal.absolute_expires_at,
        },
        "csrf_token": csrf_token,
    }


def entitlement_dto(entitlement) -> dict:
    active = entitlement.state is EntitlementState.ACTIVE
    return {
        "product_id": entitlement.product_id,
        "state": entitlement.state.value,
        "active": active,
        "granted_at": entitlement.granted_at,
        "revoked_at": entitlement.revoked_at,
    }


def entitlements_dto(entitlements) -> dict:
    return {"entitlements": [entitlement_dto(item) for item in entitlements]}


def export_dto(user, identities, purchases, entitlements) -> dict:
    return {
        "user": {"id": user.id, "created_at": user.created_at, "status": user.status.value},
        "identities": [
            {"provider": identity.provider, "issuer": identity.issuer, "subject": identity.subject,
             "created_at": identity.created_at}
            for identity in identities
        ],
        "purchases": [
            {"id": purchase.id, "product_id": purchase.product_id, "provider": purchase.provider,
             "state": purchase.state.value, "revision": purchase.revision}
            for purchase in purchases
        ],
        "entitlements": [entitlement_dto(item) for item in entitlements],
    }
