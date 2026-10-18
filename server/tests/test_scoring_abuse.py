# SPDX-License-Identifier: AGPL-3.0-or-later
"""Abuse tests for the scoring/leaderboard HTTP surface (023).

These run against the real scoring server and its fixture. Assertions bind to the
frozen interface contract (exact DTO keys, exact error codes) and to the scoring
rules in the spec: units +2/+1/0/-1 for correct/partial/not_sure/wrong and course
points 1000*sum(units)/(2*N_c), rules_version r2. A test that fails here is a real
finding, not a reason to relax the contract.
"""
from __future__ import annotations

import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor

from server.tests.scoring_support import SENTINEL, build_scoring_fixture
from server.tests.support import ORIGIN, call, json_body

INVALID = 400
NO_SESSION = 401
FORBIDDEN = 403
NOT_FOUND = 404
CONFLICT = 409
BODY_LIMIT = 413
THROTTLED = 429
UNAVAILABLE = 503

FACIT_KEYS = frozenset({
    "score", "feedback", "misconception", "partial_reason", "explanation", "verification",
    "expected", "trace", "solution", "rationale", "correct", "difficulty", "concepts", "facit",
})
Q_KEYS = frozenset({"id", "language", "locale", "type", "level", "prompt", "snippets", "options",
                    "license", "author"})
OPTION_KEYS = frozenset({"option_id", "text"})
SNIPPET_KEYS = frozenset({"id", "code", "highlight_lines"})
A_KEYS = frozenset({"question_id", "family", "course_id", "outcome", "score_units", "accepted_at",
                    "rules_version", "catalog_version"})
S_KEYS = frozenset({"explanation", "chosen_feedback", "partial_reason", "correct_option_ids"})
SEMANTIC_OPTION_IDS = frozenset({"py-q00-right", "py-q00-half", "py-q00-bad0"})
ISSUED_OPTION_ID = r"^o[a-z0-9]{10}$"
SPEC_UNITS = {"correct": 2, "partial": 1, "not_sure": 0, "wrong": -1}


def _deep_keys(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key).lower())
            found |= _deep_keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _deep_keys(item)
    return found


def _error_code(response):
    try:
        return json_body(response)["error"]["code"]
    except Exception:
        return None


class AbuseBase(unittest.TestCase):
    def setUp(self):
        self.fx, tmp = build_scoring_fixture()
        self.addCleanup(tmp.cleanup)

    @staticmethod
    def key(name):
        return (name + "0" * 16)[:16]

    def options(self, dto):
        return dto["question"]["options"]

    def option_ids(self, dto):
        return [option["option_id"] for option in self.options(dto)]

    def issue(self, qid="py-q00", user="u1"):
        response, dto = self.fx.issue(qid, user)
        self.assertEqual(response[0], 200, (response[0], response[2]))
        return dto

    def answer(self, dto, marker="Right", user="u1", key="answer"):
        return self.fx.answer(dto, marker, user, self.key(key))

    def not_sure(self, dto, user="u1", key="notsure"):
        return self.fx.post("/scoring/answers", {"issue_id": dto["issue_id"], "outcome": "not_sure",
                                                 "option_id": None, "idempotency_key": self.key(key)}, user)

    def attempt_count(self, where="", params=()):
        db = self.fx.db()
        try:
            sql = "SELECT COUNT(*) FROM first_attempts"
            if where:
                sql += " WHERE " + where
            return db.connect().execute(sql, params).fetchone()[0]
        finally:
            db.close()

    def executor(self):
        workers = ThreadPoolExecutor(max_workers=2)
        self.addCleanup(workers.shutdown)
        return workers

    def anon_answer(self, anon, qid, marker="Right", key="anon"):
        response = self.fx.anon_post(f"/scoring/questions/{qid}/issue", {}, anon)
        self.assertEqual(response[0], 200, response)
        issue = json_body(response)
        option = next(o for o in issue["question"]["options"] if o["text"].startswith(marker))
        return self.fx.anon_post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer",
                                                      "option_id": option["option_id"],
                                                      "idempotency_key": self.key(key)}, anon)

    def claim_with(self, anon_cookie=None, user="u1"):
        headers = self.fx.headers(user)
        if anon_cookie:
            headers["cookie"] = headers["cookie"] + "; " + anon_cookie
        return call(self.fx.app, "POST", "/scoring/claim", headers=headers, body=b"{}")

    def service(self, db):
        from server.repositories import Repositories
        from server.scoring_service import ScoringService
        return ScoringService(Repositories(db), self.fx.settings, self.fx.scoring, clock=lambda: self.fx.clock[0])


class FirstAttemptLockTests(AbuseBase):
    def test_second_answer_is_409_locked_without_solution(self):
        dto = self.issue()
        self.assertEqual(self.answer(dto, "Right", key="first")[0], 200)
        second = self.answer(dto, "Half", key="second")
        self.assertEqual(second[0], CONFLICT)
        self.assertEqual(_error_code(second), "locked")
        self.assertNotIn("solution", json_body(second))
        self.assertEqual(self.attempt_count(), 1)

    def test_reissue_after_lock_is_409_locked_with_attempt(self):
        dto = self.issue()
        self.answer(dto, "Right", key="first")
        response, _ = self.fx.issue("py-q00")
        self.assertEqual(response[0], CONFLICT)
        self.assertEqual(_error_code(response), "locked")
        self.assertIn("attempt", json_body(response))

    def test_not_sure_locks_with_zero_units(self):
        dto = self.issue()
        response = self.not_sure(dto)
        self.assertEqual(response[0], 200, response)
        attempt = json_body(response)["attempt"]
        self.assertEqual(attempt["outcome"], "not_sure")
        self.assertEqual(attempt["score_units"], SPEC_UNITS["not_sure"])
        self.assertEqual(self.attempt_count(), 1)


class IdempotencyReplayTests(AbuseBase):
    def test_same_key_and_body_replays_same_receipt(self):
        dto = self.issue()
        option = self.option_ids(dto)[0]
        body = {"issue_id": dto["issue_id"], "outcome": "answer", "option_id": option,
                "idempotency_key": self.key("replay")}
        first = self.fx.post("/scoring/answers", body)
        replay = self.fx.post("/scoring/answers", body)
        self.assertEqual(first[0], 200)
        self.assertEqual(replay[0], 200)
        self.assertTrue(json_body(replay).get("replayed"))
        self.assertEqual(self.attempt_count(), 1)

    def test_same_key_different_body_is_idempotency_conflict(self):
        dto = self.issue()
        ids = self.option_ids(dto)
        self.fx.post("/scoring/answers", {"issue_id": dto["issue_id"], "outcome": "answer",
                                          "option_id": ids[0], "idempotency_key": self.key("same")})
        changed = self.fx.post("/scoring/answers", {"issue_id": dto["issue_id"], "outcome": "answer",
                                                    "option_id": ids[1], "idempotency_key": self.key("same")})
        self.assertEqual(changed[0], CONFLICT)
        self.assertEqual(_error_code(changed), "idempotency_conflict")
        self.assertNotIn("solution", json_body(changed))
        self.assertEqual(self.attempt_count(), 1)

    def test_new_key_on_locked_question_is_409_locked(self):
        dto = self.issue()
        self.answer(dto, "Right", key="first")
        response = self.answer(dto, "Right", key="brand-new")
        self.assertEqual(response[0], CONFLICT)
        self.assertEqual(_error_code(response), "locked")
        self.assertEqual(self.attempt_count(), 1)

    def test_replay_after_retention_purge_is_still_locked(self):
        dto = self.issue()
        body = {"issue_id": dto["issue_id"], "outcome": "answer", "option_id": self.option_ids(dto)[0],
                "idempotency_key": self.key("old")}
        self.assertEqual(self.fx.post("/scoring/answers", body)[0], 200)
        self.fx.clock[0] += 8 * 24 * 3600
        replay = self.fx.post("/scoring/answers", body)
        self.assertEqual(replay[0], CONFLICT)
        self.assertEqual(_error_code(replay), "locked")
        self.assertEqual(self.attempt_count(), 1)

    def test_replay_after_retention_gc_is_locked_not_404(self):
        dto = self.issue("py-q00")
        issue_id = dto["issue_id"]
        body = {"issue_id": issue_id, "outcome": "answer", "option_id": self.option_ids(dto)[0],
                "idempotency_key": self.key("swept")}
        self.assertEqual(self.fx.post("/scoring/answers", body)[0], 200)
        stale_issue = self.issue("py-q01")["issue_id"]  # unanswered: the sweep must drop this one
        self.fx.clock[0] += 8 * 24 * 3600
        self.fx.issue("py-q02")  # any new issue runs the retention sweep for old issue rows
        db = self.fx.db()
        try:
            stale_left = db.connect().execute("SELECT COUNT(*) FROM question_issues WHERE id = ?",
                                              (stale_issue,)).fetchone()[0]
            consumed_left = db.connect().execute("SELECT COUNT(*) FROM question_issues WHERE id = ?",
                                                 (issue_id,)).fetchone()[0]
        finally:
            db.close()
        self.assertEqual(stale_left, 0, "the retention sweep did not run, so the replay path is untested")
        self.assertEqual(consumed_left, 1, "the answered issue lost its owner-bound replay reference")
        replay = self.fx.post("/scoring/answers", body)
        self.assertEqual(replay[0], CONFLICT)
        self.assertEqual(_error_code(replay), "locked")
        self.assertEqual(self.attempt_count(), 1)

    def test_foreign_issue_id_from_another_principal_is_404(self):
        dto = self.issue(user="u1")
        response = self.fx.post("/scoring/answers", {"issue_id": dto["issue_id"], "outcome": "answer",
                                                     "option_id": self.option_ids(dto)[0],
                                                     "idempotency_key": self.key("intruder")}, "u2")
        self.assertEqual(response[0], NOT_FOUND)
        self.assertEqual(self.attempt_count(), 0)


class ForeignOptionTests(AbuseBase):
    def test_semantic_option_id_is_400(self):
        dto = self.issue()
        response = self.answer(dto, marker="Right")
        self.assertEqual(response[0], 200)
        forged = self.fx.post("/scoring/answers", {"issue_id": dto["issue_id"], "outcome": "answer",
                                                   "option_id": "py-q00-right", "idempotency_key": self.key("forge")})
        self.assertEqual(forged[0], INVALID)
        self.assertEqual(self.attempt_count(), 1)

    def test_option_id_from_another_issue_is_rejected(self):
        one = self.issue("py-q00")
        two = self.issue("py-q01")
        foreign = self.option_ids(two)[0]
        response = self.fx.post("/scoring/answers", {"issue_id": one["issue_id"], "outcome": "answer",
                                                     "option_id": foreign, "idempotency_key": self.key("cross")})
        self.assertIn(response[0], (INVALID, NOT_FOUND))
        self.assertEqual(self.attempt_count(), 0)


class EntitlementAndCsrfTests(AbuseBase):
    def test_missing_principal_is_401(self):
        response = call(self.fx.app, "POST", "/scoring/questions/py-q00/issue",
                        headers={"origin": ORIGIN}, body=b"{}")
        self.assertEqual(response[0], NO_SESSION)

    def test_bad_origin_is_403(self):
        response = self.fx.post("/scoring/questions/py-q00/issue", {}, "u1", origin=False)
        self.assertEqual(response[0], FORBIDDEN)

    def test_missing_csrf_is_403(self):
        response = self.fx.post("/scoring/questions/py-q00/issue", {}, "u1", csrf=False)
        self.assertEqual(response[0], FORBIDDEN)

    def test_unentitled_principal_is_generic_404(self):
        response, _ = self.fx.issue("py-q00", "u2")
        self.assertEqual(response[0], NOT_FOUND)

    def test_revoked_entitlement_between_issue_and_answer_is_404(self):
        dto = self.issue()
        db = self.fx.db()
        try:
            db.connect().execute("UPDATE entitlements SET state = 'revoked', revoked_at = 0 WHERE user_id = 'u1'")
            db.connect().commit()
        finally:
            db.close()
        response = self.answer(dto)
        self.assertEqual(response[0], NOT_FOUND)
        self.assertEqual(self.attempt_count(), 0)


class FacitLeakTests(AbuseBase):
    def test_issue_dto_is_exactly_the_allowlist(self):
        response, dto = self.fx.issue("py-q00")
        self.assertEqual(response[0], 200)
        question = dto["question"]
        self.assertTrue(set(question) <= Q_KEYS, set(question) - Q_KEYS)
        self.assertFalse(FACIT_KEYS & _deep_keys(question), _deep_keys(question))
        for option in self.options(dto):
            self.assertTrue(set(option) <= OPTION_KEYS, set(option))
        for snippet in question.get("snippets", []):
            self.assertTrue(set(snippet) <= SNIPPET_KEYS, set(snippet))
        self.assertNotIn(SENTINEL, response[2].decode())
        ids = self.option_ids(dto)
        self.assertTrue(ids)
        self.assertFalse(set(ids) & SEMANTIC_OPTION_IDS, ids)
        for option_id in ids:
            self.assertRegex(str(option_id), ISSUED_OPTION_ID)

    def test_attempt_carries_no_option_id(self):
        response = self.answer(self.issue(), "Right")
        attempt = json_body(response)["attempt"]
        self.assertTrue(set(attempt) <= A_KEYS, set(attempt))
        self.assertNotIn("option_id", attempt)

    def test_old_content_route_is_gated_before_lock(self):
        response = self.fx.get("/content/question/py-q00")
        if response[0] == 200:
            self.assertFalse(FACIT_KEYS & _deep_keys(json.loads(response[2].decode())),
                             "old content route leaked facit before lock")
            self.assertNotIn(SENTINEL, response[2].decode())
        else:
            self.assertIn(response[0], (NO_SESSION, FORBIDDEN, NOT_FOUND))

    def test_export_before_lock_carries_no_facit(self):
        response = self.fx.get("/account/export")
        self.assertIn(response[0], (200, NO_SESSION))
        self.assertNotIn(SENTINEL, response[2].decode())

    def test_solution_released_only_after_lock(self):
        response = self.answer(self.issue(), "Right")
        solution = json_body(response).get("solution")
        self.assertIsNotNone(solution, "the exposed question should reveal its solution after the lock")
        self.assertTrue(set(solution) <= S_KEYS, set(solution))

    def test_untracked_material_of_an_entitled_product_is_denied(self):
        # u1 owns other-product, but other-exam was never reviewed for exposure: default-deny.
        response = self.fx.get("/content/exam/other-exam")
        self.assertIn(response[0], (FORBIDDEN, NOT_FOUND))
        self.assertNotIn(SENTINEL, response[2].decode())

    def test_reviewed_empty_material_is_open(self):
        response = self.fx.get("/content/exam/exam-ok")
        self.assertEqual(response[0], 200, response)


class RateLimitAndCapacityTests(AbuseBase):
    def test_answer_flood_yields_429_with_retry_after_then_recovers(self):
        dto = self.issue()
        self.answer(dto, "Right", key="first")
        option = self.option_ids(dto)[0]
        retry_after = None
        for index in range(40):
            status, headers, _ = self.fx.post("/scoring/answers", {
                "issue_id": dto["issue_id"], "outcome": "answer", "option_id": option,
                "idempotency_key": self.key(f"flood{index}")})
            if status == THROTTLED:
                retry_after = headers.get("retry-after")
                break
        self.assertIsNotNone(retry_after, "the answer limiter never triggered")
        self.fx.clock[0] += 61
        recovered = self.fx.post("/scoring/answers", {
            "issue_id": dto["issue_id"], "outcome": "answer", "option_id": option,
            "idempotency_key": self.key("recover")})
        self.assertNotEqual(recovered[0], THROTTLED)

    def test_open_issue_capacity_is_429(self):
        status = 200
        for index in range(25):
            response, _ = self.fx.issue("py-q00" if index % 2 == 0 else "py-q01")
            status = response[0]
            if status == THROTTLED:
                break
        self.assertEqual(status, THROTTLED)

    def test_oversized_answer_body_is_413(self):
        dto = self.issue()
        big = {"issue_id": dto["issue_id"], "outcome": "answer", "option_id": self.option_ids(dto)[0],
               "idempotency_key": self.key("big"), "pad": "x" * 20000}
        response = self.fx.post("/scoring/answers", big)
        self.assertEqual(response[0], BODY_LIMIT)


class ConcurrencyTests(AbuseBase):
    def test_parallel_answers_take_a_single_lock(self):
        dto = self.issue()
        ids = self.option_ids(dto)
        issue_id = dto["issue_id"]

        def submit(index):
            try:
                return self.fx.post("/scoring/answers", {
                    "issue_id": issue_id, "outcome": "answer", "option_id": ids[index],
                    "idempotency_key": self.key(f"race{index}")})[0]
            except sqlite3.OperationalError:
                return CONFLICT

        with self.executor() as pool:
            statuses = [status for status in pool.map(submit, (0, 1)) if status in (200, CONFLICT)]
        self.assertEqual(sorted(statuses), [200, CONFLICT])
        self.assertEqual(self.attempt_count(), 1)


class ClockAndExtraFieldTests(AbuseBase):
    def test_extra_client_fields_are_400(self):
        dto = self.issue()
        response = self.fx.post("/scoring/answers", {
            "issue_id": dto["issue_id"], "outcome": "answer", "option_id": self.option_ids(dto)[0],
            "idempotency_key": self.key("extra"), "score": 9999, "user_id": "attacker"})
        self.assertEqual(response[0], INVALID)
        self.assertEqual(self.attempt_count(), 0)

    def test_expired_issue_is_409_expired_without_consuming_attempt(self):
        dto = self.issue()
        self.fx.clock[0] += 31 * 60
        expired = self.answer(dto)
        self.assertEqual(expired[0], CONFLICT)
        self.assertEqual(_error_code(expired), "expired")
        self.assertEqual(self.attempt_count(), 0)
        self.assertEqual(self.fx.issue("py-q00")[0][0], 200)


class ClaimTests(AbuseBase):
    def test_claim_requires_an_authenticated_account(self):
        response = call(self.fx.app, "POST", "/scoring/claim", headers={"origin": ORIGIN}, body=b"{}")
        self.assertEqual(response[0], NO_SESSION)

    def test_claim_without_an_anonymous_source_is_404(self):
        self.assertEqual(self.claim_with()[0], NOT_FOUND)

    def test_two_claims_from_one_source_conflict(self):
        anon_cookie, _ = self.fx.anonymous()
        first = self.claim_with(anon_cookie)
        self.assertEqual(first[0], 200, first)
        second = self.claim_with(anon_cookie)
        self.assertEqual(second[0], CONFLICT)
        self.assertEqual(_error_code(second), "claim_conflict")


class LeaderboardTests(AbuseBase):
    def test_entry_fields_are_allowlisted(self):
        response = self.fx.get("/leaderboards", user=None, query=b"family=official&scope=total&window=all")
        self.assertEqual(response[0], 200, response)
        payload = json_body(response)
        forbidden = {"user_id", "user", "email", "provider", "purchase", "answers", "answer",
                     "session", "cookie", "history", "principal_id", "option_id"}
        self.assertFalse(forbidden & _deep_keys(payload), _deep_keys(payload))
        for entry in payload["entries"]:
            self.assertTrue(set(entry) <= {"rank", "alias", "points", "answered", "pool"}, set(entry))

    def test_bad_cursor_is_400(self):
        response = self.fx.get("/leaderboards", user=None,
                               query=b"family=official&scope=total&window=all&cursor=not-a-cursor")
        self.assertEqual(response[0], INVALID)

    def test_opt_in_publishes_and_default_is_absent(self):
        self.answer(self.issue("py-q00"), "Right")
        before = json_body(self.fx.get("/leaderboards", user=None, query=b"family=official&scope=total&window=all"))
        self.assertEqual(before["entries"], [])
        pref = self.fx.post("/scoring/preferences", {"family": "official", "opt_in": True}, "u1")
        self.assertEqual(pref[0], 200, pref)
        after = json_body(self.fx.get("/leaderboards", user=None, query=b"family=official&scope=total&window=all"))
        self.assertEqual(len(after["entries"]), 1)
        self.assertGreater(after["entries"][0]["points"], 0)

    def test_expired_anonymous_alias_leaves_the_free_list(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00", key="alias")
        self.assertEqual(self.fx.anon_post("/scoring/preferences", {"family": "free", "opt_in": True}, anon)[0], 200)
        before = json_body(self.fx.get("/leaderboards", user=None, query=b"family=free&scope=total&window=all"))
        self.assertEqual(before["participants"], 1)
        self.fx.clock[0] += 31 * 24 * 3600
        after = json_body(self.fx.get("/leaderboards", user=None, query=b"family=free&scope=total&window=all"))
        self.assertEqual(after["participants"], 0, "an expired anonymous alias is still listed")


class DisabledFlagTests(unittest.TestCase):
    def test_disabled_scoring_returns_503_without_side_effects(self):
        fixture, tmp = build_scoring_fixture(env={})
        self.addCleanup(tmp.cleanup)
        for path, method, extra in (
                ("/scoring/me", "GET", {}),
                ("/scoring/questions/py-q00/issue", "POST", {"headers": fixture.headers("u1")}),
                ("/leaderboards", "GET", {})):
            response = call(fixture.app, method, path, **extra)
            self.assertEqual(response[0], UNAVAILABLE, path)
            self.assertEqual(_error_code(response), "unavailable")


class PointsConformanceTests(AbuseBase):
    """The binding spec: units +2/+1/0/-1, 1000*sum/(2*N_c), rules_version r2."""

    def test_attempt_score_units_are_the_v12_integer_scale(self):
        response = self.answer(self.issue("py-q00"), "Right")
        self.assertEqual(json_body(response)["attempt"]["score_units"], SPEC_UNITS["correct"])

    def test_attempt_reports_rules_version_r2(self):
        response = self.answer(self.issue("py-q00"), "Right")
        self.assertEqual(json_body(response)["attempt"]["rules_version"], "r2")

    def test_spec_example_one_of_each_in_a_pool_of_ten_is_100(self):
        self.answer(self.issue("py-q00"), "Right", key="right")
        self.answer(self.issue("py-q01"), "Half", key="half")
        self.answer(self.issue("py-q02"), "Wrong", key="wrong")
        self.not_sure(self.issue("py-q03"), key="notsure")
        me = json_body(self.fx.get("/scoring/me", query=b"family=official&window=all"))
        py = next(course for course in me["courses"] if course["course_id"] == "py")
        self.assertEqual(py["pool"], 10)
        self.assertAlmostEqual(py["points"], 100.0, places=2)
        self.assertAlmostEqual(me["total"]["points"], 100.0 / 3, places=2)


class ContentIntegrityTests(AbuseBase):
    def test_tampered_pool_question_is_refused(self):
        from pathlib import Path
        (Path(self.fx.content_root) / "py-q00.txt").write_bytes(b'{"id": "py-q00", "tampered": true}')
        response, _ = self.fx.issue("py-q00")
        self.assertEqual(response[0], UNAVAILABLE)
        self.assertEqual(_error_code(response), "unavailable")
        self.assertNotEqual(self.fx.get("/content/question/py-q00")[0], 200)

    def test_file_whose_hash_differs_from_the_reviewed_exposure_is_denied(self):
        from types import SimpleNamespace
        db = self.fx.db()
        try:
            service = self.service(db)
            reviewed_hash = self.fx.scoring.catalogs["official"].exposure("exam", "exam-ok")[0]
            changed = SimpleNamespace(sha256="f" * 64)
            self.assertFalse(service.content_allowed("u1", "exam", "exam-ok", changed),
                             "a file whose hash differs from the reviewed exposure was served")
            matching = SimpleNamespace(sha256=reviewed_hash)
            self.assertTrue(service.content_allowed("u1", "exam", "exam-ok", matching))
        finally:
            db.close()

    def test_missing_official_catalog_is_fail_closed(self):
        from server.repositories import Repositories
        from server.scoring_service import ScoringService

        class NoOfficial:
            catalogs = {}

        db = self.fx.db()
        try:
            service = ScoringService(Repositories(db), self.fx.settings, NoOfficial(),
                                     clock=lambda: self.fx.clock[0])
            entry = self.fx.content_catalog.lookup("exam", "other-exam")
            self.assertFalse(service.content_allowed("u1", "exam", "other-exam", entry),
                             "a missing official catalog fell back to serving raw material")
        finally:
            db.close()

    def test_create_app_requires_a_bound_official_snapshot(self):
        from server.app import Services, create_app
        from server.db import Database
        from server.models import SetupError
        from server.scoring_service import ManifestQuestionSource, ScoringServices

        scoring = ScoringServices({"free": self.fx.catalogs["free"]},
                                  ManifestQuestionSource(self.fx.content_catalog, {}))
        services = Services(open_database=lambda: Database(self.fx.path, clock=lambda: self.fx.clock[0]),
                            catalog=self.fx.content_catalog, scoring=scoring, clock=lambda: self.fx.clock[0])
        with self.assertRaises(SetupError):
            create_app(self.fx.settings, services)


class MeAndCursorTests(AbuseBase):
    def test_me_course_scope_is_supported_and_private(self):
        self.answer(self.issue("py-q00"), "Right", key="scope")
        me = json_body(self.fx.get("/scoring/me", query=b"family=official&scope=course:py&window=all"))
        self.assertEqual(me["family"], "official")
        self.assertNotIn("user_id", _deep_keys(me))


class PrivacyRetentionTests(AbuseBase):
    def test_account_deletion_removes_all_scoring_rows_without_a_tombstone(self):
        dto = self.issue("py-q00")
        self.answer(dto, "Right", key="delete")
        response = call(self.fx.app, "POST", "/account/delete", headers=self.fx.headers("u1"), body=b"{}")
        self.assertEqual(response[0], 200, response)
        db = self.fx.db()
        try:
            for table in ("first_attempts", "scoring_principals", "question_issues",
                          "submission_receipts", "leaderboard_preferences"):
                rows = db.connect().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                self.assertEqual(rows, 0, table)
            from server.repositories import Repositories
            from server.scoring_repo import ScoringRepository
            Repositories(db).users.create(user_id="u9")
            self.assertIsNone(ScoringRepository(db).principal_for_user("u9"),
                              "a deleted account left a tombstone linking a fresh account")
        finally:
            db.close()

    def test_inactive_anonymous_profile_is_purged_after_thirty_days(self):
        self.fx.anonymous()
        self.fx.clock[0] += 31 * 24 * 3600
        db = self.fx.db()
        try:
            deleted = self.service(db).purge_inactive()
            remaining = db.connect().execute(
                "SELECT COUNT(*) FROM scoring_principals WHERE anon_cookie_hash IS NOT NULL").fetchone()[0]
        finally:
            db.close()
        self.assertEqual((deleted, remaining), (1, 0))


class ClaimAfterPurgeTests(AbuseBase):
    def test_a_purged_source_does_not_free_the_account_for_a_second_claim(self):
        first = self.fx.anonymous()
        self.anon_answer(first, "py-free-f00", key="first")
        self.assertEqual(self.claim_with(first[0])[0], 200)
        self.fx.clock[0] += 31 * 24 * 3600
        db = self.fx.db()
        try:
            self.service(db).purge_inactive()
            claim_source = db.connect().execute(
                "SELECT claim_source_id FROM scoring_principals WHERE user_id = 'u1'").fetchone()[0]
        finally:
            db.close()
        self.assertIsNotNone(claim_source, "purging the source cleared the account's claim guard")
        second = self.fx.anonymous()
        self.anon_answer(second, "py-free-f01", key="second")
        response = self.claim_with(second[0])
        self.assertEqual(response[0], CONFLICT)
        self.assertEqual(_error_code(response), "claim_conflict")
