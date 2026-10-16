# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repositories over the accounts database.

Neutral records only: no authentication decision, no price decision and no
automatic user/paid/upsert logic lives here (that is 019's job). Unique keys,
one-time claims and session revocation are enforced atomically in SQLite.
"""
from __future__ import annotations

import secrets
import sqlite3

from .models import (
    AuthIntent, AuthTransaction, ConflictError, Entitlement, EntitlementState,
    Identity, PaymentEvent, PaymentEventStatus, Product, Purchase, PurchaseState,
    Session, User, UserStatus,
)

_PAYMENT_STATES = {state.value for state in PaymentEventStatus}


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
