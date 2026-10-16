# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repositories over the accounts database.

Neutral records only: no authentication decision, no price decision and no
automatic user/paid/upsert logic lives here (that is 019's job). Unique keys,
one-time claims and session revocation are enforced atomically in SQLite.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import sqlite3

from .models import (
    CORE_ACKNOWLEDGEMENTS, AuthIntent, AuthTransaction, ConflictError, Entitlement, EntitlementState,
    Identity, NotFoundError, PaymentEvent, PaymentEventStatus, PolicyBundle, Product, Purchase, PurchaseConsent,
    PurchaseState, Session, User, UserStatus, canonical_acknowledgements, compute_bundle_digest,
)

_PAYMENT_STATES = {state.value for state in PaymentEventStatus}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def new_id() -> str:
    return secrets.token_urlsafe(16)


def _user(row: sqlite3.Row) -> User:
    return User(row["id"], row["created_at"], UserStatus(row["status"]), row["deletion_at"])


def _identity(row: sqlite3.Row) -> Identity:
    return Identity(row["id"], row["user_id"], row["provider"], row["issuer"], row["subject"], row["created_at"])


def _session(row: sqlite3.Row) -> Session:
    return Session(row["id_hash"], row["user_id"], row["csrf_hash"], row["created_at"], row["last_seen"],
                   row["expires_at"], row["auth_time"], row["absolute_expires_at"])


def _auth_transaction(row: sqlite3.Row) -> AuthTransaction:
    return AuthTransaction(row["token_hash"], row["preauth_binding_hash"], AuthIntent(row["intent"]), row["provider"],
                           row["user_id"], row["nonce_hash"], row["state_data"], row["expires_at"], row["consumed_at"],
                           row["attempts"])


def _product(row: sqlite3.Row) -> Product:
    return Product(row["id"], row["title"], row["currency"], row["amount_minor"], row["access_scope"], bool(row["active"]))


def _purchase(row: sqlite3.Row) -> Purchase:
    return Purchase(row["id"], row["user_id"], row["product_id"], row["provider"], row["provider_purchase_id"],
                    PurchaseState(row["state"]), row["revision"])


def _entitlement(row: sqlite3.Row) -> Entitlement:
    return Entitlement(row["id"], row["user_id"], row["product_id"], row["source_purchase_id"],
                       EntitlementState(row["state"]), row["granted_at"], row["revoked_at"])


def _payment_event(row: sqlite3.Row) -> PaymentEvent:
    return PaymentEvent(row["provider"], row["event_id"], row["payload_digest"], row["purchase_id"],
                        row["normalized_state"], row["revision"], PaymentEventStatus(row["status"]), row["received_at"])


def _policy_bundle(row: sqlite3.Row) -> PolicyBundle:
    return PolicyBundle(row["id"], row["product_id"], row["access_scope"], row["locale"], row["version"],
                        row["terms_sha256"], row["refunds_sha256"], row["privacy_sha256"],
                        tuple(json.loads(row["required_acknowledgements"])), row["bundle_digest"], row["effective_at"])


def _consent(row: sqlite3.Row) -> PurchaseConsent:
    return PurchaseConsent(row["id"], row["purchase_id"], row["user_id"], row["policy_bundle_id"],
                           row["bundle_version"], row["bundle_digest"], tuple(json.loads(row["acknowledgements"])),
                           row["accepted_at"])


class _Repo:
    def __init__(self, database):
        self.db = database

    def _now(self, when=None) -> float:
        return self.db.clock() if when is None else when


class UserRepository(_Repo):
    def create(self, *, user_id: str | None = None, status: UserStatus = UserStatus.ACTIVE) -> User:
        uid = user_id or new_id()
        now = self._now()
        with self.db.transaction(immediate=True) as conn:
            conn.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES (?, ?, ?, NULL)",
                         (uid, now, status.value))
        return self.get(uid)

    def get(self, user_id: str) -> User | None:
        row = self.db.connect().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user(row) if row else None

    def mark_deleted(self, user_id: str, *, when: float | None = None) -> bool:
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute("UPDATE users SET status = 'deleted', deletion_at = ? WHERE id = ? AND status = 'active'",
                               (self._now(when), user_id))
        return cur.rowcount == 1


class IdentityRepository(_Repo):
    def add(self, user_id: str, provider: str, issuer: str, subject: str, *, identity_id: str | None = None) -> Identity:
        ident = identity_id or new_id()
        try:
            with self.db.transaction(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO identities (id, user_id, provider, issuer, subject, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (ident, user_id, provider, issuer, subject, self._now()))
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise ConflictError("identity already linked") from exc
            raise
        return self.get(provider, issuer, subject)

    def get(self, provider: str, issuer: str, subject: str) -> Identity | None:
        row = self.db.connect().execute(
            "SELECT * FROM identities WHERE provider = ? AND issuer = ? AND subject = ?",
            (provider, issuer, subject)).fetchone()
        return _identity(row) if row else None

    def list_for_user(self, user_id: str) -> list[Identity]:
        rows = self.db.connect().execute("SELECT * FROM identities WHERE user_id = ? ORDER BY created_at, id",
                                         (user_id,)).fetchall()
        return [_identity(row) for row in rows]


class SessionRepository(_Repo):
    def create(self, user_id: str, *, id_hash: str, csrf_hash: str, ttl_seconds: int, absolute_ttl_seconds: int,
               auth_time: float | None = None) -> Session:
        now = self._now()
        auth = self._now(auth_time)
        with self.db.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO sessions (id_hash, user_id, csrf_hash, created_at, last_seen, expires_at, auth_time, absolute_expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (id_hash, user_id, csrf_hash, now, now, now + ttl_seconds, auth, now + absolute_ttl_seconds))
        return self.get(id_hash)

    def get(self, id_hash: str) -> Session | None:
        row = self.db.connect().execute("SELECT * FROM sessions WHERE id_hash = ?", (id_hash,)).fetchone()
        return _session(row) if row else None

    def touch(self, id_hash: str, *, ttl_seconds: int, when: float | None = None) -> Session | None:
        now = self._now(when)
        with self.db.transaction(immediate=True) as conn:
            row = conn.execute("SELECT expires_at, absolute_expires_at FROM sessions WHERE id_hash = ?",
                               (id_hash,)).fetchone()
            # No revival: an idle-expired or absolutely-expired session is not touched.
            if row is None or now >= row["expires_at"] or now >= row["absolute_expires_at"]:
                return None
            # The idle window may never outlive the absolute deadline.
            expires_at = min(now + ttl_seconds, row["absolute_expires_at"])
            conn.execute("UPDATE sessions SET last_seen = ?, expires_at = ? WHERE id_hash = ?", (now, expires_at, id_hash))
            row = conn.execute("SELECT * FROM sessions WHERE id_hash = ?", (id_hash,)).fetchone()
        return _session(row)

    def revoke(self, id_hash: str) -> bool:
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
        return cur.rowcount == 1

    def revoke_all_for_user(self, user_id: str) -> int:
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return cur.rowcount


class AuthTransactionRepository(_Repo):
    def create(self, token_hash: str, *, preauth_binding_hash: str, intent: AuthIntent, provider: str,
               expires_at: float, user_id: str | None = None, nonce_hash: str | None = None,
               state_data: str | None = None) -> AuthTransaction:
        with self.db.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO auth_transactions (token_hash, preauth_binding_hash, intent, provider, user_id, nonce_hash,"
                " state_data, expires_at, consumed_at, attempts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)",
                (token_hash, preauth_binding_hash, intent.value, provider, user_id, nonce_hash, state_data, expires_at))
        return self.get(token_hash)

    def get(self, token_hash: str) -> AuthTransaction | None:
        row = self.db.connect().execute("SELECT * FROM auth_transactions WHERE token_hash = ?", (token_hash,)).fetchone()
        return _auth_transaction(row) if row else None

    def consume_once(self, token_hash: str, *, now: float | None = None) -> bool:
        when = self._now(now)
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE auth_transactions SET consumed_at = ? WHERE token_hash = ? AND consumed_at IS NULL AND expires_at > ?",
                (when, token_hash, when))
        return cur.rowcount == 1

    def increment_attempts(self, token_hash: str) -> int:
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute("UPDATE auth_transactions SET attempts = attempts + 1 WHERE token_hash = ? RETURNING attempts",
                               (token_hash,))
            row = cur.fetchone()
        return row[0] if row else 0


class ProductRepository(_Repo):
    def upsert(self, product: Product) -> None:
        with self.db.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO products (id, title, currency, amount_minor, access_scope, active) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET title=excluded.title, currency=excluded.currency,"
                " amount_minor=excluded.amount_minor, access_scope=excluded.access_scope, active=excluded.active",
                (product.id, product.title, product.currency, product.amount_minor, product.access_scope, 1 if product.active else 0))

    def get(self, product_id: str) -> Product | None:
        row = self.db.connect().execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return _product(row) if row else None

    def list_active(self) -> list[Product]:
        rows = self.db.connect().execute("SELECT * FROM products WHERE active = 1 ORDER BY id").fetchall()
        return [_product(row) for row in rows]


class PurchaseRepository(_Repo):
    def create(self, user_id: str, product_id: str, provider: str, provider_purchase_id: str, *,
               state: PurchaseState = PurchaseState.PENDING, revision: int = 0, purchase_id: str | None = None) -> Purchase:
        pid = purchase_id or new_id()
        try:
            with self.db.transaction(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO purchases (id, user_id, product_id, provider, provider_purchase_id, state, revision)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pid, user_id, product_id, provider, provider_purchase_id, state.value, revision))
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise ConflictError("purchase already bound") from exc
            raise
        return self.get(pid)

    def get(self, purchase_id: str) -> Purchase | None:
        row = self.db.connect().execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
        return _purchase(row) if row else None

    def get_by_provider_id(self, provider: str, provider_purchase_id: str) -> Purchase | None:
        row = self.db.connect().execute(
            "SELECT * FROM purchases WHERE provider = ? AND provider_purchase_id = ?",
            (provider, provider_purchase_id)).fetchone()
        return _purchase(row) if row else None

    def compare_and_swap(self, purchase_id: str, expected_revision: int, *, state: PurchaseState, revision: int) -> bool:
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute("UPDATE purchases SET state = ?, revision = ? WHERE id = ? AND revision = ?",
                               (state.value, revision, purchase_id, expected_revision))
        return cur.rowcount == 1


class EntitlementRepository(_Repo):
    def grant(self, user_id: str, product_id: str, source_purchase_id: str, *, granted_at: float | None = None,
              entitlement_id: str | None = None) -> Entitlement:
        eid = entitlement_id or new_id()
        when = self._now(granted_at)
        with self.db.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO entitlements (id, user_id, product_id, source_purchase_id, state, granted_at, revoked_at)"
                " VALUES (?, ?, ?, ?, 'active', ?, NULL) ON CONFLICT(source_purchase_id, product_id) DO NOTHING",
                (eid, user_id, product_id, source_purchase_id, when))
            row = conn.execute("SELECT * FROM entitlements WHERE source_purchase_id = ? AND product_id = ?",
                               (source_purchase_id, product_id)).fetchone()
        return _entitlement(row)

    def revoke(self, source_purchase_id: str, product_id: str, *, when: float | None = None) -> bool:
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE entitlements SET state = 'revoked', revoked_at = ?"
                " WHERE source_purchase_id = ? AND product_id = ? AND state = 'active'",
                (self._now(when), source_purchase_id, product_id))
        return cur.rowcount == 1

    def get(self, source_purchase_id: str, product_id: str) -> Entitlement | None:
        row = self.db.connect().execute("SELECT * FROM entitlements WHERE source_purchase_id = ? AND product_id = ?",
                                        (source_purchase_id, product_id)).fetchone()
        return _entitlement(row) if row else None

    def list_active_for_user(self, user_id: str) -> list[Entitlement]:
        rows = self.db.connect().execute(
            "SELECT * FROM entitlements WHERE user_id = ? AND state = 'active' ORDER BY granted_at, id",
            (user_id,)).fetchall()
        return [_entitlement(row) for row in rows]


class PaymentEventRepository(_Repo):
    def record(self, provider: str, event_id: str, payload_digest: str, *, purchase_id: str | None,
               normalized_state: str, revision: int, status: PaymentEventStatus = PaymentEventStatus.RECEIVED,
               event_row_id: str | None = None) -> str:
        """Return 'inserted' or 'duplicate'; raise ConflictError on a payload change."""
        row_id = event_row_id or new_id()
        with self.db.transaction(immediate=True) as conn:
            existing = conn.execute("SELECT payload_digest FROM payment_events WHERE provider = ? AND event_id = ?",
                                    (provider, event_id)).fetchone()
            if existing is not None:
                if existing["payload_digest"] == payload_digest:
                    return "duplicate"
                raise ConflictError("payment event id reused with a different payload")
            conn.execute(
                "INSERT INTO payment_events (id, provider, event_id, payload_digest, purchase_id, normalized_state,"
                " revision, status, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row_id, provider, event_id, payload_digest, purchase_id, normalized_state, revision, status.value,
                 self._now()))
        return "inserted"

    def get(self, provider: str, event_id: str) -> PaymentEvent | None:
        row = self.db.connect().execute("SELECT * FROM payment_events WHERE provider = ? AND event_id = ?",
                                        (provider, event_id)).fetchone()
        return _payment_event(row) if row else None


class PolicyBundleRepository(_Repo):
    def _normalize(self, bundle: PolicyBundle) -> tuple[PolicyBundle, str]:
        acknowledgements = canonical_acknowledgements(bundle.required_acknowledgements)
        if not CORE_ACKNOWLEDGEMENTS.issubset(acknowledgements):
            raise ConflictError("bundle is missing a required acknowledgement")
        # The version is part of the digest identity: reject blank or non-string
        # versions instead of trimming or otherwise normalising them.
        if not isinstance(bundle.version, str) or not bundle.version.strip():
            raise ConflictError("version must be a non-empty string")
        for name, value in (("terms_sha256", bundle.terms_sha256), ("refunds_sha256", bundle.refunds_sha256),
                            ("privacy_sha256", bundle.privacy_sha256)):
            if not isinstance(value, str) or not _HEX64.fullmatch(value):
                raise ConflictError(f"{name} must be 64 lowercase hex characters")
        if not isinstance(bundle.effective_at, (int, float)) or isinstance(bundle.effective_at, bool) \
                or not math.isfinite(bundle.effective_at):
            raise ConflictError("effective_at must be a finite epoch second")
        expected = compute_bundle_digest(product_id=bundle.product_id, access_scope=bundle.access_scope,
                                         locale=bundle.locale, version=bundle.version, terms_sha256=bundle.terms_sha256,
                                         refunds_sha256=bundle.refunds_sha256, privacy_sha256=bundle.privacy_sha256,
                                         required_acknowledgements=acknowledgements, effective_at=bundle.effective_at)
        if expected != bundle.bundle_digest:
            raise ConflictError("bundle digest does not match its content")
        normalized = PolicyBundle(bundle.id, bundle.product_id, bundle.access_scope, bundle.locale, bundle.version,
                                  bundle.terms_sha256, bundle.refunds_sha256, bundle.privacy_sha256,
                                  acknowledgements, bundle.bundle_digest, float(bundle.effective_at))
        return normalized, json.dumps(list(acknowledgements))

    def insert(self, bundle: PolicyBundle) -> None:
        """Insert an immutable policy bundle; identical repeat is a no-op."""
        normalized, acknowledgements_json = self._normalize(bundle)
        with self.db.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM policy_bundles WHERE id = ?", (normalized.id,)).fetchone()
            if row is not None:
                existing = _policy_bundle(row)
                if existing == normalized:
                    return
                raise ConflictError("bundle id reused with different content")
            try:
                conn.execute(
                    "INSERT INTO policy_bundles (id, product_id, access_scope, locale, version, terms_sha256,"
                    " refunds_sha256, privacy_sha256, required_acknowledgements, bundle_digest, effective_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (normalized.id, normalized.product_id, normalized.access_scope, normalized.locale, normalized.version,
                     normalized.terms_sha256, normalized.refunds_sha256, normalized.privacy_sha256, acknowledgements_json,
                     normalized.bundle_digest, normalized.effective_at))
            except sqlite3.IntegrityError as exc:
                raise ConflictError("policy version already exists for this product and locale") from exc

    def get(self, bundle_id: str) -> PolicyBundle | None:
        row = self.db.connect().execute("SELECT * FROM policy_bundles WHERE id = ?", (bundle_id,)).fetchone()
        return _policy_bundle(row) if row else None

    def list_for_product(self, product_id: str) -> list[PolicyBundle]:
        rows = self.db.connect().execute("SELECT * FROM policy_bundles WHERE product_id = ? ORDER BY effective_at, id",
                                         (product_id,)).fetchall()
        return [_policy_bundle(row) for row in rows]


class ConsentRepository(_Repo):
    def record(self, purchase_id: str, policy_bundle_id: str, acknowledgements, *, consent_id: str | None = None) -> PurchaseConsent:
        """Record an immutable consent snapshot, deriving user and time server-side."""
        ack = canonical_acknowledgements(acknowledgements)
        consent = consent_id or new_id()
        with self.db.transaction(immediate=True) as conn:
            purchase = conn.execute("SELECT user_id, product_id FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
            if purchase is None:
                raise NotFoundError("purchase not found")
            bundle_row = conn.execute("SELECT * FROM policy_bundles WHERE id = ?", (policy_bundle_id,)).fetchone()
            if bundle_row is None:
                raise NotFoundError("policy bundle not found")
            if set(ack) != set(json.loads(bundle_row["required_acknowledgements"])):
                raise ConflictError("acknowledgements do not match the bundle requirements")
            if purchase["product_id"] != bundle_row["product_id"]:
                raise ConflictError("consent bundle belongs to another product")
            try:
                conn.execute(
                    "INSERT INTO purchase_consents (id, purchase_id, user_id, policy_bundle_id, bundle_version,"
                    " bundle_digest, acknowledgements, accepted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (consent, purchase_id, purchase["user_id"], policy_bundle_id, bundle_row["version"],
                     bundle_row["bundle_digest"], json.dumps(list(ack)), self._now()))
            except sqlite3.IntegrityError as exc:
                if "UNIQUE" in str(exc).upper():
                    raise ConflictError("purchase already has a consent") from exc
                raise
            row = conn.execute("SELECT * FROM purchase_consents WHERE id = ?", (consent,)).fetchone()
        return _consent(row)

    def get_for_purchase(self, purchase_id: str) -> PurchaseConsent | None:
        row = self.db.connect().execute("SELECT * FROM purchase_consents WHERE purchase_id = ?", (purchase_id,)).fetchone()
        return _consent(row) if row else None

    def list_for_user(self, user_id: str) -> list[PurchaseConsent]:
        rows = self.db.connect().execute("SELECT * FROM purchase_consents WHERE user_id = ? ORDER BY accepted_at, id",
                                         (user_id,)).fetchall()
        return [_consent(row) for row in rows]


class Repositories:
    """Facade wiring every repository to one database and clock."""

    def __init__(self, database):
        self.database = database
        self.users = UserRepository(database)
        self.identities = IdentityRepository(database)
        self.sessions = SessionRepository(database)
        self.auth_transactions = AuthTransactionRepository(database)
        self.products = ProductRepository(database)
        self.purchases = PurchaseRepository(database)
        self.entitlements = EntitlementRepository(database)
        self.payment_events = PaymentEventRepository(database)
        self.policies = PolicyBundleRepository(database)
        self.consents = ConsentRepository(database)
