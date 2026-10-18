# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoring phase 1, step 5: the standings writer inside the answer transaction, board revisions and rebuild.

Negative first: an anonymous, exposed, replayed or post-freeze attempt never moves a ranked row, and a failure while writing
standings leaves no attempt behind. All data is synthetic.
"""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from server import pool
from server.scoring_standings import StandingsWriter, rebuild
from server.scoring_versions import iso_week
from server.tests.scoring_support import build_scoring_fixture
from server.tests.support import call, json_body

TABLES = ("course_units", "standings", "anonymous_units")


class Base(unittest.TestCase):
    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=("u1", "u2", "u3"), free=(("py-free", 9),))
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

    def version(self, family="free"):
        return self.rows("SELECT id FROM ranking_versions WHERE family = ?", (family,))[0][0]

    def user_answer(self, user, qid, marker="Right", key=None):
        _, issue = self.fx.issue(qid, user=user)
        response = self.fx.answer(issue, marker, user=user, key=key or (user + qid + marker).ljust(16, "x")[:32])
        self.assertEqual(response[0], 200, response)
        return issue, response

    def anon_answer(self, anon, qid, marker="Right", key="a" * 16):
        response = self.fx.anon_post(f"/scoring/questions/{qid}/issue", {}, anon)
        issue = json_body(response)
        option = next(o for o in issue["question"]["options"] if o["text"].startswith(marker))
        out = self.fx.anon_post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer", "option_id": option["option_id"],
                                                     "idempotency_key": key}, anon)
        self.assertEqual(out[0], 200, out)

    def standings(self, user=None, **where):
        sql = "SELECT scope, window_key, numerator, score_key, answered FROM standings"
        args = ()
        if user:
            sql += " WHERE principal_id = (SELECT id FROM scoring_principals WHERE user_id = ?)"
            args = (user,)
        return sorted(self.rows(sql + " ORDER BY scope, window_key", args))

    def snapshot(self):
        return {t: sorted(self.rows(f"SELECT * FROM {t}")) for t in TABLES}

    def publish(self, user, family="free", **extra):
        return self.fx.post("/scoring/preferences", {"family": family, "opt_in": True, **extra}, user)

    def week_key(self):
        return str(int(iso_week(self.fx.clock[0])[0]))


class WriterTests(Base):
    def test_a_correct_free_answer_writes_exact_units_and_both_scopes_for_both_windows(self):
        self.user_answer("u1", "py-free-f00")
        week = self.week_key()
        self.assertEqual(self.standings("u1"), sorted([
            ("course:py-free", "all", "2", "11", 1), ("course:py-free", week, "2", "11", 1),
            ("total", "all", "2", "11", 1), ("total", week, "2", "11", 1)]))          # key = (2 + 9) padded to len(27) = 2
        self.assertEqual(self.rows("SELECT window_key, course_id, units, answered FROM course_units ORDER BY window_key"),
                         sorted([("all", "py-free", 2, 1), (week, "py-free", 2, 1)]))

    def test_units_add_up_over_outcomes_and_negative_scores_are_kept(self):
        self.user_answer("u1", "py-free-f00", "Right")
        self.user_answer("u1", "py-free-f01", "Half")
        self.user_answer("u1", "py-free-f02", "Wrong")
        self.assertEqual([r for r in self.standings("u1") if r[1] == "all"],
                         [("course:py-free", "all", "2", "11", 3), ("total", "all", "2", "11", 3)])           # 2 + 1 - 1
        self.user_answer("u1", "py-free-f03", "Wrong")
        self.user_answer("u1", "py-free-f04", "Wrong")
        self.user_answer("u1", "py-free-f05", "Wrong")
        self.assertEqual([r for r in self.standings("u1") if r[0] == "total" and r[1] == "all"], [("total", "all", "-1", "08", 6)])

    def test_official_total_uses_the_lcm_numerator_and_the_course_scope_its_own_pool(self):
        self.user_answer("u1", "py-q00")                                       # K = 3, pools 10/4/4: L = 20, D = 60
        got = {(s, w): (n, k) for s, w, n, k, _ in self.standings("u1") if w == "all"}
        self.assertEqual(got, {("total", "all"): ("4", "064"), ("course:py", "all"): ("2", "12")})            # 2 * 20/10 = 4; 4 + 60 / 2 + 10

    def test_the_week_window_is_separate_from_the_all_window(self):
        self.user_answer("u1", "py-free-f00")
        first_week = self.week_key()
        self.fx.clock[0] += 8 * 86400
        self.user_answer("u1", "py-free-f01", "Half")
        second_week = self.week_key()
        self.assertNotEqual(first_week, second_week)
        by = {(s, w): (n, a) for s, w, n, _, a in self.standings("u1") if s == "total"}
        self.assertEqual(by, {("total", "all"): ("3", 2), ("total", first_week): ("2", 1), ("total", second_week): ("1", 1)})

    def test_a_replay_is_one_effect_and_a_second_attempt_at_the_question_changes_nothing(self):
        issue, _ = self.user_answer("u1", "py-free-f00", key="r" * 16)
        before = self.snapshot()
        self.assertEqual(self.fx.answer(issue, "Right", user="u1", key="r" * 16)[0], 200)             # idempotent replay
        self.assertEqual(self.snapshot(), before)
        response, again = self.fx.issue("py-free-f00", user="u1")                                      # the question is locked
        self.assertEqual(response[0], 409)
        self.assertEqual(self.snapshot(), before)

    def test_anonymous_attempts_feed_only_the_private_aggregate(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM standings"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM course_units"), [(0,)])
        self.assertEqual(self.rows("SELECT window_key, units, answered FROM anonymous_units ORDER BY window_key"),
                         sorted([("all", 2, 1), (self.week_key(), 2, 1)]))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM board_revisions"), [(0,)])

    def test_a_claim_drops_the_sources_private_aggregate_and_moved_attempts_never_score(self):
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f00")
        headers = self.fx.headers("u1")
        headers["cookie"] += "; " + anon[0]
        self.assertEqual(call(self.fx.app, "POST", "/scoring/claim", headers=headers, body=b"{}")[0], 200)
        self.assertEqual(self.snapshot(), {"course_units": [], "standings": [], "anonymous_units": []})

    def test_an_exposed_attempt_never_scores(self):
        _, mine = self.fx.issue("py-free-f00", user="u1")
        anon = self.fx.anonymous()
        self.fx.anon_post("/scoring/questions/py-free-f00/issue", {}, anon)
        headers = self.fx.headers("u1")
        headers["cookie"] += "; " + anon[0]
        call(self.fx.app, "POST", "/scoring/claim", headers=headers, body=b"{}")
        self.assertEqual(self.fx.answer(mine, "Right", user="u1", key="x" * 16)[0], 200)
        self.assertEqual(self.rows("SELECT origin FROM first_attempts"), [("exposed",)])
        self.assertEqual(self.snapshot(), {"course_units": [], "standings": [], "anonymous_units": []})

    def test_an_answer_after_the_version_is_frozen_writes_nothing(self):
        _, issue = self.fx.issue("py-free-f00", user="u1")
        self.run_sql("UPDATE ranking_versions SET status = 'frozen', frozen_at = 1, freeze_reason = 'manual' WHERE family = 'free'")
        self.assertEqual(self.fx.answer(issue, "Right", user="u1", key="y" * 16)[0], 409)
        self.assertEqual(self.snapshot(), {"course_units": [], "standings": [], "anonymous_units": []})

    def test_a_failure_while_writing_standings_leaves_no_attempt_and_the_issue_open(self):
        _, issue = self.fx.issue("py-free-f00", user="u1")
        with mock.patch.object(StandingsWriter, "apply", side_effect=RuntimeError("boom")):
            try:
                response = self.fx.answer(issue, "Right", user="u1", key="z" * 16)
                self.assertGreaterEqual(response[0], 500)
            except RuntimeError:
                pass
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM question_issues WHERE consumed_at IS NOT NULL"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM submission_receipts"), [(0,)])
        self.assertEqual(self.fx.answer(issue, "Right", user="u1", key="z" * 16)[0], 200, "the same issue can still be answered")


class WriterDirectTests(Base):
    """The writer called with crafted attempts, for cases the HTTP flow cannot produce (a frozen version, a clock that steps back)."""

    def attempt(self, principal_id, qid, units=2, at=None, origin="account"):
        from server.scoring_repo import Attempt
        catalog = self.fx.catalogs["free"]
        pq = catalog.questions[qid]
        return Attempt(principal_id, qid, "free", pq.course_id, catalog.catalog_version, "r2", "rev", "o", "correct", units,
                       at if at is not None else self.fx.clock[0], origin)

    def apply(self, writer, attempt, user=True):
        from server.scoring_repo import ScoringRepository
        db = self.fx.db()
        try:
            with db.transaction(immediate=True) as conn:
                principal = ScoringRepository(db).principal(attempt.principal_id)
                writer.apply(conn, attempt, principal, False)
        finally:
            db.close()

    def principal_id(self):
        self.user_answer("u3", "py-free-f08")                       # creates u3's principal (and one real attempt)
        return self.rows("SELECT id FROM scoring_principals WHERE user_id = 'u3'")[0][0]

    def test_a_frozen_version_takes_no_write_from_the_writer_itself(self):
        pid = self.principal_id()
        self.run_sql("UPDATE ranking_versions SET status = 'frozen', frozen_at = 1, freeze_reason = 'manual' WHERE family = 'free'")
        before = self.snapshot()
        self.apply(StandingsWriter(), self.attempt(pid, "py-free-f00"))
        self.assertEqual(self.snapshot(), before)

    def test_the_latest_accepted_time_never_moves_back(self):
        pid = self.principal_id()
        writer = StandingsWriter()
        late = self.fx.clock[0] + 1000
        self.apply(writer, self.attempt(pid, "py-free-f00", at=late))
        self.apply(writer, self.attempt(pid, "py-free-f01", at=late - 500))
        self.assertEqual(self.rows("SELECT last_accepted_at FROM standings WHERE scope = 'total' AND window_key = 'all' AND principal_id = ?", (pid,)),
                         [(late,)])


class BoardRevisionTests(Base):
    def revisions(self):
        return sorted(self.rows("SELECT window_key, revision FROM board_revisions WHERE version_id = ?", (self.version("free"),)))

    def test_an_unpublished_answer_does_not_touch_any_board(self):
        self.user_answer("u1", "py-free-f00")
        self.assertEqual(self.revisions(), [])

    def test_a_published_answer_moves_both_windows_by_one(self):
        self.user_answer("u1", "py-free-f00")
        self.assertEqual(self.publish("u1")[0], 200)
        base = dict(self.revisions())
        self.assertEqual(set(base), {"all", self.week_key()})
        self.user_answer("u1", "py-free-f01")
        self.assertEqual({k: v - base[k] for k, v in dict(self.revisions()).items()}, {"all": 1, self.week_key(): 1})

    def test_publishing_unpublishing_and_renaming_each_move_the_boards_the_reader_stands_on(self):
        self.user_answer("u1", "py-free-f00")
        seen = [dict(self.revisions())]
        for call_ in (lambda: self.publish("u1"), lambda: self.publish("u1", nickname="Quiet Reader"),
                      lambda: self.fx.post("/scoring/preferences", {"family": "free", "opt_in": False}, "u1")):
            self.assertEqual(call_()[0], 200)
            seen.append(dict(self.revisions()))
        for before, after in zip(seen, seen[1:]):
            self.assertTrue(all(after[k] > before.get(k, -1) for k in after), (before, after))

    def test_a_reader_with_no_standing_moves_no_board_when_they_publish(self):
        self.assertEqual(self.publish("u2")[0], 200)
        self.assertEqual(self.revisions(), [])

    def test_deleting_the_account_moves_the_boards_before_the_rows_go(self):
        self.user_answer("u1", "py-free-f00")
        self.user_answer("u2", "py-free-f00")
        self.publish("u1")
        self.publish("u2")
        before = dict(self.revisions())
        response = call(self.fx.app, "POST", "/account/delete", headers=self.fx.headers("u1"), body=b"{}")
        self.assertEqual(response[0], 200)
        after = dict(self.revisions())
        self.assertTrue(all(after[k] > before[k] for k in before))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM standings WHERE principal_id IN (SELECT id FROM scoring_principals)"),
                         self.rows("SELECT COUNT(*) FROM standings"), "no standing without a principal")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM standings WHERE principal_id NOT IN "
                                   "(SELECT id FROM scoring_principals WHERE user_id = 'u2')"), [(0,)])


class RebuildTests(Base):
    def populate(self):
        self.user_answer("u1", "py-free-f00", "Right")
        self.user_answer("u1", "py-free-f01", "Wrong")
        self.user_answer("u2", "py-free-f00", "Half")
        self.user_answer("u1", "py-q00", "Right")
        self.user_answer("u1", "py-q01", "Half")
        self.fx.clock[0] += 9 * 86400
        self.user_answer("u2", "py-free-f02", "Right")
        self.user_answer("u1", "py-free-f03", "Wrong")
        anon = self.fx.anonymous()
        self.anon_answer(anon, "py-free-f04")
        self.anon_answer(anon, "py-free-f05", "Wrong", "b" * 16)

    def rebuilt(self, family):
        db = self.fx.db()
        try:
            with db.transaction(immediate=True) as conn:
                return rebuild(conn, self.version(family))
        finally:
            db.close()

    def test_a_rebuild_reproduces_the_incremental_tables_exactly(self):
        self.populate()
        before = self.snapshot()
        self.assertGreater(len(before["standings"]), 8)
        self.assertGreater(len(before["anonymous_units"]), 0)
        for family in ("free", "official"):
            self.rebuilt(family)
        self.assertEqual(self.snapshot(), before)

    def test_a_rebuild_repairs_damage_and_ignores_attempts_that_may_not_score(self):
        self.populate()
        before = self.snapshot()
        self.run_sql("UPDATE standings SET score_key = '00', numerator = '-9'")
        self.run_sql("DELETE FROM course_units WHERE rowid % 2 = 0")
        self.run_sql("UPDATE anonymous_units SET units = 99")
        self.run_sql("DROP TRIGGER first_attempts_only_claim_moves")
        self.run_sql("UPDATE first_attempts SET origin = 'legacy' WHERE question_id = 'py-free-f03'")
        for family in ("free", "official"):
            self.rebuilt(family)
        after = self.snapshot()
        self.assertNotEqual(after, before, "the legacy attempt no longer scores")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM standings WHERE numerator = '-9'"), [(0,)])
        self.assertEqual(self.rows("SELECT SUM(units) FROM anonymous_units WHERE window_key = 'all'"), [(1,)])      # 2 - 1
        legacy_free = [r for r in after["course_units"] if r[2] == self.rows("SELECT id FROM scoring_principals WHERE user_id = 'u1'")[0][0]]
        self.assertEqual(sum(r[4] for r in legacy_free if r[1] == "all" and r[3] == "py-free"), 1, "u1: +2 - 1 and the legacy -1 is gone")

    def test_a_rebuild_moves_every_board_and_never_touches_a_frozen_version(self):
        self.populate()
        self.publish("u1")
        boards = lambda: dict(self.rows("SELECT window_key, revision FROM board_revisions WHERE version_id = ?", (self.version("free"),)))
        before = boards()
        self.rebuilt("free")
        after = boards()
        self.assertEqual({k: after[k] - before[k] for k in before}, {k: 1 for k in before}, (before, after))
        pool.freeze(self.fx.db(), self.version("free"), reason_code="season-end", now=self.fx.clock[0])
        frozen = self.snapshot()
        with self.assertRaises(ValueError):
            self.rebuilt("free")
        self.assertEqual(self.snapshot(), frozen)

    def test_the_command_line_rebuild(self):
        self.populate()
        before = self.snapshot()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = pool.main(["--db", self.fx.path, "rebuild", "--version", self.version("free")])
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertIn("rebuilt", out.getvalue())
        self.assertEqual(self.snapshot(), before)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(pool.main(["--db", self.fx.path, "rebuild", "--version", "nope"]), 2)


if __name__ == "__main__":
    unittest.main()
