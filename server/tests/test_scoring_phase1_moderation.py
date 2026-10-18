# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoring phase 1, step 8: the operator hide tool and the versioned alias blocklist.

Negative first: a dry run changes nothing, a hide survives renaming and opting in again, a refused request leaves no trace, and
a missing or malformed blocklist stops start-up instead of emptying silently. All data is synthetic.
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from server import alias_blocklist, moderate
from server.app import Services, create_app
from server.models import SetupError
from server.tests.scoring_support import build_scoring_fixture
from server.tests.support import json_body

QUERY = b"family=free&scope=total&window=all"


class BlocklistTests(unittest.TestCase):
    def test_whole_words_after_normalisation_are_blocked_and_innocent_names_are_not(self):
        for blocked in ("Admin", "ADMIN", "Site Admin", "the-moderator", "Аdmin", "ａｄｍｉｎ", "4dmin", "Idi0t", "Support Team"):
            self.assertTrue(alias_blocklist.is_blocked(blocked), blocked)
        for fine in ("Modern", "Assistant", "Rootbeer", "Sysadmin", "Systematic", "Statistics", "Quiet Reader", "Ada", "Scunthorpe",
                     "Mod2", "Administrate", "Followers", "Moderation"):
            self.assertFalse(alias_blocklist.is_blocked(fine), fine)

    def test_a_missing_or_malformed_list_is_a_setup_error_never_an_empty_list(self):
        good = {"version": 1, "terms": ["admin"]}
        with tempfile.TemporaryDirectory() as tmp:
            def attempt(content, name="b.json"):
                path = Path(tmp, name)
                if content is not None:
                    path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())
                alias_blocklist.load.cache_clear()
                try:
                    return alias_blocklist.load(str(path))
                finally:
                    alias_blocklist.load.cache_clear()
            self.assertEqual(attempt(good), frozenset({"admin"}))
            for bad in (None, b"not json", b"\xff\xfe", {"version": 2, "terms": ["admin"]}, {"terms": ["admin"]}, {"version": 1},
                        {"version": 1, "terms": []}, {"version": 1, "terms": ["admin", "admin"]}, {"version": 1, "terms": ["Admin"]},
                        {"version": 1, "terms": ["a"]}, {"version": 1, "terms": [3]}, {"version": 1, "terms": ["x" * 41]},
                        {"version": 1, "terms": ["admin"], "extra": 1}, [1], b"x" * (alias_blocklist.MAX_BYTES + 1)):
                with self.assertRaises(SetupError, msg=str(bad)[:40]):
                    attempt(bad, name="bad.json" if bad is not None else "absent.json")

    def test_the_shipped_list_is_valid_and_start_up_refuses_a_broken_one(self):
        self.assertGreater(len(alias_blocklist.load()), 5)
        fx, tmp = build_scoring_fixture(users=("u1",))
        self.addCleanup(tmp.cleanup)
        with tempfile.TemporaryDirectory() as broken:
            Path(broken, "b.json").write_text("{}", encoding="utf-8")
            alias_blocklist.load.cache_clear()
            try:
                with mock.patch.object(alias_blocklist, "BLOCKLIST_PATH", Path(broken, "b.json")):
                    with self.assertRaises(SetupError):
                        create_app(fx.settings, Services(open_database=lambda: fx.db(), catalog=fx.content_catalog, scoring=fx.scoring,
                                                         clock=lambda: fx.clock[0]))
            finally:
                alias_blocklist.load.cache_clear()

    def test_the_preference_route_refuses_a_blocked_alias_and_accepts_a_neighbour(self):
        fx, tmp = build_scoring_fixture(users=("u1",))
        self.addCleanup(tmp.cleanup)
        for name, status in (("Admin", 400), ("Аdmin", 400), ("4dmin", 400), ("Modern Reader", 200)):
            self.assertEqual(fx.post("/scoring/preferences", {"family": "free", "opt_in": True, "nickname": name}, "u1")[0], status, name)


class ModerationBase(unittest.TestCase):
    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=("u1", "u2", "u3"), free=(("py-free", 9),))
        self.addCleanup(tmp.cleanup)
        for user, name in (("u1", "Ada"), ("u2", "Bob"), ("u3", "Cy")):
            for qid in ("py-free-f00", "py-q00") if user == "u1" else ("py-free-f00",):
                _, issue = self.fx.issue(qid, user=user)
                self.fx.answer(issue, "Right", user=user, key=(user + qid).ljust(16, "k")[:32])
            self.fx.post("/scoring/preferences", {"family": "free", "opt_in": True, "nickname": name}, user)
        self.fx.post("/scoring/preferences", {"family": "official", "opt_in": True, "nickname": "Ada"}, "u1")
        self.u1 = self.rows("SELECT id FROM scoring_principals WHERE user_id = 'u1'")[0][0]

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

    def db(self):
        db = self.fx.db()
        self.addCleanup(db.close)
        return db

    def hide(self, principal=None, family="free", write=False, hidden=True, **over):
        kw = dict(hidden=hidden, reason="offensive-alias", operator="op-1", now=self.fx.clock[0] + 5, write=write)
        kw.update(over)
        return moderate.set_hidden(self.db(), principal or self.u1, family, **kw)

    def board(self, user=None):
        headers = self.fx.headers(user, csrf=False, origin=False) if user else {}
        from server.tests.support import call
        return json_body(call(self.fx.app, "GET", "/leaderboards", headers=headers, query=QUERY))

    def revisions(self, family):
        return sorted(self.rows("SELECT b.window_key, b.revision FROM board_revisions b JOIN ranking_versions v ON v.id = b.version_id "
                                "WHERE v.family = ?", (family,)))

    def state(self, family="free"):
        return self.rows("SELECT moderation_state FROM leaderboard_preferences WHERE principal_id = ? AND family = ?", (self.u1, family))


class HideTests(ModerationBase):
    def test_a_dry_run_is_the_default_and_changes_nothing(self):
        before = (self.state(), self.revisions("free"), self.rows("SELECT COUNT(*) FROM scoring_moderation_audit"), self.board()["participants"])
        message = self.hide()
        self.assertIn("dry run", message)
        self.assertEqual((self.state(), self.revisions("free"), self.rows("SELECT COUNT(*) FROM scoring_moderation_audit"),
                          self.board()["participants"]), before)

    def test_a_write_hides_audits_moves_only_that_familys_boards_and_keeps_the_history(self):
        attempts = self.rows("SELECT COUNT(*) FROM first_attempts")
        free_before, official_before = self.revisions("free"), self.revisions("official")
        self.assertEqual(self.board()["participants"], 3)
        self.assertIn("now hidden", self.hide(write=True))
        self.assertEqual(self.state(), [("hidden",)])
        self.assertEqual(self.state("official"), [("ok",)], "the other family is untouched")
        self.assertEqual(self.rows("SELECT operator, principal_id, family, action, reason_code, dry_run FROM scoring_moderation_audit"),
                         [("op-1", self.u1, "free", "hide", "offensive-alias", 0)])
        board = self.board()
        self.assertEqual((sorted(e["alias"] for e in board["entries"]), board["participants"]), (["Bob", "Cy"], 2))   # a tie: order is by an internal id
        self.assertEqual([(w, r - dict(free_before)[w]) for w, r in self.revisions("free")], [(w, 1) for w, _ in free_before])
        self.assertEqual(self.revisions("official"), official_before)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts"), attempts, "the private history stays")
        me = json_body(self.fx.get("/scoring/me", user="u1", query=b"family=free"))
        self.assertEqual((me["published"], me["moderation"], me["own_cursor"]), (False, "hidden", None))
        self.assertGreater(me["total"]["answered"], 0)

    def test_a_hide_survives_renaming_and_opting_out_and_in_again(self):
        self.hide(write=True)
        for body in ({"family": "free", "opt_in": True, "nickname": "Fresh Name"}, {"family": "free", "opt_in": False},
                     {"family": "free", "opt_in": True}):
            self.assertEqual(self.fx.post("/scoring/preferences", body, "u1")[0], 200)
            self.assertNotIn("Fresh Name", [e["alias"] for e in self.board()["entries"]])
            self.assertEqual(self.state(), [("hidden",)])
        self.assertEqual(self.board()["participants"], 2)

    def test_unhide_restores_and_is_audited_and_a_repeat_is_a_no_op(self):
        self.hide(write=True)
        self.assertIn("already hidden", self.hide(write=True))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM scoring_moderation_audit"), [(1,)], "no audit row for a no-op")
        self.assertIn("now ok", self.hide(write=True, hidden=False, reason="error-correction"))
        self.assertEqual(sorted(e["alias"] for e in self.board()["entries"]), ["Ada", "Bob", "Cy"])
        self.assertEqual([r[0] for r in self.rows("SELECT action FROM scoring_moderation_audit ORDER BY id")], ["hide", "unhide"])
        self.assertIn("already ok", self.hide(write=True, hidden=False))

    def test_hiding_in_advance_keeps_a_later_opt_in_off_the_lists(self):
        u4 = self.rows("SELECT id FROM scoring_principals WHERE user_id = 'u3'")[0][0]
        self.run_sql("DELETE FROM leaderboard_preferences WHERE principal_id = ? AND family = 'official'", (u4,))
        self.hide(principal=u4, family="official", write=True)
        self.assertEqual(self.rows("SELECT opted_in_at, moderation_state FROM leaderboard_preferences WHERE principal_id = ? AND family = 'official'", (u4,)),
                         [(None, "hidden")])
        self.fx.post("/scoring/preferences", {"family": "official", "opt_in": True, "nickname": "Latecomer"}, "u3")
        self.assertEqual(self.rows("SELECT moderation_state FROM leaderboard_preferences WHERE principal_id = ? AND family = 'official'", (u4,)), [("hidden",)])

    def test_refused_requests_leave_no_trace(self):
        anonymous = self.fx.anonymous()
        self.fx.anon_post("/scoring/questions/py-free-f05/issue", {}, anonymous)
        anon_id = self.rows("SELECT id FROM scoring_principals WHERE user_id IS NULL")[0][0]
        before = (self.rows("SELECT * FROM leaderboard_preferences ORDER BY principal_id, family"), self.revisions("free"))
        for kwargs in ({"principal": "nope"}, {"principal": anon_id}, {"family": "premium"}, {"reason": "because"}, {"reason": ""},
                       {"operator": "someone@example.org"}, {"operator": ""}, {"principal": "bad id!"}, {"principal": "x" * 65}):
            with self.assertRaises(moderate.ModerationError, msg=str(kwargs)):
                self.hide(write=True, **kwargs)
        u2 = self.rows("SELECT id FROM scoring_principals WHERE user_id = 'u2'")[0][0]
        self.assertIn("already ok", self.hide(principal=u2, family="official", hidden=False, write=True), "no row means nothing to unhide")
        self.assertEqual((self.rows("SELECT * FROM leaderboard_preferences ORDER BY principal_id, family"), self.revisions("free")), before)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM scoring_moderation_audit"), [(0,)])

    def test_a_failure_while_auditing_rolls_the_whole_change_back(self):
        self.run_sql("CREATE TRIGGER fail_audit BEFORE INSERT ON scoring_moderation_audit BEGIN SELECT RAISE(ABORT, 'boom'); END")
        before = self.revisions("free")
        with self.assertRaises(Exception):
            self.hide(write=True)
        self.assertEqual((self.state(), self.revisions("free")), ([("ok",)], before))

    def test_the_audit_holds_no_personal_data_columns(self):
        columns = [r[1] for r in self.rows("PRAGMA table_info(scoring_moderation_audit)")]
        self.assertEqual(columns, ["id", "at", "operator", "principal_id", "family", "action", "reason_code", "dry_run"])


class FindAndCliTests(ModerationBase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = moderate.main(["--db", self.fx.path, *argv], clock=lambda: self.fx.clock[0] + 9)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_find_is_read_only_exact_and_case_insensitive(self):
        before = self.rows("SELECT * FROM leaderboard_preferences ORDER BY principal_id, family")
        self.assertEqual(moderate.find(self.db(), "free", "aDa"), [(self.u1, True, "ok")])
        self.assertEqual(moderate.find(self.db(), "free", "Ad"), [])
        self.assertEqual(moderate.find(self.db(), "official", "Bob"), [])
        for bad in (("premium", "Ada"), ("free", ""), ("free", "x" * 41)):
            with self.assertRaises(moderate.ModerationError):
                moderate.find(self.db(), *bad)
        self.assertEqual(self.rows("SELECT * FROM leaderboard_preferences ORDER BY principal_id, family"), before)

    def test_command_line_defaults_to_a_dry_run_and_writes_only_with_the_flag(self):
        base = ("hide", "--principal", self.u1, "--family", "free", "--reason", "abuse", "--operator", "op-1")
        code, out, err = self.run_cli(*base)
        self.assertEqual(code, 0, err)
        self.assertNotIn("refused", err)          # a stray ResourceWarning from another test may share stderr; only refusals matter here
        self.assertIn("dry run", out)
        self.assertEqual(self.state(), [("ok",)])
        self.assertEqual(self.run_cli(*base, "--dry-run")[0], 0)
        self.assertEqual(self.state(), [("ok",)])
        code, out, _ = self.run_cli(*base, "--write")
        self.assertEqual((code, out.strip()), (0, "hide: free is now hidden"))
        self.assertEqual(self.state(), [("hidden",)])
        self.assertEqual(self.run_cli(*base, "--write", "--dry-run")[0], 2, "the two modes exclude each other")
        code, out, _ = self.run_cli("find", "--family", "free", "--alias", "Ada")
        self.assertEqual((code, out.strip()), (0, f"{self.u1}\tpublished=yes\tmoderation=hidden"))
        self.assertEqual(self.run_cli("find", "--family", "free", "--alias", "Nobody")[1].strip(), "no match")

    def test_command_line_refusals_exit_two_and_a_missing_database_is_not_created(self):
        code, out, err = self.run_cli("hide", "--principal", "nope", "--family", "free", "--reason", "abuse", "--operator", "op-1", "--write")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("refused:", err)
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(moderate.main(["--db", tmp + "/nope.db", "find", "--family", "free", "--alias", "Ada"]), 2)
            self.assertFalse(Path(tmp, "nope.db").exists())


if __name__ == "__main__":
    unittest.main()
