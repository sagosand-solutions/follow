# SPDX-License-Identifier: AGPL-3.0-or-later
"""Host, key and environment policy for the Paddle adapter (offline, pure).

The API hosts are CONSTANTS, not configuration: live ``api.paddle.com`` and sandbox ``sandbox-api.paddle.com``
(developer.paddle.com/api-reference/about/, table of base URLs, read 2026-10-18, together with "Sandbox keys only
work for requests to the sandbox URL, and live keys only work for requests to the live URL"). There is no
configurable base URL and no allowlist taken from configuration.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from .types import PaddleConfigError, PaddleTransportRejected

API_HOSTS = {"live": "api.paddle.com", "sandbox": "sandbox-api.paddle.com"}
KEY_PREFIXES = {"live": "pdl_live_apikey_", "sandbox": "pdl_sdbx_apikey_"}
MAX_RESPONSE_BYTES = 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 8


def environment_for_key(api_key) -> str:
    if isinstance(api_key, str):
        for environment, prefix in KEY_PREFIXES.items():
            if api_key.startswith(prefix) and len(api_key) > len(prefix) + 8:
                return environment
    raise PaddleConfigError("API key has no recognised environment prefix")


def validate_config(*, paddle_env, api_key, endpoint_secret, app_env: str, transport_is_mock: bool = False) -> str:
    """Return the validated environment or raise PaddleConfigError (never echoes a key or secret)."""
    if paddle_env not in API_HOSTS:
        raise PaddleConfigError("PADDLE_ENV must be sandbox or live")
    if app_env == "production":
        raise PaddleConfigError("Paddle is not approved for production yet")
    if environment_for_key(api_key) != paddle_env:
        raise PaddleConfigError("API key does not match PADDLE_ENV")
    if not isinstance(endpoint_secret, str) or len(endpoint_secret) < 16 or endpoint_secret == api_key:
        raise PaddleConfigError("endpoint secret is missing, too short or equal to the API key")
    if paddle_env == "live" and transport_is_mock:
        raise PaddleConfigError("a mock or test transport must never serve live")
    return paddle_env


def api_url(environment: str, path: str) -> str:
    """The ONLY way to build a provider URL: constant host, absolute path, no query smuggling."""
    if environment not in API_HOSTS:
        raise PaddleConfigError("unknown environment")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "\\" in path or "@" in path or "#" in path or any(ord(c) < 33 for c in path):
        raise PaddleTransportRejected("path rejected")
    return f"https://{API_HOSTS[environment]}{path}"


def check_response_url(environment: str, url) -> None:
    """A transport must call back only the constant host over TLS. Any parser failure is a typed rejection."""
    if environment not in API_HOSTS:
        raise PaddleConfigError("unknown environment")
    if (not isinstance(url, str) or not url.isascii() or len(url) > 2048 or "\\" in url
            or any(ord(c) <= 32 or ord(c) == 127 for c in url)):
        raise PaddleTransportRejected("host not allowed")
    try:
        parts = urlsplit(url)
        host, port, user, password = parts.hostname, parts.port, parts.username, parts.password
    except ValueError:
        raise PaddleTransportRejected("host not allowed") from None
    if parts.scheme != "https" or host != API_HOSTS[environment] or user or password or port not in (None, 443):
        raise PaddleTransportRejected("host not allowed")
