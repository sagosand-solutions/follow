# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP routes for scoring (023), mounted by ``create_app`` only when the main flag is on.

Every handler checks its flag BEFORE opening a database, touching a cookie or
creating a principal: a disabled route answers 503 with no side effect. Mutations
need an exact Origin and the CSRF token that matches the caller's own cookie
(session or anonymous scoring cookie). Handlers only translate; rules live in
``scoring_service``.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Callable

from starlette.responses import JSONResponse
from starlette.routing import Route

from . import http
from .auth import AuthSecurity
from .models import StorageError
from .scoring_limits import RollingLimiter
from .scoring_service import (Actor, LIST_MAX, ScoringError, ScoringService, _ISSUE_RE, _KEY_RE, _OPT_RE)

_QUESTION_ID_RE = re.compile(r"[a-z][a-z0-9.-]{0,79}\Z")
WINDOWS = ("all", "week")


@dataclass
class Hooks:
    """What the scoring routes borrow from app.py (kept explicit to avoid a circular import)."""
    uow: Callable
    session_cookie: Callable
    check_origin: Callable
    client_ip: Callable
    request_id: Callable
    json_body: Callable


def _error(exc: ScoringError, request_id: str) -> JSONResponse:
    response = http.error_response(exc.code, request_id)
    if exc.attempt is not None:
        payload = {"error": {"code": exc.code, "message": "Request could not be completed."},
                   "request_id": request_id, "attempt": exc.attempt}
        response = JSONResponse(payload, status_code=http.ERROR_STATUS[exc.code],
                                headers={"Cache-Control": http.NO_STORE, "X-Request-Id": request_id})
    if exc.retry_after is not None:
        response.headers["Retry-After"] = str(exc.retry_after)
    return response


def scoring_routes(settings, services, hooks: Hooks) -> list:
    flags = settings.scoring
    limiter = RollingLimiter(settings.session_secret, clock=services.clock)
    base = settings.base_path.rstrip("/")

    def disabled(request, *, needs: str = "enabled"):
        # Master flag, plus the route group's own flag. Nothing has been read or written yet.
        if not flags.enabled or (needs != "enabled" and not getattr(flags, needs)):
            return http.error_response("unavailable", hooks.request_id(request))
        return None

    def throttle(request, bucket, value, limit, window):
        ok, retry = limiter.consume(bucket, value, limit=limit, window=window)
        if ok:
            return None
        response = http.error_response("throttled", hooks.request_id(request))
        response.headers["Retry-After"] = str(retry)
        return response

    def anon_cookie(request):
        return request.cookies.get(http.cookie_name(http.COOKIE_SCORING_ANON, settings))

    def resolve_actor(request, repositories, sessions, service: ScoringService, *, mutating: bool, need_account=False):
        """(actor, error_response). A valid session wins; an anonymous cookie is free-only and needs its own flag."""
        principal = sessions.authenticate(hooks.session_cookie(request))
        if principal is not None:
            if mutating and not sessions.verify_csrf(hooks.session_cookie(request), request.headers.get("x-csrf-token")):
                return None, http.error_response("forbidden", hooks.request_id(request))
            return Actor(user_id=principal.user_id), None
        if need_account or not flags.anonymous:
            return None, http.error_response("no-session", hooks.request_id(request))
        raw = anon_cookie(request)
        actor = service.anon_actor(raw)
        if actor is None:
            return None, http.error_response("no-session", hooks.request_id(request))
        if mutating and not service.verify_anon_csrf(raw, request.headers.get("x-csrf-token")):
            return None, http.error_response("forbidden", hooks.request_id(request))
        return actor, None

    def with_anon_refresh(response, request, actor):
        """A live anonymous profile keeps its 30-day window: re-set the cookie on owner activity."""
        raw = anon_cookie(request)
        if actor is not None and actor.anon_hash is not None and raw and response.status_code == 200:
            http.set_scoring_anon_cookie(response, raw, settings, max_age=30 * 24 * 3600)
        return response

    def make_service(repositories):
        return ScoringService(repositories, settings, services.scoring, clock=services.clock)

    def exact_body(request, keys, optional=()):
        body = hooks.json_body(request)
        if not isinstance(body, dict) or not set(keys) <= set(body) or not set(body) <= set(keys) | set(optional):
            return None
        return body

    async def anonymous(request):
        if (blocked := disabled(request, needs="anonymous")) is not None:
            return blocked
        if not hooks.check_origin(request):
            return http.error_response("forbidden", hooks.request_id(request))
        preauth = request.cookies.get(http.cookie_name(http.COOKIE_PREAUTH, settings))
        if preauth is None or not AuthSecurity(settings).verify_csrf(preauth, request.headers.get("x-csrf-token"),
                                                                     purpose="preauth"):
            return http.error_response("forbidden", hooks.request_id(request))
        if exact_body(request, ()) is None:
            return http.error_response("invalid", hooks.request_id(request))
        if (blocked := throttle(request, "anon-ip", hooks.client_ip(request, settings), 10, 3600)) is not None:
            return blocked
        with hooks.uow() as (repositories, sessions):
            service = make_service(repositories)
            try:
                raw, csrf, expires_at = service.create_anonymous(anon_cookie(request))
            except ScoringError as exc:
                return _error(exc, hooks.request_id(request))
            except (StorageError, sqlite3.Error):
                return http.error_response("unavailable", hooks.request_id(request))
        response = http.json_response({"principal": "anonymous", "csrf_token": csrf, "expires_at": expires_at},
                                      request_id=hooks.request_id(request))
        http.set_scoring_anon_cookie(response, raw, settings, max_age=30 * 24 * 3600)
        return response

    async def issue(request):
        if (blocked := disabled(request)) is not None:
            return blocked
        if not hooks.check_origin(request):
            return http.error_response("forbidden", hooks.request_id(request))
        with hooks.uow() as (repositories, sessions):
            service = make_service(repositories)
            actor, error = resolve_actor(request, repositories, sessions, service, mutating=True)
            if error is not None:
                return error
            if exact_body(request, ()) is None:
                return http.error_response("invalid", hooks.request_id(request))
            question_id = request.path_params["id"]
            if not _QUESTION_ID_RE.fullmatch(question_id):
                return http.error_response("not-found", hooks.request_id(request))
            who = actor.user_id or actor.anon_hash
            if (blocked := throttle(request, "issue", who, 60, 60)) is not None:
                return blocked
            try:
                result = service.issue(actor, question_id)
            except ScoringError as exc:
                return _error(exc, hooks.request_id(request))
            except (StorageError, sqlite3.Error):
                return http.error_response("unavailable", hooks.request_id(request))
            return with_anon_refresh(http.json_response(result, request_id=hooks.request_id(request)), request, actor)

    async def answers(request):
        if (blocked := disabled(request)) is not None:
            return blocked
        if not hooks.check_origin(request):
            return http.error_response("forbidden", hooks.request_id(request))
        with hooks.uow() as (repositories, sessions):
            service = make_service(repositories)
            actor, error = resolve_actor(request, repositories, sessions, service, mutating=True)
            if error is not None:
                return error
            body = exact_body(request, ("issue_id", "outcome", "option_id", "idempotency_key"))
            if (body is None or not isinstance(body["issue_id"], str) or not _ISSUE_RE.fullmatch(body["issue_id"])
                    or body["outcome"] not in ("answer", "not_sure") or not isinstance(body["idempotency_key"], str)
                    or not _KEY_RE.fullmatch(body["idempotency_key"])
                    or (body["outcome"] == "answer" and not (isinstance(body["option_id"], str) and _OPT_RE.fullmatch(body["option_id"])))
                    or (body["outcome"] == "not_sure" and body["option_id"] is not None)):
                return http.error_response("invalid", hooks.request_id(request))
            who = actor.user_id or actor.anon_hash
            for bucket, value, limit in (("answer-p", who, 30), ("answer-ip", hooks.client_ip(request, settings), 120)):
                if (blocked := throttle(request, bucket, value, limit, 60)) is not None:
                    return blocked
            try:
                result = service.answer(actor, body)
            except ScoringError as exc:
                return _error(exc, hooks.request_id(request))
            except (StorageError, sqlite3.Error):
                return http.error_response("unavailable", hooks.request_id(request))
            return with_anon_refresh(http.json_response(result, request_id=hooks.request_id(request)), request, actor)

    async def me(request):
        if (blocked := disabled(request)) is not None:
            return blocked
        family = request.query_params.get("family", "")
        window = request.query_params.get("window", "all")
        scope = request.query_params.get("scope", "total")
        if window not in WINDOWS or set(request.query_params) - {"family", "window", "scope"}:
            return http.error_response("invalid", hooks.request_id(request))
        with hooks.uow() as (repositories, sessions):
            service = make_service(repositories)
            actor, error = resolve_actor(request, repositories, sessions, service, mutating=False)
            if error is not None:
                return error
            try:
                return http.json_response(service.me(actor, family, window, scope), request_id=hooks.request_id(request))
            except ScoringError as exc:
                return _error(exc, hooks.request_id(request))
            except (StorageError, sqlite3.Error):
                return http.error_response("unavailable", hooks.request_id(request))

    async def claim(request):
        if (blocked := disabled(request, needs="claim")) is not None:
            return blocked
        if not hooks.check_origin(request):
            return http.error_response("forbidden", hooks.request_id(request))
        with hooks.uow() as (repositories, sessions):
            service = make_service(repositories)
            actor, error = resolve_actor(request, repositories, sessions, service, mutating=True, need_account=True)
            if error is not None:
                return error
            if exact_body(request, ()) is None:
                return http.error_response("invalid", hooks.request_id(request))
            source = service.anon_actor(anon_cookie(request))
            if source is None:
                return http.error_response("not-found", hooks.request_id(request))
            if (blocked := throttle(request, "claim", actor.user_id, 5, 3600)) is not None:
                return blocked
            try:
                result = service.claim(actor.user_id, source.anon_hash)
            except ScoringError as exc:
                return _error(exc, hooks.request_id(request))
            except (StorageError, sqlite3.Error):
                return http.error_response("unavailable", hooks.request_id(request))
        response = http.json_response(result, request_id=hooks.request_id(request))
        http.clear_scoring_anon_cookie(response, settings)
        return response

    async def preferences(request):
        if (blocked := disabled(request, needs="publish")) is not None:
            return blocked
        if not hooks.check_origin(request):
            return http.error_response("forbidden", hooks.request_id(request))
        with hooks.uow() as (repositories, sessions):
            service = make_service(repositories)
            actor, error = resolve_actor(request, repositories, sessions, service, mutating=True)
            if error is not None:
                return error
            body = exact_body(request, ("family", "opt_in"), optional=("nickname",))
            if body is None or body["family"] not in ("official", "free") or not isinstance(body["opt_in"], bool):
                return http.error_response("invalid", hooks.request_id(request))
            if "nickname" in body and body["nickname"] is not None and not isinstance(body["nickname"], str):
                return http.error_response("invalid", hooks.request_id(request))
            try:
                result = service.set_preference(actor, body["family"], body["opt_in"], body.get("nickname"), "nickname" in body)
            except ScoringError as exc:
                return _error(exc, hooks.request_id(request))
            except (StorageError, sqlite3.Error):
                return http.error_response("unavailable", hooks.request_id(request))
            return with_anon_refresh(http.json_response(result, request_id=hooks.request_id(request)), request, actor)

    async def leaderboards(request):
        if (blocked := disabled(request, needs="publish")) is not None:
            return blocked
        params = request.query_params
        allowed = {"family", "scope", "window", "limit", "cursor"}
        if set(params) - allowed or len(request.url.query) > 512:
            return http.error_response("invalid", hooks.request_id(request))
        limit_text = params.get("limit", "20")
        if not limit_text.isdigit() or not 1 <= int(limit_text) <= LIST_MAX or params.get("window", "all") not in WINDOWS:
            return http.error_response("invalid", hooks.request_id(request))
        if (blocked := throttle(request, "lists-ip", hooks.client_ip(request, settings), 60, 60)) is not None:
            return blocked
        with hooks.uow() as (repositories, sessions):
            service = make_service(repositories)
            try:
                result = service.leaderboard(params.get("family", ""), params.get("scope", "total"),
                                             params.get("window", "all"), int(limit_text), params.get("cursor"))
            except ScoringError as exc:
                return _error(exc, hooks.request_id(request))
            except (StorageError, sqlite3.Error):
                return http.error_response("unavailable", hooks.request_id(request))
        payload = http.json_response(result, request_id=hooks.request_id(request))
        if len(payload.body) > 16 * 1024:
            return http.error_response("unavailable", hooks.request_id(request))
        return payload

    return [Route(base + path, endpoint, methods=methods) for path, endpoint, methods in (
        ("/scoring/anonymous", anonymous, ["POST"]),
        ("/scoring/questions/{id}/issue", issue, ["POST"]),
        ("/scoring/answers", answers, ["POST"]),
        ("/scoring/me", me, ["GET"]),
        ("/scoring/claim", claim, ["POST"]),
        ("/scoring/preferences", preferences, ["POST"]),
        ("/leaderboards", leaderboards, ["GET"]),
    )]
