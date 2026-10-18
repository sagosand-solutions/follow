# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoring phase 1, step 9: challenge sets are a CONTRACT only. Nothing creates, reads or plays a set.

Negative first: no route and no request field reaches a set, a set reference on an issue changes nothing about the first-attempt lock
or the grind, and the reserved future kinds are refused by the schema. All data is synthetic.
"""
import re
import sqlite3
import unittest
from pathlib import Path

from server.tests.scoring_support import build_scoring_fixture
from server.tests.support import json_body

SERVER = Path(__file__).resolve().parents[1]
SET_NAMES = ("challenge_sets", "challenge_set_questions", "set_participants", "set_attempts")


class Base(unittest.TestCase):
    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=("u1", "u2"), free=(("py-free", 5),))
        self.addCleanup(tmp.cleanup)

    def rows(self, sql, args=()):
        db = self.fx.db()
        try:
            return [tuple(r) for r in db.connect().execute(sql, args)]
        finally:
            db.close()

    def run_sql(self, sql, args=()):
        db = self.fx.db()
        try:
            db.connect().execute(sql, args)
        finally:
            db.close()


class NoApiTests(Base):
    def test_the_issue_route_takes_an_empty_body_and_nothing_about_a_set(self):
        before = self.rows("SELECT COUNT(*) FROM question_issues")
        for body in ({"set_id": "x"}, {"set": "x"}, {"kind": "daily"}, {"seed": "1"}, {"set_id": None}, [], "set"):
            self.assertEqual(self.fx.post("/scoring/questions/py-free-f00/issue", body, "u1")[0], 400, str(body))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM question_issues"), before)

    def test_no_route_mentions_a_set_a_challenge_or_a_seed(self):
        app = self.fx.app
        while not hasattr(app, "routes"):          # unwrap the middleware layers
            app = app.app
        paths = []
        def collect(routes, prefix=""):
            for route in routes:
                paths.append(prefix + getattr(route, "path", ""))
                if hasattr(route, "routes"):
                    collect(route.routes, prefix + getattr(route, "path", ""))
        collect(app.routes)
        self.assertGreater(len([p for p in paths if "scoring" in p or "leaderboards" in p]), 5, "the scoring routes are in the list")
        for path in paths:
            self.assertFalse(re.search(r"set|challenge|season|daily|duel|seed", path, re.I), path)
        for method, path in (("GET", "/scoring/sets"), ("POST", "/scoring/sets"), ("GET", "/challenges"), ("POST", "/scoring/challenges")):
            response = self.fx.get(path) if method == "GET" else self.fx.post(path, {}, "u1")
            self.assertIn(response[0], (404, 405), path)

    def test_only_the_repository_purge_knows_the_set_tables(self):
        users = {path.name for path in SERVER.glob("*.py") if any(name in path.read_text(encoding="utf-8") for name in SET_NAMES)}
        self.assertEqual(users, {"scoring_repo.py"}, "a service, route or tool that starts using sets needs its own reviewed plan")
        self.assertNotRegex((SERVER / "scoring_service.py").read_text(encoding="utf-8"), r"\bset_id\b")
        self.assertNotRegex((SERVER / "scoring_http.py").read_text(encoding="utf-8"), r"\bset_id\b")

    def test_every_issue_ever_given_has_no_set(self):
        anon = self.fx.anonymous()
        self.fx.issue("py-free-f00", user="u1")
        self.fx.issue("py-q00", user="u1")
        self.fx.anon_post("/scoring/questions/py-free-f01/issue", {}, anon)
        self.assertEqual(self.rows("SELECT COUNT(*), COUNT(set_id) FROM question_issues"), [(3, 0)])


class GrindIsUnchangedTests(Base):
    def test_a_set_reference_on_an_issue_changes_neither_the_answer_nor_the_lock(self):
        _, issue = self.fx.issue("py-free-f00", user="u1")
        self.run_sql("INSERT INTO challenge_sets (id, kind, family, catalog_version, rules_version, algorithm_version, seed_ref, lock_policy, created_at) "
                     "VALUES ('s1', 'solo', 'free', 'v', 'r2', 'a1', 'seed', 'global_first_attempt', 1)")
        self.run_sql("UPDATE question_issues SET set_id = 's1' WHERE id = ?", (issue["issue_id"],))
        response = self.fx.answer(issue, "Right", user="u1", key="s" * 16)
        self.assertEqual((response[0], json_body(response)["attempt"]["outcome"]), (200, "correct"))
        self.assertEqual(self.rows("SELECT origin FROM first_attempts"), [("account",)], "the set gives no other origin")
        again, _ = self.fx.issue("py-free-f00", user="u1")
        self.assertEqual((again[0], json_body(again)["error"]["code"]), (409, "locked"), "the global first-attempt lock stands")

    def test_a_set_attempt_row_does_not_open_a_second_attempt(self):
        _, issue = self.fx.issue("py-free-f01", user="u1")
        self.run_sql("INSERT INTO challenge_sets (id, kind, family, catalog_version, rules_version, algorithm_version, seed_ref, lock_policy, created_at) "
                     "VALUES ('s1', 'solo', 'free', 'v', 'r2', 'a1', 'seed', 'global_first_attempt', 1)")
        principal = self.rows("SELECT id FROM scoring_principals WHERE user_id = 'u1'")[0][0]
        self.run_sql("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', ?, 'py-free-f01', ?)", (principal, issue["issue_id"]))
        self.assertEqual(self.fx.answer(issue, "Wrong", user="u1", key="t" * 16)[0], 200)
        self.assertEqual(self.fx.issue("py-free-f01", user="u1")[0][0], 409)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts WHERE question_id = 'py-free-f01'"), [(1,)])


class SchemaRefusesTheFutureTests(Base):
    BASE = ("INSERT INTO challenge_sets (id, kind, family, catalog_version, rules_version, algorithm_version, seed_ref, lock_policy, created_at) "
            "VALUES (?, ?, 'free', 'v', 'r2', 'a1', 'seed', ?, 1)")

    def refused(self, sql, args=()):
        with self.assertRaises(sqlite3.DatabaseError, msg=sql):
            self.run_sql(sql, args)

    def test_only_a_solo_set_with_the_global_lock_can_exist(self):
        self.run_sql(self.BASE, ("ok", "solo", "global_first_attempt"))
        for kind in ("daily", "season", "sprint", "duel", "tournament", "weekly", "Solo", "", "solo "):
            self.refused(self.BASE, (f"k-{kind}", kind, "global_first_attempt"))
        for policy in ("per_set", "retry", "", "GLOBAL_FIRST_ATTEMPT"):
            self.refused(self.BASE, (f"p-{policy}", "solo", policy))
        self.refused("INSERT INTO challenge_sets (id, kind, family, catalog_version, rules_version, algorithm_version, seed_ref, lock_policy, created_at) "
                     "VALUES ('f', 'solo', 'other', 'v', 'r2', 'a1', 'seed', 'global_first_attempt', 1)")

    def test_set_members_participants_and_attempts_are_keyed_and_linked(self):
        self.run_sql(self.BASE, ("s1", "solo", "global_first_attempt"))
        self.run_sql("INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) VALUES ('s1', 0, 'q1', ?)", ("a" * 64,))
        for sql in ("INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) VALUES ('s1', 0, 'q2', '" + "b" * 64 + "')",   # same position
                    "INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) VALUES ('s1', 1, 'q1', '" + "b" * 64 + "')",   # same question
                    "INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) VALUES ('s1', -1, 'q3', '" + "b" * 64 + "')",  # negative
                    "INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) VALUES ('s1', 1, 'q3', 'short')",
                    "INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) VALUES ('nope', 0, 'q1', '" + "b" * 64 + "')"):
            self.refused(sql)
        _, issue = self.fx.issue("py-free-f00", user="u1")
        principal = self.rows("SELECT id FROM scoring_principals WHERE user_id = 'u1'")[0][0]
        self.run_sql("INSERT INTO set_participants (set_id, principal_id, started_at, status) VALUES ('s1', ?, 1, 'active')", (principal,))
        self.refused("INSERT INTO set_participants (set_id, principal_id, started_at, status) VALUES ('s1', ?, 1, 'active')", (principal,))   # PK
        self.refused("INSERT INTO set_participants (set_id, principal_id, started_at, status) VALUES ('s1', 'ghost', 1, 'active')")            # FK
        self.refused("INSERT INTO set_participants (set_id, principal_id, started_at, status) VALUES ('s1', ?, 1, 'paused')", (principal,))   # enum
        self.run_sql("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', ?, 'py-free-f00', ?)", (principal, issue["issue_id"]))
        self.refused("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', ?, 'py-free-f00', ?)", (principal, issue["issue_id"]))   # PK + issue unique
        self.refused("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', ?, 'py-free-f07', ?)", (principal, issue["issue_id"]))   # one issue, one set attempt
        self.refused("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', ?, 'py-free-f09', 'no-such-issue')", (principal,))   # FK
        self.refused("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('nope', ?, 'py-free-f08', ?)", (principal, issue["issue_id"]))  # FK


if __name__ == "__main__":
    unittest.main()
