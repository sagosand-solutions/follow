# SPDX-License-Identifier: AGPL-3.0-or-later
"""033 — Fas 1, oberoende bevis. Grupp S8: set är ett kontrakt utan API, och första-försökslåset kan inte kringgås.

Denna svit är skriven oberoende av K1:s steg 9-fil: den bevisar kontraktet via rutter och DB och namnger i varje test
den mutation som måste få testet att FALLA. Endast syntetiska data; inga produktionsfiler ändras. Implementerade
grupper i denna fil markeras nedan; övriga grupper (S1–S7) läggs till i samma fil.

Grupper: [x] S8   [ ] S1  [ ] S2  [ ] S3  [ ] S4  [ ] S5  [ ] S6  [ ] S7
"""
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server.tests.scoring_support import build_scoring_fixture
from server.tests.support import ORIGIN, call, json_body

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


class S4CursorTests(S8Base):
    """Mutation: trust str.isdigit()/int() on cursor parts, or skip the revision check -> a tampered/older cursor is accepted."""

    def preferences(self, user, nickname, family="free", opt_in=True):
        return self.fx.post("/scoring/preferences", {"family": family, "opt_in": opt_in, "nickname": nickname}, user)

    def play(self, user, qids):
        for q in qids:
            _, issue = self.fx.issue(q, user=user)
            key = (user + q).replace("-", "").ljust(16, "k")[:16]
            self.assertEqual(self.fx.answer(issue, "Right", user=user, key=key)[0], 200, q)

    def board(self, user=None, **query):
        from urllib.parse import urlencode
        return self.fx.get("/leaderboards", user=user, query=urlencode(query).encode())

    def published_board(self):
        self.assertEqual(self.preferences("u1", "AAA")[0], 200)
        self.assertEqual(self.preferences("u2", "BBB")[0], 200)
        self.play("u1", self.fx.questions["free"][:2])
        self.play("u2", self.fx.questions["free"][:1])
        body = json_body(self.board(family="free", scope="total", window="all", limit=1))
        self.assertIsNotNone(body["next_cursor"])
        return body

    def test_a_tampered_or_misshapen_cursor_is_invalid(self):
        offset, revision, signature = self.published_board()["next_cursor"].split(".")
        for bad in ("", "a.b", f"{int(offset) + 1}.{revision}.{signature}", f"{offset}.{revision}.{'z' * 24}",
                    f"{offset}.{revision}.{signature}.extra"):
            response = self.board(family="free", scope="total", window="all", limit=1, cursor=bad)
            self.assertEqual((response[0], json_body(response)["error"]["code"]), (400, "invalid"), bad)

    def test_non_ascii_digits_in_a_cursor_part_are_invalid_never_a_server_error(self):
        for offset, revision in (("²", "1"), ("１", "1"), ("1", "١"), ("١", "١"), ("1²", "1"), ("1", "2٣")):
            response = self.board(family="free", scope="total", window="all", limit=1, cursor=f"{offset}.{revision}.{'a' * 24}")
            self.assertEqual((response[0], json_body(response)["error"]["code"]), (400, "invalid"), (offset, revision))

    def test_a_cursor_from_an_older_board_revision_is_stale(self):
        cursor = self.published_board()["next_cursor"]
        self.play("u2", self.fx.questions["free"][1:2])          # a later published answer bumps this (version, window)
        response = self.board(family="free", scope="total", window="all", limit=1, cursor=cursor)
        self.assertEqual((response[0], json_body(response)["error"]["code"]), (409, "stale_cursor"))


class S9DevModeTests(unittest.TestCase):
    """Mutation: keep the production refusal only in main(), or serve the in-memory dev source / real content -> a dev app runs in prod."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="k3-033-dev-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_production_is_refused_before_any_side_effect(self):
        from server import dev, dev_scoring
        for value in ("production", " Production ", "PRODUCTION\n", "\tproduction"):
            for scoring in (True, False):
                with mock.patch.dict(os.environ, {"APP_ENV": value}), \
                        mock.patch.object(dev_scoring, "write_pools") as write, mock.patch.object(dev_scoring, "seed_accounts") as seed, \
                        mock.patch.object(dev, "Database") as database, mock.patch.object(dev, "create_app") as create:
                    with self.assertRaises(dev.DevRefused):
                        dev.build_dev_app(port=8123, db_path=str(self.root / "a.db"),
                                          scoring_dir=str(self.root / "pools") if scoring else None)
                    for called in (write, seed, database, create):
                        called.assert_not_called()
        self.assertFalse((self.root / "pools").exists())

    def test_the_dev_pool_is_synthetic_labelled_private_and_off_without_the_flag(self):
        from server import dev, dev_scoring
        with mock.patch.dict(os.environ, {"APP_ENV": "development"}):
            app, settings = dev.build_dev_app(port=8123, db_path=str(self.root / "a.db"), scoring_dir=str(self.root / "pools"))
        self.assertEqual(len(app.state.dev_scoring), 3, "three seeded readers")
        self.assertTrue(settings.scoring.free_manifest and not settings.scoring.dev_source,
                        "the private manifest, never the in-memory dev source")
        self.assertNotIn("public", settings.scoring.free_manifest)
        entries = [e for family in ("official", "free")
                   for e in json.loads((self.root / "pools" / family / "manifest.json").read_text())["entries"]]
        self.assertGreater(len(entries), 10)
        for entry in entries:
            self.assertTrue(entry["id"].startswith("dev-"), entry["id"])
        for path in (self.root / "pools").rglob("*.txt"):
            self.assertIn(dev_scoring.LABEL, path.read_text(encoding="utf-8"), "every question says it is synthetic")
        with mock.patch.dict(os.environ, {"APP_ENV": "development"}):
            app2, settings2 = dev.build_dev_app(port=8123, db_path=str(self.root / "b.db"))
        self.assertFalse(settings2.scoring.any(), "without --scoring nothing is switched on")
        self.assertIsNone(app2.state.dev_scoring)


class S5RankTests(unittest.TestCase):
    """Mutation: ROW_NUMBER instead of RANK, or a hypothetical 0 for an empty scope -> ties break and an empty scope looks ranked."""

    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=("u1", "u2", "u3", "u4"), free=(("py-free", 3),))
        self.addCleanup(tmp.cleanup)

    def publish(self, user, nickname):
        self.assertEqual(self.fx.post("/scoring/preferences", {"family": "free", "opt_in": True, "nickname": nickname}, user)[0], 200)

    def answer(self, user, q, correct):
        _, issue = self.fx.issue(q, user=user)
        key = (user + q + "x").replace("-", "").ljust(16, "k")[:16]
        self.assertEqual(self.fx.answer(issue, "Right" if correct else "Wrong", user=user, key=key)[0], 200)

    def board(self, **query):
        from urllib.parse import urlencode
        return json_body(self.fx.get("/leaderboards", user=None, query=urlencode(query).encode()))

    def me(self, user, **query):
        from urllib.parse import urlencode
        return json_body(self.fx.get("/scoring/me", user=user, query=urlencode(query).encode()))

    def test_equal_exact_scores_share_a_rank_and_the_next_row_is_third(self):
        for user, nickname in (("u1", "One"), ("u2", "Two"), ("u3", "Three")):
            self.publish(user, nickname)
        self.answer("u1", "py-free-f00", True)
        self.answer("u2", "py-free-f00", True)
        self.answer("u3", "py-free-f00", False)
        entries = self.board(family="free", scope="total", window="all", limit=10)["entries"]
        self.assertEqual(sorted(e["rank"] for e in entries), [1, 1, 3], "a tie shares the rank and the next row skips to third")
        ranks = {e["alias"]: e for e in entries}
        self.assertEqual(ranks["One"]["points"], ranks["Two"]["points"], "the two tied rows have identical exact scores")

    def test_no_attempt_in_a_scope_is_none_not_a_hypothetical_zero(self):
        self.publish("u4", "Four")                              # published, but no attempt in free
        body = self.me("u4", family="free")
        self.assertEqual(body["rank"], {"kind": "none", "value": None, "participants": 0})
        self.assertIsNone(body["scope_score"], "an empty scope is never reported as a hypothetical zero")


class S3LifecycleTests(S8Base):
    """Mutation: skip the frozen-version/retired check on a new answer, or drop the replay path -> a withdrawn issue is answered."""

    def pool_cli(self, *argv):
        import contextlib
        import io
        from server import pool
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            return pool.main(["--db", self.fx.path, *argv])

    def answer_key(self, key):
        _, issue = self.fx.issue(self.fx.questions["free"][0], user="u1")
        return issue, key

    def test_a_frozen_version_withdraws_open_issues_and_refuses_new_ones(self):
        free = self.fx.questions["free"]
        _, receipt = self.fx.issue(free[0], user="u1")
        key = "k" * 16
        first = self.fx.answer(receipt, "Right", user="u1", key=key)
        self.assertEqual(first[0], 200)
        _, open_issue = self.fx.issue(free[1], user="u1")                 # stays open
        version = self.rows("SELECT id FROM ranking_versions WHERE family = 'free'")[0][0]

        self.assertEqual(self.pool_cli("freeze", "--version", version, "--reason", "season-end"), 0)

        # a new issue on the frozen version is unavailable
        self.assertEqual(self.fx.issue(free[2], user="u2")[0][0], 503)
        # the issue withdrawn by the freeze cannot be answered
        self.assertEqual((self.fx.answer(open_issue, "Right", user="u1", key="z" * 16)[0]), 409)
        # but a verified replay of an accepted answer still works, exactly once (no new effect)
        before = self.rows("SELECT COUNT(*) FROM first_attempts")
        replayed = self.fx.answer(receipt, "Right", user="u1", key=key)
        self.assertEqual(replayed[0], 200)
        self.assertTrue(json_body(replayed)["replayed"])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts"), before)

    def test_retire_needs_the_version_frozen_first_and_keeps_history_and_replay(self):
        free = self.fx.questions["free"]
        # freeze every open version, then retire
        _, receipt = self.fx.issue(free[0], user="u1")
        self.assertEqual(self.fx.answer(receipt, "Right", user="u1", key="k" * 16)[0], 200)
        version = self.rows("SELECT id FROM ranking_versions WHERE family = 'free'")[0][0]
        self.assertNotEqual(self.pool_cli("retire", "--question", free[4], "--release", "rel-2", "--reason", "exposed",
                                          "--operator", "op-1"), 0, "retire is refused while a version is still open")
        self.assertEqual(self.pool_cli("freeze", "--version", version, "--reason", "season-end"), 0)
        self.assertEqual(self.pool_cli("retire", "--question", free[4], "--release", "rel-2", "--reason", "exposed",
                                       "--operator", "op-1"), 0)
        self.assertEqual(self.rows("SELECT state FROM pool_questions WHERE question_id = ?", (free[4],)), [("retired",)])
        # history is untouched and a replay still answers
        self.assertEqual(self.fx.answer(receipt, "Right", user="u1", key="k" * 16)[0], 200)


class S7ModerateTests(S8Base):
    """Mutation: ignore moderation_state in the list/population, or let a dry run change state -> a hidden reader returns."""

    def pub(self, user, nickname):
        self.assertEqual(self.fx.post("/scoring/preferences", {"family": "free", "opt_in": True, "nickname": nickname}, user)[0], 200)

    def play(self, user, q):
        _, issue = self.fx.issue(q, user=user)
        key = (user + q).replace("-", "").ljust(16, "k")[:16]
        self.assertEqual(self.fx.answer(issue, "Right", user=user, key=key)[0], 200)

    def aliases(self):
        body = json_body(self.fx.get("/leaderboards", user=None, query=b"family=free&scope=total&window=all&limit=50"))
        return [e["alias"] for e in body["entries"]]

    def principal(self, user):
        return self.rows("SELECT id FROM scoring_principals WHERE user_id = ?", (user,))[0][0]

    def moderate(self, *argv):
        import contextlib
        import io
        from server import moderate
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            return moderate.main(["--db", self.fx.path, *argv])

    def test_a_dry_run_changes_nothing_and_write_hides_the_reader_for_good(self):
        self.pub("u1", "One")
        self.play("u1", self.fx.questions["free"][0])
        pid = self.principal("u1")
        self.assertIn("One", self.aliases())

        self.assertEqual(self.moderate("hide", "--principal", pid, "--family", "free", "--reason", "abuse",
                                       "--operator", "op-1", "--dry-run"), 0)
        self.assertIn("One", self.aliases(), "a dry run changes nothing")
        self.assertEqual(self.rows("SELECT moderation_state FROM leaderboard_preferences WHERE principal_id = ?", (pid,)), [("ok",)])

        self.assertEqual(self.moderate("hide", "--principal", pid, "--family", "free", "--reason", "abuse",
                                       "--operator", "op-1", "--write"), 0)
        self.assertNotIn("One", self.aliases(), "a hidden reader leaves the list")
        self.assertEqual(self.rows("SELECT moderation_state FROM leaderboard_preferences WHERE principal_id = ?", (pid,)), [("hidden",)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM scoring_moderation_audit"), [(1,)])
        # a new alias does not restore publishing
        self.pub("u1", "Renamed")
        self.assertNotIn("Renamed", self.aliases())
        # private history is preserved
        self.assertEqual(len(json_body(self.fx.get("/scoring/me", user="u1", query=b"family=free"))["attempts"]), 1)


class S2ExposureTests(S8Base):
    """Mutation: add the solution/score to the issue DTO, or let check-static miss a protected id -> facit leaks before the answer."""

    def static(self, root):
        import contextlib
        import io
        from server import pool
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            return pool.main(["--db", self.fx.path, "check-static", "--public-root", str(root)])

    def test_the_issue_dto_never_carries_the_solution_or_any_scoring(self):
        _, issue = self.fx.issue(self.fx.questions["free"][0], user="u1")
        self.assertNotIn("solution", issue)
        for option in issue["question"]["options"]:
            self.assertEqual(set(option), {"option_id", "text"}, "an option carries no score, feedback or misconception")
        for secret in ("score", "feedback", "misconception", "content_sha256", "solution"):
            self.assertNotIn(secret, json.dumps(issue))

    def test_the_answer_response_is_the_only_place_with_a_solution_field(self):
        _, issue = self.fx.issue(self.fx.questions["free"][0], user="u1")
        body = json_body(self.fx.answer(issue, "Right", user="u1", key="k" * 16))
        self.assertIn("solution", body, "the solution field exists, after the commit")

    def test_check_static_refuses_a_protected_id_in_a_public_file_and_passes_a_clean_tree(self):
        protected = self.rows("SELECT question_id FROM pool_questions WHERE state IN ('ranked', 'retired') LIMIT 1")[0][0]
        root = Path(tempfile.mkdtemp(prefix="k3-033-static-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "index.html").write_text("<!doctype html><p>clean</p>", encoding="utf-8")
        self.assertEqual(self.static(root), 0, "a clean public tree passes")
        (root / "leak.json").write_text(json.dumps({"copied": protected}), encoding="utf-8")
        self.assertEqual(self.static(root), 1, "a protected id in the scanned bytes is a finding")


class S1AnonymityTests(S8Base):
    """Mutation: let an anonymous reader publish, count a moved anonymous attempt, or accept a second claim -> anonymity leaks rank."""

    def anon_issue(self, anon, q):
        return self.fx.anon_post(f"/scoring/questions/{q}/issue", {}, anon)

    def anon_answer(self, anon, q):
        issue = json_body(self.anon_issue(anon, q))
        option = next(o for o in issue["question"]["options"] if o["text"].startswith("Right"))
        return self.fx.anon_post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer",
                                                      "option_id": option["option_id"], "idempotency_key": "a" * 16}, anon)

    def claim(self, user, anon):
        session = self.fx.cookies[user]
        return call(self.fx.app, "POST", "/scoring/claim",
                    headers={"origin": ORIGIN, "cookie": f"session={session[0]}; {anon[0]}", "x-csrf-token": session[1]},
                    body=b"{}")

    def test_an_anonymous_reader_can_never_publish(self):
        anon = self.fx.anonymous()
        before = self.rows("SELECT COUNT(*) FROM leaderboard_preferences")
        response = self.fx.anon_post("/scoring/preferences", {"family": "free", "opt_in": True, "nickname": "Anon"}, anon)
        self.assertEqual((response[0], json_body(response)["error"]["code"]), (401, "no-session"))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM leaderboard_preferences"), before, "no preference row was written")

    def test_an_anonymous_attempt_is_never_in_the_published_list(self):
        anon = self.fx.anonymous()
        self.assertEqual(self.anon_answer(anon, self.fx.questions["free"][0])[0], 200)
        board = json_body(self.fx.get("/leaderboards", user=None, query=b"family=free&scope=total&window=all&limit=50"))
        self.assertEqual(board["participants"], 0, "an anonymous attempt does not create a participant")
        self.assertEqual([e["alias"] for e in board["entries"]], [])

    def test_claim_moves_the_attempt_without_ranking_it_and_refuses_a_second_claim(self):
        anon = self.fx.anonymous()
        q = self.fx.questions["free"][0]
        self.assertEqual(self.anon_answer(anon, q)[0], 200)
        self.assertGreaterEqual(self.rows("SELECT COUNT(*) FROM anonymous_units")[0][0], 1)

        response = self.claim("u1", anon)
        self.assertEqual(response[0], 200)
        body = json_body(response)
        self.assertGreaterEqual(body["moved"], 1, "the free attempt moved to the account")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM anonymous_units"), [(0,)], "the source rows are gone")

        me = json_body(self.fx.get("/scoring/me", user="u1", query=b"family=free"))
        moved = [a for a in me["attempts"] if a["question_id"] == q]
        self.assertEqual(len(moved), 1)
        self.assertFalse(moved[0]["rank_eligible"], "a moved anonymous attempt is never rank-eligible")
        self.assertIsNone(me["scope_score"], "the moved attempt does not count in the scope")
        self.assertTrue(all(c["answered"] == 0 for c in me["courses"]))

        second = self.claim("u1", anon)
        self.assertEqual((second[0], json_body(second)["error"]["code"]), (409, "claim_conflict"))


class _QueryCounter:
    """Counts the DML statements of the requests made inside the context, by tracing every connection the app opens."""

    def __enter__(self):
        import server.db as db
        self._db = db
        self._orig = db.connect
        self.statements = []
        seen = self.statements

        def wrapped(path, **kwargs):
            conn = self._orig(path, **kwargs)
            conn.set_trace_callback(seen.append)
            return conn

        db.connect = wrapped
        return self

    def __exit__(self, *exc):
        self._db.connect = self._orig

    def dml(self):
        return [s for s in self.statements if s.strip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE"))]


class S6QueryBudgetTests(unittest.TestCase):
    """Mutation: a second COUNT for the participants of an empty page, or a per-row alias query -> the count stops being constant."""

    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=tuple(f"u{i}" for i in range(1, 9)), free=(("py-free", 5),))
        self.addCleanup(tmp.cleanup)

    def publish_and_answer(self, user):
        self.assertEqual(self.fx.post("/scoring/preferences", {"family": "free", "opt_in": True, "nickname": f"N{user}"}, user)[0], 200)
        _, issue = self.fx.issue(self.fx.questions["free"][0], user=user)
        self.assertEqual(self.fx.answer(issue, "Right", user=user, key=(user + "k").ljust(16, "k")[:16])[0], 200)

    def board(self, **query):
        from urllib.parse import urlencode
        return self.fx.get("/leaderboards", user=None, query=urlencode(query).encode())

    def test_the_query_count_is_constant_for_an_empty_one_row_and_a_full_page(self):
        with _QueryCounter() as counter:
            self.board(family="free", scope="total", window="all", limit=50)
        empty = len(counter.dml())
        self.publish_and_answer("u1")
        with _QueryCounter() as counter:
            self.board(family="free", scope="total", window="all", limit=1)
        one = len(counter.dml())
        self.publish_and_answer("u2")
        with _QueryCounter() as counter:
            self.board(family="free", scope="total", window="all", limit=50)
        full = len(counter.dml())
        self.assertEqual(empty, one, "an empty page costs what a one-row page costs")
        self.assertEqual(one, full, "a full page costs what a one-row page costs")

    def test_board_revision_is_bound_to_the_family_and_moves_both_windows(self):
        official = lambda: json_body(self.fx.get("/leaderboards", user=None, query=b"family=official&scope=total&window=all"))["board_revision"]
        free_all = lambda: json_body(self.fx.get("/leaderboards", user=None, query=b"family=free&scope=total&window=all"))["board_revision"]
        free_week = lambda: json_body(self.fx.get("/leaderboards", user=None, query=b"family=free&scope=total&window=week"))["board_revision"]
        self.fx.get("/leaderboards", user=None, query=b"family=free&scope=total&window=all")       # create/open both versions
        self.fx.get("/leaderboards", user=None, query=b"family=official&scope=total&window=all")
        official_before, free_all_before, free_week_before = official(), free_all(), free_week()

        self.publish_and_answer("u1")
        self.assertEqual(official(), official_before, "a free preference/answer does not touch the official board")
        self.assertGreater(int(free_all()), int(free_all_before), "a published answer moves the free board (all)")
        self.assertGreater(int(free_week()), int(free_week_before), "a published answer moves the free board (week)")

    def test_an_own_row_beyond_the_first_page_is_reachable_through_its_own_cursor(self):
        for i in range(1, 9):
            self.publish_and_answer(f"u{i}")
        me = json_body(self.fx.get("/scoring/me", user="u8", query=b"family=free"))
        self.assertIsNotNone(me["own_cursor"], "an own row past the first page has its own cursor")
        from urllib.parse import urlencode
        page = json_body(self.fx.get("/leaderboards", user="u8",
                                     query=urlencode({"family": "free", "scope": "total", "window": "all", "limit": 5,
                                                      "cursor": me["own_cursor"]}).encode()))
        self.assertTrue(any(e.get("is_me") for e in page["entries"]), "the fetched page holds the reader's own row")


if __name__ == "__main__":
    unittest.main()
