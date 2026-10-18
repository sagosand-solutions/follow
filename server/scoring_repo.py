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


@dataclass(frozen=True)
class Preference:
    principal_id: str
    family: str
    opted_in_at: float | None
    alias: str
    nickname: str | None
    moderation_state: str


def _p(row) -> Principal:
    return Principal(*(row[k] for k in ("id", "user_id", "anon_cookie_hash", "created_at", "expires_at",
                                          "claimed_to", "claim_source_id", "state")))


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
        self._c().execute("INSERT INTO scoring_principals (id, user_id, created_at, state) VALUES (?, ?, ?, 'active')",
                          (pid, user_id, now))
        return self.principal(pid)

    def principal_by_anon_hash(self, cookie_hash: str) -> Principal | None:
        row = self._c().execute("SELECT * FROM scoring_principals WHERE anon_cookie_hash = ? AND state = 'active'",
                                (cookie_hash,)).fetchone()
        return _p(row) if row else None

    def create_anonymous(self, cookie_hash: str, now: float, expires_at: float) -> Principal:
        pid = new_id("p")
        self._c().execute("INSERT INTO scoring_principals (id, anon_cookie_hash, created_at, expires_at, state) "
                          "VALUES (?, ?, ?, ?, 'active')", (pid, cookie_hash, now, expires_at))
        return self.principal(pid)

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
            "issued_at, expires_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (issue.id, issue.principal_id, issue.question_id, issue.family, issue.catalog_version, issue.revision,
             issue.option_map, issue.issued_at, issue.expires_at))

    def issue(self, issue_id: str) -> Issue | None:
        row = self._c().execute("SELECT * FROM question_issues WHERE id = ?", (issue_id,)).fetchone()
        return _issue(row) if row else None

    def consume_issue(self, issue_id: str, now: float) -> bool:
        cur = self._c().execute("UPDATE question_issues SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
                                (now, issue_id))
        return cur.rowcount == 1

    def purge_issues(self, before: float) -> int:
        return self._c().execute("DELETE FROM question_issues WHERE expires_at < ?", (before,)).rowcount

    # -- attempts ------------------------------------------------------
    def attempt(self, principal_id: str, question_id: str) -> Attempt | None:
        row = self._c().execute("SELECT * FROM first_attempts WHERE principal_id = ? AND question_id = ?",
                                (principal_id, question_id)).fetchone()
        return _attempt(row) if row else None

    def insert_attempt(self, a: Attempt) -> None:
        self._c().execute(
            "INSERT INTO first_attempts (principal_id, question_id, family, course_id, catalog_version, rules_version, "
            "revision, option_id, outcome, score_units, accepted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (a.principal_id, a.question_id, a.family, a.course_id, a.catalog_version, a.rules_version, a.revision,
             a.option_id, a.outcome, a.score_units, a.accepted_at))

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

    def published_sums(self, family: str, start=None, end=None) -> dict:
        """principal_id -> {course_id: (units, answered)} for opted-in, visible, active principals."""
        sql = ("SELECT a.principal_id, a.course_id, SUM(a.score_units), COUNT(*) FROM first_attempts a "
               "JOIN leaderboard_preferences p ON p.principal_id = a.principal_id AND p.family = a.family "
               "JOIN scoring_principals s ON s.id = a.principal_id "
               "WHERE a.family = ? AND p.opted_in_at IS NOT NULL AND p.moderation_state = 'ok' AND s.state = 'active'")
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
