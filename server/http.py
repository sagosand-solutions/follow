# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP boundary primitives: body limits, stable JSON errors, request ids,
Origin checks, cookie flags and local-path allowlisting.

No provider or payment logic lives here. The ASGI app wires these helpers to the
frozen 018/019 services, which own authentication, sessions and entitlement.
"""
from __future__ import annotations

import json
import secrets
import unicodedata
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

# Stable machine error codes (generic messages; never tokens, SQL or paths).
ERROR_STATUS = {
    "invalid": 400,
    "no-session": 401,
    "forbidden": 403,
    "not-found": 404,
    "identity-conflict": 409,
    "stale-policy": 409,
    "body-limit": 413,
    "throttled": 429,
    "server-error": 500,
    "unavailable": 503,
}

NO_STORE = "no-store"

COOKIE_SESSION = "session"
COOKIE_PREAUTH = "preauth"


class BodyLimitExceeded(Exception):
    """The request body exceeded the configured limit."""


def new_request_id() -> str:
    return secrets.token_urlsafe(12)


def error_response(code: str, request_id: str, *, message: str = "Request could not be completed.") -> JSONResponse:
    status = ERROR_STATUS.get(code, 400)
    return JSONResponse({"error": {"code": code, "message": message}, "request_id": request_id},
                        status_code=status, headers={"Cache-Control": NO_STORE, "X-Request-Id": request_id})


def json_response(payload, *, status_code: int = 200, request_id: str | None = None) -> JSONResponse:
    headers = {"Cache-Control": NO_STORE}
    if request_id:
        headers["X-Request-Id"] = request_id
    return JSONResponse(payload, status_code=status_code, headers=headers)


def text_response(body: str, *, media_type: str = "text/plain; charset=utf-8", status_code: int = 200) -> "Response":
    from starlette.responses import Response
    return Response(body, media_type=media_type, status_code=status_code, headers={"Cache-Control": NO_STORE})


async def read_body(receive, limit: int) -> bytes:
    """Read the raw body, enforcing the limit before any parsing."""
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise BodyLimitExceeded("client disconnected")
        if message["type"] != "http.request":
            continue
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > limit:
            raise BodyLimitExceeded("body too large")
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def parse_json(raw: bytes):
    if not raw:
        raise ValueError("empty body")
    return json.loads(raw.decode("utf-8"))


def origin_allowed(origin: str | None, expected: str) -> bool:
    """Exact configured Origin required for mutating browser requests."""
    if not origin:
        return False
    parts = urlsplit(origin)
    return f"{parts.scheme}://{parts.netloc}" == expected


def has_disallowed_text_characters(value: str) -> bool:
    """Reject controls, Unicode formatting controls and separator characters in inputs."""
    return any((category := unicodedata.category(ch)) in ("Cc", "Cf") or category.startswith("Z")
               for ch in value)


def local_return_to(value) -> str | None:
    """Allow only a local absolute path; never a scheme-relative or external URL."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    if "://" in value or has_disallowed_text_characters(value):
        return None
    return value


def cookie_name(base: str, settings) -> str:
    return f"__Host-{base}" if settings.app_env.value == "production" else base


def set_session_cookie(response, raw_value: str, settings, *, max_age: int) -> None:
    response.set_cookie(
        cookie_name(COOKIE_SESSION, settings), raw_value, max_age=max_age, httponly=True,
        secure=settings.app_env.value == "production", samesite="lax", path="/",
    )


def clear_session_cookie(response, settings) -> None:
    response.delete_cookie(cookie_name(COOKIE_SESSION, settings), path="/",
                           secure=settings.app_env.value == "production", httponly=True, samesite="lax")


def clear_preauth_cookie(response, settings) -> None:
    response.delete_cookie(cookie_name(COOKIE_PREAUTH, settings), path="/",
                           secure=settings.app_env.value == "production", httponly=True, samesite="lax")


def set_preauth_cookie(response, raw_value: str, settings, *, max_age: int) -> None:
    # Short-lived preauth binding; never the app session cookie.
    response.set_cookie(
        cookie_name(COOKIE_PREAUTH, settings), raw_value, max_age=max_age, httponly=True,
        secure=settings.app_env.value == "production", samesite="lax", path="/",
    )
