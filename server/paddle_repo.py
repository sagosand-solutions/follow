# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bookkeeping tables for the Paddle adapter: checkout operations, verified-event evidence and reconcile leases.

Pure SQL over the request-owned Database. Nothing here calls the provider, decides a purchase state or grants
access; that stays in ``paddle.mapping`` and ``payment_apply``. Limits and timings are module constants (an open
product/ops decision), not hidden magic: they can be adjusted in one place.
"""
from __future__ import annotations

import re
import secrets
import sqlite3

ORPHAN_GLOBAL_MAX = 1000          # pending events for transactions that are not bound to any purchase
ORPHAN_PER_TXN_MAX = 20
ORPHAN_TTL_SECONDS = 7 * 24 * 3600
LEASE_SECONDS = 60
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600
MAX_ATTEMPTS = 8
BOUND_EVENTS_PER_TXN_MAX = 500     # evidence for a bound transaction is never evicted; beyond this the caller answers 503
AWAIT_EVENT_POLL_SECONDS = 15 * 60
AWAIT_EVENT_WINDOW_SECONDS = 3 * 24 * 3600   # the provider's own live retry window
ENVIRONMENTS = ("sandbox", "live")
ERROR_CLASSES = frozenset({"transport", "timeout", "malformed", "rejected", "unexpected"})   # closed set: no free text is ever stored


def _check_error_class(value) -> str:
    if value not in ERROR_CLASSES:
        raise ValueError("unknown error class")
    return value


class PaddleQueueFull(Exception):
    """The bounded orphan queue refuses a new event (the caller answers 503 so the provider retries)."""


_TXN_RE = re.compile(r"txn_[a-z0-9]{10,40}\Z")


def _check_txn(value) -> str:
    if not isinstance(value, str) or not _TXN_RE.fullmatch(value):
        raise ValueError("invalid transaction id")
    return value


def _check_env(environment) -> str:
    if environment not in ENVIRONMENTS:
        raise ValueError("unknown Paddle environment")
    return environment


class PaddleRepository:
    def __init__(self, database):
        self.db = database

    def _c(self) -> sqlite3.Connection:
        return self.db.connect()

    # ---- operations ----------------------------------------------------------------------------
    def plan(self, purchase_id: str, environment: str, now: float) -> str:
        """Record the intent to create a remote transaction. Call inside the transaction that creates the purchase."""
        row = self._c().execute("SELECT provider FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
        if row is None or row["provider"] != f"paddle-{_check_env(environment)}":
            raise ValueError("operation environment does not match the purchase provider")
        op_id = "op" + secrets.token_urlsafe(12)
        self._c().execute("INSERT INTO paddle_operations (id, purchase_id, environment, state, created_at) "
                          "VALUES (?, ?, ?, 'planned', ?)", (op_id, purchase_id, _check_env(environment), now))
        return op_id

    def operation(self, op_id: str):
        return self._c().execute("SELECT * FROM paddle_operations WHERE id = ?", (op_id,)).fetchone()

    def operation_for_purchase(self, purchase_id: str):
        return self._c().execute("SELECT * FROM paddle_operations WHERE purchase_id = ?", (purchase_id,)).fetchone()

    def bound_operation(self, environment: str, txn_id: str):
        return self._c().execute("SELECT * FROM paddle_operations WHERE environment = ? AND provider_txn_id = ? "
                                 "AND state = 'bound'", (_check_env(environment), txn_id)).fetchone()

    def _cas(self, op_id: str, allowed: tuple, new_state: str, extra_sql: str = "", extra_args: tuple = ()) -> bool:
        marks = ",".join("?" for _ in allowed)
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute(f"UPDATE paddle_operations SET state = ?{extra_sql} WHERE id = ? AND state IN ({marks})",
                               (new_state, *extra_args, op_id, *allowed))
        return cur.rowcount == 1

    def mark_request_sent(self, op_id: str, now: float) -> bool:
        """planned -> request_sent, committed BEFORE the remote call. False means the call must not be made."""
        return self._cas(op_id, ("planned",), "request_sent", ", request_sent_at = ?", (now,))

    def bind(self, op_id: str, txn_id: str, now: float, *, environment: str) -> str:
        """request_sent -> bound with the provider transaction id. 'bound', 'not_sent', 'conflict' or 'wrong_env'.

        The environment of the call that produced the id must equal the operation's environment. Transaction ids
        are reserved GLOBALLY (one table, one primary key) for both bound and late ids, so an id saved as evidence by
        one operation can never be bound by another. A conflict marks this operation ``unknown`` for manual review.
        """
        _check_txn(txn_id)
        op = self.operation(op_id)
        if op is not None and op["environment"] != _check_env(environment):
            return "wrong_env"
        try:
            with self.db.transaction(immediate=True) as conn:
                cur = conn.execute("UPDATE paddle_operations SET state = 'bound', provider_txn_id = ?, bound_at = ? "
                                   "WHERE id = ? AND state = 'request_sent'", (txn_id, now, op_id))
                if cur.rowcount != 1:
                    return "not_sent"
                conn.execute("INSERT INTO paddle_txn_reservations (txn_id, operation_id, kind) VALUES (?, ?, 'bound')", (txn_id, op_id))
            return "bound"
        except sqlite3.IntegrityError:
            self._cas(op_id, ("request_sent",), "unknown")
            return "conflict"

    def abandon(self, op_id: str) -> bool:
        """Only from ``planned``: a local failure BEFORE request_sent, when no remote call can have happened.

        Once a request may have been sent there is no documented proof that a refusal created nothing, so every
        later failure (any HTTP status, timeout, parse failure, crash) is ``unknown``, never ``abandoned``.
        """
        return self._cas(op_id, ("planned",), "abandoned")

    def record_late_response(self, op_id: str, txn_id: str, *, environment: str) -> str:
        """An authentic create response that arrives after the operation became ``unknown``.

        The id is saved as evidence (``late_txn_id``) and RESERVED globally; the operation stays ``unknown`` for
        explicit manual adoption. Nothing is bound automatically and nothing is created again. 'saved',
        'not_unknown', 'wrong_env' or 'conflict'.
        """
        _check_txn(txn_id)
        op = self.operation(op_id)
        if op is None or op["state"] != "unknown":
            return "not_unknown"
        if op["environment"] != _check_env(environment):
            return "wrong_env"
        try:
            with self.db.transaction(immediate=True) as conn:
                cur = conn.execute("UPDATE paddle_operations SET late_txn_id = ? WHERE id = ? AND state = 'unknown' "
                                   "AND late_txn_id IS NULL", (txn_id, op_id))
                if cur.rowcount != 1:
                    return "not_unknown"
                conn.execute("INSERT INTO paddle_txn_reservations (txn_id, operation_id, kind) VALUES (?, ?, 'late')", (txn_id, op_id))
            return "saved"
        except sqlite3.IntegrityError:
            return "conflict"

    def adopt_late_response(self, op_id: str, now: float) -> bool:
        """Explicit manual decision: bind the saved late transaction id (state unknown -> bound)."""
        try:
            with self.db.transaction(immediate=True) as conn:
                cur = conn.execute("UPDATE paddle_operations SET state = 'bound', provider_txn_id = late_txn_id, bound_at = ? "
                                   "WHERE id = ? AND state = 'unknown' AND late_txn_id IS NOT NULL", (now, op_id))
                if cur.rowcount != 1:
                    return False
                conn.execute("UPDATE paddle_txn_reservations SET kind = 'bound' WHERE operation_id = ? AND kind = 'late'", (op_id,))
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_unknown(self, op_id: str) -> bool:
        return self._cas(op_id, ("request_sent",), "unknown")

    def sweep_stale_requests(self, now: float, older_than: float) -> int:
        """A request_sent older than the lease cannot be told apart from a crash: it becomes unknown (never re-created)."""
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute("UPDATE paddle_operations SET state = 'unknown' WHERE state = 'request_sent' "
                               "AND request_sent_at <= ?", (now - older_than,))
        return cur.rowcount

    # ---- verified-event evidence ----------------------------------------------------------------
    def record_verified(self, event, now: float) -> str:
        """Store ids + digest of a VERIFIED event. 'new', 'duplicate' or 'conflict'; PaddleQueueFull when bounded out.

        A reused event id with another digest is a conflict and changes nothing (no new row, no new transaction).
        Events for a transaction that is not bound to a purchase count against the orphan caps; events for a bound
        transaction are evidence and are never refused.
        """
        env = _check_env(event.environment)
        if not event.transaction_id:
            raise ValueError("event has no transaction")
        with self.db.transaction(immediate=True) as conn:
            row = conn.execute("SELECT payload_digest, transaction_id FROM paddle_events WHERE environment = ? AND event_id = ?",
                               (env, event.event_id)).fetchone()
            if row is not None:
                same = row["payload_digest"] == event.payload_digest and row["transaction_id"] == event.transaction_id
                return "duplicate" if same else "conflict"
            if self.bound_operation(env, event.transaction_id) is None:
                if self._orphan_count() >= ORPHAN_GLOBAL_MAX or self._orphan_count(event.transaction_id, env) >= ORPHAN_PER_TXN_MAX:
                    raise PaddleQueueFull("orphan queue is full")
            elif conn.execute("SELECT COUNT(*) FROM paddle_events WHERE environment = ? AND transaction_id = ?",
                              (env, event.transaction_id)).fetchone()[0] >= BOUND_EVENTS_PER_TXN_MAX:
                raise PaddleQueueFull("too many events for one bound transaction")   # backpressure, never eviction
            conn.execute("INSERT INTO paddle_events (environment, event_id, transaction_id, event_type, payload_digest, "
                         "occurred_at, received_at, state) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
                         (env, event.event_id, event.transaction_id, event.event_type, event.payload_digest,
                          event.occurred_at, now))
        return "new"

    def _orphan_count(self, txn_id: str | None = None, environment: str | None = None) -> int:
        """Pending events whose transaction is not bound to a purchase: GLOBAL across environments unless a txn is given."""
        sql = ("SELECT COUNT(*) FROM paddle_events e WHERE e.state = 'pending' AND NOT EXISTS "
               "(SELECT 1 FROM paddle_operations o WHERE o.environment = e.environment AND o.provider_txn_id = e.transaction_id "
               "AND o.state = 'bound')")
        args: list = []
        if txn_id is not None:
            sql += " AND e.transaction_id = ? AND e.environment = ?"
            args += [txn_id, _check_env(environment)]
        return self._c().execute(sql, args).fetchone()[0]

    def has_completed_evidence(self, environment: str, txn_id: str) -> bool:
        return self._c().execute("SELECT 1 FROM paddle_events WHERE environment = ? AND transaction_id = ? "
                                 "AND event_type = 'transaction.completed' LIMIT 1", (_check_env(environment), txn_id)).fetchone() is not None

    def events_for_transaction(self, environment: str, txn_id: str) -> list:
        return list(self._c().execute("SELECT * FROM paddle_events WHERE environment = ? AND transaction_id = ? "
                                      "ORDER BY received_at, event_id", (_check_env(environment), txn_id)))

    def settle_transaction(self, environment: str, txn_id: str) -> int:
        """Mark evidence settled once its purchase is terminal (retention then follows the product decision)."""
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute("UPDATE paddle_events SET state = 'settled' WHERE environment = ? AND transaction_id = ? "
                               "AND state = 'pending'", (_check_env(environment), txn_id))
        return cur.rowcount

    def purge_expired_orphans(self, now: float, limit: int = 200) -> int:
        """Delete pending evidence for transactions that never got bound, once older than the TTL. Bounded batch.

        Evidence for a BOUND transaction (for example its completed event) is never touched by the TTL.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute(
                "DELETE FROM paddle_events WHERE rowid IN (SELECT e.rowid FROM paddle_events e WHERE e.state = 'pending' "
                "AND e.received_at <= ? AND NOT EXISTS (SELECT 1 FROM paddle_operations o WHERE o.environment = e.environment "
                "AND o.provider_txn_id = e.transaction_id AND o.state = 'bound') LIMIT ?)", (now - ORPHAN_TTL_SECONDS, limit))
        return cur.rowcount

    # ---- reconcile leases with fencing -----------------------------------------------------------
    # A lease is not enough: a slow worker can outlive it, and it may not even have been reclaimed. Fencing therefore
    # has two parts. (1) Every write after a claim is a compare-and-swap on (purchase_id, owner, generation). (2) Every
    # arrival of new work increments ``pending_seq`` REGARDLESS of any lease; a claim copies it to ``claimed_seq``. A
    # claim whose ``claimed_seq`` is behind ``pending_seq`` has new evidence it never saw: its decision is stale
    # and is never applied or recorded (the row is requeued instead), on EVERY completion path.
    def mark_due(self, purchase_id: str, now: float, *, reawaken: bool = False, reason: str = "transport") -> None:
        """Queue a purchase. New work always bumps ``pending_seq``; manual review is left alone unless ``reawaken``.

        ``reawaken`` is only for NEW verified evidence; the decision function still applies every guard.
        """
        if reason not in ("transport", "await_event", "reconcile", "proof"):
            raise ValueError("unknown reconcile reason")
        with self.db.transaction(immediate=True) as conn:
            row = conn.execute("SELECT state FROM paddle_reconcile WHERE purchase_id = ?", (purchase_id,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO paddle_reconcile (purchase_id, attempts, next_attempt_at, reason, pending_seq, state) "
                             "VALUES (?, 0, ?, ?, 1, 'due')", (purchase_id, now, reason))
            elif row["state"] == "manual":
                if reawaken:
                    conn.execute("UPDATE paddle_reconcile SET state = 'due', attempts = 0, next_attempt_at = ?, "
                                 "pending_seq = pending_seq + 1, wait_deadline = NULL, reason = ? WHERE purchase_id = ?",
                                 (now, reason, purchase_id))
            else:
                conn.execute("UPDATE paddle_reconcile SET next_attempt_at = MIN(next_attempt_at, ?), "
                             "pending_seq = pending_seq + 1 WHERE purchase_id = ?", (now, purchase_id))

    def reconcile_row(self, purchase_id: str):
        return self._c().execute("SELECT * FROM paddle_reconcile WHERE purchase_id = ?", (purchase_id,)).fetchone()

    def claim_due(self, now: float, limit: int) -> list:
        """Atomically lease up to ``limit`` due purchases and read each r0 INSIDE the same transaction.

        Returns dicts ``{purchase_id, owner, generation, revision}``: the token every later call must present, and the
        local revision to use as the fence of the snapshot taken after this claim.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        claims = []
        with self.db.transaction(immediate=True) as conn:
            rows = conn.execute(
                "SELECT purchase_id, generation, pending_seq FROM paddle_reconcile WHERE state = 'due' AND next_attempt_at <= ? "
                "AND (lease_until IS NULL OR lease_until <= ?) ORDER BY next_attempt_at, purchase_id LIMIT ?", (now, now, limit)).fetchall()
            for row in rows:
                owner = "w" + secrets.token_urlsafe(9)
                generation = row["generation"] + 1
                conn.execute("UPDATE paddle_reconcile SET lease_until = ?, owner = ?, generation = ?, claimed_seq = ? WHERE purchase_id = ?",
                             (now + LEASE_SECONDS, owner, generation, row["pending_seq"], row["purchase_id"]))
                revision = conn.execute("SELECT revision FROM purchases WHERE id = ?", (row["purchase_id"],)).fetchone()
                claims.append({"purchase_id": row["purchase_id"], "owner": owner, "generation": generation,
                               "revision": revision["revision"] if revision else None})
        return claims

    def claim_is_current(self, purchase_id: str, owner: str, generation: int) -> bool:
        """True only if this worker still holds the claim AND saw all the work (no new evidence since the claim).

        Call it INSIDE the same transaction as a transition/grant (the caller's ``BEGIN IMMEDIATE`` makes the answer
        stable until commit): checking only at ``finish`` after the commit would be too late.
        """
        row = self._c().execute("SELECT owner, generation, claimed_seq, pending_seq FROM paddle_reconcile WHERE purchase_id = ?",
                                (purchase_id,)).fetchone()
        return bool(row and row["owner"] == owner and row["generation"] == generation and row["claimed_seq"] == row["pending_seq"])

    def _fenced(self, conn, purchase_id: str, owner: str, generation: int):
        return conn.execute("SELECT * FROM paddle_reconcile WHERE purchase_id = ? AND owner = ? AND generation = ?",
                            (purchase_id, owner, generation)).fetchone()

    def _requeue(self, conn, purchase_id: str) -> str:
        conn.execute("UPDATE paddle_reconcile SET owner = NULL, lease_until = NULL, next_attempt_at = 0 WHERE purchase_id = ?", (purchase_id,))
        return "requeued"

    def renew(self, purchase_id: str, owner: str, generation: int, now: float) -> bool:
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute("UPDATE paddle_reconcile SET lease_until = ? WHERE purchase_id = ? AND owner = ? AND generation = ?",
                               (now + LEASE_SECONDS, purchase_id, owner, generation))
        return cur.rowcount == 1

    def finish(self, purchase_id: str, owner: str, generation: int) -> str:
        """Success. 'deleted', 'requeued' (new work arrived since the claim, so it stays due) or 'lost' (stale worker)."""
        with self.db.transaction(immediate=True) as conn:
            row = self._fenced(conn, purchase_id, owner, generation)
            if row is None:
                return "lost"
            if row["claimed_seq"] != row["pending_seq"]:
                return self._requeue(conn, purchase_id)
            conn.execute("DELETE FROM paddle_reconcile WHERE purchase_id = ?", (purchase_id,))
        return "deleted"

    def retry_now(self, purchase_id: str, owner: str, generation: int, now: float) -> bool:
        """A stale revision fence: look again at once with a fresh fetch (never a decision from the old snapshot)."""
        with self.db.transaction(immediate=True) as conn:
            cur = conn.execute("UPDATE paddle_reconcile SET next_attempt_at = ?, lease_until = NULL, owner = NULL "
                               "WHERE purchase_id = ? AND owner = ? AND generation = ? AND state = 'due'",
                               (now, purchase_id, owner, generation))
        return cur.rowcount == 1

    def fail(self, purchase_id: str, owner: str, generation: int, now: float, error_class: str) -> str:
        """Transport failure: exponential backoff, then manual review. 'due', 'manual', 'requeued' or 'lost'.

        ``error_class`` is one of the closed ``ERROR_CLASSES`` codes: exception text, bodies and messages are never stored.
        """
        _check_error_class(error_class)
        with self.db.transaction(immediate=True) as conn:
            row = self._fenced(conn, purchase_id, owner, generation)
            if row is None:
                return "lost"
            if row["claimed_seq"] != row["pending_seq"]:
                return self._requeue(conn, purchase_id)        # new evidence: this old failure must not bury it
            attempts = row["attempts"] + 1
            if attempts >= MAX_ATTEMPTS:
                conn.execute("UPDATE paddle_reconcile SET attempts = ?, state = 'manual', lease_until = NULL, owner = NULL, "
                             "reason = 'transport', last_error = ? WHERE purchase_id = ?", (attempts, error_class, purchase_id))
                return "manual"
            delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), BACKOFF_CAP_SECONDS)
            conn.execute("UPDATE paddle_reconcile SET attempts = ?, next_attempt_at = ?, lease_until = NULL, owner = NULL, "
                         "reason = 'transport', last_error = ? WHERE purchase_id = ?", (attempts, now + delay, error_class, purchase_id))
        return "due"

    def await_event(self, purchase_id: str, owner: str, generation: int, now: float, first_seen: float) -> str:
        """The snapshot says completed but no verified completed event is stored yet: poll gently until the provider's
        own retry window (measured from ``first_seen``) has passed, then manual review. 'due', 'manual', 'requeued' or 'lost'."""
        with self.db.transaction(immediate=True) as conn:
            row = self._fenced(conn, purchase_id, owner, generation)
            if row is None:
                return "lost"
            if row["claimed_seq"] != row["pending_seq"]:
                return self._requeue(conn, purchase_id)
            deadline = row["wait_deadline"] if row["wait_deadline"] is not None else first_seen + AWAIT_EVENT_WINDOW_SECONDS
            if now >= deadline:
                conn.execute("UPDATE paddle_reconcile SET state = 'manual', reason = 'await_event', lease_until = NULL, owner = NULL, "
                             "wait_deadline = ? WHERE purchase_id = ?", (deadline, purchase_id))
                return "manual"
            conn.execute("UPDATE paddle_reconcile SET reason = 'await_event', wait_deadline = ?, next_attempt_at = ?, "
                         "lease_until = NULL, owner = NULL WHERE purchase_id = ?", (deadline, now + AWAIT_EVENT_POLL_SECONDS, purchase_id))
        return "due"

    def to_manual(self, purchase_id: str, owner: str, generation: int, reason: str) -> str:
        """The decision function asked for manual reconciliation. 'manual', 'requeued' or 'lost'."""
        if reason not in ("reconcile", "proof"):
            raise ValueError("unknown reconcile reason")
        with self.db.transaction(immediate=True) as conn:
            row = self._fenced(conn, purchase_id, owner, generation)
            if row is None:
                return "lost"
            if row["claimed_seq"] != row["pending_seq"]:
                return self._requeue(conn, purchase_id)
            conn.execute("UPDATE paddle_reconcile SET state = 'manual', reason = ?, lease_until = NULL, owner = NULL "
                         "WHERE purchase_id = ?", (reason, purchase_id))
        return "manual"
