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
import sqlite3
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
from .config import AppEnv, check_paddle_settings
from .content_access import ContentAccessError, resolve_content
from .scoring_http import Hooks, scoring_routes
from .scoring_service import ScoringService
from .paddle.types import PaddleConfigError, PaddleError
from .paddle.signature import verify_webhook as verify_paddle_webhook
from .paddle_repo import PaddleQueueFull
from .paddle_checkout import build_checkout_context, reserve_or_reuse_checkout
from .paddle_service import PaddleService
from .content_catalog import build_release_summary
from .dto import entitlements_dto, me_dto, product_list_dto
from .models import AuthIntent, ConflictError, ServerError, SetupError, UserStatus, canonical_acknowledgements, resolve_active_bundle
from .repositories import Repositories
from .rate_limit import EmailStartLimiter
from .sessions import AuthenticatedPrincipal, SessionService

PADDLE_PROVIDERS = frozenset({"paddle-sandbox", "paddle-live"})   # exact names; nothing parses substrings


@dataclass
class Services:
    open_database: Callable[[], object]
    catalog: object | None = None
    payments: object | None = None
    payment_provider: str = "mock"
    payment_redirect_hosts: frozenset[str] = frozenset()
    providers: Mapping[str, object] = field(default_factory=dict)
    policy_activations: tuple = ()
    scoring: object | None = None
    paddle_transport: object | None = None     # injected; no real transport exists yet (sandbox/test only)
    on_decision: object | None = None          # optional reconcile observer(purchase_id, reason); stores nothing
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


def _is_active_user(repositories, user_id: str) -> bool:
    """A last re-check inside the checkout transaction: the session was valid, the account must still be active."""
    user = repositories.users.get(user_id)
    return user is not None and user.status is UserStatus.ACTIVE


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

    # The injected Settings are checked here by the same function load_config uses, not trusted because an env parser ran.
    check_paddle_settings(settings.payments_provider, settings.paddle_env, settings.paddle_endpoint_secret, settings.paddle_products)
    if settings.payments_provider == "paddle":
        if settings.app_env is AppEnv.PRODUCTION or services.paddle_transport is None or services.payments is not None:
            raise SetupError("paddle needs the sandbox/test configuration, an injected transport and no mock payment adapter")
    elif services.paddle_transport is not None:
        raise SetupError("a Paddle transport was injected but PAYMENTS_PROVIDER is not paddle")
    if settings.scoring.any():
        if settings.app_env is AppEnv.PRODUCTION or services.scoring is None:
            raise SetupError("scoring requires the mock/test configuration and a loaded scoring catalog")
        if settings.scoring.enabled:
            from .models import ContentError
            from .scoring_catalog import verify_binding
            official = services.scoring.catalogs.get("official")
            try:
                if official is None:
                    raise ContentError("no official scoring snapshot")
                verify_binding(official, services.catalog)
                from .scoring_service import verify_pool
                verify_pool(services.scoring)
            except ContentError as exc:
                raise SetupError(f"scoring snapshot is not bound to the content manifest: {exc}") from None
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
            gate = None
            if settings.scoring.enabled and principal is not None:
                scoring_service = ScoringService(repositories, settings, services.scoring, clock=services.clock)
                gate = lambda entry: scoring_service.content_allowed(  # noqa: E731
                    principal.user_id, request.path_params["kind"], request.path_params["id"], entry)
            try:
                data, media_type = resolve_content(services.catalog, repositories, principal,
                                                   request.path_params["kind"], request.path_params["id"], gate=gate)
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
            scoring_part = (ScoringService(repositories, settings, services.scoring, clock=services.clock).export(user.id)
                            if settings.scoring.enabled else None)
            return http.json_response(export_account(repositories, user, scoring=scoring_part), request_id=_request_id(request))

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

    def paddle_service(repositories) -> PaddleService:
        return PaddleService(repositories, services.paddle_transport, settings.paddle_products,
                             environment=settings.paddle_env, clock=services.clock, on_decision=services.on_decision)

    async def checkout(request: Request):
        if settings.payments_provider != "paddle":
            return await create_checkout(request)
        try:        # a storage or operational failure anywhere on the Paddle path (also the first intent transaction) is a 503
            return await create_checkout(request)
        except (ServerError, sqlite3.Error):
            return http.error_response("unavailable", _request_id(request))

    async def create_checkout(request: Request):
        policy = services.checkout_policy
        paddle = settings.payments_provider == "paddle"
        if policy is None or (services.paddle_transport is None if paddle else services.payments is None):
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
            if paddle and product.id not in settings.paddle_products:
                return http.error_response("unavailable", _request_id(request))     # the server sells only what it configured
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
            if paddle:
                # Everything that authorises this checkout is revalidated INSIDE the same BEGIN IMMEDIATE as
                # reuse/quota/reserve, and the context is built from those fresh values. The cheap checks above only
                # shape the error codes; they are never the authority.
                with repositories.database.transaction(immediate=True):
                    fresh_principal = _principal(request, sessions)
                    if (fresh_principal is None or fresh_principal.user_id != principal.user_id
                            or not _is_active_user(repositories, principal.user_id)):
                        return http.error_response("no-session", _request_id(request))
                    fresh_product = repositories.products.get(product.id)
                    if fresh_product is None or not fresh_product.active:
                        return http.error_response("invalid", _request_id(request))
                    fresh_bundle = resolve_active_bundle(services.policy_activations,
                                                         repositories.policies.list_for_product(product.id),
                                                         product_id=product.id, access_scope=fresh_product.access_scope,
                                                         locale="en", now=services.clock(), app_env=settings.app_env.value)
                    if fresh_bundle is None:
                        return http.error_response("unavailable", _request_id(request))
                    if body["policy_version"] != fresh_bundle.version or policy.get("version") != fresh_bundle.version:
                        return http.error_response("stale-policy", _request_id(request))
                    if set(acknowledgements) != set(required) or set(required) != set(fresh_bundle.required_acknowledgements):
                        return http.error_response("invalid", _request_id(request))
                    context = build_checkout_context(environment=settings.paddle_env, user_id=principal.user_id,
                                                     product=fresh_product, bundle=fresh_bundle,
                                                     acknowledgements=acknowledgements,
                                                     paddle_product=settings.paddle_products[product.id])
                    reservation = reserve_or_reuse_checkout(repositories, context, services.clock())
            else:
                with repositories.database.transaction(immediate=True):
                    purchase = repositories.purchases.create(
                        principal.user_id, product.id, services.payment_provider, f"srv-{secrets.token_urlsafe(12)}")
                    repositories.consents.record(purchase.id, bundle.id, list(acknowledgements))
        if paddle:
            if reservation.kind == "throttled":     # no ids, no quota details and no remote call on a refusal
                return http.error_response("throttled", _request_id(request))
            if reservation.kind == "reused":        # reopening: the SAME purchase and transaction, no new send
                return http.json_response({"purchase_id": reservation.purchase_id, "state": "pending", "provider": "paddle",
                                           "checkout": {"mode": "overlay", "transaction_id": reservation.transaction_id}},
                                          request_id=_request_id(request))
            # AFTER the commit and with no open transaction: the remote call can never run inside one.
            try:
                with _uow(settings, services) as (repositories, sessions):
                    outcome = paddle_service(repositories).start_checkout(repositories.purchases.get(reservation.purchase_id))
            except (ServerError, sqlite3.Error):
                return http.error_response("unavailable", _request_id(request))
            if outcome["result"] != "bound":
                return http.error_response("unavailable", _request_id(request))     # no provider id in any failure answer
            return http.json_response({"purchase_id": reservation.purchase_id, "state": "pending", "provider": "paddle",
                                       "checkout": {"mode": "overlay", "transaction_id": outcome["transaction_id"]}},
                                      request_id=_request_id(request))
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

    async def paddle_webhook(request: Request, provider_path: str):
        """Server-to-server: the signature over the ORIGINAL bytes is the gate (no Origin/CSRF). Evidence + job only."""
        if (settings.payments_provider != "paddle" or provider_path != f"paddle-{settings.paddle_env}"
                or services.paddle_transport is None):
            return http.error_response("not-found", _request_id(request))
        values = request.headers.getlist("paddle-signature")          # the RAW header list: two fields are ambiguous
        try:
            event = verify_paddle_webhook(request.scope["app_body"], {"paddle-signature": values[0] if len(values) == 1 else values},
                                          services.clock(), secret=settings.paddle_endpoint_secret,
                                          environment=settings.paddle_env)
        except PaddleConfigError:
            return http.error_response("unavailable", _request_id(request))       # our own configuration problem, not the caller's
        except PaddleError:
            return http.error_response("invalid", _request_id(request))           # one generic answer, no detail
        try:
            with _uow(settings, services) as (repositories, sessions):
                outcome = paddle_service(repositories).handle_event(event)
        except PaddleQueueFull:
            return http.error_response("unavailable", _request_id(request))
        except (ServerError, sqlite3.Error):
            return http.error_response("unavailable", _request_id(request))
        if outcome == "conflict":
            return http.error_response("conflict", _request_id(request))
        return http.json_response({"ok": True}, request_id=_request_id(request))

    async def webhook(request: Request):
        if request.path_params["provider"] in PADDLE_PROVIDERS:
            return await paddle_webhook(request, request.path_params["provider"])
        if settings.payments_provider == "paddle":      # with Paddle configured there is no mock webhook route at all
            return http.error_response("not-found", _request_id(request))
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
    # Always mounted: with the flags off every scoring route answers 503 before touching the database.
    routes += scoring_routes(settings, services, Hooks(
        uow=lambda: _uow(settings, services), session_cookie=_session_cookie, check_origin=_check_origin,
        client_ip=_client_ip, request_id=_request_id, json_body=_json_body))

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
    """Apply only a newer authoritative revision; never create users or grants for unknown purchases.

    Dedup, the locked purchase read, the revision check, the CAS and the grant/revoke are ONE transaction
    (``payment_apply.apply_decision``); an unknown state value raises ValueError before any write.
    """
    from .models import PurchaseState
    from .payment_apply import apply_decision
    state = getattr(event, "state", None)
    purchase = repositories.purchases.get_by_provider_id(provider_path, getattr(event, "provider_purchase_id", ""))
    if purchase is None:
        return
    new_state = PurchaseState(state)  # unknown state -> ValueError before any write
    revision = getattr(event, "revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        return                         # a malformed provider revision is ignored, as before
    result = apply_decision(repositories, provider=provider_path, event_id=getattr(event, "event_id", ""),
                   payload_digest=getattr(event, "payload_digest", ""), purchase_id=purchase.id,
                   new_state=new_state, event_label=str(state), new_revision=revision)
    if result.outcome == "conflict":
        from .models import ConflictError
        raise ConflictError("payment event id reused with a different payload")  # unchanged external contract
