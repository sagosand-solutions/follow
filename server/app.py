# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mock-mode accounts HTTP app (020).

Wires the frozen 018 repositories/content catalog and 019 session/auth services
to the allowlisted routes in accounts-v1 section 3. Real OIDC/OAuth/SIWE/email
and payment protocols are injected adapters: a disabled or missing adapter
answers 503 and never fakes success. No real provider, key or external call is
made here.
"""
from __future__ import annotations

import hashlib
import ipaddress
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
from .rate_limit import EmailStartLimiter
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


def _safe_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or http.new_request_id()


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


def _canonical_ip(address):
    """An IPv4-mapped IPv6 address (::ffff:a.b.c.d, as a dual-stack listener reports it) is the IPv4 address."""
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _client_ip(request: Request, settings) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = _canonical_ip(ipaddress.ip_address(peer))
    except ValueError:
        return peer
    networks = []
    for value in settings.trusted_proxies:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    # The trusted-proxy check sees the canonical peer, so a proxy reached over a dual-stack socket is still recognised.
    if not any(peer_ip in network for network in networks):
        return peer_ip.compressed
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded or len(forwarded) > 4096:
        return peer_ip.compressed
    parts = forwarded.split(",")
    if len(parts) > 16:
        return peer_ip.compressed
    try:
        chain = [_canonical_ip(ipaddress.ip_address(part.strip())) for part in parts]
    except ValueError:
        return peer_ip.compressed
    for candidate in reversed(chain):
        if not any(candidate in network for network in networks):
            return candidate.compressed
    return chain[0].compressed if chain else peer_ip.compressed


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

    email_limiter = EmailStartLimiter(settings.session_secret, clock=services.clock,
                                       per_email=settings.limits.email_per_address_per_hour,
                                       per_ip=settings.limits.email_per_ip_per_hour)

    async def auth_csrf(request: Request):
        credentials = AuthSecurity(settings).new_preauth()
        response = http.json_response({"csrf_token": credentials.csrf_token}, request_id=_request_id(request))
        http.set_preauth_cookie(response, credentials.binding_cookie, settings, max_age=600)
        return response

    async def auth_providers(request: Request):
        providers = []
        for provider_id, adapter in services.providers.items():
            kind = getattr(adapter, "kind", None)
            if isinstance(provider_id, str) and kind in ("oauth", "email"):
                providers.append({"id": provider_id, "kind": kind})
        providers.sort(key=lambda item: item["id"])
        return http.json_response({"providers": providers}, request_id=_request_id(request))

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

    async def purchases(request: Request):
        with _uow(settings, services) as (repositories, sessions):
            principal = _principal(request, sessions)
            if principal is None:
                return http.error_response("no-session", _request_id(request))
            items = repositories.purchases.list_for_user(principal.user_id, limit=20)
            return http.json_response({"purchases": [
                {"id": item.id, "product_id": item.product_id, "state": item.state.value,
                 "entitled": repositories.purchases.is_entitled(item)} for item in items
            ]}, request_id=_request_id(request))

    async def purchase_detail(request: Request):
        with _uow(settings, services) as (repositories, sessions):
            principal = _principal(request, sessions)
            if principal is None:
                return http.error_response("no-session", _request_id(request))
            item = repositories.purchases.get_for_user(request.path_params["id"], principal.user_id)
            if item is None:
                return http.error_response("not-found", _request_id(request))
            return http.json_response({
                "id": item.id, "product_id": item.product_id, "state": item.state.value,
                "entitled": repositories.purchases.is_entitled(item),
            }, request_id=_request_id(request))

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
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                return http.error_response("server-error", _request_id(request))
            return http.text_response(text, media_type=media_type)

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

    async def email_start(request: Request):
        if not _check_origin(request) or not _check_preauth_csrf(request):
            return http.error_response("forbidden", _request_id(request))
        body = _json_body(request)
        if (not isinstance(body, dict) or set(body) != {"email", "return_to", "intent"}
                or not isinstance(body.get("email"), str)):
            return http.error_response("invalid", _request_id(request))
        email = body["email"]
        if (len(email) > 254 or email.count("@") != 1
                or http.has_disallowed_text_characters(email) or any(ch.isspace() for ch in email)):
            return http.error_response("invalid", _request_id(request))
        local, domain = email.rsplit("@", 1)
        if not local or not domain:
            return http.error_response("invalid", _request_id(request))
        normalized_email = f"{local}@{domain.lower()}"
        return_to = http.local_return_to(body.get("return_to"))
        if return_to is None or body.get("intent") != "login":
            return http.error_response("invalid", _request_id(request))
        adapter = services.providers.get("email")
        if adapter is None or getattr(adapter, "kind", None) != "email":
            return http.error_response("unavailable", _request_id(request))
        client_ip = _client_ip(request, settings)
        allowed, retry_after = email_limiter.consume(normalized_email, client_ip)
        if not allowed:
            response = http.error_response("throttled", _request_id(request))
            response.headers["Retry-After"] = str(retry_after)
            return response
        preauth_cookie = request.cookies.get(http.cookie_name(http.COOKIE_PREAUTH, settings))
        if preauth_cookie is None:
            return http.error_response("forbidden", _request_id(request))
        auth_security = AuthSecurity(settings)
        raw_transaction_token = auth_security.new_token()
        transaction_hash = hashlib.sha256(raw_transaction_token.encode("ascii")).hexdigest()
        with _uow(settings, services) as (repositories, sessions):
            with repositories.database.transaction(immediate=True):
                if repositories.auth_transactions.count_active(now=services.clock()) >= settings.limits.max_active_auth_transactions:
                    return http.error_response("unavailable", _request_id(request))
                repositories.auth_transactions.create(
                    transaction_hash, preauth_binding_hash=auth_security.binding_hash(preauth_cookie),
                    intent=AuthIntent.LOGIN, provider="email", expires_at=services.clock() + 900,
                    state_data=json.dumps({"return_to": return_to}, separators=(",", ":")))
        callback_url = (f"{settings.public_origin}{settings.base_path.rstrip('/')}/auth/email/callback"
                        f"?state={raw_transaction_token}")
        try:
            adapter.start(intent="login", return_to=return_to, email=normalized_email,
                          transaction_token_hash=transaction_hash, callback_url=callback_url)
        except Exception:
            # Do not reveal account or delivery state; never serialize provider failures.
            pass
        return http.json_response({"ok": True}, status_code=202, request_id=_request_id(request))

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
        transaction_hash = getattr(result, "transaction_token_hash", None)
        if (not isinstance(user_id, str) or not isinstance(transaction_hash, str)
                or (request.path_params["provider"] != "email" and return_to is None)):
            return http.error_response("invalid", _request_id(request))
        preauth_cookie = request.cookies.get(http.cookie_name(http.COOKIE_PREAUTH, settings))
        try:
            with _uow(settings, services) as (repositories, sessions):
                transaction = repositories.auth_transactions.get(transaction_hash)
                # Bind to the browser that started the flow before any claim or side effect.
                if (transaction is None or transaction.provider != request.path_params["provider"]
                        or preauth_cookie is None
                        or not sessions.security.verify_binding(preauth_cookie, transaction.preauth_binding_hash)):
                    return http.error_response("invalid", _request_id(request))
                if transaction.provider == "email":
                    try:
                        stored = json.loads(transaction.state_data or "{}")
                    except (TypeError, ValueError):
                        return http.error_response("invalid", _request_id(request))
                    return_to = http.local_return_to(stored.get("return_to")) if isinstance(stored, dict) else None
                    if return_to is None:
                        return http.error_response("invalid", _request_id(request))
                with repositories.database.transaction(immediate=True):
                    if not repositories.auth_transactions.consume_once(transaction_hash, now=services.clock()):
                        return http.error_response("invalid", _request_id(request))
                    # The authentication event is this verified callback, not a client value.
                    credentials = sessions.issue(user_id, old_cookie=_session_cookie(request),
                                                 auth_time=services.clock())
        except ServerError:
            return http.error_response("invalid", _request_id(request))
        response = RedirectResponse(return_to, status_code=302, headers={"Cache-Control": http.NO_STORE})
        http.set_session_cookie(response, credentials.cookie_value, settings, max_age=settings.session_idle_seconds)
        http.clear_preauth_cookie(response, settings)
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
        return http.json_response({"state": "pending", "redirect_url": redirect, "purchase_id": purchase.id},
                                  request_id=_request_id(request))

    async def webhook(request: Request):
        if services.payments is None:
            return http.error_response("unavailable", _request_id(request))
        if request.path_params["provider"] != services.payment_provider:
            return http.error_response("not-found", _request_id(request))
        try:
            event = services.payments.verify_webhook(request.scope["app_body"], dict(request.headers), services.clock())
        except ServerError:
            return http.error_response("invalid", _request_id(request))
        if event is None:
            return http.error_response("invalid", _request_id(request))
        with _uow(settings, services) as (repositories, sessions):
            try:
                _apply_verified_event(repositories, services.payment_provider, event)
            except ValueError:
                return http.error_response("invalid", _request_id(request))
            except ServerError:
                return http.error_response("server-error", _request_id(request))
        return http.json_response({"ok": True}, request_id=_request_id(request))

    base = settings.base_path.rstrip("/")
    routes = [Route(base + path, endpoint, methods=methods) for path, endpoint, methods in (
        ("/auth/csrf", auth_csrf, ["GET"]),
        ("/auth/providers", auth_providers, ["GET"]),
        ("/auth/email/start", email_start, ["POST"]),
        ("/auth/{provider}/start", provider_start, ["POST"]),
        ("/auth/{provider}/callback", provider_callback, ["GET", "POST"]),
        ("/me", me, ["GET"]),
        ("/logout", logout, ["POST"]),
        ("/products", products, ["GET"]),
        ("/purchases", purchases, ["GET"]),
        ("/purchases/{id}", purchase_detail, ["GET"]),
        ("/entitlements", entitlements, ["GET"]),
        ("/checkout", checkout, ["POST"]),
        ("/payments/{provider}/webhook", webhook, ["POST"]),
        ("/content/{kind}/{id}", content, ["GET"]),
        ("/account/export", export, ["GET"]),
        ("/account/delete", account_delete, ["POST"]),
    )]
    async def not_found(request: Request, exc):
        return http.error_response("not-found", _safe_request_id(request))

    async def method_not_allowed(request: Request, exc):
        response = http.error_response("invalid", _safe_request_id(request))
        response.status_code = 405
        response.headers["Allow"] = exc.headers.get("allow", "") if getattr(exc, "headers", None) else ""
        return response

    async def unexpected(request: Request, exc: Exception):
        return http.error_response("server-error", _safe_request_id(request))

    app = Starlette(routes=routes, exception_handlers={404: not_found, 405: method_not_allowed, Exception: unexpected})
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
    from .models import PaymentEventStatus, PurchaseState, UserStatus
    new_state = PurchaseState(state)  # unknown state -> ValueError before any write
    with repositories.database.transaction(immediate=True):
        repositories.payment_events.record(provider_path, getattr(event, "event_id", ""),
                                           getattr(event, "payload_digest", ""), purchase_id=purchase.id,
                                           normalized_state=str(state), revision=revision)
        repositories.purchases.compare_and_swap(purchase.id, purchase.revision,
                                                state=new_state, revision=revision)
        owner = repositories.users.get(purchase.user_id)
        if state == "paid":
            # A late webhook for a known purchase must not grant to a deleted account.
            if owner is not None and owner.status is UserStatus.ACTIVE:
                repositories.entitlements.grant(purchase.user_id, purchase.product_id, purchase.id)
        elif state in ("refunded", "chargeback", "cancelled"):
            repositories.entitlements.revoke(purchase.id, purchase.product_id)
