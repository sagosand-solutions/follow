# SPDX-License-Identifier: AGPL-3.0-or-later
"""023 scoring server: catalog, points, issue/answer lock, gate, claim, lists, flags."""
import copy
import json
import sqlite3
import threading
import unittest
from fractions import Fraction

from server.config import load_config
from server.models import ContentError, SetupError
from server.scoring_catalog import parse_catalog
from server.scoring_repo import ScoringRepository, new_id
from server.scoring_versions import ensure_version
from server.scoring_service import course_points, iso_week, total_points
from server.tests.scoring_support import SENTINEL, build_scoring_fixture, make_question
from server.tests.support import ORIGIN, call, json_body

ENV = {"APP_ENV": "test", "PUBLIC_ORIGIN": ORIGIN, "DB_PATH": ":memory:", "CSRF_SECRET_KEY": "a1" * 32,
       "SESSION_SECRET_KEY": "b2" * 32}
Q_KEYS = {"id", "language", "locale", "type", "level", "prompt", "snippets", "options", "license", "author"}


class Base(unittest.TestCase):
    users = ("u1", "u2")

    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=self.users)
        self.addCleanup(tmp.cleanup)

    def rows(self, sql, args=()):
        db = self.fx.db()
        try:
            return [tuple(r) for r in db.connect().execute(sql, args)]
        finally:
            db.close()

    def attempts(self):
        return self.rows("SELECT principal_id, question_id, outcome, score_units FROM first_attempts ORDER BY question_id")


class CatalogTests(unittest.TestCase):
    def good(self):
        q = {"question_id": "a-1", "product_id": "p", "content_sha256": "1" * 64}
        return {"schema_version": 1, "catalog_version": "v1", "rules_version": "r2", "family": "official",
                "courses": [{"course_id": "c", "N_c": 1, "questions": [q]}],
                "exposures": [{"kind": "question", "id": "a-1", "content_sha256": "1" * 64, "affected_question_ids": ["a-1"]}]}

    def test_valid_and_each_corruption_fails_closed(self):
        catalog = parse_catalog(self.good())
        self.assertEqual((catalog.course_count, catalog.pool_sizes), (1, {"c": 1}))
        mutations = {
            "n_c": lambda d: d["courses"][0].update(N_c=2),
            "rules": lambda d: d.update(rules_version="r1"),
            "extra_key": lambda d: d.update(note="x"),
            "family": lambda d: d.update(family="other"),
            "no_self_exposure": lambda d: d["exposures"].clear(),
            "self_missing_in_affected": lambda d: d["exposures"][0].update(affected_question_ids=[]),
            "outside_pool": lambda d: d["exposures"][0].update(affected_question_ids=["a-1", "zzz"]),
            "hash_differs": lambda d: d["exposures"][0].update(content_sha256="2" * 64),
            "dup_question": lambda d: d["courses"][0]["questions"].append(dict(d["courses"][0]["questions"][0])),
            "empty_pool": lambda d: d["courses"][0].update(questions=[], N_c=0),
            "bad_hash": lambda d: d["courses"][0]["questions"][0].update(content_sha256="xyz"),
            "official_without_product": lambda d: d["courses"][0]["questions"][0].update(product_id=None),
            "dup_exposure": lambda d: d["exposures"].append(dict(d["exposures"][0])),
            "kind": lambda d: d["exposures"][0].update(kind="trace"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                data = copy.deepcopy(self.good())
                mutate(data)
                with self.assertRaises(ContentError):
                    parse_catalog(data)

    def test_question_in_two_courses_is_rejected(self):
        data = self.good()
        data["courses"].append({"course_id": "d", "N_c": 1, "questions": copy.deepcopy(data["courses"][0]["questions"])})
        with self.assertRaises(ContentError):
            parse_catalog(data)


class PointsTests(unittest.TestCase):
    def test_spec_example_and_total(self):
        # r2: 1 right, 1 half, 1 wrong, 1 not sure in a pool of 10: (2+1-1+0)*500/10 = 100.
        self.assertEqual(course_points(2 + 1 - 1 + 0, 10), Fraction(100))
        cat = parse_catalog({"schema_version": 1, "catalog_version": "v", "rules_version": "r2", "family": "free",
                             "courses": [{"course_id": c, "N_c": 1, "questions": [
                                 {"question_id": f"{c}-1", "product_id": None, "content_sha256": "1" * 64}]} for c in "abc"],
                             "exposures": [{"kind": "question", "id": f"{c}-1", "content_sha256": "1" * 64,
                                            "affected_question_ids": [f"{c}-1"]} for c in "abc"]})
        self.assertEqual(total_points({"a": 2}, cat), Fraction(1000, 3))
        self.assertEqual(total_points({"a": 2, "b": 2, "c": 2}, cat), Fraction(1000))
        self.assertEqual(total_points({}, cat), 0)
        self.assertEqual(course_points(-1, 4), Fraction(-125))
        self.assertEqual(course_points(-4, 4), Fraction(-500))  # range floor -500  # negatives are not clipped

    def test_iso_week_is_monday_utc(self):
        start, end = iso_week(1_700_000_000.0)  # Tue 2023-11-14 22:13 UTC
        self.assertEqual(start, 1_699_833_600.0)  # Mon 2023-11-13 00:00 UTC
        self.assertEqual(end - start, 7 * 86400)
        self.assertEqual(iso_week(end - 1)[0], start)
        self.assertEqual(iso_week(end)[0], end)


class FlagTests(unittest.TestCase):
    def test_defaults_off_and_production_or_orphan_flags_rejected(self):
        self.assertFalse(load_config(ENV).scoring.any())
        with self.assertRaises(SetupError):
            load_config({**ENV, "SCORING_ANONYMOUS_ENABLED": "true"})
        with self.assertRaises(SetupError):
            load_config({**ENV, "SCORING_ENABLED": "maybe"})
        prod = {**ENV, "APP_ENV": "production", "PUBLIC_ORIGIN": "https://example.org", "DB_PATH": "/x/db",
                "TRUSTED_PROXIES": ""}
        with self.assertRaises(SetupError):
            load_config({**prod, "SCORING_ENABLED": "true"})
        self.assertFalse(load_config(prod).scoring.any())

    def test_disabled_routes_503_without_any_side_effect(self):
        fx, tmp = build_scoring_fixture(env={})
        self.addCleanup(tmp.cleanup)
        for method, path in (("POST", "/scoring/anonymous"), ("POST", "/scoring/questions/py-q00/issue"),
                             ("POST", "/scoring/answers"), ("GET", "/scoring/me"), ("POST", "/scoring/claim"),
                             ("POST", "/scoring/preferences"), ("GET", "/leaderboards")):
            with self.subTest(path):
                status, headers, body = call(fx.app, method, path, headers=fx.headers("u1"), body=b"{}")
                self.assertEqual(status, 503)
                self.assertNotIn("set-cookie", headers)
        db = fx.db()
        try:
            for table in ("scoring_principals", "question_issues", "first_attempts", "submission_receipts"):
                self.assertEqual(db.connect().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        finally:
            db.close()

    def test_sub_flag_off_blocks_only_its_group(self):
        fx, tmp = build_scoring_fixture(env={"SCORING_ENABLED": "true", "SCORING_DEV_SOURCE": "true"})
        self.addCleanup(tmp.cleanup)
        self.assertEqual(fx.issue("py-q00")[0][0], 200)
        self.assertEqual(call(fx.app, "POST", "/scoring/claim", headers=fx.headers("u1"), body=b"{}")[0], 503)
        self.assertEqual(call(fx.app, "POST", "/scoring/anonymous", headers={"origin": ORIGIN}, body=b"{}")[0], 503)
        self.assertEqual(fx.get("/leaderboards", query=b"family=free")[0], 503)


class IssueAnswerTests(Base):
    def test_outcomes_and_units(self):
        for qid, marker, outcome, units in (("py-q00", "Right", "correct", 2), ("py-q01", "Half", "partial", 1),
                                            ("py-q02", "Wrong", "wrong", -1)):
            _, issue = self.fx.issue(qid)
            response = self.fx.answer(issue, marker, key=f"{qid}-key-0123456")
            body = json_body(response)
            self.assertEqual((response[0], body["attempt"]["outcome"], body["attempt"]["score_units"]), (200, outcome, units))
        _, issue = self.fx.issue("py-q03")
        response = self.fx.post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "not_sure",
                                                     "option_id": None, "idempotency_key": "n" * 16})
        self.assertEqual(json_body(response)["attempt"]["score_units"], 0)
        me = json_body(self.fx.get("/scoring/me", query=b"family=official"))
        py = next(c for c in me["courses"] if c["course_id"] == "py")
        self.assertEqual(py["points"], 500 * (2 + 1 - 1 + 0) / 10)
        self.assertAlmostEqual(me["total"]["points"], 500 * 2 / 10 / 3)
        self.assertEqual((me["total"]["answered"], me["total"]["pool"]), (4, 18))

    def test_issue_dto_is_an_allowlist_with_no_facit(self):
        response, issue = self.fx.issue("py-q00")
        self.assertEqual(set(issue), {"issue_id", "revision", "expires_at", "family", "course_id", "question"})
        question = issue["question"]
        self.assertEqual(set(question), Q_KEYS)
        self.assertTrue(all(set(o) == {"option_id", "text"} for o in question["options"]))
        self.assertTrue(all(set(s) == {"id", "code", "highlight_lines"} for s in question["snippets"]))
        raw = response[2].decode()
        for forbidden in (SENTINEL, "py-q00-right", "py-q00-half", "score", "feedback", "misconception", "explanation",
                          "verification", "difficulty", "concepts", "sha256"):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(response[1]["cache-control"], "no-store")

    def test_two_issues_one_lock_second_answer_is_409_without_solution(self):
        _, first = self.fx.issue("py-q00")
        _, second = self.fx.issue("py-q00")
        self.assertEqual(self.fx.answer(first, "Wrong", key="a" * 16)[0], 200)
        response = self.fx.answer(second, "Right", key="b" * 16)
        body = json_body(response)
        self.assertEqual((response[0], body["error"]["code"], body["attempt"]["outcome"]), (409, "locked", "wrong"))
        self.assertNotIn(SENTINEL, response[2].decode())
        self.assertNotIn("solution", body)
        self.assertEqual(len(self.attempts()), 1)
        self.assertEqual(self.fx.issue("py-q00")[0][0], 409)  # no new issue for a locked question

    def test_concurrent_different_answers_commit_at_most_one(self):
        _, issue = self.fx.issue("py-q00")
        results = []
        options = issue["question"]["options"]

        def go(i):
            results.append(self.fx.post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer",
                                        "option_id": options[i]["option_id"], "idempotency_key": f"key-{i}-" + "x" * 12})[0])
        threads = [threading.Thread(target=go, args=(i,)) for i in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results).count(200), 1)
        self.assertEqual(len(self.attempts()), 1)

    def test_idempotent_replay_conflict_expiry_and_receipt_gc(self):
        _, issue = self.fx.issue("py-q00")
        first = self.fx.answer(issue, "Half", key="k" * 16)
        again = self.fx.answer(issue, "Half", key="k" * 16)
        self.assertEqual(json_body(again)["attempt"], json_body(first)["attempt"])
        self.assertTrue(json_body(again)["replayed"])
        # Same key, different body: conflict, and no solution in the error.
        clash = self.fx.answer(issue, "Right", key="k" * 16)
        self.assertEqual((clash[0], json_body(clash)["error"]["code"]), (409, "idempotency_conflict"))
        self.assertNotIn(SENTINEL, clash[2].decode())
        # Replay still works after the issue expired (receipt exists, access intact) ...
        self.fx.clock[0] += 40 * 60
        self.assertTrue(json_body(self.fx.answer(issue, "Half", key="k" * 16))["replayed"])
        # ... but after receipt GC the exact retry is a plain 409 locked: never a new score.
        self.fx.clock[0] += 8 * 86400
        gone = self.fx.answer(issue, "Half", key="k" * 16)
        self.assertEqual((gone[0], json_body(gone)["error"]["code"]), (409, "locked"))
        self.assertEqual(len(self.attempts()), 1)

    def test_expired_stale_and_foreign_option_consume_nothing(self):
        _, issue = self.fx.issue("py-q00")
        wrong_option = {"issue_id": issue["issue_id"], "outcome": "answer", "option_id": "oabcdefghij", "idempotency_key": "z" * 16}
        self.assertEqual(self.fx.post("/scoring/answers", wrong_option)[0], 400)
        db = self.fx.db()
        db.connect().execute("UPDATE question_issues SET revision = 'other' WHERE id = ?", (issue["issue_id"],))
        db.close()
        stale = self.fx.answer(issue, "Right", key="s" * 16)
        self.assertEqual((stale[0], json_body(stale)["error"]["code"]), (409, "stale"))
        _, fresh = self.fx.issue("py-q01")
        self.fx.clock[0] += 31 * 60
        expired = self.fx.answer(fresh, "Right", key="e" * 16)
        self.assertEqual((expired[0], json_body(expired)["error"]["code"]), (409, "expired"))
        self.assertEqual(self.attempts(), [])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM question_issues WHERE consumed_at IS NOT NULL"), [(0,)])
        # A new issue after expiry works and can still be answered.
        _, again = self.fx.issue("py-q01")
        self.assertEqual(self.fx.answer(again, "Right", key="f" * 16)[0], 200)

    def test_option_of_another_issue_for_same_user_is_400(self):
        _, a = self.fx.issue("py-q00")
        _, b = self.fx.issue("py-q01")
        foreign = b["question"]["options"][0]["option_id"]
        response = self.fx.post("/scoring/answers", {"issue_id": a["issue_id"], "outcome": "answer", "option_id": foreign,
                                                     "idempotency_key": "q" * 16})
        self.assertEqual(response[0], 400)

    def test_other_principals_issue_is_404_and_unentitled_question_is_generic_404(self):
        _, issue = self.fx.issue("py-q00")
        stolen = self.fx.post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "not_sure", "option_id": None,
                                                   "idempotency_key": "t" * 16}, user="u2")
        self.assertEqual(stolen[0], 404)
        self.assertEqual(self.fx.issue("py-q00", user="u2")[0][0], 404)
        self.assertEqual(self.fx.issue("nope-q00", user="u2")[0][0], 404)
        unentitled, unknown = (json_body(self.fx.issue(q, user="u2")[0]) for q in ("py-q00", "nope-q00"))
        unentitled.pop("request_id"), unknown.pop("request_id")
        self.assertEqual(unentitled, unknown)  # indistinguishable: no existence leak
        self.assertEqual(self.attempts(), [])

    def test_body_form_csrf_origin_and_auth(self):
        _, issue = self.fx.issue("py-q00")
        base = {"issue_id": issue["issue_id"], "outcome": "not_sure", "option_id": None, "idempotency_key": "w" * 16}
        for name, body in {"extra": {**base, "user_id": "u2"}, "missing": {k: v for k, v in base.items() if k != "option_id"},
                           "not_sure_with_option": {**base, "option_id": "oabcdefghij"},
                           "answer_without_option": {**base, "outcome": "answer"}, "bad_key": {**base, "idempotency_key": "short"},
                           "bad_outcome": {**base, "outcome": "guess"}}.items():
            with self.subTest(name):
                self.assertEqual(self.fx.post("/scoring/answers", body)[0], 400)
        self.assertEqual(self.fx.post("/scoring/answers", base, csrf=False)[0], 403)
        self.assertEqual(self.fx.post("/scoring/answers", base, origin=False)[0], 403)
        self.assertEqual(self.fx.post("/scoring/answers", base, user=None)[0], 401)
        self.assertEqual(self.fx.post("/scoring/answers", b"not json")[0], 400)
        self.assertEqual(self.fx.post("/scoring/answers", {**base, "pad": "x" * 20000})[0], 413)
        self.assertEqual(self.attempts(), [])

    def test_revoked_entitlement_and_deleted_account_between_issue_and_answer(self):
        _, issue = self.fx.issue("py-q00")
        db = self.fx.db()
        pid = db.connect().execute("SELECT id FROM purchases WHERE user_id = 'u1' AND product_id = 'python-lifetime'").fetchone()[0]
        from server.repositories import Repositories
        Repositories(db).entitlements.revoke(pid, "python-lifetime")
        db.close()
        self.assertEqual(self.fx.answer(issue, "Right", key="r" * 16)[0], 404)
        self.assertEqual(self.attempts(), [])
        # Own points stay visible after a refund, but no solution is ever returned again.
        self.assertEqual(self.fx.get("/scoring/me", query=b"family=official")[0], 200)

    def test_refund_after_answer_keeps_points_and_blocks_replay(self):
        _, issue = self.fx.issue("py-q00")
        self.assertEqual(self.fx.answer(issue, "Right", key="r" * 16)[0], 200)
        db = self.fx.db()
        pid = db.connect().execute("SELECT id FROM purchases WHERE user_id = 'u1' AND product_id = 'python-lifetime'").fetchone()[0]
        from server.repositories import Repositories
        Repositories(db).entitlements.revoke(pid, "python-lifetime")
        db.close()
        self.assertEqual(self.fx.answer(issue, "Right", key="r" * 16)[0], 404)  # replay needs current access
        me = json_body(self.fx.get("/scoring/me", query=b"family=official"))
        self.assertEqual(me["total"]["answered"], 1)

    def test_deleted_account_cannot_answer(self):
        _, issue = self.fx.issue("py-q00")
        db = self.fx.db()
        from server.repositories import Repositories
        Repositories(db).users.mark_deleted("u1")
        db.close()
        self.assertEqual(self.fx.answer(issue, "Right", key="d" * 16)[0], 401)

    def test_storage_failure_gives_no_score_and_no_solution(self):
        _, issue = self.fx.issue("py-q00")
        db = self.fx.db()
        db.connect().execute("CREATE TRIGGER boom BEFORE INSERT ON submission_receipts BEGIN SELECT RAISE(ABORT, 'x'); END")
        db.close()
        response = self.fx.answer(issue, "Right", key="x" * 16)
        self.assertEqual(response[0], 503)
        self.assertNotIn(SENTINEL, response[2].decode())
        self.assertEqual(self.attempts(), [])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM question_issues WHERE consumed_at IS NOT NULL"), [(0,)])

    def test_issue_cap_per_principal(self):
        for i in range(20):
            self.assertEqual(self.fx.issue(f"py-q{i % 10:02d}")[0][0], 200)
            self.fx.clock[0] += 1.1
        self.fx.clock[0] += 0  # still inside the 30 minute window
        self.assertEqual(self.fx.issue("py-q00")[0][0], 429)

    def test_solution_shape_and_correct_ids_only_after_commit(self):
        _, issue = self.fx.issue("py-q00")
        body = json_body(self.fx.answer(issue, "Wrong", key="c" * 16))
        solution = body["solution"]
        self.assertEqual(set(solution), {"explanation", "chosen_feedback", "partial_reason", "correct_option_ids"})
        right = [o["option_id"] for o in issue["question"]["options"] if o["text"].startswith("Right")]
        self.assertEqual(solution["correct_option_ids"], right)
        self.assertNotIn("half", json.dumps(solution))  # no other alternative's feedback


class HardeningTests(Base):
    def test_content_changed_after_issue_fails_closed_with_no_score(self):
        _, issue = self.fx.issue("py-q00")
        from pathlib import Path
        Path(self.fx.content_root, "py-q00.txt").write_bytes(b'{"id": "py-q00", "tampered": true}')
        response = self.fx.answer(issue, "Right", key="h" * 16)
        self.assertEqual(response[0], 503)
        self.assertNotIn(SENTINEL, response[2].decode())
        self.assertEqual(self.attempts(), [])
        self.assertEqual(self.fx.issue("py-q00")[0][0], 503)

    def test_same_key_race_gives_one_attempt_and_equal_receipts(self):
        _, issue = self.fx.issue("py-q00")
        option = issue["question"]["options"][0]["option_id"]
        out = []

        def go():
            out.append(self.fx.post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer",
                                    "option_id": option, "idempotency_key": "same-key-" + "0" * 8}))
        threads = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual([r[0] for r in out], [200] * 4)
        self.assertEqual(len({json.dumps(json_body(r)["attempt"], sort_keys=True) for r in out}), 1)
        self.assertEqual(len(self.attempts()), 1)

    def test_solution_is_withheld_until_every_related_question_is_locked(self):
        _, issue = self.fx.issue("py-q02")
        first = json_body(self.fx.answer(issue, "Right", key="a" * 16))
        self.assertIsNone(first["solution"])  # the feedback also touches py-q03, still open
        _, other = self.fx.issue("py-q03")
        self.fx.answer(other, "Right", key="b" * 16)
        replay = json_body(self.fx.answer(issue, "Right", key="a" * 16))
        self.assertTrue(replay["replayed"])
        self.assertIsNotNone(replay["solution"])

    def test_rate_limits_return_429_with_retry_after_and_methods_are_fixed(self):
        _, issue = self.fx.issue("py-q00")
        body = {"issue_id": issue["issue_id"], "outcome": "answer", "option_id": "oabcdefghij", "idempotency_key": "r" * 16}
        codes = [self.fx.post("/scoring/answers", body)[0] for _ in range(31)]
        self.assertEqual(codes[:30], [400] * 30)
        limited = self.fx.post("/scoring/answers", body)
        self.assertEqual(limited[0], 429)
        self.assertIn("retry-after", limited[1])
        self.fx.clock[0] += 61
        self.assertEqual(self.fx.post("/scoring/answers", body)[0], 400)
        for method, path in (("GET", "/scoring/answers"), ("PUT", "/scoring/claim"), ("POST", "/leaderboards")):
            self.assertEqual(call(self.fx.app, method, path, headers=self.fx.headers("u1"))[0], 405, (method, path))

    def test_anonymous_creation_is_limited_per_ip_and_reuses_a_live_cookie(self):
        first = self.fx.anonymous()
        for _ in range(9):
            self.fx.anonymous()
        csrf = call(self.fx.app, "GET", "/auth/csrf")
        response = call(self.fx.app, "POST", "/scoring/anonymous", headers={"origin": ORIGIN, "cookie": csrf[1]["set-cookie"].split(";")[0],
                        "x-csrf-token": json_body(csrf)["csrf_token"]}, body=b"{}")
        self.assertEqual(response[0], 429)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM scoring_principals WHERE anon_cookie_hash IS NOT NULL"), [(10,)])
        self.assertTrue(first[0].startswith("scoring_anon="))


class RetentionAndScopeTests(Base):
    users = ("u1", "u2", "u3")

    def service(self, db):
        from server.repositories import Repositories
        from server.scoring_service import ScoringService
        return ScoringService(Repositories(db), self.fx.settings, self.fx.scoring, clock=lambda: self.fx.clock[0])

    def anon_answer(self, anon, qid, marker, key):
        issue = json_body(self.fx.anon_post(f"/scoring/questions/{qid}/issue", {}, anon))
        option = next(o for o in issue["question"]["options"] if o["text"].startswith(marker))
        return self.fx.anon_post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer",
                                 "option_id": option["option_id"], "idempotency_key": key}, anon)

    def test_inactive_anonymous_profiles_are_purged_after_30_days_but_activity_keeps_them(self):
        idle, active = self.fx.anonymous(), self.fx.anonymous()
        self.anon_answer(idle, "py-free-f00", "Right", "a" * 16)
        self.fx.clock[0] += 20 * 86400
        self.anon_answer(active, "py-free-f01", "Right", "b" * 16)
        self.fx.clock[0] += 11 * 86400
        db = self.fx.db()
        try:
            self.assertEqual(self.service(db).purge_inactive(), 1)
        finally:
            db.close()
        self.assertEqual(self.rows("SELECT COUNT(*) FROM scoring_principals WHERE anon_cookie_hash IS NOT NULL"), [(1,)])
        self.assertEqual(self.rows("SELECT question_id FROM first_attempts"), [("py-free-f01",)])
        self.assertEqual(self.fx.anon_post("/scoring/questions/py-free-f02/issue", {}, idle)[0], 401)

    def test_claim_of_a_purged_source_is_a_conflict_not_a_resurrection(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00", "Right", "a" * 16)
        self.fx.clock[0] += 31 * 86400
        db = self.fx.db()
        try:
            self.service(db).purge_inactive()
        finally:
            db.close()
        headers = self.fx.headers("u1")
        headers["cookie"] += "; " + anon[0]
        response = call(self.fx.app, "POST", "/scoring/claim", headers=headers, body=b"{}")
        self.assertEqual((response[0], json_body(response)["error"]["code"]), (409, "claim_conflict"))

    def claim(self, user, anon):
        headers = self.fx.headers(user)
        headers["cookie"] += "; " + anon[0]
        return call(self.fx.app, "POST", "/scoring/claim", headers=headers, body=b"{}")

    def test_claimed_source_is_not_purged_so_no_second_claim_after_31_days(self):
        first, second = self.fx.anonymous(), self.fx.anonymous()
        self.anon_answer(first, "py-free-f00", "Right", "a" * 16)
        self.assertEqual(self.claim("u1", first)[0], 200)
        self.fx.clock[0] += 31 * 86400
        db = self.fx.db()
        try:
            self.service(db).purge_inactive()
        finally:
            db.close()
        # The consumed source stays as the account's lifetime claim marker; only the unclaimed profile went.
        self.assertEqual(self.rows("SELECT COUNT(*) FROM scoring_principals WHERE claimed_to IS NOT NULL"), [(1,)])
        again = self.claim("u1", second)
        self.assertEqual(again[0], 409)  # second is purged (and u1 already used its claim): never a new claim
        fresh = self.fx.anonymous()
        self.assertEqual(json_body(self.claim("u1", fresh))["error"]["code"], "claim_conflict")

    def test_exactly_30_days_idle_is_purgeable_and_29_is_not(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00", "Right", "a" * 16)
        db = self.fx.db()
        try:
            self.fx.clock[0] += 30 * 86400 - 1
            self.assertEqual(self.service(db).purge_inactive(), 0)
            self.fx.clock[0] += 1
            self.assertEqual(self.service(db).purge_inactive(), 1)
        finally:
            db.close()

    def test_purge_is_one_bounded_batch_and_lists_hide_idle_anonymous_profiles(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00", "Right", "a" * 16)
        self.assertEqual(self.fx.anon_post("/scoring/preferences", {"family": "free", "opt_in": True}, anon)[0], 401)
        board = lambda: json_body(self.fx.get("/leaderboards", user=None, query=b"family=free"))["participants"]
        self.assertEqual(board(), 0, "an anonymous reader is never listed")
        self.fx.clock[0] += 31 * 86400  # idle, not yet purged by any job: still not listed
        self.assertEqual(board(), 0)
        db = self.fx.db()
        try:
            for _ in range(3):
                self.fx.anonymous()  # lazy purge runs one batch per anonymous creation
            self.assertEqual(self.service(db).purge_inactive(limit=1), 0)  # already purged lazily
        finally:
            db.close()

    def test_owner_activity_slides_the_anonymous_window_and_refreshes_the_cookie(self):
        anon = self.fx.anonymous()
        self.fx.clock[0] += 20 * 86400
        response = self.fx.anon_post("/scoring/questions/py-free-f00/issue", {}, anon)
        self.assertEqual(response[0], 200)
        self.assertIn("scoring_anon", response[1]["set-cookie"])
        self.fx.clock[0] += 20 * 86400  # 40 days after creation, 20 after activity: still alive
        self.assertEqual(self.fx.anon_post("/scoring/questions/py-free-f01/issue", {}, anon)[0], 200)

    def test_replay_after_issue_gc_and_receipt_gc_is_locked_not_404(self):
        _, issue = self.fx.issue("py-q00")
        self.fx.answer(issue, "Right", key="k" * 16)
        self.fx.clock[0] += 40 * 86400
        db = self.fx.db()
        try:
            self.service(db).repo.purge_issues(self.fx.clock[0])
            self.service(db).repo.purge_receipts(self.fx.clock[0])
        finally:
            db.close()
        gone = self.fx.answer(issue, "Right", key="k" * 16)
        self.assertEqual((gone[0], json_body(gone)["error"]["code"]), (409, "locked"))

    def test_week_cursor_dies_when_the_week_turns(self):
        for i, user in enumerate(self.users):
            _, issue = self.fx.issue("py-free-f00", user=user)
            self.fx.answer(issue, "Right", user=user, key=f"{i}" * 16)
            self.fx.post("/scoring/preferences", {"family": "free", "opt_in": True}, user)
        me = json_body(self.fx.get("/scoring/me", user="u1", query=b"family=free&window=week"))
        ok = self.fx.get("/leaderboards", user=None, query=f"family=free&window=week&cursor={me['own_cursor']}".encode())
        self.assertEqual(ok[0], 200)
        self.fx.clock[0] += 7 * 86400
        stale = self.fx.get("/leaderboards", user=None, query=f"family=free&window=week&cursor={me['own_cursor']}".encode())
        self.assertEqual(stale[0], 400)

    def test_startup_requires_an_official_snapshot_bound_to_the_manifest(self):
        from server.app import Services, create_app
        from server.db import Database
        no_official = type(self.fx.scoring)({"free": self.fx.catalogs["free"]}, self.fx.scoring.question_source)
        with self.assertRaises(SetupError):
            create_app(self.fx.settings, Services(open_database=lambda: Database(self.fx.path), catalog=self.fx.content_catalog,
                                                  scoring=no_official))
        with self.assertRaises(SetupError):
            create_app(self.fx.settings, Services(open_database=lambda: Database(self.fx.path), catalog=None, scoring=self.fx.scoring))

    def test_gate_denies_when_the_served_file_is_not_the_reviewed_file(self):
        from server.content_catalog import ContentEntry
        entry = self.fx.content_catalog.lookup("question", "py-q00")
        db = self.fx.db()
        try:
            service = self.service(db)
            service.scoring = type(self.fx.scoring)(dict(self.fx.catalogs), self.fx.scoring.question_source)
            self.assertFalse(service.content_allowed("u1", "question", "py-q00", ContentEntry("question", "py-q00", entry.product_id, entry.path, "0" * 64)))
            service.scoring.catalogs.pop("official")
            self.assertFalse(service.content_allowed("u1", "question", "py-q00", entry))  # no snapshot: closed
        finally:
            db.close()

    def test_account_delete_also_removes_the_claimed_source(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00", "Right", "a" * 16)
        headers = self.fx.headers("u1")
        headers["cookie"] += "; " + anon[0]
        self.assertEqual(call(self.fx.app, "POST", "/scoring/claim", headers=headers, body=b"{}")[0], 200)
        self.assertEqual(call(self.fx.app, "POST", "/account/delete", headers=self.fx.headers("u1"), body=b"{}")[0], 200)
        for table in ("first_attempts", "scoring_principals"):
            self.assertEqual(self.rows(f"SELECT COUNT(*) FROM {table}"), [(0,)], table)

    def test_me_scope_and_private_own_cursor_point_into_the_public_list(self):
        for i, user in enumerate(self.users):
            _, issue = self.fx.issue("py-free-f00", user=user)
            self.fx.answer(issue, "Right" if i < 2 else "Wrong", user=user, key=f"{i}" * 16)
            self.fx.post("/scoring/preferences", {"family": "free", "opt_in": True}, user)
        me = json_body(self.fx.get("/scoring/me", user="u3", query=b"family=free&scope=course:py-free"))
        self.assertEqual((me["scope"], me["rank"]["kind"], me["rank"]["value"], me["rank"]["participants"]), ("course:py-free", "actual", 3, 3))
        cursor = me["own_cursor"]
        listing = self.fx.get("/leaderboards", user=None, query=f"family=free&scope=course:py-free&cursor={cursor}".encode())
        self.assertEqual(listing[0], 200)
        self.assertEqual(json_body(listing)["entries"][-1]["rank"], 3)
        for text in (cursor, json.dumps(me["own_cursor"])):
            self.assertNotIn("u3", text)
        # The cursor is bound to scope/window: it does not open another list.
        other = self.fx.get("/leaderboards", user=None, query=f"family=free&scope=total&cursor={cursor}".encode())
        self.assertEqual(other[0], 400)
        self.assertEqual(self.fx.get("/scoring/me", user="u3", query=b"family=free&scope=bogus")[0], 400)

    def test_attribution_is_allowlisted_and_license_must_be_a_string(self):
        from server.scoring_service import ScoringService
        self.assertEqual(ScoringService._author({"name": "Ada", "url": "http://x", "email": "a@b"}), {"name": "Ada"})
        self.assertIsNone(ScoringService._author({"name": "bad\u202e"}))
        self.assertIsNone(ScoringService._author({"nick": "x"}))
        self.assertIsNone(ScoringService._author(["Ada"]))
        _, issue = self.fx.issue("py-q00")
        self.assertEqual(issue["question"]["license"], "CC-BY-SA-4.0")
        self.assertEqual(issue["question"]["author"], {"name": "Anonymous contributor"})

    def test_flag_off_is_503_even_without_scoring_services(self):
        from server.app import Services, create_app
        from server.config import load_config
        from server.db import Database
        settings = load_config({"APP_ENV": "test", "PUBLIC_ORIGIN": ORIGIN, "DB_PATH": self.fx.path,
                                "CSRF_SECRET_KEY": "a1" * 32, "SESSION_SECRET_KEY": "b2" * 32})
        app = create_app(settings, Services(open_database=lambda: Database(self.fx.path), clock=lambda: self.fx.clock[0]))
        self.assertEqual(call(app, "GET", "/scoring/me", headers=self.fx.headers("u1"))[0], 503)
        self.assertEqual(call(app, "GET", "/leaderboards")[0], 503)
        with self.assertRaises(SetupError):
            create_app(load_config({"APP_ENV": "test", "PUBLIC_ORIGIN": ORIGIN, "DB_PATH": self.fx.path,
                                    "CSRF_SECRET_KEY": "a1" * 32, "SESSION_SECRET_KEY": "b2" * 32,
                                    "SCORING_ENABLED": "true"}), Services(open_database=lambda: Database(self.fx.path)))


class ProfileGateTests(Base):
    """Only standard-profile questions may be scored; the four-option practice profile never is."""

    @staticmethod
    def question(version="0.1", profile=None, options=5, qid="prac-q1"):
        q = make_question(qid, n_options=options)
        q["schema_version"] = version
        if profile is not None:
            q["answer_profile"] = profile
        return q

    def question_with_scores(self, scores):
        q = self.question("0.2", "standard", len(scores))
        for option, score in zip(q["options"], scores):
            option["score"] = score
        return q

    def question_with_ids(self, ids):
        q = self.question("0.2", "standard", len(ids))
        for option, option_id in zip(q["options"], ids):
            option["id"] = option_id
        return q

    def question_with_options_replaced(self, index, value):
        q = self.question("0.2", "standard", 5)
        q["options"][index] = value
        return q

    def test_eligibility_matrix(self):
        from server.scoring_service import check_scoring_profile
        ok = [self.question(), self.question(options=8), self.question("0.2", "standard"), self.question("0.2", "standard", 6)]
        for q in ok:
            check_scoring_profile(q)
        bad = {
            "beginner_practice_four": self.question("0.2", "beginner-practice", 4),
            "beginner_practice_five": self.question("0.2", "beginner-practice", 5),
            "profile_on_legacy": self.question("0.1", "standard"),
            "legacy_beginner": self.question("0.1", "beginner-practice", 4),
            "missing_profile_on_02": self.question("0.2"),
            "unknown_profile": self.question("0.2", "advanced"),
            "unknown_version": self.question("0.3", "standard"),
            "four_options_standard": self.question("0.2", "standard", 4),
            "nine_options": self.question("0.2", "standard", 9),
            "three_options": self.question("0.1", None, 3),
            "profile_is_not_a_string": self.question("0.2", ["standard"]),
            "lying_standard_with_two_full": self.question_with_scores([1, 1, 0.5, 0, 0]),
            "no_half": self.question_with_scores([1, 0, 0, 0, 0]),
            "too_few_zero": self.question_with_scores([1, 0.5, 0, 0, 0.5]),
            "boolean_score": self.question_with_scores([True, 0.5, 0, 0, 0]),
            "duplicate_option_id": self.question_with_ids(["a", "a", "b", "c", "d"]),
            "extra_score_in_six": self.question_with_scores([1, 0.5, 0, 0, 0, 0.25]),
            "extra_negative_in_seven": self.question_with_scores([1, 0.5, 0, 0, 0, 0, -1]),
            "extra_nan_in_eight": self.question_with_scores([1, 0.5, 0, 0, 0, 0, 0, float("nan")]),
            "extra_two_in_six": self.question_with_scores([1, 0.5, 0, 0, 0, 2]),
            "string_score": self.question_with_scores([1, 0.5, 0, 0, "0"]),
            "id_is_a_list": self.question_with_ids(["a", [], "b", "c", "d"]),
            "id_is_a_dict": self.question_with_ids(["a", {}, "b", "c", "d"]),
            "empty_id": self.question_with_ids(["a", "", "b", "c", "d"]),
            "option_is_not_an_object": self.question_with_options_replaced(2, "x"),
            "missing_id": self.question_with_options_replaced(1, {"text": "t", "score": 0}),
            "not_an_object": [],
        }
        for name, q in bad.items():
            with self.subTest(name), self.assertRaises(ContentError):
                check_scoring_profile(q)

    def hand_built(self, question):
        import hashlib
        from server.scoring_service import ManifestQuestionSource, ScoringServices
        raw = json.dumps(question, sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        qid = question["id"]
        catalog = parse_catalog({"schema_version": 1, "catalog_version": "hand", "rules_version": "r2", "family": "free",
                                 "courses": [{"course_id": "practice", "N_c": 1, "questions": [
                                     {"question_id": qid, "product_id": None, "content_sha256": digest}]}],
                                 "exposures": [{"kind": "question", "id": qid, "content_sha256": digest, "affected_question_ids": [qid]}]},
                                expected_family="free")
        return ScoringServices({"free": catalog}, ManifestQuestionSource(None, {qid: raw}))

    def test_hand_built_artifact_with_a_practice_question_is_refused_at_start_up(self):
        from server.scoring_service import verify_pool
        verify_pool(self.hand_built(self.question("0.2", "standard")))  # a standard one passes
        with self.assertRaises(ContentError):
            verify_pool(self.hand_built(self.question("0.2", "beginner-practice", 4)))

    def test_runtime_refuses_issue_for_a_practice_question_even_if_start_up_was_bypassed(self):
        from server.repositories import Repositories
        from server.scoring_service import Actor, ScoringError, ScoringService
        scoring = self.hand_built(self.question("0.2", "beginner-practice", 4))
        db = self.fx.db()
        try:
            service = ScoringService(Repositories(db), self.fx.settings, scoring, clock=lambda: self.fx.clock[0])
            with self.assertRaises(ScoringError) as caught:
                service.issue(Actor(user_id="u1"), "prac-q1")
            self.assertEqual(caught.exception.code, "unavailable")
            self.assertEqual(db.connect().execute("SELECT COUNT(*) FROM question_issues").fetchone()[0], 0)
            self.assertEqual(db.connect().execute("SELECT COUNT(*) FROM first_attempts").fetchone()[0], 0)
        finally:
            db.close()

    def stub_services(self, family):
        """A hand-built pool whose single question can be flipped between eligible and ineligible at run time."""
        import hashlib
        from server.scoring_service import ScoringServices

        class Flip:
            def __init__(self, good, bad):
                self.good, self.bad, self.valid = good, bad, True

            def read(self, fam, qid, sha):
                return self.good if self.valid else self.bad

        qid = f"prac-{family}"
        source = Flip(self.question("0.2", "standard", 5, qid), self.question("0.2", "beginner-practice", 4, qid))
        digest = hashlib.sha256(qid.encode()).hexdigest()
        product = "python-lifetime" if family == "official" else None
        catalog = parse_catalog({"schema_version": 1, "catalog_version": "flip", "rules_version": "r2", "family": family,
                                 "courses": [{"course_id": "c", "N_c": 1, "questions": [
                                     {"question_id": qid, "product_id": product, "content_sha256": digest}]}],
                                 "exposures": [{"kind": "question", "id": qid, "content_sha256": digest, "affected_question_ids": [qid]}]},
                                expected_family=family)
        pool_db = self.fx.db()                      # a hand-built pool is registered the way start-up registers a catalog
        try:
            with pool_db.transaction(immediate=True):
                ScoringRepository(pool_db).sync_pool([(qid, family, digest)], self.fx.clock[0], "flip")
                ensure_version(pool_db.connect(), family, catalog, self.fx.clock[0], new_id)
        finally:
            pool_db.close()
        return ScoringServices({family: catalog}, source), source

    def counts(self, db):
        c = db.connect()
        return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
                     ("question_issues", "first_attempts", "submission_receipts")) + (
            c.execute("SELECT COUNT(*) FROM question_issues WHERE consumed_at IS NOT NULL").fetchone()[0],
            c.execute("SELECT last_activity FROM scoring_principals WHERE user_id = 'u1'").fetchone()[0])

    def test_answer_and_replay_return_503_without_side_effects_when_the_question_turns_ineligible(self):
        from server.repositories import Repositories
        from server.scoring_service import Actor, ScoringError, ScoringService
        for family in ("free", "official"):
            with self.subTest(family=family):
                scoring, source = self.stub_services(family)
                db = self.fx.db()
                try:
                    service = ScoringService(Repositories(db), self.fx.settings, scoring, clock=lambda: self.fx.clock[0])
                    actor = Actor(user_id="u1")
                    issued = service.issue(actor, f"prac-{family}")
                    option = issued["question"]["options"][0]["option_id"]
                    body = {"issue_id": issued["issue_id"], "outcome": "answer", "option_id": option, "idempotency_key": "k" * 16 + family}
                    # (a) a NEW answer against an ineligible question: 503, issue stays unconsumed, nothing written
                    source.valid = False
                    before = self.counts(db)
                    self.fx.clock[0] += 5
                    with self.assertRaises(ScoringError) as caught:
                        service.answer(actor, body)
                    self.assertEqual(caught.exception.code, "unavailable")
                    self.assertEqual(self.counts(db), before)
                    # (b) the same answer once the question is eligible again succeeds ...
                    source.valid = True
                    first = service.answer(actor, body)
                    self.assertFalse(first["replayed"])
                    # (c) ... and a later idempotent replay against an ineligible question is a 503 with no side effect,
                    # not "200 + attempt + no solution".
                    source.valid = False
                    before = self.counts(db)
                    self.fx.clock[0] += 5
                    with self.assertRaises(ScoringError) as caught:
                        service.answer(actor, body)
                    self.assertEqual(caught.exception.code, "unavailable")
                    self.assertEqual(self.counts(db), before)
                    # (d) a normal valid replay is untouched
                    source.valid = True
                    self.assertTrue(service.answer(actor, body)["replayed"])
                finally:
                    db.close()

    def test_malformed_questions_are_content_errors_never_leaked_exceptions(self):
        from server.scoring_service import check_scoring_profile
        for data in (None, 5, "q", [], {"schema_version": "0.1", "options": None}, {"schema_version": "0.1", "options": [None] * 5}):
            with self.subTest(data=repr(data)[:30]), self.assertRaises(ContentError):
                check_scoring_profile(data)

    def test_app_does_not_start_with_a_practice_question_in_the_pool(self):
        from server.app import Services, create_app
        from server.db import Database
        scoring = self.hand_built(self.question("0.2", "beginner-practice", 4))
        scoring.catalogs["official"] = self.fx.catalogs["official"]
        with self.assertRaises(SetupError):
            create_app(self.fx.settings, Services(open_database=lambda: Database(self.fx.path), catalog=self.fx.content_catalog,
                                                  scoring=scoring))


class GateTests(Base):
    def content(self, kind, cid, user="u1"):
        return call(self.fx.app, "GET", f"/content/{kind}/{cid}", headers=self.fx.headers(user, csrf=False, origin=False))

    def lock(self, qid, key):
        _, issue = self.fx.issue(qid)
        self.assertEqual(self.fx.answer(issue, "Right", key=key)[0], 200)

    def test_old_content_route_is_blocked_until_the_question_is_locked(self):
        self.assertEqual(self.content("question", "py-q00")[0], 404)
        self.lock("py-q00", "g" * 16)
        self.assertEqual(self.content("question", "py-q00")[0], 200)
        self.assertEqual(self.content("question", "py-q01")[0], 404)

    def test_related_material_needs_all_affected_questions_locked(self):
        self.assertEqual(self.content("study", "study-a")[0], 404)
        self.lock("py-q00", "g" * 16)
        self.assertEqual(self.content("study", "study-a")[0], 404)  # q01 still open
        self.lock("py-q01", "h" * 16)
        self.assertEqual(self.content("study", "study-a")[0], 200)

    def test_reviewed_empty_is_open_and_unreviewed_is_blocked(self):
        self.assertEqual(self.content("exam", "exam-ok")[0], 200)
        self.assertEqual(self.content("exam", "exam-unreviewed")[0], 404)
        self.assertEqual(self.content("exam", "exam-ok", user=None)[0], 401)

    def test_material_of_another_product_without_an_exposure_row_is_denied(self):
        self.assertEqual(self.content("exam", "other-exam")[0], 404)  # entitled, but never reviewed: default-deny

    def test_head_and_conditional_requests_do_not_bypass(self):
        response = call(self.fx.app, "HEAD", "/content/question/py-q05", headers=self.fx.headers("u1", csrf=False, origin=False))
        self.assertIn(response[0], (404, 405))
        cond = call(self.fx.app, "GET", "/content/question/py-q05", headers=self.fx.headers(
            "u1", csrf=False, origin=False, extra={"if-none-match": "*", "range": "bytes=0-10"}))
        self.assertEqual(cond[0], 404)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts"), [(0,)])  # a GET never writes a lock

    def test_export_has_own_answers_and_no_pool_facit(self):
        self.lock("py-q00", "g" * 16)
        status, _, body = call(self.fx.app, "GET", "/account/export", headers=self.fx.headers("u1", csrf=False, origin=False))
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual([a["question_id"] for a in data["scoring"]["attempts"]], ["py-q00"])
        self.assertNotIn(SENTINEL, body.decode())
        self.assertNotIn("py-q05", body.decode())


class ListAndPreferenceTests(Base):
    users = ("u1", "u2", "u3")

    def free_answer(self, user, qid, marker, key):
        _, issue = self.fx.issue(qid, user=user)
        self.assertEqual(self.fx.answer(issue, marker, user=user, key=key)[0], 200)

    def publish(self, user, nickname=None, family="free"):
        body = {"family": family, "opt_in": True}
        if nickname is not None:
            body["nickname"] = nickname
        return self.fx.post("/scoring/preferences", body, user)

    def board(self, **query):
        q = "&".join(f"{k}={v}" for k, v in {"family": "free", **query}.items()).encode()
        return json_body(self.fx.get("/leaderboards", user=None, query=q))

    def test_unpublished_by_default_and_ties_share_rank(self):
        self.free_answer("u1", "py-free-f00", "Right", "a" * 16)
        self.free_answer("u2", "py-free-f00", "Right", "b" * 16)
        self.free_answer("u3", "py-free-f00", "Wrong", "c" * 16)
        self.assertEqual(self.board()["entries"], [])
        for user in self.users:
            self.assertEqual(self.publish(user)[0], 200)
        board = self.board()
        self.assertEqual([e["rank"] for e in board["entries"]], [1, 1, 3])
        self.assertEqual(board["participants"], 3)
        entry = board["entries"][0]
        self.assertEqual(set(entry), {"rank", "alias", "points", "score_display", "answered", "pool"})
        self.assertEqual((entry["points"], entry["answered"], entry["pool"]), (500 * 2 / 3, 1, 3))
        self.assertEqual(board["entries"][2]["points"], 500 * -1 / 3)  # negatives rank last and are kept
        raw = json.dumps(board)
        for private in ("u1", "u2", "u3", "user", "accepted_at", "principal"):
            self.assertNotIn(private, raw)

    def test_opt_out_removes_at_once_and_pagination_is_signed(self):
        for i, user in enumerate(self.users):
            self.free_answer(user, "py-free-f00", "Right", f"{i}" * 16)
            self.publish(user)
        page = self.board(limit=2)
        self.assertEqual(len(page["entries"]), 2)
        self.assertEqual(len(self.board(limit=2, cursor=page["next_cursor"])["entries"]), 1)
        tampered = page["next_cursor"].replace("2.", "1.", 1)
        self.assertEqual(self.fx.get("/leaderboards", user=None, query=f"family=free&limit=2&cursor={tampered}".encode())[0], 400)
        self.fx.post("/scoring/preferences", {"family": "free", "opt_in": False}, "u1")
        self.assertEqual(self.board()["participants"], 2)
        for bad in (b"family=free&limit=51", b"family=free&limit=0", b"family=nope", b"family=free&scope=course:zzz",
                    b"family=free&window=month", b"family=free&q=x"):
            self.assertIn(self.fx.get("/leaderboards", user=None, query=bad)[0], (400, 503), bad)

    def test_week_window_counts_only_attempts_accepted_inside_it(self):
        self.free_answer("u1", "py-free-f00", "Right", "a" * 16)
        self.publish("u1")
        self.assertEqual(len(self.board(window="week")["entries"]), 1)
        self.fx.clock[0] += 8 * 86400  # next ISO week: old attempt does not count, and cannot be redone
        self.assertEqual(self.board(window="week")["entries"], [])
        self.assertEqual(len(self.board(window="all")["entries"]), 1)
        self.assertEqual(self.fx.issue("py-free-f00")[0][0], 409)

    def test_course_scope_and_me_rank_kinds(self):
        self.free_answer("u1", "py-free-f00", "Right", "a" * 16)
        self.free_answer("u2", "py-free-f00", "Half", "b" * 16)
        self.publish("u1")
        me2 = json_body(self.fx.get("/scoring/me", user="u2", query=b"family=free"))
        self.assertEqual((me2["rank"]["kind"], me2["rank"]["value"], me2["published"]), ("hypothetical", 2, False))
        me1 = json_body(self.fx.get("/scoring/me", user="u1", query=b"family=free"))
        self.assertEqual((me1["rank"]["kind"], me1["rank"]["value"]), ("actual", 1))
        me3 = json_body(self.fx.get("/scoring/me", user="u3", query=b"family=free"))
        self.assertEqual(me3["rank"]["kind"], "none")
        self.assertEqual(self.board(scope="course:py-free")["entries"][0]["pool"], 3)

    def test_nickname_rules_and_official_needs_an_account(self):
        for bad in ("a", "x" * 25, "<b>hi</b>", "see http://x.y", "a@b.co", "tab\there", "bidi‮evil", "two  spaces", 5):
            with self.subTest(bad=bad):
                self.assertEqual(self.publish("u1", nickname=bad)[0], 400)
        ok = json_body(self.publish("u1", nickname="Quiet Reader"))
        self.assertEqual(ok["alias"], "Quiet Reader")
        generated = json_body(self.publish("u2"))["alias"]
        self.assertTrue(generated.startswith("Reader-"))
        self.assertEqual(json_body(self.publish("u2"))["alias"], generated)  # stable
        anon = self.fx.anonymous()
        for family in ("official", "free"):
            self.assertEqual(self.fx.anon_post("/scoring/preferences", {"family": family, "opt_in": True}, anon)[0], 401, family)

    def test_account_delete_removes_all_scoring_data_with_no_tombstone(self):
        self.free_answer("u1", "py-free-f00", "Right", "a" * 16)
        self.publish("u1")
        self.assertEqual(self.board()["participants"], 1)
        response = call(self.fx.app, "POST", "/account/delete", headers=self.fx.headers("u1"), body=b"{}")
        self.assertEqual(response[0], 200)
        self.assertEqual(self.board()["participants"], 0)
        for table in ("first_attempts", "scoring_principals", "question_issues", "submission_receipts", "leaderboard_preferences"):
            self.assertEqual(self.rows(f"SELECT COUNT(*) FROM {table}"), [(0,)], table)


class AnonymousAndClaimTests(Base):
    def anon_answer(self, anon, qid, marker, key):
        response = self.fx.anon_post(f"/scoring/questions/{qid}/issue", {}, anon)
        self.assertEqual(response[0], 200, response)
        issue = json_body(response)
        option = next(o for o in issue["question"]["options"] if o["text"].startswith(marker))
        return self.fx.anon_post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer",
                                                      "option_id": option["option_id"], "idempotency_key": key}, anon)

    def test_anonymous_is_free_only_needs_csrf_and_origin(self):
        anon = self.fx.anonymous()
        self.assertEqual(self.fx.anon_post("/scoring/questions/py-q00/issue", {}, anon)[0], 404)  # official: generic 404
        self.assertEqual(self.fx.anon_post("/scoring/questions/py-free-f00/issue", {}, anon, csrf=False)[0], 403)
        self.assertEqual(self.anon_answer(anon, "py-free-f00", "Right", "a" * 16)[0], 200)
        csrf = call(self.fx.app, "GET", "/auth/csrf")
        no_origin = call(self.fx.app, "POST", "/scoring/anonymous", headers={"cookie": csrf[1]["set-cookie"].split(";")[0],
                         "x-csrf-token": json_body(csrf)["csrf_token"]}, body=b"{}")
        self.assertEqual(no_origin[0], 403)
        no_preauth = call(self.fx.app, "POST", "/scoring/anonymous", headers={"origin": ORIGIN}, body=b"{}")
        self.assertEqual(no_preauth[0], 403)

    def claim_headers(self, user, anon):
        headers = self.fx.headers(user)
        headers["cookie"] += "; " + anon[0]
        return headers

    def test_claim_moves_free_attempts_once_with_original_time_and_kills_the_source(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00", "Right", "a" * 16)
        self.fx.clock[0] += 100
        self.anon_answer(anon, "py-free-f01", "Wrong", "b" * 16)
        original = self.rows("SELECT question_id, accepted_at FROM first_attempts ORDER BY question_id")
        self.fx.clock[0] += 5000
        _, mine = self.fx.issue("py-free-f01")  # the account already answered f01 itself
        self.fx.answer(mine, "Half", key="m" * 16)
        response = call(self.fx.app, "POST", "/scoring/claim", headers=self.claim_headers("u1", anon), body=b"{}")
        body = json_body(response)
        self.assertEqual((response[0], body["moved"], body["kept"]), (200, 1, 1))
        by_q = {a["question_id"]: a for a in body["attempts"]}
        self.assertEqual(by_q["py-free-f01"]["outcome"], "partial")  # the account's own lock wins the overlap
        self.assertEqual(by_q["py-free-f00"]["accepted_at"], dict(original)["py-free-f00"])
        me = json_body(self.fx.get("/scoring/me", query=b"family=free"))
        # Claimed anonymous practice stays in the history but never scores: only the account's own half-credit counts.
        self.assertEqual(me["total"]["answered"], 1)
        self.assertAlmostEqual(me["total"]["points"], 500 * 1 / 3)
        self.assertEqual({a["question_id"]: a["rank_eligible"] for a in me["attempts"]}, {"py-free-f00": False, "py-free-f01": True})
        # The source is dead: cookie answers nothing and cannot be claimed again, by anyone.
        self.assertEqual(self.fx.anon_post("/scoring/questions/py-free-f02/issue", {}, anon)[0], 401)
        again = call(self.fx.app, "POST", "/scoring/claim", headers=self.claim_headers("u2", anon), body=b"{}")
        self.assertEqual((again[0], json_body(again)["error"]["code"]), (409, "claim_conflict"))
        self.assertIn("scoring_anon", response[1]["set-cookie"])  # cookie cleared in the claim response

    def test_second_source_for_the_same_account_and_empty_claim(self):
        first, second = self.fx.anonymous(), self.fx.anonymous()
        empty = call(self.fx.app, "POST", "/scoring/claim", headers=self.claim_headers("u1", first), body=b"{}")
        self.assertEqual((empty[0], json_body(empty)["moved"]), (200, 0))
        clash = call(self.fx.app, "POST", "/scoring/claim", headers=self.claim_headers("u1", second), body=b"{}")
        self.assertEqual((clash[0], json_body(clash)["error"]["code"]), (409, "claim_conflict"))

    def test_claim_needs_account_csrf_cookie_and_an_empty_body_and_never_grants_premium(self):
        anon = self.fx.anonymous()
        self.assertEqual(call(self.fx.app, "POST", "/scoring/claim", headers=self.fx.headers("u1"), body=b"{}")[0], 404)
        h = self.claim_headers("u1", anon)
        del h["x-csrf-token"]
        self.assertEqual(call(self.fx.app, "POST", "/scoring/claim", headers=h, body=b"{}")[0], 403)
        self.assertEqual(call(self.fx.app, "POST", "/scoring/claim", headers=self.claim_headers("u1", anon),
                              body=json.dumps({"source_id": "x"}).encode())[0], 400)
        self.assertEqual(call(self.fx.app, "POST", "/scoring/claim", headers={"origin": ORIGIN, "cookie": anon[0],
                         "x-csrf-token": anon[1]}, body=b"{}")[0], 401)  # anonymous callers cannot claim
        self.assertEqual(self.fx.get("/scoring/me", query=b"family=official")[0], 200)

    def test_claim_is_atomic_when_a_step_fails(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00", "Right", "a" * 16)
        db = self.fx.db()
        db.connect().execute("CREATE TRIGGER boom BEFORE UPDATE ON scoring_principals WHEN NEW.state = 'consumed' "
                             "BEGIN SELECT RAISE(ABORT, 'x'); END")
        db.close()
        response = call(self.fx.app, "POST", "/scoring/claim", headers=self.claim_headers("u1", anon), body=b"{}")
        self.assertEqual(response[0], 503)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts WHERE principal_id = "
                                   "(SELECT id FROM scoring_principals WHERE user_id = 'u1')"), [(0,)])  # move rolled back


class MigrationTests(unittest.TestCase):
    def test_first_attempts_are_immutable_except_claim_moves(self):
        fx, tmp = build_scoring_fixture()
        self.addCleanup(tmp.cleanup)
        _, issue = fx.issue("py-q00")
        fx.answer(issue, "Right", key="a" * 16)
        db = fx.db()
        conn = db.connect()
        self.addCleanup(db.close)
        for sql in ("UPDATE first_attempts SET score_units = 4, outcome = 'correct', accepted_at = 1",
                    "UPDATE first_attempts SET outcome = 'wrong', score_units = -1",
                    "DELETE FROM first_attempts"):
            with self.subTest(sql), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO first_attempts SELECT * FROM first_attempts")  # UNIQUE(principal, question)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO scoring_principals (id, user_id, anon_cookie_hash, created_at, state) "
                         "VALUES ('x', 'u2', 'h', 1, 'active')")  # an account principal never has a cookie


if __name__ == "__main__":
    unittest.main()
