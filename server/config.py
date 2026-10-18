# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-closed configuration for the accounts server.

Secrets arrive through the environment (or a later secret injection that fills
the same mapping); nothing is read from a committed file and nothing has a
default. ``load_config`` raises ``SetupError`` before anything binds a port when
the environment is unsafe.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Mapping
from urllib.parse import urlsplit

import json

from .models import SetupError

KNOWN_ADAPTERS = frozenset({"email", "google", "apple", "discord", "siwe", "mock", "test"})
MOCK_ADAPTERS = frozenset({"mock", "test"})
_MIN_SECRET_BYTES = 32


class AppEnv(str, Enum):
    TEST = "test"
    DEVELOPMENT = "development"
    PRODUCTION = "production"


@dataclass(frozen=True)
class LimitSettings:
    auth_body_bytes: int = 16 * 1024
    webhook_body_bytes: int = 256 * 1024
    email_per_address_per_hour: int = 5
    email_per_ip_per_hour: int = 20
    verify_per_transaction: int = 10
    verify_per_ip_per_hour: int = 60
    auth_start_per_ip_per_hour: int = 30
    max_active_auth_transactions: int = 1024


@dataclass(frozen=True)
class ScoringFlags:
    """Scoring is mock-only and default-off; sub-flags need the main flag (023)."""
    enabled: bool = False
    anonymous: bool = False
    claim: bool = False
    publish: bool = False
    #: Test/dev only: allow the protected free pool to be served from an in-memory {id: bytes} map instead of a private,
    #: hash-checked manifest. Refused in production and without SCORING_ENABLED.
    dev_source: bool = False
    #: Private free pool: content root and manifest, set together (never a path under the public site).
    free_content_root: str | None = None
    free_manifest: str | None = None

    def any(self) -> bool:
        return self.enabled or self.anonymous or self.claim or self.publish


@dataclass(frozen=True)
class Settings:
    app_env: AppEnv
    public_origin: str
    base_path: str
    db_path: str
    content_root: str | None
    trusted_proxies: tuple[str, ...]
    limits: LimitSettings
    enabled_adapters: frozenset[str]
    session_idle_seconds: int
    session_absolute_seconds: int
    recent_auth_seconds: int
    csrf_key: bytes = field(repr=False)
    session_secret: bytes = field(repr=False)
    scoring: ScoringFlags = field(default_factory=lambda: ScoringFlags())
    payments_provider: str = "mock"
    paddle_env: str | None = None
    paddle_endpoint_secret: str | None = field(default=None, repr=False)
    paddle_products: dict = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:  # keep secrets out of logs and tracebacks
        return f"Settings({self.redacted()!r})"

    def redacted(self) -> dict:
        return {
            "app_env": self.app_env.value,
            "public_origin": self.public_origin,
            "base_path": self.base_path,
            "db_path": self.db_path,
            "content_root": self.content_root,
            "trusted_proxies": list(self.trusted_proxies),
            "enabled_adapters": sorted(self.enabled_adapters),
            "session_idle_seconds": self.session_idle_seconds,
            "session_absolute_seconds": self.session_absolute_seconds,
            "recent_auth_seconds": self.recent_auth_seconds,
            "csrf_key": "<redacted>",
            "session_secret": "<redacted>",
            "payments_provider": self.payments_provider,
            "paddle_env": self.paddle_env,
            "paddle_endpoint_secret": "<redacted>" if self.paddle_endpoint_secret else None,
            "paddle_products": sorted(self.paddle_products),
            "scoring": {"enabled": self.scoring.enabled, "anonymous": self.scoring.anonymous,
                        "claim": self.scoring.claim, "publish": self.scoring.publish,
                        "dev_source": self.scoring.dev_source, "free_pool_private": self.scoring.free_manifest is not None},
        }


def _decode_secret(name: str, raw: str) -> bytes:
    text = raw.strip()
    if not text:
        raise SetupError(f"{name}: empty secret")
    if len(text) % 2 == 0:
        try:
            value = bytes.fromhex(text)
            if len(value) >= _MIN_SECRET_BYTES:
                return value
        except ValueError:
            pass
    try:
        padded = text + "=" * (-len(text) % 4)
        value = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise SetupError(f"{name}: not valid hex or base64") from exc
    if len(value) < _MIN_SECRET_BYTES:
        raise SetupError(f"{name}: must be at least {_MIN_SECRET_BYTES} bytes (256 bits)")
    return value


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SetupError(f"{name}: must be an integer") from exc
    if value <= 0:
        raise SetupError(f"{name}: must be positive")
    return value


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


def _flag_env(env: Mapping[str, str], name: str) -> bool:
    value = (env.get(name) or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise SetupError(f"{name}: must be a boolean (true/false)")


def _parse_scoring(env: Mapping[str, str], app_env: AppEnv) -> ScoringFlags:
    flags = ScoringFlags(enabled=_flag_env(env, "SCORING_ENABLED"), anonymous=_flag_env(env, "SCORING_ANONYMOUS_ENABLED"),
                         claim=_flag_env(env, "SCORING_CLAIM_ENABLED"), publish=_flag_env(env, "SCORING_PUBLISH_ENABLED"))
    if flags.any() and app_env is AppEnv.PRODUCTION:
        raise SetupError("SCORING_*: scoring must not be enabled in production before a separately approved release")
    if (flags.anonymous or flags.claim or flags.publish) and not flags.enabled:
        raise SetupError("SCORING_*: sub-flags require SCORING_ENABLED")
    dev_source = _flag_env(env, "SCORING_DEV_SOURCE")
    root, manifest = (env.get(name, "").strip() or None for name in ("SCORING_FREE_CONTENT_ROOT", "SCORING_FREE_MANIFEST"))
    if dev_source and (app_env is AppEnv.PRODUCTION or not flags.enabled):
        raise SetupError("SCORING_DEV_SOURCE: only for test/development with SCORING_ENABLED, never in production")
    if (root is None) != (manifest is None):
        raise SetupError("SCORING_FREE_CONTENT_ROOT and SCORING_FREE_MANIFEST must be set together")
    if root is not None and (not flags.enabled or dev_source):
        raise SetupError("SCORING_FREE_*: needs SCORING_ENABLED and excludes SCORING_DEV_SOURCE (one source for the free pool)")
    return replace(flags, dev_source=dev_source, free_content_root=root, free_manifest=manifest)


def _strict_json_object(text: str, name: str) -> dict:
    """Strict JSON for configuration: duplicate keys and non-finite numbers are refused; errors never echo the input."""
    def no_duplicates(pairs):
        keys = [k for k, _ in pairs]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate key")
        return dict(pairs)

    def reject(_constant):
        raise ValueError("non-finite number")
    try:
        data = json.loads(text, object_pairs_hook=no_duplicates, parse_constant=reject)
    except (ValueError, RecursionError):
        raise SetupError(f"{name}: not valid strict JSON") from None
    if not isinstance(data, dict) or not data:
        raise SetupError(f"{name}: must be a non-empty JSON object")
    return data


def _parse_paddle(env: Mapping[str, str], app_env: AppEnv):
    """Paddle configuration (mock preparation only: sandbox, no real transport, never production).

    The API key is used here ONCE for the environment/prefix check and is never stored; the endpoint secret is kept
    only in a field that is redacted everywhere. Any problem is a SetupError that does not echo a secret.
    """
    from .paddle.policy import validate_config
    from .paddle.types import PaddleConfigError
    from .paddle_service import PaddleProduct
    provider = (env.get("PAYMENTS_PROVIDER") or "mock").strip().lower()
    if provider == "mock":
        for name in ("PADDLE_ENV", "PADDLE_ENDPOINT_SECRET", "PADDLE_API_KEY", "PADDLE_PRODUCTS"):
            if env.get(name):
                raise SetupError(f"{name}: only valid with PAYMENTS_PROVIDER=paddle")
        return "mock", None, None, {}
    if provider != "paddle":
        raise SetupError("PAYMENTS_PROVIDER: must be mock or paddle")
    paddle_env = (env.get("PADDLE_ENV") or "").strip()
    if paddle_env != "sandbox":
        raise SetupError("PADDLE_ENV: only sandbox is supported until a real transport has been approved")
    try:
        validate_config(paddle_env=paddle_env, api_key=env.get("PADDLE_API_KEY"), endpoint_secret=env.get("PADDLE_ENDPOINT_SECRET"),
                        app_env=app_env.value)
    except PaddleConfigError as exc:
        raise SetupError(f"PADDLE_*: {exc}") from None
    raw = _strict_json_object(env.get("PADDLE_PRODUCTS") or "", "PADDLE_PRODUCTS")
    products = {}
    for product_id, spec in raw.items():
        if (not isinstance(product_id, str) or not isinstance(spec, dict)
                or set(spec) - {"price_id", "quantity", "currency_code", "total_minor", "total_field"}
                or not {"price_id", "quantity", "currency_code"} <= set(spec)):
            raise SetupError("PADDLE_PRODUCTS: each product needs price_id, quantity, currency_code (and optional total_minor/total_field)")
        try:
            product = PaddleProduct(**spec)
        except (ValueError, TypeError):
            raise SetupError("PADDLE_PRODUCTS: a product entry is invalid") from None
        products[product_id] = product
    check_paddle_settings("paddle", paddle_env, env["PADDLE_ENDPOINT_SECRET"], products)
    return "paddle", paddle_env, env["PADDLE_ENDPOINT_SECRET"], products


def check_paddle_settings(provider, paddle_env, endpoint_secret, products) -> None:
    """The ONE check of the Paddle settings, run by load_config and again by create_app on whatever Settings it is given.

    After it passes, building a PaddleService per request cannot fail on configuration. Messages never echo a secret.
    """
    from .paddle_service import PaddleProduct
    if provider == "mock":
        if paddle_env or endpoint_secret or products:
            raise SetupError("Paddle settings are present but PAYMENTS_PROVIDER is not paddle")
        return
    if provider != "paddle":
        raise SetupError("PAYMENTS_PROVIDER: must be mock or paddle")
    if paddle_env != "sandbox":
        raise SetupError("paddle: only the sandbox environment is supported")
    if not isinstance(endpoint_secret, str) or len(endpoint_secret) < 16:
        raise SetupError("paddle: the endpoint secret is missing or too short")
    if (not isinstance(products, dict) or not products
            or not all(isinstance(key, str) and key and isinstance(value, PaddleProduct) and value.quantity == 1
                       for key, value in products.items())):
        raise SetupError("paddle: the product catalog is missing, empty or invalid (string ids, PaddleProduct values, quantity 1)")


def _parse_adapters(raw: str) -> frozenset[str]:
    items = {item.strip() for item in raw.split(",") if item.strip()}
    unknown = items - KNOWN_ADAPTERS
    if unknown:
        raise SetupError("AUTH_ADAPTERS: unknown adapter(s): " + ", ".join(sorted(unknown)))
    return frozenset(items)


def _parse_origin(value: str, app_env: AppEnv) -> str:
    text = value.strip()
    if not text:
        raise SetupError("PUBLIC_ORIGIN: required")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise SetupError("PUBLIC_ORIGIN: scheme must be http or https")
    if not parts.hostname:
        raise SetupError("PUBLIC_ORIGIN: missing host")
    if parts.username or parts.password:
        raise SetupError("PUBLIC_ORIGIN: must not contain userinfo")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise SetupError("PUBLIC_ORIGIN: must be a bare origin without path, query or fragment")
    if app_env is AppEnv.PRODUCTION and parts.scheme != "https":
        raise SetupError("PUBLIC_ORIGIN: production requires https")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def _parse_base_path(value: str) -> str:
    text = value.strip()
    if not text.startswith("/"):
        raise SetupError("BASE_PATH: must start with /")
    if ".." in text.split("/") or any(ch.isspace() for ch in text):
        raise SetupError("BASE_PATH: must not contain .. or whitespace")
    if text != "/" and text.endswith("/"):
        text = text.rstrip("/")
    return text


def load_config(env: Mapping[str, str]) -> Settings:
    raw_env = (env.get("APP_ENV") or "development").strip()
    try:
        app_env = AppEnv(raw_env)
    except ValueError as exc:
        raise SetupError(f"APP_ENV: unknown environment {raw_env!r}") from exc

    if "PUBLIC_ORIGIN" not in env:
        raise SetupError("PUBLIC_ORIGIN: required")
    public_origin = _parse_origin(env["PUBLIC_ORIGIN"], app_env)
    base_path = _parse_base_path(env.get("BASE_PATH", "/"))

    db_path = env.get("DB_PATH")
    if not db_path:
        raise SetupError("DB_PATH: required")
    if app_env is AppEnv.PRODUCTION and db_path == ":memory:":
        raise SetupError("DB_PATH: production must not use an in-memory database")

    content_root = env.get("CONTENT_ROOT") or None

    if app_env is AppEnv.PRODUCTION:
        if "TRUSTED_PROXIES" not in env:
            raise SetupError("TRUSTED_PROXIES: production requires an explicit value (may be empty)")
        trusted_proxies = tuple(item.strip() for item in env["TRUSTED_PROXIES"].split(",") if item.strip())
    else:
        trusted_proxies = tuple(item.strip() for item in env.get("TRUSTED_PROXIES", "").split(",") if item.strip())

    enabled_adapters = _parse_adapters(env.get("AUTH_ADAPTERS", ""))
    if app_env is AppEnv.PRODUCTION and enabled_adapters & MOCK_ADAPTERS:
        raise SetupError("AUTH_ADAPTERS: mock/test adapters are not allowed in production")

    if "CSRF_SECRET_KEY" not in env:
        raise SetupError("CSRF_SECRET_KEY: required")
    if "SESSION_SECRET_KEY" not in env:
        raise SetupError("SESSION_SECRET_KEY: required")
    csrf_key = _decode_secret("CSRF_SECRET_KEY", env["CSRF_SECRET_KEY"])
    session_secret = _decode_secret("SESSION_SECRET_KEY", env["SESSION_SECRET_KEY"])

    limits = LimitSettings(
        auth_body_bytes=_int_env(env, "LIMIT_AUTH_BODY_BYTES", 16 * 1024),
        webhook_body_bytes=_int_env(env, "LIMIT_WEBHOOK_BODY_BYTES", 256 * 1024),
        email_per_address_per_hour=_int_env(env, "LIMIT_EMAIL_PER_ADDRESS_PER_HOUR", 5),
        email_per_ip_per_hour=_int_env(env, "LIMIT_EMAIL_PER_IP_PER_HOUR", 20),
        verify_per_transaction=_int_env(env, "LIMIT_VERIFY_PER_TRANSACTION", 10),
        verify_per_ip_per_hour=_int_env(env, "LIMIT_VERIFY_PER_IP_PER_HOUR", 60),
        auth_start_per_ip_per_hour=_int_env(env, "LIMIT_AUTH_START_PER_IP_PER_HOUR", 30),
        max_active_auth_transactions=_int_env(env, "LIMIT_MAX_ACTIVE_AUTH_TRANSACTIONS", 1024),
    )

    return Settings(
        app_env=app_env,
        public_origin=public_origin,
        base_path=base_path,
        db_path=db_path,
        content_root=content_root,
        trusted_proxies=trusted_proxies,
        limits=limits,
        enabled_adapters=enabled_adapters,
        session_idle_seconds=_int_env(env, "SESSION_IDLE_SECONDS", 7 * 24 * 3600),
        session_absolute_seconds=_int_env(env, "SESSION_ABSOLUTE_SECONDS", 30 * 24 * 3600),
        recent_auth_seconds=_int_env(env, "RECENT_AUTH_SECONDS", 10 * 60),
        csrf_key=csrf_key,
        session_secret=session_secret,
        scoring=_parse_scoring(env, app_env),
        **dict(zip(("payments_provider", "paddle_env", "paddle_endpoint_secret", "paddle_products"), _parse_paddle(env, app_env))),
    )
