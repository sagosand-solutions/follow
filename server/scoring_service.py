# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoring service (023): issue, first-answer lock, points, lists, claim, exposure gate.

One instance serves ONE request over that request's Database. Every authoritative
decision (active user, current entitlement, unconsumed claim source, existing
lock) is made under the write transaction. Points are exact ``Fraction`` values;
only the DTO layer converts to float. Mock mode only: nothing here talks to a
network, provider or payment adapter.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from dataclasses import dataclass
from fractions import Fraction

from .auth import AuthSecurity
from .content_access import has_entitlement
from .models import ContentError, ServerError, UserStatus
from .scoring_catalog import FAMILIES, RULES_VERSION, SCORE_UNITS
from .scoring_repo import Attempt, Issue, Preference, ScoringRepository, new_id
from . import scoring_boards
from .alias_blocklist import is_blocked
from .scoring_standings import StandingsWriter, VersionShape, bump_principal_boards, load_shape
from .scoring_versions import iso_week, points, score_display, score_key, total_numerator, total_parameters, window_key

ISSUE_TTL = 30 * 60
RECEIPT_TTL = 7 * 24 * 3600
ANON_TTL = 30 * 24 * 3600
INACTIVE_TTL = 30 * 24 * 3600
MAX_OPEN_ISSUES = 20
MAX_ANON_GLOBAL = 100_000
MAX_OPEN_ISSUES_GLOBAL = 50_000
LIST_MAX = 50
HISTORY_MAX = 500
_KEY_RE = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
_OPT_RE = re.compile(r"o[a-z0-9]{10}\Z")
_ISSUE_RE = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")
_COURSE_SCOPE_RE = re.compile(r"course:([a-z][a-z0-9-]{0,63})\Z")
_CURSOR_OFFSET = re.compile(r"[0-9]{1,7}")
_CURSOR_REVISION = re.compile(r"[0-9]{1,12}")
_CURSOR_SIGNATURE = re.compile(r"[0-9a-f]{24}")


class ScoringError(ServerError):
    def __init__(self, code: str, *, attempt=None, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.attempt = attempt
        self.retry_after = retry_after


@dataclass
class ScoringServices:
    catalogs: dict            # family -> ScoringCatalog
    question_source: object   # .read(family, question_id, content_sha256) -> dict


@dataclass(frozen=True)
class Actor:
    """Who is calling: an authenticated account or an anonymous (free-only) cookie profile."""
    user_id: str | None = None
    anon_hash: str | None = None


def course_points(units: int, pool: int) -> Fraction:
    return Fraction(500 * units, pool)  # 1000 * units / (2 * N_c)


def total_points(per_course_units: dict, catalog) -> Fraction:
    """Mean of course points over ALL K catalog courses (untouched courses count 0)."""
    k = catalog.course_count
    return sum((course_points(per_course_units.get(c, 0), catalog.pool_sizes[c]) for c in catalog.course_ids),
               Fraction(0)) / k


def outcome_for(question: dict, option_id: str | None) -> tuple[str, int]:
    if option_id is None:
        return "not_sure", SCORE_UNITS["not_sure"]
    for option in question.get("options", ()):
        if option.get("id") == option_id:
            score = option.get("score")
            if score == 1:
                return "correct", SCORE_UNITS["correct"]
            if score == 0.5:
                return "partial", SCORE_UNITS["partial"]
            if score == 0:
                return "wrong", SCORE_UNITS["wrong"]
            raise ContentError("option score outside the scoring scheme")
    raise ContentError("chosen option missing from the question")


def attempt_dto(a: Attempt) -> dict:
    return {"question_id": a.question_id, "family": a.family, "course_id": a.course_id, "outcome": a.outcome,
            "score_units": a.score_units, "accepted_at": a.accepted_at, "rules_version": a.rules_version,
            "catalog_version": a.catalog_version}


def check_scoring_profile(data) -> None:
    """Only standard-profile questions may be scored on the server.

    A question is eligible when it is a legacy 0.1 document (which has no ``answer_profile``) or a 0.2 document
    that says ``standard``, and it has 5-8 option objects, each with a non-empty string id (unique) and a score that
    is exactly 0, 0.5 or 1 (booleans are never scores), with exactly one full, one half and at least three zero-credit
    options. Anything else (the four-option beginner-practice profile, an unknown version or profile, a profile on
    0.1, a missing profile on 0.2, malformed options, a non-object) is refused: never inferred, never a silent
    fallback, always ContentError. The check reads the hash-bound question itself, so a hand-built or stale artifact
    cannot smuggle a practice question into the competition pool.
    """
    if not isinstance(data, dict):
        raise ContentError("question is not eligible for server scoring")
    version = data.get("schema_version")
    has_profile = "answer_profile" in data
    ok = (version == "0.1" and not has_profile) or (version == "0.2" and data.get("answer_profile") == "standard")
    options = data.get("options")
    if not ok or not isinstance(options, list) or not 5 <= len(options) <= 8 or not all(isinstance(o, dict) for o in options):
        raise ContentError("question is not eligible for server scoring")
    ids = [option.get("id") for option in options]
    scores = [option.get("score") for option in options]
    if (not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids)
            or any(isinstance(score, bool) or not isinstance(score, (int, float)) or score not in (0, 0.5, 1) for score in scores)
            or scores.count(1) != 1 or scores.count(0.5) != 1 or scores.count(0) < len(scores) - 2 or scores.count(0) < 3):
        raise ContentError("question is not eligible for server scoring")


def verify_pool(scoring: "ScoringServices") -> None:
    """Start-up gate: every pooled question must exist, match its hash and be eligible for scoring."""
    for family, catalog in scoring.catalogs.items():
        for pq in catalog.questions.values():
            check_scoring_profile(scoring.question_source.read(family, pq.question_id, pq.content_sha256))


class ManifestQuestionSource:
    """Where a pool question's bytes come from, always hash-checked.

    Official: the content catalog. Free: a PRIVATE hash-checked catalog (``free_catalog``), never the public bank; an explicit
    in-memory ``{id: bytes}`` map exists only for tests and local development and is refused at start-up unless
    SCORING_DEV_SOURCE is set (and always in production). There is no fallback from one source to another."""

    def __init__(self, content_catalog=None, free: dict | None = None, free_catalog=None):
        if free and free_catalog is not None:
            raise ContentError("the free pool has one source, not two")
        self.content_catalog = content_catalog
        self.free = dict(free or {})
        self.free_catalog = free_catalog

    @property
    def free_from_memory(self) -> bool:
        return bool(self.free)

    def read(self, family: str, question_id: str, sha256: str) -> dict:
        catalog = self.content_catalog if family == "official" else self.free_catalog
        if family == "official" or self.free_catalog is not None:
            entry = catalog.lookup("question", question_id) if catalog else None
            if entry is None or entry.sha256 != sha256:
                raise ContentError("question missing or changed")
            raw = catalog.read(entry)
        else:
            raw = self.free.get(question_id)
            if raw is None or hashlib.sha256(raw).hexdigest() != sha256:
                raise ContentError("question missing or changed")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ContentError("question is not valid JSON") from exc
        if not isinstance(data, dict) or data.get("id") != question_id:
            raise ContentError("question id mismatch")
        return data


class ScoringService:
    def __init__(self, repositories, settings, scoring: ScoringServices, *, clock):
        self.repositories = repositories
        self.db = repositories.database
        self.repo = ScoringRepository(self.db)
        self.settings = settings
        self.scoring = scoring
        self.clock = clock
        self.security = AuthSecurity(settings)
        self.standings = StandingsWriter()

    # ---- helpers ------------------------------------------------------
    def _now(self) -> float:
        return self.clock()

    def _revision(self, catalog, question_id: str) -> str:
        pq = catalog.questions[question_id]
        digest = hmac.new(self.settings.session_secret, f"rev:{catalog.catalog_version}:{pq.content_sha256}".encode(),
                          hashlib.sha256).hexdigest()
        return digest[:16]

    def _find(self, question_id: str):
        for family in FAMILIES:
            catalog = self.scoring.catalogs.get(family)
            if catalog is not None and question_id in catalog.questions:
                return family, catalog, catalog.questions[question_id]
        return None

    def _active_user(self, user_id: str) -> bool:
        user = self.repositories.users.get(user_id)
        return user is not None and user.status is UserStatus.ACTIVE

    def _principal(self, actor: Actor, *, create: bool):
        """Resolve the principal row UNDER the caller's transaction; raise 401/404-style errors."""
        now = self._now()
        if actor.user_id is not None:
            if not self._active_user(actor.user_id):
                raise ScoringError("no-session")
            if create:
                return self.repo.ensure_account_principal(actor.user_id, now)
            principal = self.repo.principal_for_user(actor.user_id)
            return principal
        if actor.anon_hash is not None:
            principal = self.repo.principal_by_anon_hash(actor.anon_hash)
            if principal is None or principal.state != "active" or (principal.expires_at or 0) <= now:
                raise ScoringError("no-session")
            return principal
        raise ScoringError("no-session")

    def _entitled(self, actor: Actor, family: str, pq) -> bool:
        if family == "free":
            return True
        return actor.user_id is not None and pq.product_id is not None and has_entitlement(
            self.repositories, actor.user_id, pq.product_id)

    def _read_question(self, family: str, pq) -> dict:
        try:
            data = self.scoring.question_source.read(family, pq.question_id, pq.content_sha256)
            check_scoring_profile(data)
            return data
        except ContentError:
            raise ScoringError("unavailable") from None

    # ---- anonymous profile -------------------------------------------
    def create_anonymous(self, existing_cookie: str | None):
        """Return (raw_cookie, csrf, expires_at). A live cookie keeps its profile (and is not re-issued)."""
        now = self._now()
        if existing_cookie:
            try:
                existing_hash = self.security.binding_hash(existing_cookie)
            except ValueError:
                existing_hash = None
            if existing_hash:
                principal = self.repo.principal_by_anon_hash(existing_hash)
                if principal is not None and (principal.expires_at or 0) > now:
                    return existing_cookie, self.security.csrf_for(existing_cookie, purpose="scoring-anon"), principal.expires_at
        raw = self.security.new_token()
        self.purge_inactive(now)
        with self.db.transaction(immediate=True):
            if self.repo.count_active_anonymous(now) >= MAX_ANON_GLOBAL:
                raise ScoringError("unavailable")
            principal = self.repo.create_anonymous(self.security.binding_hash(raw), now, now + ANON_TTL)
        return raw, self.security.csrf_for(raw, purpose="scoring-anon"), principal.expires_at

    def anon_actor(self, raw_cookie: str | None):
        if not raw_cookie:
            return None
        try:
            return Actor(anon_hash=self.security.binding_hash(raw_cookie))
        except ValueError:
            return None

    def verify_anon_csrf(self, raw_cookie: str | None, submitted) -> bool:
        if not raw_cookie:
            return False
        return self.security.verify_csrf(raw_cookie, submitted, purpose="scoring-anon")

    # ---- issue --------------------------------------------------------
    def issue(self, actor: Actor, question_id: str) -> dict:
        found = self._find(question_id) if isinstance(question_id, str) else None
        if found is None:
            raise ScoringError("not-found")
        family, catalog, pq = found
        now = self._now()
        if not self._entitled(actor, family, pq):
            # Unauthenticated callers are rejected by the HTTP layer; an authenticated caller learns nothing.
            raise ScoringError("not-found")
        question = self._read_question(family, pq)
        revision = self._revision(catalog, question_id)
        shuffled = list(question.get("options", ()))
        if len(shuffled) < 2:
            raise ScoringError("unavailable")
        secrets.SystemRandom().shuffle(shuffled)
        issued = {}
        for option in shuffled:
            while True:
                issued_id = "o" + "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(10))
                if issued_id not in issued:
                    break
            issued[issued_id] = option["id"]
        with self.db.transaction(immediate=True):
            principal = self._principal(actor, create=True)
            self.repo.touch(principal.id, now, ANON_TTL)
            lock = self.repo.attempt(principal.id, question_id)
            if lock is not None:
                raise ScoringError("locked", attempt=attempt_dto(lock))
            if not self._entitled(actor, family, pq):
                raise ScoringError("not-found")
            # Only a ranked pool question held, with this content, by an OPEN ranking version of the catalog is issued (default-deny).
            if (self.repo.pool_state(question_id) != "ranked"
                    or not self.repo.open_member(family, catalog.catalog_version, RULES_VERSION, question_id, pq.content_sha256)):
                raise ScoringError("unavailable")
            if self.repo.count_open_issues(principal.id, now) >= MAX_OPEN_ISSUES:
                raise ScoringError("throttled", retry_after=60)
            if self.repo.count_open_issues_total(now) >= MAX_OPEN_ISSUES_GLOBAL:
                raise ScoringError("unavailable")
            self.repo.purge_issues(now - RECEIPT_TTL)
            # An anonymous issue always blocks later ranked play of that question; an account's own issue does not, unless
            # an earlier (claimed) exposure already does.
            account = principal.user_id is not None
            rankable = account and not self.repo.rank_blocked(principal.id, question_id, family)
            self.repo.record_exposure(principal.id, question_id, family, now, "issue", 0 if account else 1)
            issue = Issue(new_id("i"), principal.id, question_id, family, catalog.catalog_version, revision,
                          json.dumps(issued, sort_keys=True), now, now + ISSUE_TTL, None, 1 if rankable else 0)
            self.repo.insert_issue(issue)
        options = [{"option_id": oid, "text": next(o["text"] for o in question["options"] if o["id"] == orig)}
                   for oid, orig in ((o_id, issued[o_id]) for o_id in self._issued_order(issued, shuffled))]
        dto_question = {
            "id": question["id"], "language": question.get("language"), "locale": question.get("locale"),
            "type": question.get("type"), "level": question.get("level"), "prompt": question.get("prompt"),
            "snippets": [{"id": s.get("id"), "code": s.get("code"), "highlight_lines": s.get("highlight_lines", [])}
                         for s in question.get("snippets", ())],
            "options": options,
            "license": question["license"] if isinstance(question.get("license"), str) else None,
            "author": self._author(question.get("author")),
        }
        return {"issue_id": issue.id, "revision": revision, "expires_at": issue.expires_at, "family": family,
                "course_id": pq.course_id, "question": dto_question}

    @staticmethod
    def _author(value):
        """Attribution allowlist: only a plain-text display name, never a free object."""
        name = value.get("name") if isinstance(value, dict) else value if isinstance(value, str) else None
        if not isinstance(name, str) or not 1 <= len(name) <= 100 or any(unicodedata.category(c) in ("Cc", "Cf") for c in name):
            return None
        return {"name": name}

    @staticmethod
    def _issued_order(issued: dict, shuffled: list):
        by_orig = {orig: oid for oid, orig in issued.items()}
        return [by_orig[o["id"]] for o in shuffled]

    # ---- answer -------------------------------------------------------
    def answer(self, actor: Actor, body: dict) -> dict:
        issue_id, outcome, option_id, key = body["issue_id"], body["outcome"], body["option_id"], body["idempotency_key"]
        digest = hashlib.sha256(json.dumps({"issue_id": issue_id, "outcome": outcome, "option_id": option_id},
                                           sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        now = self._now()
        with self.db.transaction(immediate=True):
            principal = self._principal(actor, create=True)
            self.repo.touch(principal.id, now, ANON_TTL)
            issue = self.repo.issue(issue_id)
            if issue is None or issue.principal_id != principal.id:
                raise ScoringError("not-found")
            catalog = self.scoring.catalogs.get(issue.family)
            pq = catalog.questions.get(issue.question_id) if catalog is not None else None
            if pq is None:
                raise ScoringError("stale")
            if not self._entitled(actor, issue.family, pq):
                raise ScoringError("not-found")
            receipt = self.repo.receipt(principal.id, key, now)
            replayed = False
            if receipt is not None:
                if receipt["request_digest"] != digest or receipt["question_id"] != issue.question_id:
                    raise ScoringError("idempotency_conflict")
                attempt = self.repo.attempt(principal.id, issue.question_id)
                if attempt is None:
                    raise ScoringError("unavailable")
                # A successful replay re-validates the question inside the rollback scope: an ineligible
                # question answers 503 and leaves no touch, attempt or receipt change behind.
                self._read_question(issue.family, pq)
                replayed = True
            else:
                existing = self.repo.attempt(principal.id, issue.question_id)
                if existing is not None:
                    raise ScoringError("locked", attempt=attempt_dto(existing))
                if (issue.consumed_at is not None or now >= issue.expires_at):
                    raise ScoringError("expired")
                # A retired question or a frozen version accepts no new attempt (an accepted replay above is still answered).
                if (self.repo.pool_state(issue.question_id) != "ranked"
                        or self.repo.version_frozen(issue.family, issue.catalog_version, RULES_VERSION)):
                    raise ScoringError("expired")
                if issue.catalog_version != catalog.catalog_version or issue.revision != self._revision(catalog, issue.question_id):
                    raise ScoringError("stale")
                option_map = json.loads(issue.option_map)
                if outcome == "answer" and option_id not in option_map:
                    raise ScoringError("invalid")
                question = self._read_question(issue.family, pq)
                original = option_map[option_id] if outcome == "answer" else None
                try:
                    result, units = outcome_for(question, original)
                except ContentError:
                    raise ScoringError("unavailable") from None
                # Rankable only for an account, from an issue that was rank-eligible, with no blocking exposure right now
                # (an issue given before a claim can never yield a ranked attempt after it).
                if principal.user_id is None:
                    origin = "anonymous"
                elif issue.rank_eligible and not self.repo.rank_blocked(principal.id, issue.question_id, issue.family):
                    origin = "account"
                else:
                    origin = "exposed"
                attempt = Attempt(principal.id, issue.question_id, issue.family, pq.course_id, catalog.catalog_version,
                                  RULES_VERSION, issue.revision, original, result, units, now, origin)
                self.repo.insert_attempt(attempt)
                if not self.repo.consume_issue(issue.id, now):
                    raise ScoringError("expired")
                self.db.connect().execute(
                    "DELETE FROM submission_receipts WHERE principal_id = ? AND idempotency_key = ? AND expires_at <= ?",
                    (principal.id, key, now))
                self.repo.insert_receipt(principal.id, key, digest, issue.question_id, now + RECEIPT_TTL)
                self.repo.purge_receipts(now - RECEIPT_TTL)
                preference = self.repo.preference(principal.id, issue.family)
                published = preference is not None and preference.opted_in_at is not None and preference.moderation_state == "ok"
                self.standings.apply(self.db.connect(), attempt, principal, published)
        # Committed (or a verified replay): only now may a solution be projected.
        solution = self._solution(principal, issue, catalog, pq, attempt)
        return {"attempt": attempt_dto(attempt), "replayed": replayed, "solution": solution}

    def _solution(self, principal, issue: Issue, catalog, pq, attempt: Attempt):
        row = catalog.exposure("question", issue.question_id)
        if row is None or not row[1] <= self.repo.locked_question_ids(principal.id):
            return None
        try:
            question = self._read_question(issue.family, pq)
        except ScoringError:
            return None
        option_map = json.loads(issue.option_map)
        chosen = next((o for o in question.get("options", ()) if o.get("id") == attempt.option_id), None)
        return {
            "explanation": question.get("explanation"),
            "chosen_feedback": chosen.get("feedback") if chosen else None,
            "partial_reason": chosen.get("partial_reason") if chosen and attempt.outcome == "partial" else None,
            "correct_option_ids": sorted(oid for oid, orig in option_map.items()
                                         if any(o.get("id") == orig and o.get("score") == 1 for o in question["options"])),
        }

    # ---- exposure gate (central, used by /content) --------------------
    def content_allowed(self, user_id: str, kind: str, content_id: str, entry) -> bool:
        """Served material is released only when every ranked or retired question it reveals is locked by this reader.

        Rows from BOTH families count. Default-deny: no reviewed row anywhere, a row for different bytes, or no active
        official snapshot all refuse. Paying for the product is not enough. A question that has become free is public and
        needs no lock; a question the pool does not know is treated as still protected."""
        if self.scoring.catalogs.get("official") is None:
            return False  # no active snapshot: never fall back to raw bytes
        rows = [row for catalog in self.scoring.catalogs.values() if (row := catalog.exposure(kind, content_id)) is not None]
        if not rows or any(row[0] != entry.sha256 for row in rows):
            return False
        affected = frozenset().union(*(row[1] for row in rows))
        protected = {q for q in affected if self.repo.pool_state(q) != "free"}
        if not protected:
            return True
        principal = self.repo.principal_for_user(user_id)
        return principal is not None and protected <= self.repo.locked_question_ids(principal.id)

    # ---- me / lists ---------------------------------------------------
    @staticmethod
    def _window(kind: str, now: float):
        if kind == "all":
            return {"kind": "all", "start": None, "end": None}, None, None
        start, end = iso_week(now)
        return {"kind": "week", "start": start, "end": end}, start, end

    def _catalog(self, family: str):
        catalog = self.scoring.catalogs.get(family) if family in FAMILIES else None
        if catalog is None:
            raise ScoringError("unavailable" if family in FAMILIES else "invalid")
        return catalog

    def _scope(self, catalog, scope: str) -> str:
        if scope != "total":
            match = _COURSE_SCOPE_RE.fullmatch(scope) if isinstance(scope, str) else None
            if match is None or match.group(1) not in catalog.pool_sizes:
                raise ScoringError("invalid")
        return scope

    def _version(self, conn, family: str, catalog):
        row = scoring_boards.current_version(conn, family, catalog.catalog_version, catalog.rules_version)
        if row is None:
            raise ScoringError("unavailable")
        return row["id"]

    @staticmethod
    def _shape(conn, version_id: str) -> VersionShape:
        return load_shape(conn, version_id)

    @staticmethod
    def _scope_terms(shape: VersionShape, scope: str, units: dict):
        """(numerator, denominator, pool) of a scope for these course units, or None when the scope holds no attempt."""
        if scope == "total":
            if not units:
                return None
            _, _, d = total_parameters(shape)
            return total_numerator({c: u for c, (u, _n) in units.items()}, shape), d, sum(shape.pool_sizes.values())
        course = _COURSE_SCOPE_RE.fullmatch(scope).group(1)
        if course not in units:
            return None
        return units[course][0], shape.pool_sizes[course], shape.pool_sizes[course]

    def _sign(self, family: str, version_id: str, scope: str, window: str, revision: int, offset: int) -> str:
        message = f"cursor:{family}:{version_id}:{scope}:{window}:{revision}:{offset}".encode()
        return hmac.new(self.settings.session_secret, message, hashlib.sha256).hexdigest()[:24]

    def _cursor(self, family, version_id, scope, window, revision, offset) -> str:
        return f"{offset}.{revision}.{self._sign(family, version_id, scope, window, revision, offset)}"

    def me(self, actor: Actor, family: str, window_kind: str, scope: str = "total") -> dict:
        catalog = self._catalog(family)
        scope = self._scope(catalog, scope)
        now = self._now()
        window, start, end = self._window(window_kind, now)
        wkey = window_key("all") if start is None else window_key("week", start)
        with self.db.transaction():                           # one read snapshot for every number below
            conn = self.db.connect()
            principal = self._principal(actor, create=False)
            if principal is not None and family == "official" and actor.user_id is None:
                raise ScoringError("not-found")
            version_id = self._version(conn, family, catalog)
            shape = self._shape(conn, version_id)
            revision = scoring_boards.board_revision(conn, version_id, wkey)
            units, attempts, pref, members = {}, [], None, set()
            if principal is not None:
                table = "course_units" if principal.user_id is not None else "anonymous_units"
                units = {r["course_id"]: (r["units"], r["answered"]) for r in conn.execute(
                    f"SELECT course_id, units, answered FROM {table} WHERE version_id = ? AND window_key = ? AND principal_id = ?",
                    (version_id, wkey, principal.id))}
                attempts = self.repo.attempts(principal.id, family, start=start, end=end, limit=HISTORY_MAX)
                pref = self.repo.preference(principal.id, family)
                members = {r[0] for r in conn.execute("SELECT question_id FROM ranking_version_members WHERE version_id = ?", (version_id,))}
            _, _, total_d = total_parameters(shape)
            total = self._scope_terms(shape, "total", units)
            courses = []
            for c in catalog.course_ids:
                u, n = units.get(c, (0, 0))
                n_c = shape.pool_sizes[c]
                courses.append({"course_id": c, "points": float(points(u, n_c)), "score_display": score_display(u, n_c),
                                "answered": n, "pool": n_c})
            total_numer = total[0] if total else 0
            terms = self._scope_terms(shape, scope, units)
            published = bool(pref and pref.opted_in_at is not None and pref.moderation_state == "ok" and principal.user_id is not None)
            rank, own_cursor, scope_score = {"kind": "none", "value": None, "participants": 0}, None, None
            if terms is not None:
                numerator, denominator, pool = terms
                answered = sum(n for _u, n in units.values()) if scope == "total" else units[scope.split(":", 1)[1]][1]
                scope_score = {"scope": scope, "score_display": score_display(numerator, denominator), "answered": answered, "pool": pool}
                key = score_key(numerator, denominator)
                own = scoring_boards.own_position(conn, family, version_id, scope, wkey, principal.id) if published else None
                if own is not None:
                    rank = {"kind": "actual", "value": own["rnk"], "participants": own["participants"]}
                    offset = max(0, own["pos"] - 1 - 2)
                    own_cursor = self._cursor(family, version_id, scope, wkey, revision, offset)
                else:
                    value, count = scoring_boards.hypothetical(conn, family, version_id, scope, wkey, key)
                    if count:
                        rank = {"kind": "hypothetical", "value": value, "participants": count}
            history = []
            for a in attempts:
                item = attempt_dto(a)
                item["rank_eligible"] = (a.origin == "account" and a.question_id in members
                                         and a.catalog_version == catalog.catalog_version and a.rules_version == catalog.rules_version)
                history.append(item)
        return {"family": family, "rules_version": catalog.rules_version, "catalog_version": catalog.catalog_version,
                "scope": scope, "window": window, "board_revision": str(revision),
                "total": {"points": float(points(total_numer, total_d)), "score_display": score_display(total_numer, total_d),
                          "answered": sum(n for _u, n in units.values()), "pool": sum(shape.pool_sizes.values())},
                "scope_score": scope_score, "courses": courses, "attempts": history, "rank": rank, "published": published,
                "moderation": pref.moderation_state if pref else "ok", "own_cursor": own_cursor}

    def leaderboard(self, family: str, scope: str, window_kind: str, limit: int, cursor: str | None, viewer_user_id: str | None = None) -> dict:
        catalog = self._catalog(family)
        scope = self._scope(catalog, scope)
        now = self._now()
        window, start, end = self._window(window_kind, now)
        wkey = window_key("all") if start is None else window_key("week", start)
        with self.db.transaction():
            conn = self.db.connect()
            version_id = self._version(conn, family, catalog)
            revision = scoring_boards.board_revision(conn, version_id, wkey)
            offset = 0
            if cursor is not None:
                parts = cursor.split(".") if isinstance(cursor, str) else []
                # Plain ASCII shapes only: str.isdigit() also accepts digits such as U+00B2 that int() then refuses, and
                # compare_digest refuses a non-ASCII str, so every part is checked BEFORE it is converted or compared.
                if len(parts) != 3 or not _CURSOR_OFFSET.fullmatch(parts[0]) or not _CURSOR_REVISION.fullmatch(parts[1]) \
                        or not _CURSOR_SIGNATURE.fullmatch(parts[2]):
                    raise ScoringError("invalid")
                if not hmac.compare_digest(parts[2], self._sign(family, version_id, scope, wkey, int(parts[1]), int(parts[0]))):
                    raise ScoringError("invalid")
                if int(parts[1]) != revision:
                    raise ScoringError("stale_cursor")
                offset = int(parts[0])
            shape = self._shape(conn, version_id)
            rows, participants = scoring_boards.page(conn, family, version_id, scope, wkey, offset, limit)
            viewer = self.repo.principal_for_user(viewer_user_id) if viewer_user_id else None
            if scope == "total":
                denominator, pool = total_parameters(shape)[2], sum(shape.pool_sizes.values())
            else:
                denominator = pool = shape.pool_sizes[_COURSE_SCOPE_RE.fullmatch(scope).group(1)]
        entries = []
        for r in rows:
            numerator = int(r["numerator"])
            entry = {"rank": r["rnk"], "alias": r["alias"], "points": float(points(numerator, denominator)),
                     "score_display": score_display(numerator, denominator), "answered": r["answered"], "pool": pool}
            if viewer is not None and r["principal_id"] == viewer.id:
                entry["is_me"] = True
            entries.append(entry)
        more = offset + len(rows) < participants and len(rows) == limit
        return {"family": family, "rules_version": catalog.rules_version, "catalog_version": catalog.catalog_version,
                "scope": scope, "window": window, "board_revision": str(revision), "entries": entries, "participants": participants,
                "next_cursor": self._cursor(family, version_id, scope, wkey, revision, offset + limit) if more else None}

    # ---- preferences --------------------------------------------------
    @staticmethod
    def valid_nickname(value) -> str | None:
        if not isinstance(value, str):
            return None
        text = unicodedata.normalize("NFKC", value).strip()
        if not 2 <= len(text) <= 24:
            return None
        for ch in text:
            category = unicodedata.category(ch)
            if category in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") or (category == "Zs" and ch != " ") or ch in "<>&\"'`\\":
                return None
        lowered = text.casefold()
        if "  " in text or any(token in lowered for token in ("http", "www.", "://", "@")) or is_blocked(text):
            return None
        return text

    def set_preference(self, actor: Actor, family: str, opt_in: bool, nickname, nickname_given: bool) -> dict:
        self._catalog(family)
        if actor.user_id is None:
            # Publishing is account-only for every family; an anonymous reader has no leaderboard identity.
            raise ScoringError("no-session")
        clean = None
        if nickname_given and nickname is not None:
            clean = self.valid_nickname(nickname)
            if clean is None:
                raise ScoringError("invalid")
        now = self._now()
        with self.db.transaction(immediate=True):
            principal = self._principal(actor, create=True)
            self.repo.touch(principal.id, now, ANON_TTL)
            current = self.repo.preference(principal.id, family)
            nick = clean if nickname_given else (current.nickname if current else None)
            if nick:
                alias = nick
            elif current is not None and current.nickname is None:
                alias = current.alias
            else:
                alias = "Reader-" + secrets.token_hex(3)
            self.repo.upsert_preference(Preference(principal.id, family, now if opt_in else None, alias, nick,
                                                   current.moderation_state if current else "ok"))
            bump_principal_boards(self.db.connect(), principal.id, family)
        return {"family": family, "opted_in": bool(opt_in), "alias": alias}

    # ---- claim --------------------------------------------------------
    def claim(self, user_id: str, source_hash: str) -> dict:
        now = self._now()
        with self.db.transaction(immediate=True):
            if not self._active_user(user_id):
                raise ScoringError("no-session")
            target = self.repo.ensure_account_principal(user_id, now)
            source = self.repo.principal_by_anon_hash(source_hash)
            if source is None or (source.expires_at or 0) <= now:
                raise ScoringError("claim_conflict")
            if source.claimed_to is not None or target.claim_source_id is not None or source.id == target.id:
                raise ScoringError("claim_conflict")
            conn = self.db.connect()
            # Exposure first (it reads the source's attempts and issues before they are moved or dropped); the moved
            # attempts keep their origin, so nothing anonymous becomes rankable.
            self.repo.union_exposure(source.id, target.id, now)
            self.repo.withdraw_rank_eligibility(target.id)
            moved = conn.execute(
                "UPDATE first_attempts SET principal_id = ? WHERE principal_id = ? AND family = 'free' AND question_id NOT IN "
                "(SELECT question_id FROM first_attempts WHERE principal_id = ?)", (target.id, source.id, target.id)).rowcount
            kept = conn.execute("SELECT COUNT(*) FROM first_attempts WHERE principal_id = ?", (source.id,)).fetchone()[0]
            # Overlapping source rows are inert (the account's own lock won): drop them, keep only the claim marker.
            conn.execute("INSERT OR IGNORE INTO scoring_purge_gate (open) VALUES (1)")
            conn.execute("DELETE FROM first_attempts WHERE principal_id = ?", (source.id,))
            conn.execute("DELETE FROM scoring_purge_gate")
            conn.execute("UPDATE question_issues SET consumed_at = ? WHERE principal_id = ? AND consumed_at IS NULL",
                         (now, source.id))
            conn.execute("DELETE FROM submission_receipts WHERE principal_id = ?", (source.id,))
            conn.execute("DELETE FROM leaderboard_preferences WHERE principal_id = ?", (source.id,))
            conn.execute("DELETE FROM anonymous_units WHERE principal_id = ?", (source.id,))
            conn.execute("UPDATE scoring_principals SET state = 'consumed', anon_cookie_hash = NULL, claimed_to = ? WHERE id = ?",
                         (target.id, source.id))
            conn.execute("UPDATE scoring_principals SET claim_source_id = ? WHERE id = ?", (source.id, target.id))
            self.repo.touch(target.id, now, ANON_TTL)
            mine = self.repo.attempts(target.id, "free")
        return {"moved": moved, "kept": kept, "attempts": [attempt_dto(a) for a in mine]}

    # ---- retention / account data -------------------------------------
    def purge_inactive(self, now: float | None = None, limit: int = 200) -> int:
        """Delete up to ``limit`` unclaimed anonymous profiles idle for 30 days (exactly 30 d counts). One bounded
        batch per call: a scheduled job loops until 0, request paths run a single batch. Runs under the write
        lock, so it serialises with answer/claim. Claimed sources are kept (the account's lifetime claim marker)
        and only go with the account."""
        now = self._now() if now is None else now
        with self.db.transaction(immediate=True):
            batch = self.repo.inactive_anonymous(now - INACTIVE_TTL, limit)
            if batch:
                self.repo.purge_principals(batch)
        return len(batch)

    def delete_user_data(self, user_id: str) -> None:
        """Account deletion: remove every scoring row of the account and of anonymous sources claimed into it.
        No tombstone and no re-registration guard remain. Call inside the delete txn.
        Thin delegate: the ONE implementation lives in ``ScoringRepository.purge_user``.
        """
        self.repo.purge_user(user_id)

    # ---- account data -------------------------------------------------
    def export(self, user_id: str) -> dict:
        principal = self.repo.principal_for_user(user_id)
        if principal is None:
            return {"attempts": [], "preferences": []}
        attempts = [a for family in FAMILIES for a in self.repo.attempts(principal.id, family)]
        return {"attempts": [attempt_dto(a) for a in attempts],
                "preferences": [{"family": p.family, "published": p.opted_in_at is not None, "alias": p.alias,
                                 "nickname": p.nickname} for p in self.repo.preferences_for(principal.id)]}
