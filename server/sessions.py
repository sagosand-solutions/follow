# SPDX-License-Identifier: AGPL-3.0-or-later
"""Server-side sessions and authenticated-principal helpers (019-S).

The service is constructed per request/unit-of-work with request-owned
repositories. Raw cookie values are returned only to trusted HTTP transport and
are excluded from repr; they are never stored in SQLite or principals.
"""
from __future__ import annotations

import math
import secrets
from dataclasses import dataclass, field
from typing import Callable

from .auth import AuthSecurity
from .config import Settings
from .models import Session, ServerError, UserStatus
from .repositories import Repositories


class SessionDenied(ServerError):
    """A session operation cannot be completed without a valid active session."""


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user_id: str
    session_id_hash: str
    auth_time: float
    expires_at: float
    absolute_expires_at: float


@dataclass(frozen=True)
class SessionCredentials:
    session: Session
    cookie_value: str = field(repr=False)
    csrf_token: str = field(repr=False)


class SessionService:
    """Session lifecycle over the frozen 018 repository interfaces.

    Do not share a service/repository Database connection across concurrent
    requests or tasks. The HTTP layer owns cookie flags and Origin checks.
    """

    def __init__(self, repositories: Repositories, settings: Settings, *,
                 clock: Callable[[], float] | None = None,
                 token_factory: Callable[[int], str] = secrets.token_urlsafe):
        self.repositories = repositories
        self.database = repositories.database
        self.settings = settings
        self.clock = clock or self.database.clock
        self.security = AuthSecurity(settings, token_factory=token_factory)

    def issue(self, user_id: str, *, old_cookie: str | None = None,
              auth_time: float | None = None) -> SessionCredentials:
        """Create a session and atomically revoke a known prior session.

        Call only after verified authentication. This does not create users or
        resolve identities; login's claim→identity→session transaction remains
        the responsibility of the later auth service.
        """
        now = self.clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            raise SessionDenied("server clock unavailable")
        authenticated_at = now if auth_time is None else auth_time
        if (not isinstance(authenticated_at, (int, float)) or isinstance(authenticated_at, bool)
                or not math.isfinite(authenticated_at) or authenticated_at < 0 or authenticated_at > now):
            raise SessionDenied("authentication time is invalid")

        raw_cookie = self.security.new_token()
        csrf_token = self.security.csrf_for(raw_cookie, purpose="session")
        id_hash = self.security.session_id_hash(raw_cookie)
        csrf_hash = self.security.csrf_digest(csrf_token)
        idle = self.settings.session_idle_seconds
        absolute = self.settings.session_absolute_seconds
        if idle < 1 or absolute < 1:
            raise SessionDenied("session lifetime configuration is invalid")

        old_hash = None
        if old_cookie is not None:
            try:
                old_hash = self.security.session_id_hash(old_cookie)
            except ValueError:
                # A malformed stale cookie is not an identity and cannot select a row.
                old_hash = None

        with self.database.transaction(immediate=True):
            user = self.repositories.users.get(user_id)
            if user is None or user.status is not UserStatus.ACTIVE:
                raise SessionDenied("active account required")
            old_session = self.repositories.sessions.get(old_hash) if old_hash else None
            if old_session is not None and old_session.user_id != user_id:
                raise SessionDenied("session rotation binding failed")
            created = self.repositories.sessions.create(
                user_id,
                id_hash=id_hash,
                csrf_hash=csrf_hash,
                ttl_seconds=idle,
                absolute_ttl_seconds=absolute,
                auth_time=float(authenticated_at),
            )
            if old_session is not None:
                self.repositories.sessions.revoke(old_session.id_hash)
        return SessionCredentials(created, raw_cookie, csrf_token)

    def authenticate(self, raw_cookie: str | None) -> AuthenticatedPrincipal | None:
        """Validate, slide idle expiry within the absolute limit, or deny."""
        try:
            id_hash = self.security.session_id_hash(raw_cookie)
        except (TypeError, ValueError):
            return None
        now = self.clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            return None
        session = self.repositories.sessions.get(id_hash)
        if session is None:
            return None
        user = self.repositories.users.get(session.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            self.repositories.sessions.revoke(id_hash)
            return None
        if (now >= session.expires_at or now >= session.absolute_expires_at
                or session.created_at > now or session.last_seen > now):
            self.repositories.sessions.revoke(id_hash)
            return None

        # 018's touch takes integer seconds. Floor the remaining absolute life so
        # the sliding idle deadline never crosses the immutable absolute cutoff.
        remaining = math.floor(session.absolute_expires_at - now)
        if remaining >= 1:
            touched = self.repositories.sessions.touch(
                id_hash, ttl_seconds=min(self.settings.session_idle_seconds, remaining), when=now)
            if touched is None:
                return None
            session = touched
        if now >= session.expires_at or now >= session.absolute_expires_at:
            self.repositories.sessions.revoke(id_hash)
            return None
        return AuthenticatedPrincipal(
            user_id=session.user_id,
            session_id_hash=session.id_hash,
            auth_time=session.auth_time,
            expires_at=session.expires_at,
            absolute_expires_at=session.absolute_expires_at,
        )

    def verify_csrf(self, raw_cookie: str | None, submitted: str) -> bool:
        """Verify a session cookie's purpose-bound CSRF token against its DB digest."""
        try:
            id_hash = self.security.session_id_hash(raw_cookie)
        except (TypeError, ValueError):
            return False
        session = self.repositories.sessions.get(id_hash)
        now = self.clock()
        if (session is None or not isinstance(now, (int, float)) or isinstance(now, bool)
                or not math.isfinite(now) or now >= session.expires_at
                or now >= session.absolute_expires_at):
            return False
        user = self.repositories.users.get(session.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            return False
        return self.security.verify_csrf(
            raw_cookie, submitted, purpose="session", stored_hash=session.csrf_hash)

    def require_recent_auth(self, principal: AuthenticatedPrincipal) -> bool:
        """Check the current stored session, active user and configured age."""
        if not isinstance(principal, AuthenticatedPrincipal):
            return False
        now = self.clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            return False
        session = self.repositories.sessions.get(principal.session_id_hash)
        if (session is None or session.user_id != principal.user_id
                or now >= session.expires_at or now >= session.absolute_expires_at):
            return False
        user = self.repositories.users.get(session.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            return False
        age = now - session.auth_time
        return 0 <= age <= self.settings.recent_auth_seconds

    def revoke(self, raw_cookie: str | None) -> bool:
        """Idempotently revoke the server-side record for a presented cookie."""
        try:
            id_hash = self.security.session_id_hash(raw_cookie)
        except (TypeError, ValueError):
            return False
        return self.repositories.sessions.revoke(id_hash)

    def revoke_all_for_user(self, user_id: str) -> int:
        return self.repositories.sessions.revoke_all_for_user(user_id)
