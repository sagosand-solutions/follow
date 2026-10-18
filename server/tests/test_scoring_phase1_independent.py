# SPDX-License-Identifier: AGPL-3.0-or-later
"""033 — Fas 1, oberoende bevis. Grupp S8: set är ett kontrakt utan API, och första-försökslåset kan inte kringgås.

Denna svit är skriven oberoende av K1:s steg 9-fil: den bevisar kontraktet via rutter och DB och namnger i varje test
den mutation som måste få testet att FALLA. Endast syntetiska data; inga produktionsfiler ändras. Implementerade
grupper i denna fil markeras nedan; övriga grupper (S1–S7) läggs till i samma fil.

Grupper: [x] S8   [ ] S1  [ ] S2  [ ] S3  [ ] S4  [ ] S5  [ ] S6  [ ] S7
"""
import re
import sqlite3
import unittest
from pathlib import Path

from server.tests.scoring_support import build_scoring_fixture
from server.tests.support import json_body

# 032 step 9's file is the author's; this one is the independent proof and must stay self-contained.
SET_TABLES = ("challenge_sets", "challenge_set_questions", "set_participants", "set_attempts")
SERVER = Path(__file__).resolve().parents[1]


class S8Base(unittest.TestCase):
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

    def insert_set(self, set_id="s1", kind="solo", lock_policy="global_first_attempt"):
        self.run_sql(
            "INSERT INTO challenge_sets (id, kind, family, catalog_version, rules_version, algorithm_version, "
            "seed_ref, lock_policy, created_at) VALUES (?, ?, 'free', 'v', 'r2', 'a1', 'seed', ?, 1)",
            (set_id, kind, lock_policy))


class S8NoApiTests(S8Base):
    """Mutation: allow any set field on the issue body, or add a set route -> the first two tests fall."""

    def test_issue_rejects_every_set_field_and_writes_nothing(self):
        before = self.rows("SELECT COUNT(*) FROM question_issues")
        for body in ({"set_id": "x"}, {"set": "x"}, {"kind": "daily"}, {"seed": "1"}, {"set_id": None},
                     {"set_id": "x", "extra": 1}, [], "set", 0):
            response = self.fx.post("/scoring/questions/py-free-f00/issue", body, "u1")
            self.assertEqual(response[0], 400, str(body))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM question_issues"), before, "no issue was created")

    def test_no_route_path_mentions_a_set_challenge_season_daily_duel_or_seed(self):
        app = self.fx.app
        while not hasattr(app, "routes"):              # unwrap middleware
            app = app.app
        paths = []

        def collect(routes, prefix=""):
            for route in routes:
                paths.append(prefix + getattr(route, "path", ""))
                if hasattr(route, "routes"):
                    collect(route.routes, prefix + getattr(route, "path", ""))

        collect(app.routes)
        self.assertGreater(len([p for p in paths if "scoring" in p or "leaderboards" in p]), 5, "scoring routes present")
        for path in paths:                             # mutation: add any set/challenge route -> falls
            self.assertFalse(re.search(r"set|challenge|season|daily|duel|seed", path, re.I), path)
        for method, path in (("GET", "/scoring/sets"), ("POST", "/scoring/sets"),
                             ("GET", "/challenges"), ("POST", "/scoring/challenges"), ("GET", "/scoring/daily")):
            response = self.fx.get(path) if method == "GET" else self.fx.post(path, {}, "u1")
            self.assertIn(response[0], (404, 405), path)


class S8SchemaTests(S8Base):
    """Mutation: widen the CHECK to allow daily/per_set -> test_only_solo_and_the_global_lock_can_exist falls."""

    def refused(self, sql, args=()):
        with self.assertRaises(sqlite3.DatabaseError, msg=sql):
            self.run_sql(sql, args)

    def test_only_solo_and_the_global_lock_can_exist(self):
        self.insert_set("ok")
        for kind in ("daily", "season", "sprint", "duel", "tournament", "weekly", "skiftl\u00e4ge", "", "solo "):
            self.refused("INSERT INTO challenge_sets (id, kind, family, catalog_version, rules_version, "
                         "algorithm_version, seed_ref, lock_policy, created_at) VALUES (?, ?, 'free', 'v', 'r2', 'a1', "
                         "'seed', 'global_first_attempt', 1)", (f"k-{kind}", kind))
        for policy in ("per_set", "retry", "", "GLOBAL_FIRST_ATTEMPT"):
            self.refused("INSERT INTO challenge_sets (id, kind, family, catalog_version, rules_version, "
                         "algorithm_version, seed_ref, lock_policy, created_at) VALUES (?, 'solo', 'free', 'v', 'r2', "
                         "'a1', 'seed', ?, 1)", (f"p-{policy}", policy))

    def test_set_tables_are_keyed_and_linked(self):
        self.insert_set("s1")
        self.run_sql("INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) "
                     "VALUES ('s1', 0, 'q1', ?)", ("a" * 64,))
        self.refused("INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) "
                     "VALUES ('s1', 0, 'q2', ?)", ("b" * 64,))                      # same position
        self.refused("INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) "
                     "VALUES ('s1', 1, 'q1', ?)", ("b" * 64,))                      # same question
        self.refused("INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) "
                     "VALUES ('nope', 0, 'q1', ?)", ("b" * 64,))                    # FK to set

    def test_a_set_attempt_cannot_double_claim_one_issue(self):
        self.insert_set("s1")
        _, issue = self.fx.issue("py-free-f00", user="u1")
        principal = self.rows("SELECT id FROM scoring_principals WHERE user_id = 'u1'")[0][0]
        self.run_sql("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', ?, 'py-free-f00', ?)",
                     (principal, issue["issue_id"]))
        self.refused("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', ?, 'py-free-f00', ?)",
                     (principal, issue["issue_id"]))                                # PK + UNIQUE(issue_id)
        self.refused("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', ?, 'py-free-f01', ?)",
                     (principal, issue["issue_id"]))                                # one issue = one set attempt
        self.refused("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', ?, 'py-free-f02', 'no-such-issue')",
                     (principal,))                                                  # FK to issue


class S8LockUnchangedTests(S8Base):
    """Mutation: let a set row open a second attempt (set-bypass) -> the lock test falls."""

    def test_every_fas1_issue_has_no_set(self):
        anon = self.fx.anonymous()
        self.fx.issue("py-free-f00", user="u1")
        self.fx.issue("py-q00", user="u1")
        self.fx.anon_post("/scoring/questions/py-free-f01/issue", {}, anon)
        self.assertEqual(self.rows("SELECT COUNT(*), COUNT(set_id) FROM question_issues"), [(3, 0)])

    def test_a_set_reference_never_unlocks_the_first_attempt(self):
        _, issue = self.fx.issue("py-free-f00", user="u1")
        self.insert_set("s1")
        self.run_sql("UPDATE question_issues SET set_id = 's1' WHERE id = ?", (issue["issue_id"],))
        response = self.fx.answer(issue, "Right", user="u1", key="s" * 16)
        self.assertEqual((response[0], json_body(response)["attempt"]["outcome"]), (200, "correct"))
        self.assertEqual(self.rows("SELECT origin FROM first_attempts"), [("account",)], "the set gives no other origin")
        again, _ = self.fx.issue("py-free-f00", user="u1")
        self.assertEqual((again[0], json_body(again)["error"]["code"]), (409, "locked"), "the global lock stands")

    def test_a_set_attempt_row_opens_no_second_attempt(self):
        _, issue = self.fx.issue("py-free-f01", user="u1")
        self.insert_set("s1")
        principal = self.rows("SELECT id FROM scoring_principals WHERE user_id = 'u1'")[0][0]
        self.run_sql("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', ?, 'py-free-f01', ?)",
                     (principal, issue["issue_id"]))
        self.assertEqual(self.fx.answer(issue, "Wrong", user="u1", key="t" * 16)[0], 200)
        self.assertEqual(self.fx.issue("py-free-f01", user="u1")[0][0], 409)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts WHERE question_id = 'py-free-f01'"), [(1,)])


if __name__ == "__main__":
    unittest.main()
