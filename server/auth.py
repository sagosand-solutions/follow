# SPDX-License-Identifier: AGPL-3.0-or-later
"""Purpose-bound CSRF primitives and the bounded, process-local PKCE cache.

This module does not implement provider protocols. HTTP callers must also check
Origin/Fetch-Metadata; these helpers only verify the bound token and cookie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import math
import re
import secrets
import threading
from dataclasses import dataclass, field
from typing import Callable

from .config import Settings
from .models import ServerError

_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_PKCE_VERIFIER_RE = re.compile(r"[A-Za-z0-9._~-]{43,128}\Z")
_PURPOSES = frozenset({"preauth", "session", "scoring-anon"})


class AuthRestartRequired(ServerError):
    """A required process-local PKCE verifier is unavailable; restart auth."""


class AuthCapacityError(ServerError):
    """The bounded active-auth/PKCE capacity is full; fail closed."""


@dataclass(frozen=True)
class PreAuthCredentials:
    # These are bearer-like values for HTTP cookie/header transport only.
    binding_cookie: str = field(repr=False)
    csrf_token: str = field(repr=False)


class AuthSecurity:
    """HMAC/digest helpers sharing Settings' separately keyed secrets."""

    def __init__(self, settings: Settings, *, token_factory: Callable[[int], str] = secrets.token_urlsafe):
        self._settings = settings
        self._token_factory = token_factory

    def new_token(self) -> str:
        token = self._token_factory(32)
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise ValueError("token generator must return a 256-bit URL-safe token")
        return token

    @staticmethod
    def _raw_bytes(raw_cookie: str) -> bytes:
        if not isinstance(raw_cookie, str) or not _TOKEN_RE.fullmatch(raw_cookie):
            raise ValueError("invalid opaque token")
        return raw_cookie.encode("ascii")

    def session_id_hash(self, raw_cookie: str) -> str:
        raw = self._raw_bytes(raw_cookie)
        return hmac.new(self._settings.session_secret, b"session-id:v1\0" + raw, hashlib.sha256).hexdigest()

    def binding_hash(self, raw_cookie: str) -> str:
        return hashlib.sha256(self._raw_bytes(raw_cookie)).hexdigest()

    def csrf_for(self, raw_cookie: str, *, purpose: str) -> str:
        if purpose not in _PURPOSES:
            raise ValueError("unknown CSRF purpose")
        raw = self._raw_bytes(raw_cookie)
        message = b"csrf:v1\0" + purpose.encode("ascii") + b"\0" + raw
        digest = hmac.new(self._settings.csrf_key, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @staticmethod
    def csrf_digest(csrf_token: str) -> str:
        if not isinstance(csrf_token, str) or not _TOKEN_RE.fullmatch(csrf_token):
            raise ValueError("invalid CSRF token")
        return hashlib.sha256(csrf_token.encode("ascii")).hexdigest()

    def verify_binding(self, raw_cookie: str, expected_hash: str) -> bool:
        if not isinstance(expected_hash, str) or not _HASH_RE.fullmatch(expected_hash):
            return False
        try:
            actual = self.binding_hash(raw_cookie)
        except ValueError:
            return False
        return hmac.compare_digest(actual, expected_hash)

    def verify_csrf(self, raw_cookie: str, submitted: str, *, purpose: str,
                    stored_hash: str | None = None) -> bool:
        """Verify a purpose-bound token; session callers also pass DB digest.

        Preauth callers must separately verify ``binding_hash`` against the
        transaction's stored ``preauth_binding_hash``. Exact Origin and
        Fetch-Metadata checks remain mandatory at the HTTP boundary.
        """
        try:
            expected = self.csrf_for(raw_cookie, purpose=purpose)
            submitted_digest = self.csrf_digest(submitted)
        except (TypeError, ValueError):
            return False
        if not hmac.compare_digest(expected, submitted):
            return False
        if stored_hash is None:
            return purpose in ("preauth", "scoring-anon")
        if not isinstance(stored_hash, str) or not _HASH_RE.fullmatch(stored_hash):
            return False
        return hmac.compare_digest(submitted_digest, stored_hash)

    def new_preauth(self) -> PreAuthCredentials:
        binding_cookie = self.new_token()
        return PreAuthCredentials(binding_cookie, self.csrf_for(binding_cookie, purpose="preauth"))


class BoundedPKCECache:
    """Thread-safe TTL cache for verifier secrets; never persists or evicts live entries.

    Cache keys are transaction digests, not bearer tokens. A missing/expired
    value is a hard restart-required condition; there is no DB fallback.
    """

    def __init__(self, *, max_entries: int, clock: Callable[[], float]):
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        self._max_entries = max_entries
        self._clock = clock
        self._values: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def _purge_expired(self, now: float) -> None:
        for key, (_, expires_at) in tuple(self._values.items()):
            if expires_at <= now:
                del self._values[key]

    def put(self, transaction_hash: str, verifier: str, *, expires_at: float) -> None:
        if not isinstance(transaction_hash, str) or not _HASH_RE.fullmatch(transaction_hash):
            raise ValueError("transaction key must be a SHA-256 digest")
        if not isinstance(verifier, str) or not _PKCE_VERIFIER_RE.fullmatch(verifier):
            raise ValueError("PKCE verifier must be 43-128 URL-safe characters")
        if (not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool)
                or not math.isfinite(expires_at)):
            raise ValueError("expires_at must be a finite epoch timestamp")
        now = self._clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            raise ValueError("cache clock must return finite epoch seconds")
        if expires_at <= now:
            raise ValueError("verifier expiry must be in the future")
        with self._lock:
            self._purge_expired(now)
            if transaction_hash in self._values:
                raise ValueError("verifier already exists for transaction")
            if len(self._values) >= self._max_entries:
                raise AuthCapacityError("active authentication capacity is full")
            self._values[transaction_hash] = (verifier, float(expires_at))

    def take(self, transaction_hash: str) -> str:
        """Consume one verifier, or require a fresh authentication attempt."""
        if not isinstance(transaction_hash, str) or not _HASH_RE.fullmatch(transaction_hash):
            raise AuthRestartRequired("authentication must be restarted")
        now = self._clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            raise AuthRestartRequired("authentication must be restarted")
        with self._lock:
            value = self._values.pop(transaction_hash, None)
        if value is None or value[1] <= now:
            raise AuthRestartRequired("authentication must be restarted")
        return value[0]

    def purge(self) -> int:
        now = self._clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            raise ValueError("cache clock must return finite epoch seconds")
        with self._lock:
            before = len(self._values)
            self._purge_expired(now)
            return before - len(self._values)

    def __len__(self) -> int:
        now = self._clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            return 0
        with self._lock:
            self._purge_expired(now)
            return len(self._values)
