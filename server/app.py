# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mock-mode accounts HTTP app (020).

Wires the frozen 018 repositories/content catalog and 019 session/auth services
to the allowlisted routes in accounts-v1 section 3. Real OIDC/OAuth/SIWE/email
and payment protocols are injected adapters: a disabled or missing adapter
answers 503 and never fakes success. No real provider, key or external call is
made here.
"""
from __future__ import annotations

import json
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Mapping

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route

from . import http
from .account_data import delete_account, export_account
from .auth import AuthSecurity
from .config import AppEnv
from .content_access import ContentAccessError, resolve_content
from .content_catalog import build_release_summary
from .dto import entitlements_dto, me_dto, product_list_dto
from .models import AuthIntent, ConflictError, ServerError, SetupError, canonical_acknowledgements, resolve_active_bundle
from .repositories import Repositories
from .sessions import AuthenticatedPrincipal, SessionService


@dataclass
class Services:
    open_database: Callable[[], object]
    catalog: object | None = None
    payments: object | None = None
    payment_provider: str = "mock"
    payment_redirect_hosts: frozenset[str] = frozenset()
    providers: Mapping[str, object] = field(default_factory=dict)
    policy_activations: tuple = ()
    published_chapters: tuple = ()
    checkout_policy: dict | None = None
    clock: Callable[[], float] = time.time


class _BodyLimitMiddleware:
    """Read and cap the body before any route parses it."""

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = http.new_request_id()
        path = scope.get("path", "")
        limit = (self.settings.limits.webhook_body_bytes
                 if "/payments/" in path and path.endswith("/webhook")
                 else self.settings.limits.auth_body_bytes)
        chunks: list[bytes] = []
        total = 0
        exceeded = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > limit:
                exceeded = True
                break
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        if exceeded:
            await http.error_response("body-limit", request_id)(scope, receive, send)
            return
        body = b"".join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        scope = dict(scope)
        scope["state"] = {"request_id": request_id}
        scope["app_body"] = body
        await self.app(scope, replay, send)


def _request_id(request: Request) -> str:
    return request.state.request_id


@contextmanager
def _uow(settings, services: Services):
    database = services.open_database()
    try:
        repositories = Repositories(database)
        yield repositories, SessionService(repositories, settings, clock=services.clock)
    finally:
        database.close()


def _session_cookie(request: Request) -> str | None:
    return request.cookies.get(http.cookie_name(http.COOKIE_SESSION, request.app.state.settings))


def _principal(request: Request, sessions: SessionService) -> AuthenticatedPrincipal | None:
    return sessions.authenticate(_session_cookie(request))


def _check_origin(request: Request) -> bool:
    return http.origin_allowed(request.headers.get("origin"), request.app.state.settings.public_origin)


def _check_preauth_csrf(request: Request) -> bool:
    settings = request.app.state.settings
    cookie = request.cookies.get(http.cookie_name(http.COOKIE_PREAUTH, settings))
    return cookie is not None and AuthSecurity(settings).verify_csrf(
        cookie, request.headers.get("x-csrf-token"), purpose="preauth")


def _check_session_csrf(request: Request, sessions: SessionService) -> bool:
    return sessions.verify_csrf(_session_cookie(request), request.headers.get("x-csrf-token"))


def _json_body(request: Request):
    try:
        return json.loads(request.scope["app_body"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def create_app(settings, services: Services) -> Starlette:
    if settings.app_env is AppEnv.PRODUCTION:
        if services.payments is not None and services.payment_provider in ("mock", "test"):
            raise SetupError("production must not use a mock payment adapter")
        if "mock" in services.providers or "test" in services.providers:
            raise SetupError("production must not enable mock providers")

    async def auth_csrf(request: Request):
        credentials = AuthSecurity(settings).new_preauth()
        response = http.json_response({"csrf_token": credentials.csrf_token}, request_id=_request_id(request))
        http.set_preauth_cookie(response, credentials.binding_cookie, settings, max_age=600)
        return response

    async def me(request: Request):
        with _uow(settings, services) as (repositories, sessions):
            principal = _principal(request, sessions)
            if principal is None:
                return http.error_response("no-session", _request_id(request))
            user = repositories.users.get(principal.user_id)
            identities = repositories.identities.list_for_user(principal.user_id)
            csrf_token = sessions.security.csrf_for(_session_cookie(request), purpose="session")
            return http.json_response(me_dto(user, identities, principal, csrf_token=csrf_token),
                                      request_id=_request_id(request))

    async def logout(request: Request):
        with _uow(settings, services) as (repositories, sessions):
            if not _check_origin(request):
                return http.error_response("forbidden", _request_id(request))
            cookie = _session_cookie(request)
            # CSRF is enforced only when there is a live session to protect; an
            # already logged-out browser gets an idempotent 200 with a cleared cookie.
            if cookie is not None and sessions.authenticate(cookie) is not None and not _check_session_csrf(request, sessions):
                return http.error_response("forbidden", _request_id(request))
            sessions.revoke(cookie)
        response = http.json_response({"ok": True}, request_id=_request_id(request))
        http.clear_session_cookie(response, settings)
        return response

    async def products(request: Request):
        with _uow(settings, services) as (repositories, sessions):
            try:
                summary = build_release_summary(repositories.products.list_active(), services.published_chapters)
            except ServerError:
                return http.error_response("unavailable", _request_id(request))
            policy = services.checkout_policy
            return http.json_response(product_list_dto(summary, checkout_enabled=services.payments is not None and policy is not None,
                                                       checkout_policy=policy), request_id=_request_id(request))

    async def entitlements(request: Request):
        with _uow(settings, services) as (repositories, sessions):
            principal = _principal(request, sessions)
            if principal is None:
                return http.error_response("no-session", _request_id(request))
            return http.json_response(entitlements_dto(repositories.entitlements.list_active_for_user(principal.user_id)),
                                      request_id=_request_id(request))

    async def content(request: Request):
        with _uow(settings, services) as (repositories, sessions):
            principal = _principal(request, sessions)
            try:
                data, media_type = resolve_content(services.catalog, repositories, principal,
                                                   request.path_params["kind"], request.path_params["id"])
            except ContentAccessError as exc:
                return http.error_response(exc.code, _request_id(request))
            return http.text_response(data.decode("utf-8"), media_type=media_type)

    async def export(request: Request):
        with _uow(settings, services) as (repositories, sessions):
            principal = _principal(request, sessions)
            if principal is None:
                return http.error_response("no-session", _request_id(request))
            if not sessions.require_recent_auth(principal):
                return http.error_response("forbidden", _request_id(request))
            user = repositories.users.get(principal.user_id)
            return http.json_response(export_account(repositories, user), request_id=_request_id(request))

    async def account_delete(request: Request):
        with _uow(settings, services) as (repositories, sessions):
            principal = _principal(request, sessions)
            if principal is None:
                return http.error_response("no-session", _request_id(request))
            if not _check_origin(request) or not _check_session_csrf(request, sessions):
                return http.error_response("forbidden", _request_id(request))
            if not sessions.require_recent_auth(principal):
                return http.error_response("forbidden", _request_id(request))
            delete_account(repositories, principal.user_id)
        response = http.json_response({"ok": True}, request_id=_request_id(request))
        http.clear_session_cookie(response, settings)
        return response

    async def provider_start(request: Request):
        adapter = services.providers.get(request.path_params["provider"])
        if adapter is None:
            return http.error_response("unavailable", _request_id(request))
        if not _check_origin(request) or not _check_preauth_csrf(request):
            return http.error_response("forbidden", _request_id(request))
        body = _json_body(request)
        return_to = http.local_return_to(body.get("return_to")) if isinstance(body, dict) else None
        if return_to is None:
            return http.error_response("invalid", _request_id(request))
        try:
            result = adapter.start(intent=body.get("intent"), return_to=return_to,
                                   preauth_binding_cookie=request.cookies.get(
                                       http.cookie_name(http.COOKIE_PREAUTH, settings)))
        except ServerError:
            return http.error_response("unavailable", _request_id(request))
        authorize = result.get("authorize_url") if isinstance(result, dict) else getattr(result, "authorize_url", None)
        if not isinstance(authorize, str):
            return http.error_response("server-error", _request_id(request))
        return http.json_response({"authorize_url": authorize}, request_id=_request_id(request))

    async def provider_callback(request: Request):
        adapter = services.providers.get(request.path_params["provider"])
        if adapter is None:
            return http.error_response("unavailable", _request_id(request))
        try:
            result = adapter.callback(request)
        except ServerError:
            return http.error_response("invalid", _request_id(request))
        user_id = getattr(result, "user_id", None)
        return_to = http.local_return_to(getattr(result, "return_to", None))
        auth_time = getattr(result, "auth_time", None)
        if not isinstance(user_id, str) or return_to is None:
            return http.error_response("invalid", _request_id(request))
        with _uow(settings, services) as (repositories, sessions):
            credentials = sessions.issue(user_id, old_cookie=_session_cookie(request), auth_time=auth_time)
        response = RedirectResponse(return_to, status_code=302, headers={"Cache-Control": http.NO_STORE})
        http.set_session_cookie(response, credentials.cookie_value, settings, max_age=settings.session_idle_seconds)
        return response

    async def checkout(request: Request):
        policy = services.checkout_policy
        if services.payments is None or policy is None:
            return http.error_response("unavailable", _request_id(request))
        with _uow(settings, services) as (repositories, sessions):
            principal = _principal(request, sessions)
            if principal is None:
                return http.error_response("no-session", _request_id(request))
            if not _check_origin(request) or not _check_session_csrf(request, sessions):
                return http.error_response("forbidden", _request_id(request))
            body = _json_body(request)
            if not isinstance(body, dict) or set(body) != {"product_id", "policy_version", "acknowledgements"}:
                return http.error_response("invalid", _request_id(request))
            product = repositories.products.get(body["product_id"])
            if product is None or not product.active:
                return http.error_response("invalid", _request_id(request))
            try:
                acknowledgements = canonical_acknowledgements(body["acknowledgements"])
                required = canonical_acknowledgements(policy.get("required_acknowledgements"))
            except ConflictError:
                return http.error_response("invalid", _request_id(request))
            bundle = resolve_active_bundle(services.policy_activations,
                                           repositories.policies.list_for_product(product.id),
                                           product_id=product.id, access_scope=product.access_scope,
                                           locale="en", now=services.clock(), app_env=settings.app_env.value)
            if bundle is None:
                return http.error_response("unavailable", _request_id(request))  # no legally approved policy
            if body["policy_version"] != bundle.version or policy.get("version") != bundle.version:
                return http.error_response("stale-policy", _request_id(request))
            if set(acknowledgements) != set(required) or set(required) != set(bundle.required_acknowledgements):
                return http.error_response("invalid", _request_id(request))
            with repositories.database.transaction(immediate=True):
                purchase = repositories.purchases.create(principal.user_id, product.id, services.payment_provider,
                                                         f"srv-{secrets.token_urlsafe(12)}")
                repositories.consents.record(purchase.id, bundle.id, list(acknowledgements))
        try:
            result = services.payments.create_checkout(purchase, product, purchase.id)
        except ServerError:
            return http.error_response("unavailable", _request_id(request))
        redirect = _allowlisted_redirect(result.get("redirect_url") if isinstance(result, dict)
                                        else getattr(result, "redirect_url", None), services)
        if redirect is None:
            return http.error_response("server-error", _request_id(request))
        return http.json_response({"state": "pending", "redirect_url": redirect}, request_id=_request_id(request))

    async def webhook(request: Request):
        if services.payments is None:
            return http.error_response("unavailable", _request_id(request))
        try:
            event = services.payments.verify_webhook(request.scope["app_body"], dict(request.headers), services.clock())
        except ServerError:
            return http.error_response("invalid", _request_id(request))
        if event is None:
            return http.error_response("invalid", _request_id(request))
        with _uow(settings, services) as (repositories, sessions):
            try:
                _apply_verified_event(repositories, request.path_params["provider"], event)
            except ServerError:
                return http.error_response("server-error", _request_id(request))
        return http.json_response({"ok": True}, request_id=_request_id(request))

    base = settings.base_path.rstrip("/")
    routes = [Route(base + path, endpoint, methods=methods) for path, endpoint, methods in (
        ("/auth/csrf", auth_csrf, ["GET"]),
        ("/auth/{provider}/start", provider_start, ["POST"]),
        ("/auth/{provider}/callback", provider_callback, ["GET", "POST"]),
        ("/me", me, ["GET"]),
        ("/logout", logout, ["POST"]),
        ("/products", products, ["GET"]),
        ("/entitlements", entitlements, ["GET"]),
        ("/checkout", checkout, ["POST"]),
        ("/payments/{provider}/webhook", webhook, ["POST"]),
        ("/content/{kind}/{id}", content, ["GET"]),
        ("/account/export", export, ["GET"]),
        ("/account/delete", account_delete, ["POST"]),
    )]
    app = Starlette(routes=routes)
    app.state.settings = settings
    app.state.services = services
    return _BodyLimitMiddleware(app, settings)


def _allowlisted_redirect(url, services: Services):
    if not isinstance(url, str):
        return None
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if services.payment_redirect_hosts and parts.hostname not in services.payment_redirect_hosts:
        return None
    return url


def _apply_verified_event(repositories, provider_path: str, event) -> None:
    """Apply only a newer authoritative revision; never create users or grants for unknown purchases."""
    state = getattr(event, "state", None)
    purchase = repositories.purchases.get_by_provider_id(provider_path, getattr(event, "provider_purchase_id", ""))
    if purchase is None:
        return
    revision = getattr(event, "revision", 0)
    if not isinstance(revision, int) or revision <= purchase.revision:
        return
    from .models import PaymentEventStatus, PurchaseState
    with repositories.database.transaction(immediate=True):
        repositories.payment_events.record(provider_path, getattr(event, "event_id", ""),
                                           getattr(event, "payload_digest", ""), purchase_id=purchase.id,
                                           normalized_state=str(state), revision=revision)
        repositories.purchases.compare_and_swap(purchase.id, purchase.revision,
                                                state=PurchaseState(state), revision=revision)
        if state == "paid":
            repositories.entitlements.grant(purchase.user_id, purchase.product_id, purchase.id)
        elif state in ("refunded", "chargeback", "cancelled"):
            repositories.entitlements.revoke(purchase.id, purchase.product_id)
