# SPDX-License-Identifier: MIT
"""Shared test support for the 020 HTTP tests: a dependency-free ASGI client and
fixtures built on temporary databases and synthetic private content."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from server.app import Services, create_app
from server.config import load_config
from server.content_catalog import ContentCatalog, PublishedChapter
from server.db import Database
from server.models import PolicyActivation, PolicyBundle, Product, compute_bundle_digest, canonical_acknowledgements
from server.repositories import Repositories
from server.sessions import SessionService

CORE_ACK = tuple(sorted(("terms", "immediate_delivery_request", "withdrawal_acknowledgement")))
ORIGIN = "http://testserver"


def call(app, method, path, *, headers=None, body=b"", query=b""):
    async def run():
        scope = {
            "type": "http", "method": method, "path": path, "raw_path": path.encode(),
            "query_string": query, "scheme": "http", "server": ("testserver", 80), "client": ("t", 1),
            "root_path": "", "http_version": "1.1",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        }
        inbox = [{"type": "http.request", "body": body, "more_body": False}]
        sent: list[dict] = []

        async def receive():
            return inbox.pop(0) if inbox else {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                sent.append({"status": message["status"], "headers": message["headers"]})
            elif message["type"] == "http.response.body":
                sent.append({"body": message.get("body", b"")})

        await app(scope, receive, send)
        status = sent[0]["status"]
        response_headers = {}
        for k, v in sent[0]["headers"]:
            key = k.decode()
            response_headers[key] = f"{response_headers[key]}\n{v.decode()}" if key == "set-cookie" and key in response_headers else v.decode()
        payload = b"".join(part.get("body", b"") for part in sent[1:])
        return status, response_headers, payload

    return asyncio.run(run())


def json_body(response):
    return json.loads(response[2].decode("utf-8"))


@dataclass
class Fixture:
    app: object
    settings: object
    path: str
    clock: list
    repos: object
    cookie: str
    csrf: str
    session_cookie_name: str
    content_root: str

    def headers(self, *, cookie=True, csrf=False, origin=True, extra=None):
        h = {}
        if cookie:
            h["cookie"] = f"{self.session_cookie_name}={self.cookie}"
        if csrf:
            h["x-csrf-token"] = self.csrf
        if origin:
            h["origin"] = ORIGIN
        if extra:
            h.update(extra)
        return h


def issue_for(fixture, user_id, *, auth_time=None):
    """Issue a session for an arbitrary seeded user (returns cookie value)."""
    db = Database(fixture.path, clock=lambda: fixture.clock[0])
    try:
        credentials = SessionService(Repositories(db), fixture.settings,
                                     clock=lambda: fixture.clock[0]).issue(user_id, auth_time=auth_time)
    finally:
        db.close()
    return credentials.cookie_value


class FakePayments:
    def __init__(self, *, redirect="https://pay.example/checkout/1", webhooks=None):
        self.redirect = redirect
        self.webhooks = webhooks or {}
        self.calls = []

    def create_checkout(self, purchase, product, idempotency_key):
        self.calls.append((purchase.id, product.id, idempotency_key))
        return {"redirect_url": self.redirect}

    def verify_webhook(self, raw, headers, now):
        digest = hashlib.sha256(raw).hexdigest()
        return self.webhooks.get(headers.get("x-signature"))


def build_fixture(*, payments=None, providers=None, checkout=True, auth_time=None, migrate=True):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    db_path = str(root / "accounts.db")
    clock = [1_000_000.0]
    settings = load_config({
        "APP_ENV": "test", "PUBLIC_ORIGIN": ORIGIN, "DB_PATH": db_path,
        "CSRF_SECRET_KEY": "a1" * 32, "SESSION_SECRET_KEY": "b2" * 32,
    })

    def open_database():
        return Database(db_path, clock=lambda: clock[0])

    db = Database(db_path, clock=lambda: clock[0])
    db.migrate()
    repos = Repositories(db)
    repos.products.upsert(Product("python-lifetime", "Python lifetime", "USD", 500, "python", True))
    repos.users.create(user_id="u1")
    acquisition = repos.purchases.create("u1", "python-lifetime", "mock", "seed-prov")
    repos.entitlements.grant("u1", "python-lifetime", acquisition.id)

    # Synthetic private content.
    content_root = root / "private"
    content_root.mkdir()
    payload = b'{"ok": true}'
    (content_root / "q-1.txt").write_bytes(payload)
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"entries": [
        {"kind": "question", "id": "q-1", "product_id": "python-lifetime", "path": "q-1.txt",
         "sha256": hashlib.sha256(payload).hexdigest()},
    ]}), encoding="utf-8")
    catalog = ContentCatalog.load(content_root, manifest)

    # Policy bundle + explicit approved activation matching the checkout policy.
    digest = compute_bundle_digest(product_id="python-lifetime", access_scope="python", locale="en", version="2026-10-16",
                                   terms_sha256="a" * 64, refunds_sha256="b" * 64, privacy_sha256="c" * 64,
                                   required_acknowledgements=CORE_ACK, effective_at=999_000.0)
    repos.policies.insert(PolicyBundle("pb-1", "python-lifetime", "python", "en", "2026-10-16",
                                       "a" * 64, "b" * 64, "c" * 64, CORE_ACK, digest, 999_000.0))
    db.close()

    sessions_db = Database(db_path, clock=lambda: clock[0])
    sessions = SessionService(Repositories(sessions_db), settings, clock=lambda: clock[0])
    credentials = sessions.issue("u1", auth_time=auth_time)
    sessions_db.close()

    services = Services(
        open_database=open_database,
        catalog=catalog,
        payments=payments,
        payment_provider="mock",
        payment_redirect_hosts=frozenset({"pay.example"}),
        providers=providers or {},
        policy_activations=(PolicyActivation("python-lifetime", "python", "en", "pb-1", approved=True),),
        published_chapters=(PublishedChapter("python-lifetime", "C01"), PublishedChapter("python-lifetime", "C01")),
        checkout_policy=({"version": "2026-10-16", "documents": ["terms", "refunds", "privacy"],
                          "required_acknowledgements": list(CORE_ACK)} if checkout else None),
        clock=lambda: clock[0],
    )
    app = create_app(settings, services)
    return Fixture(app, settings, db_path, clock, repos, credentials.cookie_value, credentials.csrf_token,
                   "session", str(content_root)), tmp
