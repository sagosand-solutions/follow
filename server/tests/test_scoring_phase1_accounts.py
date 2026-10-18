# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoring phase 1, step 2: attempt origin, account-only publishing and the claim's exposure union.

Negative first: an anonymous or claimed attempt, a stale legacy preference or an issue given before a claim never ends up
as a ranked row. All data is synthetic.
"""
import unittest

from server.tests.scoring_support import build_scoring_fixture
from server.tests.support import call, json_body


class Base(unittest.TestCase):
    users = ("u1", "u2")

    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=self.users, free=(("py-free", 9),))
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

    def user_answer(self, user, qid, marker="Right", key=None):
        _, issue = self.fx.issue(qid, user=user)
        response = self.fx.answer(issue, marker, user=user, key=key or (user + qid).ljust(16, "x")[:32])
        self.assertEqual(response[0], 200, response)
        return issue

    def anon_issue(self, anon, qid):
        response = self.fx.anon_post(f"/scoring/questions/{qid}/issue", {}, anon)
        self.assertEqual(response[0], 200, response)
        return json_body(response)

    def anon_answer(self, anon, qid, marker="Right", key="a" * 16, issue=None):
        issue = issue or self.anon_issue(anon, qid)
        option = next(o for o in issue["question"]["options"] if o["text"].startswith(marker))
        return self.fx.anon_post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer",
                                                      "option_id": option["option_id"], "idempotency_key": key}, anon)

    def claim(self, user, anon):
        headers = self.fx.headers(user)
        headers["cookie"] += "; " + anon[0]
        return call(self.fx.app, "POST", "/scoring/claim", headers=headers, body=b"{}")

    def origins(self):
        return {q: o for q, o in self.rows("SELECT question_id, origin FROM first_attempts")}

    def exposure(self, user, qid):
        return self.rows("SELECT rank_blocking, via FROM question_exposure WHERE question_id = ? AND principal_id = "
                         "(SELECT id FROM scoring_principals WHERE user_id = ?)", (qid, user))


class OriginTests(Base):
    def test_a_new_attempt_carries_its_origin_from_the_principal(self):
        self.user_answer("u1", "py-free-f00")
        anon = self.fx.anonymous()
        self.assertEqual(self.anon_answer(anon, "py-free-f01")[0], 200)
        self.assertEqual(self.origins(), {"py-free-f00": "account", "py-free-f01": "anonymous"})

    def test_an_account_issue_is_rankable_and_an_anonymous_one_is_not(self):
        self.user_answer("u1", "py-free-f00")
        anon = self.fx.anonymous()
        self.anon_issue(anon, "py-free-f01")
        self.assertEqual(self.rows("SELECT q.question_id, q.rank_eligible, e.rank_blocking FROM question_issues q "
                                   "JOIN question_exposure e ON e.principal_id = q.principal_id AND e.question_id = q.question_id "
                                   "ORDER BY q.question_id"), [("py-free-f00", 1, 0), ("py-free-f01", 0, 1)])


class PublishingTests(Base):
    def preference(self, family, user="u1", **extra):
        return self.fx.post("/scoring/preferences", {"family": family, "opt_in": True, **extra}, user)

    def test_anonymous_never_publishes_in_any_family(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00")
        for family in ("free", "official"):
            self.assertEqual(self.fx.anon_post("/scoring/preferences", {"family": family, "opt_in": True}, anon)[0], 401)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM leaderboard_preferences"), [(0,)])

    def test_a_stale_anonymous_opt_in_row_never_lists_even_with_a_valid_alias(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00")
        self.run_sql("INSERT INTO leaderboard_preferences (principal_id, family, opted_in_at, alias, nickname, moderation_state) "
                     "SELECT id, 'free', 1, 'Reader-old', 'Old Nick', 'ok' FROM scoring_principals WHERE user_id IS NULL")
        board = json_body(self.fx.get("/leaderboards", user=None, query=b"family=free&scope=total&window=all"))
        self.assertEqual((board["participants"], board["entries"]), (0, []))

    def test_only_rankable_account_attempts_are_listed(self):
        self.user_answer("u1", "py-free-f00")
        self.user_answer("u1", "py-free-f01", "Wrong")
        self.assertEqual(self.preference("free")[0], 200)
        query = b"family=free&scope=total&window=all"
        listed = lambda: json_body(self.fx.get("/leaderboards", user=None, query=query))["entries"][0]["answered"]
        self.assertEqual(listed(), 2)
        self.run_sql("DROP TRIGGER first_attempts_only_claim_moves")      # test-only: the origin is otherwise immutable
        for origin in ("exposed", "anonymous", "legacy"):
            self.run_sql("UPDATE first_attempts SET origin = ? WHERE question_id = 'py-free-f01'", (origin,))
            self.assertEqual(listed(), 1, origin)
        self.run_sql("UPDATE first_attempts SET origin = 'account' WHERE question_id = 'py-free-f01'")

    def test_a_suspended_user_is_not_listed(self):
        self.user_answer("u1", "py-free-f00")
        self.assertEqual(self.preference("free")[0], 200)
        self.run_sql("UPDATE users SET status = 'deleted' WHERE id = 'u1'")
        board = json_body(self.fx.get("/leaderboards", user=None, query=b"family=free&scope=total&window=all"))
        self.assertEqual(board["participants"], 0)


class ClaimTests(Base):
    def test_claimed_anonymous_attempts_keep_their_origin_and_never_become_ranked(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00")
        self.user_answer("u1", "py-free-f01")
        self.assertEqual(self.claim("u1", anon)[0], 200)
        self.assertEqual(self.origins(), {"py-free-f00": "anonymous", "py-free-f01": "account"})
        self.assertEqual(self.fx.post("/scoring/preferences", {"family": "free", "opt_in": True}, "u1")[0], 200)
        board = json_body(self.fx.get("/leaderboards", user=None, query=b"family=free&scope=total&window=all"))
        self.assertEqual([e["answered"] for e in board["entries"]], [1], "only the account's own attempt is ranked")

    def test_issue_claim_issue_answer_is_exposed_not_account(self):
        anon = self.fx.anonymous()
        self.anon_issue(anon, "py-free-f02")                     # seen anonymously, never answered
        self.assertEqual(self.claim("u1", anon)[0], 200)
        self.assertEqual(self.exposure("u1", "py-free-f02"), [(1, "claim")])
        self.user_answer("u1", "py-free-f02")
        self.assertEqual(self.origins(), {"py-free-f02": "exposed"})
        self.assertEqual(self.rows("SELECT rank_eligible FROM question_issues WHERE principal_id = "
                                   "(SELECT id FROM scoring_principals WHERE user_id = 'u1')"), [(0,)])

    def test_an_issue_given_before_the_claim_can_never_yield_a_ranked_attempt(self):
        _, mine = self.fx.issue("py-free-f03", user="u1")        # rank-eligible when given
        self.assertEqual(self.rows("SELECT rank_eligible FROM question_issues WHERE id = ?", (mine["issue_id"],)), [(1,)])
        anon = self.fx.anonymous()
        self.anon_issue(anon, "py-free-f03")
        self.assertEqual(self.claim("u1", anon)[0], 200)
        self.assertEqual(self.rows("SELECT rank_eligible FROM question_issues WHERE id = ?", (mine["issue_id"],)), [(0,)])
        self.assertEqual(self.fx.answer(mine, "Right", user="u1", key="z" * 16)[0], 200)
        self.assertEqual(self.origins(), {"py-free-f03": "exposed"})

    def test_a_pre_claim_issue_is_exposed_even_if_the_stored_flag_was_still_one(self):
        _, mine = self.fx.issue("py-free-f04", user="u1")
        anon = self.fx.anonymous()
        self.anon_issue(anon, "py-free-f04")
        self.assertEqual(self.claim("u1", anon)[0], 200)
        self.run_sql("UPDATE question_issues SET rank_eligible = 1 WHERE id = ?", (mine["issue_id"],))    # defence in depth at answer time
        self.assertEqual(self.fx.answer(mine, "Right", user="u1", key="y" * 16)[0], 200)
        self.assertEqual(self.origins(), {"py-free-f04": "exposed"})

    def test_exposure_merges_upward_only_in_every_combination(self):
        for target, source, expected in ((0, 1, 1), (1, 0, 1), (1, 1, 1), (0, 0, 0)):
            with self.subTest(target=target, source=source):
                fx, tmp = build_scoring_fixture(users=("u1",), free=(("py-free", 9),))
                self.addCleanup(tmp.cleanup)
                self.fx = fx
                _, issue = fx.issue("py-free-f05", user="u1")                      # target exposure 0
                if target:
                    self.run_sql("UPDATE question_exposure SET rank_blocking = 1, via = 'content'")
                anon = fx.anonymous()
                self.anon_issue(anon, "py-free-f05")                               # source exposure 1
                if not source:
                    self.run_sql("DROP TRIGGER question_exposure_is_monotone")
                    self.run_sql("UPDATE question_exposure SET rank_blocking = 0 WHERE principal_id IN "
                                 "(SELECT id FROM scoring_principals WHERE user_id IS NULL)")
                    self.run_sql("DELETE FROM question_issues WHERE principal_id IN (SELECT id FROM scoring_principals WHERE user_id IS NULL)")
                self.assertEqual(self.claim("u1", anon)[0], 200)
                self.assertEqual(self.exposure("u1", "py-free-f05")[0][0], expected)

    def test_the_union_keeps_the_earliest_first_exposure_and_a_second_claim_is_refused(self):
        anon = self.fx.anonymous()
        self.anon_issue(anon, "py-free-f06")
        first = self.rows("SELECT first_exposed_at FROM question_exposure")[0][0]
        self.fx.clock[0] += 1000
        self.user_answer("u1", "py-free-f06")
        self.assertEqual(self.claim("u1", anon)[0], 200)
        self.assertEqual(self.rows("SELECT first_exposed_at FROM question_exposure WHERE principal_id = "
                                   "(SELECT id FROM scoring_principals WHERE user_id = 'u1')"), [(first,)])
        again = self.claim("u1", anon)
        self.assertEqual((again[0], json_body(again)["error"]["code"]), (409, "claim_conflict"))

    def test_an_older_exposure_on_the_target_is_kept_while_the_block_is_upgraded(self):
        _, mine = self.fx.issue("py-free-f01", user="u1")                      # target exposed first, not blocking
        t0 = self.rows("SELECT first_exposed_at FROM question_exposure")[0][0]
        self.fx.clock[0] += 500
        anon = self.fx.anonymous()
        self.anon_issue(anon, "py-free-f01")                                   # source exposed later, blocking
        self.assertEqual(self.claim("u1", anon)[0], 200)
        self.assertEqual(self.rows("SELECT rank_blocking, via, first_exposed_at FROM question_exposure WHERE principal_id = "
                                   "(SELECT id FROM scoring_principals WHERE user_id = 'u1')"), [(1, "claim", t0)])

    def test_the_union_also_covers_issues_and_attempts_that_have_no_exposure_row(self):
        anon = self.fx.anonymous()
        self.anon_issue(anon, "py-free-f01")
        self.anon_answer(anon, "py-free-f02")
        self.run_sql("INSERT INTO scoring_purge_gate (open) VALUES (1)")
        self.run_sql("DELETE FROM question_exposure")
        self.run_sql("DELETE FROM scoring_purge_gate")
        self.run_sql("DELETE FROM question_issues WHERE question_id = 'py-free-f02'")      # the attempt is the only trace of f02
        self.assertEqual(self.claim("u1", anon)[0], 200)
        self.assertEqual(self.exposure("u1", "py-free-f01"), [(1, "claim")])
        self.assertEqual(self.exposure("u1", "py-free-f02"), [(1, "claim")])

    def test_an_open_issue_without_the_eligibility_flag_is_exposed_even_with_no_blocking_exposure(self):
        _, issue = self.fx.issue("py-free-f01", user="u1")
        self.run_sql("UPDATE question_issues SET rank_eligible = 0 WHERE id = ?", (issue["issue_id"],))   # e.g. an issue given before migration 7
        self.assertEqual(self.fx.answer(issue, "Right", user="u1", key="l" * 16)[0], 200)
        self.assertEqual(self.origins(), {"py-free-f01": "exposed"})

    def test_the_accounts_own_earlier_attempt_survives_the_claim_and_stays_account_origin(self):
        self.user_answer("u1", "py-free-f07")
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f07", "Wrong")
        self.assertEqual(self.claim("u1", anon)[0], 200)
        self.assertEqual(self.origins(), {"py-free-f07": "account"})
        self.assertEqual(self.rows("SELECT outcome FROM first_attempts"), [("correct",)])

    def test_replay_after_a_claim_returns_the_original_attempt_without_a_new_row(self):
        _, issue = self.fx.issue("py-free-f08", user="u1")
        first = self.fx.answer(issue, "Right", user="u1", key="r" * 16)
        anon = self.fx.anonymous()
        self.assertEqual(self.claim("u1", anon)[0], 200)
        again = self.fx.answer(issue, "Right", user="u1", key="r" * 16)
        self.assertEqual((first[0], again[0], json_body(again)["replayed"]), (200, 200, True))
        self.assertEqual(self.origins(), {"py-free-f08": "account"})


if __name__ == "__main__":
    unittest.main()
