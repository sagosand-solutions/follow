# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQL for scoring (023): principals, issues, first attempts, receipts, preferences.

Every method runs on the caller's request-owned Database. Writes that must be
atomic are composed by the service inside ONE ``transaction(immediate=True)``.
"""
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass


def new_id(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(12)


@dataclass(frozen=True)
class Principal:
    id: str
    user_id: str | None
    anon_cookie_hash: str | None
    created_at: float
    last_activity: float
    expires_at: float | None
    claimed_to: str | None
    claim_source_id: str | None
    state: str


@dataclass(frozen=True)
class Issue:
    id: str
    principal_id: str
    question_id: str
    family: str
    catalog_version: str
    revision: str
    option_map: str
    issued_at: float
    expires_at: float
    consumed_at: float | None
    rank_eligible: int = 0


@dataclass(frozen=True)
class Attempt:
    principal_id: str
    question_id: str
    family: str
    course_id: str
    catalog_version: str
    rules_version: str
    revision: str
    option_id: str | None
    outcome: str
    score_units: int
    accepted_at: float
    origin: str = "legacy"


@dataclass(frozen=True)
class Preference:
    principal_id: str
    family: str
    opted_in_at: float | None
    alias: str
    nickname: str | None
    moderation_state: str


def _p(row) -> Principal:
    return Principal(*(row[k] for k in ("id", "user_id", "anon_cookie_hash", "created_at", "last_activity",
                                          "expires_at", "claimed_to", "claim_source_id", "state")))


def _issue(row) -> Issue:
    return Issue(*(row[k] for k in Issue.__dataclass_fields__))


def _attempt(row) -> Attempt:
    return Attempt(*(row[k] for k in Attempt.__dataclass_fields__))


def _pref(row) -> Preference:
    return Preference(*(row[k] for k in Preference.__dataclass_fields__))


class ScoringRepository:
    def __init__(self, database):
        self.db = database

    def _c(self) -> sqlite3.Connection:
        return self.db.connect()

    # -- principals ---------------------------------------------------
    def principal(self, principal_id: str) -> Principal | None:
        row = self._c().execute("SELECT * FROM scoring_principals WHERE id = ?", (principal_id,)).fetchone()
        return _p(row) if row else None

    def principal_for_user(self, user_id: str) -> Principal | None:
        row = self._c().execute("SELECT * FROM scoring_principals WHERE user_id = ?", (user_id,)).fetchone()
        return _p(row) if row else None

    def ensure_account_principal(self, user_id: str, now: float) -> Principal:
        existing = self.principal_for_user(user_id)
        if existing is not None:
            return existing
        pid = new_id("p")
        self._c().execute("INSERT INTO scoring_principals (id, user_id, created_at, last_activity, state) "
                          "VALUES (?, ?, ?, ?, 'active')", (pid, user_id, now, now))
        return self.principal(pid)

    def principal_by_anon_hash(self, cookie_hash: str) -> Principal | None:
        row = self._c().execute("SELECT * FROM scoring_principals WHERE anon_cookie_hash = ? AND state = 'active'",
                                (cookie_hash,)).fetchone()
        return _p(row) if row else None

    def create_anonymous(self, cookie_hash: str, now: float, expires_at: float) -> Principal:
        pid = new_id("p")
        self._c().execute("INSERT INTO scoring_principals (id, anon_cookie_hash, created_at, last_activity, expires_at, state) "
                          "VALUES (?, ?, ?, ?, ?, 'active')", (pid, cookie_hash, now, now, expires_at))
        return self.principal(pid)

    def touch(self, principal_id: str, now: float, anon_ttl: float) -> None:
        """Owner activity: slides last_activity and (anonymous only) the profile's expiry, both 30 days out."""
        self._c().execute("UPDATE scoring_principals SET last_activity = ?, "
                          "expires_at = CASE WHEN user_id IS NULL THEN ? ELSE expires_at END WHERE id = ? AND last_activity <= ?",
                          (now, now + anon_ttl, principal_id, now))

    def purge_principals(self, principal_ids: list) -> None:
        """Hard-delete scoring data of these principals (retention / account deletion). Opens the purge gate for
        this transaction only; callers run it inside ``transaction(immediate=True)``."""
        conn = self._c()
        conn.execute("INSERT OR IGNORE INTO scoring_purge_gate (open) VALUES (1)")
        try:
            for pid in principal_ids:
                conn.execute("UPDATE scoring_principals SET claimed_to = NULL WHERE claimed_to = ?", (pid,))
                conn.execute("UPDATE scoring_principals SET claim_source_id = NULL WHERE claim_source_id = ?", (pid,))
                # Phase 1 tables first (set_attempts references question_issues); exposure rows need the purge gate.
                for table in ("set_attempts", "set_participants", "standings", "course_units", "anonymous_units",
                              "question_exposure", "first_attempts", "question_issues", "submission_receipts",
                              "leaderboard_preferences"):
                    conn.execute(f"DELETE FROM {table} WHERE principal_id = ?", (pid,))
                conn.execute("DELETE FROM scoring_principals WHERE id = ?", (pid,))
        finally:
            conn.execute("DELETE FROM scoring_purge_gate")

    def purge_user(self, user_id: str) -> list:
        """One implementation of account deletion's scoring purge (also used by ``ScoringService.delete_user_data``).

        Removes the account principal and every anonymous source claimed into it, with all their attempts and
        preferences, and no tombstone or re-registration guard. Call inside the existing delete transaction: it opens
        no transaction of its own, so a failure rolls the whole account deletion back.
        """
        principal = self.principal_for_user(user_id)
        if principal is None:
            return []
        principal_ids = self.sources_claimed_to(principal.id) + [principal.id]
        self.purge_principals(principal_ids)
        return principal_ids

    def sources_claimed_to(self, principal_id: str) -> list:
        return [r[0] for r in self._c().execute("SELECT id FROM scoring_principals WHERE claimed_to = ?", (principal_id,))]

    def inactive_anonymous(self, cutoff: float, limit: int = 500) -> list:
        return [r[0] for r in self._c().execute(
            # A claimed (consumed) source is NOT idle: it stays as the account's lifetime claim marker until
            # the account is deleted. Exactly 30 days idle is already purgeable (<=).
            "SELECT id FROM scoring_principals WHERE user_id IS NULL AND claimed_to IS NULL AND last_activity <= ? LIMIT ?",
            (cutoff, limit))]

    def count_active_anonymous(self, now: float) -> int:
        return self._c().execute("SELECT COUNT(*) FROM scoring_principals WHERE anon_cookie_hash IS NOT NULL "
                                 "AND state = 'active' AND expires_at > ?", (now,)).fetchone()[0]

    # -- issues --------------------------------------------------------
    def count_open_issues(self, principal_id: str, now: float) -> int:
        return self._c().execute("SELECT COUNT(*) FROM question_issues WHERE principal_id = ? AND consumed_at IS NULL "
                                 "AND expires_at > ?", (principal_id, now)).fetchone()[0]

    def count_open_issues_total(self, now: float) -> int:
        return self._c().execute("SELECT COUNT(*) FROM question_issues WHERE consumed_at IS NULL AND expires_at > ?",
                                 (now,)).fetchone()[0]

    def insert_issue(self, issue: Issue) -> None:
        self._c().execute(
            "INSERT INTO question_issues (id, principal_id, question_id, family, catalog_version, revision, option_map, "
            "issued_at, expires_at, consumed_at, rank_eligible) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (issue.id, issue.principal_id, issue.question_id, issue.family, issue.catalog_version, issue.revision,
             issue.option_map, issue.issued_at, issue.expires_at, issue.rank_eligible))

    def issue(self, issue_id: str) -> Issue | None:
        row = self._c().execute("SELECT * FROM question_issues WHERE id = ?", (issue_id,)).fetchone()
        return _issue(row) if row else None

    def consume_issue(self, issue_id: str, now: float) -> bool:
        cur = self._c().execute("UPDATE question_issues SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
                                (now, issue_id))
        return cur.rowcount == 1

    def purge_issues(self, before: float) -> int:
        # Only never-answered issues go; an answered (consumed) issue stays with its attempt so a late replay
        # still resolves to the lock (409 locked), never to a 404.
        return self._c().execute("DELETE FROM question_issues WHERE consumed_at IS NULL AND expires_at < ?", (before,)).rowcount

    # -- exposure: what a principal has been shown. rank_blocking only ever goes 0 -> 1 (trigger-enforced) -----------
    def record_exposure(self, principal_id: str, question_id: str, family: str, now: float, via: str, blocking: int) -> None:
        """Insert, or upgrade a 0 to a 1 (and move the first exposure earlier). An unchanged row is not updated."""
        self._c().execute(
            "INSERT INTO question_exposure (principal_id, question_id, family, first_exposed_at, via, rank_blocking) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (principal_id, question_id, family) DO UPDATE SET "
            "rank_blocking = MAX(question_exposure.rank_blocking, excluded.rank_blocking), "
            "via = CASE WHEN excluded.rank_blocking > question_exposure.rank_blocking THEN excluded.via ELSE question_exposure.via END, "
            "first_exposed_at = MIN(question_exposure.first_exposed_at, excluded.first_exposed_at) "
            "WHERE excluded.rank_blocking > question_exposure.rank_blocking OR excluded.first_exposed_at < question_exposure.first_exposed_at",
            (principal_id, question_id, family, now, via, blocking))

    def rank_blocked(self, principal_id: str, question_id: str, family: str) -> bool:
        row = self._c().execute("SELECT rank_blocking FROM question_exposure WHERE principal_id = ? AND question_id = ? AND family = ?",
                                (principal_id, question_id, family)).fetchone()
        return bool(row and row[0])

    def union_exposure(self, source_id: str, target_id: str, now: float) -> None:
        """Claim: everything the anonymous source was shown (exposure rows, every issue ever given, every answered
        question) now blocks ranked play for the target, monotonically."""
        conn = self._c()
        sources = conn.execute(
            "SELECT question_id, family, first_exposed_at, rank_blocking FROM question_exposure WHERE principal_id = ? "
            "UNION ALL SELECT question_id, family, issued_at, 1 FROM question_issues WHERE principal_id = ? "
            "UNION ALL SELECT question_id, family, accepted_at, 1 FROM first_attempts WHERE principal_id = ?",
            (source_id, source_id, source_id)).fetchall()
        for question_id, family, at, blocking in sources:
            self.record_exposure(target_id, question_id, family, at, "claim", blocking)

    def withdraw_rank_eligibility(self, principal_id: str) -> None:
        """Open issues for a question the principal is now blocked on can never produce a ranked attempt."""
        self._c().execute(
            "UPDATE question_issues SET rank_eligible = 0 WHERE principal_id = ? AND consumed_at IS NULL AND rank_eligible = 1 "
            "AND EXISTS (SELECT 1 FROM question_exposure e WHERE e.principal_id = question_issues.principal_id "
            "AND e.question_id = question_issues.question_id AND e.family = question_issues.family AND e.rank_blocking = 1)",
            (principal_id,))

    # -- attempts ------------------------------------------------------
    def attempt(self, principal_id: str, question_id: str) -> Attempt | None:
        row = self._c().execute("SELECT * FROM first_attempts WHERE principal_id = ? AND question_id = ?",
                                (principal_id, question_id)).fetchone()
        return _attempt(row) if row else None

    def insert_attempt(self, a: Attempt) -> None:
        self._c().execute(
            "INSERT INTO first_attempts (principal_id, question_id, family, course_id, catalog_version, rules_version, "
            "revision, option_id, outcome, score_units, accepted_at, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (a.principal_id, a.question_id, a.family, a.course_id, a.catalog_version, a.rules_version, a.revision,
             a.option_id, a.outcome, a.score_units, a.accepted_at, a.origin))

    def locked_question_ids(self, principal_id: str) -> set:
        return {r[0] for r in self._c().execute("SELECT question_id FROM first_attempts WHERE principal_id = ?",
                                                (principal_id,))}

    def attempts(self, principal_id: str, family: str, *, start: float | None = None, end: float | None = None,
                 limit: int | None = None) -> list[Attempt]:
        sql = "SELECT * FROM first_attempts WHERE principal_id = ? AND family = ?"
        args: list = [principal_id, family]
        if start is not None:
            sql += " AND accepted_at >= ? AND accepted_at < ?"
            args += [start, end]
        sql += " ORDER BY accepted_at DESC, question_id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [_attempt(r) for r in self._c().execute(sql, args)]

    def course_sums(self, principal_id: str, family: str, start=None, end=None) -> dict:
        """course_id -> (sum of units, answered count)."""
        sql = "SELECT course_id, SUM(score_units), COUNT(*) FROM first_attempts WHERE principal_id = ? AND family = ?"
        args: list = [principal_id, family]
        if start is not None:
            sql += " AND accepted_at >= ? AND accepted_at < ?"
            args += [start, end]
        sql += " GROUP BY course_id"
        return {r[0]: (r[1], r[2]) for r in self._c().execute(sql, args)}

    def published_sums(self, family: str, now: float, start=None, end=None) -> dict:
        """principal_id -> {course_id: (units, answered)} for opted-in, visible, active ACCOUNT principals, rankable attempts only."""
        sql = ("SELECT a.principal_id, a.course_id, SUM(a.score_units), COUNT(*) FROM first_attempts a "
               "JOIN leaderboard_preferences p ON p.principal_id = a.principal_id AND p.family = a.family "
               "JOIN scoring_principals s ON s.id = a.principal_id "
               "JOIN users u ON u.id = s.user_id "
               "WHERE a.family = ? AND a.origin = 'account' AND p.opted_in_at IS NOT NULL AND p.moderation_state = 'ok' "
               "AND s.state = 'active' AND u.status = 'active'")
        args: list = [family]
        if start is not None:
            sql += " AND a.accepted_at >= ? AND a.accepted_at < ?"
            args += [start, end]
        sql += " GROUP BY a.principal_id, a.course_id"
        out: dict = {}
        for pid, course, units, count in self._c().execute(sql, args):
            out.setdefault(pid, {})[course] = (units, count)
        return out

    # -- receipts ------------------------------------------------------
    def receipt(self, principal_id: str, key: str, now: float):
        return self._c().execute("SELECT * FROM submission_receipts WHERE principal_id = ? AND idempotency_key = ? "
                                 "AND expires_at > ?", (principal_id, key, now)).fetchone()

    def insert_receipt(self, principal_id: str, key: str, digest: str, question_id: str, expires_at: float) -> None:
        self._c().execute("INSERT INTO submission_receipts (principal_id, idempotency_key, request_digest, question_id, "
                          "expires_at) VALUES (?, ?, ?, ?, ?)", (principal_id, key, digest, question_id, expires_at))

    def purge_receipts(self, now: float) -> int:
        return self._c().execute("DELETE FROM submission_receipts WHERE expires_at < ?", (now,)).rowcount

    # -- preferences ---------------------------------------------------
    def preference(self, principal_id: str, family: str) -> Preference | None:
        row = self._c().execute("SELECT * FROM leaderboard_preferences WHERE principal_id = ? AND family = ?",
                                (principal_id, family)).fetchone()
        return _pref(row) if row else None

    def upsert_preference(self, p: Preference) -> None:
        self._c().execute(
            "INSERT INTO leaderboard_preferences (principal_id, family, opted_in_at, alias, nickname, moderation_state) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (principal_id, family) DO UPDATE SET opted_in_at = excluded.opted_in_at, "
            "alias = excluded.alias, nickname = excluded.nickname",
            (p.principal_id, p.family, p.opted_in_at, p.alias, p.nickname, p.moderation_state))

    def unpublish_all(self, principal_id: str) -> None:
        self._c().execute("UPDATE leaderboard_preferences SET opted_in_at = NULL, nickname = NULL WHERE principal_id = ?",
                          (principal_id,))

    def preferences_for(self, principal_id: str) -> list[Preference]:
        return [_pref(r) for r in self._c().execute("SELECT * FROM leaderboard_preferences WHERE principal_id = ? "
                                                    "ORDER BY family", (principal_id,))]
