# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoring phase 1, step 6: the read side, ranks, cursors, neighbours, hypothetical placement and the additive DTO.

Negative first: an unpublished, hidden, anonymous, deleted or inactive reader is never listed, a stale or foreign cursor never
returns rows, and an anonymous placement is never inserted into the list. All data is synthetic.
"""
import unittest

from server import scoring_boards
from server.repositories import Repositories
from server.scoring_service import ScoringService
from server.scoring_versions import score_display
from server.tests.scoring_support import build_scoring_fixture
from server.tests.support import call, json_body

NAMES = ["Ada", "Bob", "Cy", "Dee", "Eli", "Fay", "Gus", "Hal"]
QUERY = b"family=free&scope=total&window=all"


class Base(unittest.TestCase):
    users = tuple(f"u{i}" for i in range(1, 9))

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

    def play(self, user, outcomes, name=None, publish=True, family="free", start=0, key_tag=""):
        """Answer free questions f<start>.. with the given markers, then optionally publish under a name."""
        for i, marker in enumerate(outcomes):
            qid = f"py-free-f{start + i:02d}" if family == "free" else f"py-q{start + i:02d}"
            _, issue = self.fx.issue(qid, user=user)
            self.assertEqual(self.fx.answer(issue, marker, user=user, key=(user + qid + key_tag).ljust(16, "k")[:32])[0], 200)
        if publish:
            body = {"family": family, "opt_in": True}
            if name:
                body["nickname"] = name
            self.assertEqual(self.fx.post("/scoring/preferences", body, user)[0], 200)

    def board(self, query=QUERY, user=None):
        return call(self.fx.app, "GET", "/leaderboards", headers=self.fx.headers(user, csrf=False, origin=False) if user else {}, query=query)

    def me(self, user, query=b"family=free"):
        return json_body(self.fx.get("/scoring/me", user=user, query=query))

    def six(self):
        """Six published players with scores 6 > 4 > 2 = 2 > 0 > -2 ... see assertions in each test."""
        plans = [("u1", ["Right"] * 3, "Ada"), ("u2", ["Right", "Right"], "Bob"), ("u3", ["Right"], "Cy"),
                 ("u4", ["Half", "Half"], "Dee"), ("u5", ["Not"], "Eli"), ("u6", ["Wrong"], "Fay")]
        for user, outcomes, name in plans:
            self.play(user, [o if o != "Not" else "Half" for o in outcomes], name)
        return plans


class RankAndOrderTests(Base):
    def test_equal_scores_share_a_rank_and_order_is_fewer_answers_then_earlier_then_internal_id(self):
        self.play("u3", ["Right", "Half", "Wrong"], "Cy", key_tag="a")          # 2 units, 3 answers, earliest
        self.fx.clock[0] += 10
        self.play("u2", ["Right"], "Bob", start=4)                                # 2 units, 1 answer
        self.fx.clock[0] += 10
        self.play("u1", ["Right"], "Ada", start=5)                                # 2 units, 1 answer, later than Bob
        self.play("u4", ["Wrong"], "Dee", start=6)                                # -1 unit
        data = json_body(self.board())
        self.assertEqual([(e["alias"], e["rank"]) for e in data["entries"]], [("Bob", 1), ("Ada", 1), ("Cy", 1), ("Dee", 4)])
        self.assertEqual(data["participants"], 4)

    def test_pages_follow_one_stable_order_and_the_rank_continues_across_the_cursor(self):
        self.six()
        first = json_body(self.board(QUERY + b"&limit=2"))
        self.assertEqual([e["alias"] for e in first["entries"]], ["Ada", "Bob"])
        seen, cursor = list(first["entries"]), first["next_cursor"]
        while cursor:
            page = json_body(self.board(QUERY + b"&limit=2&cursor=" + cursor.encode()))
            seen += page["entries"]
            cursor = page["next_cursor"]
        self.assertEqual([e["alias"] for e in seen], ["Ada", "Bob", "Cy", "Dee", "Eli", "Fay"])
        self.assertEqual([e["rank"] for e in seen], [1, 2, 3, 3, 5, 6], "the tie keeps its shared rank and the next rank skips")
        self.assertEqual(len({e["alias"] for e in seen}), 6, "no row twice, none missing")

    def test_entries_carry_the_exact_display_score_and_never_an_internal_id(self):
        self.play("u1", ["Right"], "Ada")
        entry = json_body(self.board())["entries"][0]
        self.assertEqual(set(entry), {"rank", "alias", "points", "score_display", "answered", "pool"})
        self.assertEqual((entry["score_display"], entry["points"], entry["answered"], entry["pool"]), ("111.11", 500 * 2 / 9, 1, 9))
        self.assertNotIn("principal", json_body(self.board()).__repr__())

    def test_score_display_rounds_half_away_from_zero_and_never_shows_negative_zero(self):
        for numerator, denominator, expected in ((1, 4000, "0.13"), (-1, 4000, "-0.13"), (3, 4000, "0.38"), (-1, 125000, "0.00"),
                                                 (0, 7, "0.00"), (2, 9, "111.11"), (-3, 4000, "-0.38")):
            self.assertEqual(score_display(numerator, denominator), expected, (numerator, denominator))


class FilterTests(Base):
    def test_only_published_active_visible_accounts_are_listed(self):
        self.play("u1", ["Right"], "Ada")
        self.play("u2", ["Right"], publish=False)                               # never opted in
        self.play("u3", ["Right"], "Cy")                                          # will be hidden
        self.play("u4", ["Right"], "Dee")                                         # will be deleted
        self.play("u5", ["Right"], "Eli")                                         # will be unpublished
        anon = self.fx.anonymous()
        self.fx.anon_post("/scoring/questions/py-free-f08/issue", {}, anon)
        self.run_sql("UPDATE leaderboard_preferences SET moderation_state = 'hidden' WHERE principal_id = (SELECT id FROM scoring_principals WHERE user_id = 'u3')")
        self.run_sql("UPDATE users SET status = 'deleted' WHERE id = 'u4'")
        self.assertEqual(self.fx.post("/scoring/preferences", {"family": "free", "opt_in": False}, "u5")[0], 200)
        data = json_body(self.board())
        self.assertEqual([e["alias"] for e in data["entries"]], ["Ada"])
        self.assertEqual(data["participants"], 1)

    def test_an_inactive_principal_is_not_listed(self):
        self.play("u1", ["Right"], "Ada")
        self.run_sql("UPDATE scoring_principals SET state = 'consumed' WHERE user_id = 'u1'")
        self.assertEqual(json_body(self.board())["entries"], [])

    def test_the_week_board_is_separate_and_turns_with_the_week(self):
        self.play("u1", ["Right"], "Ada")
        week = QUERY.replace(b"window=all", b"window=week")
        self.assertEqual(json_body(self.board(week))["participants"], 1)
        self.fx.clock[0] += 8 * 86400
        self.assertEqual(json_body(self.board(week))["entries"], [])
        self.assertEqual(json_body(self.board())["participants"], 1)

    def test_a_frozen_version_is_still_readable_as_history(self):
        self.play("u1", ["Right"], "Ada")
        self.run_sql("UPDATE ranking_versions SET status = 'frozen', frozen_at = 1, freeze_reason = 'manual' WHERE family = 'free'")
        self.assertEqual([e["alias"] for e in json_body(self.board())["entries"]], ["Ada"])
        self.assertEqual(self.me("u1")["rank"]["kind"], "actual")

    def test_a_course_scope_lists_only_those_who_answered_in_it(self):
        self.play("u1", ["Right"], "Ada")
        self.assertEqual(json_body(self.board(b"family=free&scope=course:py-free&window=all"))["participants"], 1)
        self.assertEqual(self.board(b"family=free&scope=course:nope&window=all")[0], 400)


class CursorTests(Base):
    def cursor(self):
        self.six()
        return json_body(self.board(QUERY + b"&limit=2"))["next_cursor"]

    def test_a_cursor_is_bound_to_its_family_scope_window_and_signature(self):
        cursor = self.cursor()
        q = lambda base: base + b"&limit=2&cursor=" + cursor.encode()
        self.assertEqual(self.board(q(QUERY))[0], 200)
        self.assertEqual(self.board(q(b"family=free&scope=course:py-free&window=all"))[0], 400, "other scope")
        self.assertEqual(self.board(q(b"family=free&scope=total&window=week"))[0], 400, "other window")
        self.assertEqual(self.board(q(b"family=official&scope=total&window=all"))[0], 400, "other family")
        offset, revision, sig = cursor.split(".")
        for bad in (f"{offset}.{revision}.{'0' * len(sig)}", f"{int(offset) + 1}.{revision}.{sig}", f"{offset}.{sig}", f"x.{revision}.{sig}",
                    f"{offset}.{revision}.{sig}.extra", f"{'9' * 8}.{revision}.{sig}", f"{offset}.{'9' * 13}.{sig}", "", "."):
            self.assertEqual(self.board(QUERY + b"&limit=2&cursor=" + bad.encode())[0], 400, bad)

    def test_a_cursor_with_non_ascii_or_non_decimal_parts_is_a_400_never_a_server_error(self):
        from urllib.parse import quote
        cursor = self.cursor()
        offset, revision, sig = cursor.split(".")
        forms = [f"0.\u00b2.{sig}", f"\u00b2.0.{sig}", f"\uff11.{revision}.{sig}", f"{offset}.\uff11.{sig}", f"\u0661.{revision}.{sig}",
                 f"{offset}.\u0661.{sig}", f"{offset}.{revision}.\u00e9{sig[1:]}", f"{offset}.{revision}.{sig[:-1]}\u00e9",
                 f"{offset}.{revision}.{sig.upper()}", f"{offset}. {revision}.{sig}", f"{offset}.{revision}.{sig} ", f"-1.{revision}.{sig}",
                 f"{offset}.{revision}.{sig[:23]}", f"{offset}.{revision}.{sig}0", "0.\u00b2.x", f"+{offset}.{revision}.{sig}",
                 f"{offset}.{revision}.{sig}\n", f"\n{offset}.{revision}.{sig}"]
        for form in forms:
            response = self.board(QUERY + b"&limit=2&cursor=" + quote(form).encode())
            self.assertEqual(response[0], 400, repr(form))
        self.assertEqual(self.board(QUERY + b"&limit=2&cursor=" + cursor.encode())[0], 200, "the real cursor still works")

    def test_a_change_to_the_board_makes_every_older_cursor_stale_without_returning_rows(self):
        cursor = self.cursor()
        self.play("u7", ["Right"], "Gus")                                        # a published reader's answer moves the board
        response = self.board(QUERY + b"&limit=2&cursor=" + cursor.encode())
        self.assertEqual((response[0], json_body(response)["error"]["code"]), (409, "stale_cursor"))
        fresh = json_body(self.board(QUERY + b"&limit=2"))
        self.assertEqual(self.board(QUERY + b"&limit=2&cursor=" + fresh["next_cursor"].encode())[0], 200)

    def test_each_kind_of_change_makes_the_cursor_stale(self):
        for change in (lambda: self.fx.post("/scoring/preferences", {"family": "free", "opt_in": True, "nickname": "Renamed"}, "u1"),
                       lambda: self.fx.post("/scoring/preferences", {"family": "free", "opt_in": False}, "u2"),
                       lambda: self.play("u1", ["Right"], start=7, publish=False)):
            with self.subTest():
                fx, tmp = build_scoring_fixture(users=self.users, free=(("py-free", 9),))
                self.addCleanup(tmp.cleanup)
                self.fx = fx
                cursor = self.cursor()
                change()
                self.assertEqual(self.board(QUERY + b"&limit=2&cursor=" + cursor.encode())[0], 409)

    def test_a_cursor_past_the_end_is_an_empty_page_with_the_right_participant_count(self):
        self.six()
        cursor = json_body(self.board(QUERY + b"&limit=5"))["next_cursor"]
        page = json_body(self.board(QUERY + b"&limit=5&cursor=" + cursor.encode()))
        self.assertEqual((len(page["entries"]), page["participants"], page["next_cursor"]), (1, 6, None))
        db = self.fx.db()
        self.addCleanup(db.close)
        service = ScoringService(Repositories(db), self.fx.settings, self.fx.scoring, clock=lambda: self.fx.clock[0])
        version = db.connect().execute("SELECT id FROM ranking_versions WHERE family = 'free'").fetchone()[0]
        revision = int(json_body(self.board())["board_revision"])
        beyond = service._cursor("free", version, "total", "all", revision, 500)
        empty = json_body(self.board(QUERY + b"&limit=5&cursor=" + beyond.encode()))
        self.assertEqual((empty["entries"], empty["participants"], empty["next_cursor"]), ([], 6, None))


class MeTests(Base):
    def test_a_published_reader_gets_an_actual_rank_a_cursor_to_their_neighbours_and_an_is_me_row(self):
        self.six()
        me = self.me("u5")                                                        # Eli: 5th row; Cy and Dee tie on rank 3, so Eli is 5
        self.assertEqual(me["rank"], {"kind": "actual", "value": 5, "participants": 6})
        self.assertTrue(me["published"] and me["moderation"] == "ok")
        near = json_body(self.board(QUERY + b"&limit=5&cursor=" + me["own_cursor"].encode(), user="u5"))
        self.assertEqual([e["alias"] for e in near["entries"]], ["Cy", "Dee", "Eli", "Fay"])
        self.assertEqual([e["alias"] for e in near["entries"] if e.get("is_me")], ["Eli"])
        self.assertEqual(near["board_revision"], me["board_revision"])
        everyone = json_body(self.board(QUERY, user="u5"))
        self.assertEqual(sum(1 for e in everyone["entries"] if "is_me" in e), 1)
        self.assertEqual(sum(1 for e in json_body(self.board(QUERY))["entries"] if "is_me" in e), 0, "no session: no own row")

    def test_the_neighbour_window_is_clamped_at_the_start_of_the_list(self):
        self.six()
        me = self.me("u2")
        self.assertTrue(me["own_cursor"].startswith("0."))
        page = json_body(self.board(QUERY + b"&limit=5&cursor=" + me["own_cursor"].encode(), user="u2"))
        self.assertEqual([e["alias"] for e in page["entries"]][:3], ["Ada", "Bob", "Cy"])

    def test_an_unpublished_account_gets_a_hypothetical_placement_that_is_not_in_the_list(self):
        self.six()
        self.play("u7", ["Right", "Right"], publish=False)                        # 4 units, not published
        me = self.me("u7")
        self.assertEqual(me["rank"], {"kind": "hypothetical", "value": 2, "participants": 6})
        self.assertFalse(me["published"])
        self.assertIsNone(me["own_cursor"])
        self.assertEqual(json_body(self.board())["participants"], 6, "the placement never inserts the reader")

    def test_an_anonymous_reader_gets_a_hypothetical_rank_from_their_private_aggregate_only(self):
        self.six()
        anon = self.fx.anonymous()
        for i, marker in enumerate(("Right", "Right")):
            qid = f"py-free-f0{i}"
            issue = json_body(self.fx.anon_post(f"/scoring/questions/{qid}/issue", {}, anon))
            option = next(o for o in issue["question"]["options"] if o["text"].startswith(marker))
            self.fx.anon_post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer", "option_id": option["option_id"],
                                                   "idempotency_key": f"{i}" * 16}, anon)
        response = call(self.fx.app, "GET", "/scoring/me", headers={"cookie": anon[0]}, query=b"family=free")
        me = json_body(response)
        self.assertEqual((me["rank"], me["published"], me["own_cursor"]), ({"kind": "hypothetical", "value": 2, "participants": 6}, False, None))
        self.assertEqual(json_body(self.board())["participants"], 6)
        self.assertEqual(me["total"]["answered"], 2)

    def test_no_comparable_pool_and_no_attempt_in_scope_mean_no_rank_at_all(self):
        self.play("u1", ["Right"], publish=False)                                # nobody is published
        me = self.me("u1")
        self.assertEqual(me["rank"], {"kind": "none", "value": None, "participants": 0})
        self.assertEqual(me["scope_score"]["score_display"], "111.11")
        self.play("u2", ["Right"], "Bob")
        none_in_scope = self.me("u2", b"family=free&scope=course:py-free&window=all")
        self.assertEqual(none_in_scope["rank"]["kind"], "actual")
        fresh = self.me("u3")
        self.assertEqual((fresh["rank"]["kind"], fresh["scope_score"], fresh["total"]["answered"], fresh["total"]["score_display"]),
                         ("none", None, 0, "0.00"))

    def test_a_hidden_reader_sees_their_state_but_no_list_position_and_no_cursor(self):
        self.six()
        self.run_sql("UPDATE leaderboard_preferences SET moderation_state = 'hidden' WHERE principal_id = (SELECT id FROM scoring_principals WHERE user_id = 'u2')")
        me = self.me("u2")
        self.assertEqual((me["published"], me["moderation"], me["own_cursor"]), (False, "hidden", None))
        self.assertNotIn("Bob", [e["alias"] for e in json_body(self.board())["entries"]])

    def test_the_history_marks_which_attempts_were_rankable_in_this_version(self):
        self.play("u1", ["Right"], "Ada")
        anon = self.fx.anonymous()
        headers = self.fx.headers("u1")
        headers["cookie"] += "; " + anon[0]
        issue = json_body(self.fx.anon_post("/scoring/questions/py-free-f05/issue", {}, anon))
        option = next(o for o in issue["question"]["options"] if o["text"].startswith("Right"))
        self.fx.anon_post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer", "option_id": option["option_id"], "idempotency_key": "q" * 16}, anon)
        self.assertEqual(call(self.fx.app, "POST", "/scoring/claim", headers=headers, body=b"{}")[0], 200)
        me = self.me("u1")
        self.assertEqual({a["question_id"]: a["rank_eligible"] for a in me["attempts"]}, {"py-free-f00": True, "py-free-f05": False})
        self.assertEqual(me["total"]["answered"], 1, "the claimed practice does not score")

    def test_official_numbers_use_the_lcm_total_and_the_course_pool(self):
        self.play("u1", ["Right"], "Ada", family="official")                     # py pool 10, K = 3: total 4/60
        me = self.me("u1", b"family=official&scope=course:py&window=all")
        self.assertEqual(me["scope_score"], {"scope": "course:py", "score_display": "100.00", "answered": 1, "pool": 10})
        self.assertEqual(me["total"]["score_display"], "33.33")
        self.assertEqual([c["score_display"] for c in me["courses"]], ["0.00", "100.00", "0.00"])      # prod, py, sql
        self.assertEqual(set(me), {"family", "rules_version", "catalog_version", "scope", "window", "board_revision", "total", "scope_score",
                                   "courses", "attempts", "rank", "published", "moderation", "own_cursor"})

    def test_board_revision_is_the_same_text_in_both_answers_and_rises_on_a_visible_change(self):
        self.play("u1", ["Right"], "Ada")
        first = self.me("u1")["board_revision"]
        self.assertIsInstance(first, str)
        self.assertEqual(json_body(self.board())["board_revision"], first)
        self.play("u1", ["Right"], start=1, publish=False)
        self.assertGreater(int(self.me("u1")["board_revision"]), int(first))


class CostTests(Base):
    users = tuple(f"u{i}" for i in range(1, 56))

    def statements(self, call_):
        db = self.fx.db()
        self.addCleanup(db.close)
        seen = []
        db.connect().set_trace_callback(seen.append)
        call_(ScoringService(Repositories(db), self.fx.settings, self.fx.scoring, clock=lambda: self.fx.clock[0]))
        return [s for s in seen if not s.startswith(("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"))]

    def test_a_page_costs_the_same_number_of_statements_for_one_row_and_for_fifty(self):
        for i, user in enumerate(self.users):
            self.play(user, ["Right"], f"Reader {chr(65 + i // 26)}{chr(97 + i % 26)}")
        one = self.statements(lambda s: s.leaderboard("free", "total", "all", 1, None))
        fifty = self.statements(lambda s: s.leaderboard("free", "total", "all", 50, None))
        self.assertEqual(len(one), len(fifty))
        self.assertEqual(len(json_body(self.board(QUERY + b"&limit=50"))["entries"]), 50)
        mine = self.statements(lambda s: s.me(type("A", (), {"user_id": "u55", "anon_hash": None})(), "free", "all", "total"))
        self.assertLess(len(mine), 25, "me is a fixed number of statements")

    def test_exactly_one_population_statement_per_page_for_every_page_size_and_for_an_empty_board(self):
        service_page = lambda limit, cursor=None: (lambda s: s.leaderboard("free", "total", "all", limit, cursor))
        population = lambda seen: [s for s in seen if "standings" in s]
        empty = self.statements(service_page(50))                                  # nobody has published yet
        self.assertEqual(len(population(empty)), 1, "an empty board is still one population statement")
        for i, user in enumerate(self.users):
            self.play(user, ["Right"], f"Reader {chr(65 + i // 26)}{chr(97 + i % 26)}")
        counts = {}
        for limit in (1, 50):
            counts[limit] = self.statements(service_page(limit))
        self.assertEqual({limit: len(population(seen)) for limit, seen in counts.items()}, {1: 1, 50: 1})
        self.assertEqual(len(counts[1]), len(counts[50]), "the whole statement budget is identical for 1 and 50 rows")
        self.assertEqual(len(counts[1]), len(empty), "and for an empty board")
        db = self.fx.db()
        self.addCleanup(db.close)
        service = ScoringService(Repositories(db), self.fx.settings, self.fx.scoring, clock=lambda: self.fx.clock[0])
        version = db.connect().execute("SELECT id FROM ranking_versions WHERE family = 'free'").fetchone()[0]
        revision = int(json_body(self.board())["board_revision"])
        beyond = service._cursor("free", version, "total", "all", revision, 5000)
        past = self.statements(service_page(50, beyond))
        self.assertEqual((len(population(past)), len(past)), (1, len(empty)), "a cursor past the end is the same single statement")
        self.assertEqual(json_body(self.board(QUERY + b"&limit=50&cursor=" + beyond.encode()))["participants"], len(self.users))

    def test_the_page_query_searches_the_board_index_by_version_prefix(self):
        db = self.fx.db()
        self.addCleanup(db.close)
        version = db.connect().execute("SELECT id FROM ranking_versions WHERE family = 'free'").fetchone()[0]
        args = {"family": "free", "version": version, "scope": "total", "window": "all", "after": 0, "limit": 50, "me": "x", "key": "0"}
        self.assertEqual(scoring_boards.PAGE_SQL.count("FROM standings"), 1, "the population is read once")
        for sql in (scoring_boards.PAGE_SQL, scoring_boards.OWN_SQL, scoring_boards.HYPOTHETICAL_SQL):
            plan = " | ".join(r[3] for r in db.connect().execute("EXPLAIN QUERY PLAN " + sql, args))
            self.assertIn("idx_standings_board", plan, plan)
            self.assertIn("version_id=? AND scope=? AND window_key=?", plan)
            self.assertNotIn("SCAN st", plan)


if __name__ == "__main__":
    unittest.main()
