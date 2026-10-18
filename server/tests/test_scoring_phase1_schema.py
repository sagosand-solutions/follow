# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migration 7 (scoring phase 1): additive schema, conservative backfill, fail-closed defaults and the monotone triggers.

All rows are synthetic. The upgrade path starts from a database at migration 6 holding anonymous, account and claimed
principals, so the backfill is exercised on realistic old data; nothing is deleted and no lock disappears.
"""
import os
import sqlite3
import tempfile
import unittest

from server.db import apply_migrations, connect
from server.migrations import MIGRATIONS
from server.scoring_repo import ScoringRepository

SHA = "a" * 64


class Phase1SchemaCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "p1.db")
        self.conn = connect(self.path)
        self.addCleanup(self.conn.close)

    def fresh(self):
        apply_migrations(self.conn)
        return self.conn

    def refused(self, sql, args=()):
        with self.assertRaises(sqlite3.DatabaseError, msg=sql):
            self.conn.execute(sql, args)

    # -- old data at migration 6 ---------------------------------------------------------------------------------------
    def seed_old(self):
        c = self.conn
        apply_migrations(c, migrations=MIGRATIONS[:6])
        for uid in ("u-clean", "u-claimed"):
            c.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES (?, 1, 'active', NULL)", (uid,))
        c.execute("INSERT INTO scoring_principals (id, anon_cookie_hash, created_at, last_activity, expires_at, state) "
                  "VALUES ('p-anon', 'h-anon', 1, 1, 9e9, 'active')")
        c.execute("INSERT INTO scoring_principals (id, anon_cookie_hash, created_at, last_activity, expires_at, state) "
                  "VALUES ('p-src', 'h-src', 1, 1, 9e9, 'active')")
        c.execute("INSERT INTO scoring_principals (id, user_id, created_at, last_activity, state) VALUES ('p-clean', 'u-clean', 1, 1, 'active')")
        c.execute("INSERT INTO scoring_principals (id, user_id, created_at, last_activity, claim_source_id, state) "
                  "VALUES ('p-claimed', 'u-claimed', 1, 1, 'p-src', 'active')")
        c.execute("UPDATE scoring_principals SET claimed_to = 'p-claimed', state = 'consumed', anon_cookie_hash = NULL WHERE id = 'p-src'")
        for principal, question in (("p-anon", "q-a"), ("p-clean", "q-c"), ("p-claimed", "q-k1"), ("p-claimed", "q-k2")):
            c.execute("INSERT INTO first_attempts (principal_id, question_id, family, course_id, catalog_version, rules_version, revision, "
                      "option_id, outcome, score_units, accepted_at) VALUES (?, ?, 'free', 'course', 'cat1', 'r2', 'rev', 'o', 'correct', 2, 5)",
                      (principal, question))
        for issue, principal, question, family in (("i-anon", "p-anon", "q-a", "free"), ("i-anon2", "p-anon", "q-open", "free"),
                                                   ("i-clean", "p-clean", "q-c", "free"), ("i-src", "p-src", "q-src", "free")):
            c.execute("INSERT INTO question_issues (id, principal_id, question_id, family, catalog_version, revision, option_map, issued_at, "
                      "expires_at, consumed_at) VALUES (?, ?, ?, ?, 'cat1', 'rev', '{}', 3, 4, NULL)", (issue, principal, question, family))
        for principal, family, opted in (("p-anon", "free", 7.0), ("p-clean", "free", 7.0), ("p-claimed", "official", 7.0)):
            c.execute("INSERT INTO leaderboard_preferences (principal_id, family, opted_in_at, alias, nickname, moderation_state) "
                      "VALUES (?, ?, ?, 'Reader-x', 'Nick', 'ok')", (principal, family, opted))
        return c


class UpgradeBackfillTests(Phase1SchemaCase):
    def test_the_backfill_is_conservative_and_deletes_nothing(self):
        c = self.seed_old()
        before = c.execute("SELECT COUNT(*) FROM first_attempts").fetchone()[0]
        self.assertEqual(apply_migrations(c), 1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM first_attempts").fetchone()[0], before)
        origins = {r[0]: r[1] for r in c.execute("SELECT question_id, origin FROM first_attempts")}
        self.assertEqual(origins, {"q-a": "anonymous", "q-c": "account", "q-k1": "legacy", "q-k2": "legacy"})
        self.assertEqual(apply_migrations(c), 0, "repeated calls are a no-op")

    def test_an_anonymous_opt_in_is_unpublished_but_nothing_else_is_touched(self):
        c = self.seed_old()
        apply_migrations(c)
        rows = {r["principal_id"]: (r["opted_in_at"], r["nickname"], r["alias"]) for r in c.execute("SELECT * FROM leaderboard_preferences")}
        self.assertEqual(rows["p-anon"], (None, None, "Reader-x"), "unpublished, the row and alias are kept")
        self.assertEqual(rows["p-clean"], (7.0, "Nick", "Reader-x"), "an account opt-in is untouched")
        self.assertEqual(rows["p-claimed"], (7.0, "Nick", "Reader-x"))

    def test_every_issue_ever_given_is_an_exposure_and_an_anonymous_one_blocks_ranked_play(self):
        c = self.seed_old()
        apply_migrations(c)
        exposure = {(r["principal_id"], r["question_id"]): (r["rank_blocking"], r["via"]) for r in c.execute("SELECT * FROM question_exposure")}
        self.assertEqual(exposure[("p-anon", "q-a")], (1, "issue"))
        self.assertEqual(exposure[("p-anon", "q-open")], (1, "issue"))
        self.assertEqual(exposure[("p-clean", "q-c")], (0, "issue"), "an account's own issue does not block its ranked play")
        self.assertEqual(exposure[("p-src", "q-src")], (1, "issue"))
        self.assertEqual(exposure[("p-claimed", "q-src")], (1, "claim"), "a claimed source's exposure blocks the account that claimed it")

    def test_open_issues_and_new_columns_have_fail_closed_defaults(self):
        c = self.seed_old()
        apply_migrations(c)
        self.assertEqual({r[0] for r in c.execute("SELECT rank_eligible FROM question_issues")}, {0})
        self.assertEqual({r[0] for r in c.execute("SELECT set_id FROM question_issues")}, {None})

    def test_the_upgraded_schema_has_the_same_objects_as_a_fresh_install(self):
        c = self.seed_old()
        apply_migrations(c)
        upgraded = {(r[0], r[1]) for r in c.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        other = connect(os.path.join(self.tmp.name, "fresh.db"))
        self.addCleanup(other.close)
        apply_migrations(other)
        fresh = {(r[0], r[1]) for r in other.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        self.assertEqual(upgraded, fresh)


class OriginTests(Phase1SchemaCase):
    def attempt(self, principal="p", question="q", **over):
        values = dict(principal_id=principal, question_id=question, family="free", course_id="c", catalog_version="cat", rules_version="r2",
                      revision="rev", option_id="o", outcome="correct", score_units=2, accepted_at=1.0)
        values.update(over)
        cols = ", ".join(values)
        return f"INSERT INTO first_attempts ({cols}) VALUES ({', '.join('?' * len(values))})", tuple(values.values())

    def principals(self):
        c = self.fresh()
        c.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('u', 1, 'active', NULL)")
        c.execute("INSERT INTO scoring_principals (id, user_id, created_at, last_activity, state) VALUES ('p', 'u', 1, 1, 'active')")
        c.execute("INSERT INTO scoring_principals (id, anon_cookie_hash, created_at, last_activity, expires_at, state) VALUES ('p2', 'h', 1, 1, 9e9, 'active')")
        return c

    def test_an_insert_that_does_not_name_the_origin_is_legacy_never_rankable(self):
        c = self.principals()
        c.execute(*self.attempt())
        self.assertEqual(c.execute("SELECT origin FROM first_attempts").fetchone()[0], "legacy")

    def test_only_the_four_origins_exist_and_the_origin_never_changes(self):
        c = self.principals()
        for origin in ("account", "anonymous", "exposed", "legacy"):
            c.execute(*self.attempt(question=f"q-{origin}", origin=origin))
        for bad in ("ranked", "", "ACCOUNT", None):
            self.refused(*self.attempt(question="q-bad", origin=bad))
        for target in ("account", "anonymous", "exposed", "legacy"):
            self.refused("UPDATE first_attempts SET origin = ? WHERE question_id = 'q-account'", (target,))
        self.refused("UPDATE first_attempts SET score_units = 0 WHERE question_id = 'q-account'")

    def test_a_claim_may_still_move_a_row_and_the_origin_stays_with_it(self):
        c = self.principals()
        c.execute(*self.attempt(principal="p2", question="q-moved", origin="anonymous"))
        c.execute("UPDATE first_attempts SET principal_id = 'p' WHERE question_id = 'q-moved'")
        self.assertEqual(c.execute("SELECT principal_id, origin FROM first_attempts").fetchone()[:], ("p", "anonymous"))
        self.refused("UPDATE first_attempts SET principal_id = 'p2', origin = 'account' WHERE question_id = 'q-moved'")
        self.refused("DELETE FROM first_attempts WHERE question_id = 'q-moved'")


class ExposureTests(OriginTests):
    def exposure(self, principal="p", question="q", blocking=0, at=10.0, via="issue"):
        return ("INSERT INTO question_exposure (principal_id, question_id, family, first_exposed_at, via, rank_blocking) "
                "VALUES (?, ?, 'free', ?, ?, ?)", (principal, question, at, via, blocking))

    def test_rank_blocking_only_goes_up_and_the_first_exposure_only_moves_earlier(self):
        c = self.principals()
        c.execute(*self.exposure(blocking=0))
        key = "WHERE principal_id = 'p' AND question_id = 'q'"
        self.refused(f"UPDATE question_exposure SET first_exposed_at = 11 {key}")                    # later
        self.refused(f"UPDATE question_exposure SET via = 'content' {key}")                           # same block, other via
        self.refused(f"UPDATE question_exposure SET question_id = 'other' {key}")
        c.execute(f"UPDATE question_exposure SET first_exposed_at = 9 {key}")                         # earlier is allowed
        c.execute(f"UPDATE question_exposure SET rank_blocking = 1, via = 'claim' {key}")             # 0 -> 1 may change via
        self.refused(f"UPDATE question_exposure SET rank_blocking = 0 {key}")                         # never down
        self.refused(f"UPDATE question_exposure SET rank_blocking = 0, via = 'issue' {key}")
        c.execute(f"UPDATE question_exposure SET rank_blocking = 1 {key}")                            # an unchanged block is allowed
        self.assertEqual(tuple(c.execute(f"SELECT rank_blocking, via, first_exposed_at FROM question_exposure {key}").fetchone()), (1, "claim", 9.0))

    def test_the_upsert_that_unions_exposure_only_upgrades_and_never_aborts(self):
        c = self.principals()
        upsert = ("INSERT INTO question_exposure (principal_id, question_id, family, first_exposed_at, via, rank_blocking) "
                  "VALUES ('p', 'q', 'free', ?, 'claim', ?) ON CONFLICT (principal_id, question_id, family) DO UPDATE SET "
                  "rank_blocking = 1, via = 'claim', first_exposed_at = MIN(question_exposure.first_exposed_at, excluded.first_exposed_at) "
                  "WHERE excluded.rank_blocking > question_exposure.rank_blocking")
        for existing, incoming, expected in ((0, 1, (1, "claim")), (1, 0, (1, "issue")), (1, 1, (1, "issue")), (0, 0, (0, "issue"))):
            self.conn.execute("INSERT INTO scoring_purge_gate (open) VALUES (1)")
            c.execute("DELETE FROM question_exposure")
            c.execute("DELETE FROM scoring_purge_gate")
            c.execute(*self.exposure(blocking=existing, at=10.0))
            c.execute(upsert, (5.0, incoming))
            row = c.execute("SELECT rank_blocking, via FROM question_exposure").fetchone()
            self.assertEqual(tuple(row), expected, f"{existing}+{incoming}")

    def test_exposure_is_append_only_except_under_the_purge_gate(self):
        c = self.principals()
        c.execute(*self.exposure())
        self.refused("DELETE FROM question_exposure")
        c.execute("INSERT INTO scoring_purge_gate (open) VALUES (1)")
        c.execute("DELETE FROM question_exposure")
        c.execute("DELETE FROM scoring_purge_gate")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM question_exposure").fetchone()[0], 0)

    def test_constraints(self):
        c = self.principals()
        for bad in (self.exposure(blocking=2), self.exposure(via="somewhere"), self.exposure(principal="nobody")):
            self.refused(*bad)


class PoolAndVersionTests(Phase1SchemaCase):
    def pool(self, question="q1", sha=SHA, state="ranked"):
        self.fresh()
        return ("INSERT INTO pool_questions (question_id, family, content_sha256, state, release_id, since, reason_code, operator_id) "
                "VALUES (?, 'official', ?, ?, 'rel', 1, 'seed', 'op')", (question, sha, state))

    def test_a_question_moves_only_ranked_then_retired_then_free(self):
        c = self.conn
        c.execute(*self.pool())
        self.refused("UPDATE pool_questions SET state = 'free', clone_audit_ref = 'a', license_ok_ref = 'b', exposure_audit_ref = 'c'")   # skips retired
        c.execute("UPDATE pool_questions SET state = 'retired'")
        self.refused("UPDATE pool_questions SET state = 'ranked'")
        self.refused("UPDATE pool_questions SET state = 'free'")                                                                         # no audit refs
        self.refused("UPDATE pool_questions SET state = 'free', clone_audit_ref = 'a', license_ok_ref = 'b'")                             # one missing
        c.execute("UPDATE pool_questions SET state = 'free', clone_audit_ref = 'a', license_ok_ref = 'b', exposure_audit_ref = 'c'")
        for target in ("ranked", "retired", "free"):
            self.refused("UPDATE pool_questions SET state = ?", (target,))
        self.refused("UPDATE pool_questions SET content_sha256 = ?", ("b" * 64,))
        self.refused("DELETE FROM pool_questions")

    def test_content_is_unique_across_ids_so_an_identical_clone_cannot_be_re_ranked(self):
        c = self.conn
        c.execute(*self.pool("q1"))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO pool_questions (question_id, family, content_sha256, state, release_id, since, reason_code, operator_id) "
                      "VALUES ('q2', 'official', ?, 'ranked', 'rel', 1, 'seed', 'op')", (SHA,))
        self.refused("INSERT INTO pool_questions (question_id, family, content_sha256, state, release_id, since, reason_code, operator_id) "
                     "VALUES ('q3', 'official', 'short', 'ranked', 'rel', 1, 'seed', 'op')")

    def test_lifecycle_events_are_append_only_and_only_legal_transitions(self):
        c = self.conn
        c.execute(*self.pool())
        c.execute("INSERT INTO pool_lifecycle_events (question_id, from_state, to_state, release_id, effective_at, operator_id, reason_code) "
                  "VALUES ('q1', 'ranked', 'retired', 'rel', 2, 'op', 'exposed')")
        self.refused("INSERT INTO pool_lifecycle_events (question_id, from_state, to_state, release_id, effective_at, operator_id, reason_code) "
                     "VALUES ('q1', 'free', 'ranked', 'rel', 2, 'op', 'x')")
        self.refused("INSERT INTO pool_lifecycle_events (question_id, from_state, to_state, release_id, effective_at, operator_id, reason_code) "
                     "VALUES ('q1', 'ranked', 'free', 'rel', 2, 'op', 'x')")
        self.refused("UPDATE pool_lifecycle_events SET reason_code = 'y'")
        self.refused("DELETE FROM pool_lifecycle_events")

    def version(self, vid="v1", catalog="cat1", denominator="12"):
        self.fresh()
        return ("INSERT INTO ranking_versions (id, family, catalog_version, rules_version, snapshot_digest, courses_k, denominator_total, "
                "created_at, status) VALUES (?, 'official', ?, 'r2', ?, 2, ?, 1, 'open')", (vid, catalog, SHA, denominator))

    def test_a_version_moves_open_to_frozen_exactly_once_and_its_snapshot_never_changes(self):
        c = self.conn
        c.execute(*self.version())
        self.refused("UPDATE ranking_versions SET status = 'frozen'")                                           # no frozen_at / reason
        self.refused("UPDATE ranking_versions SET status = 'frozen', frozen_at = 5, freeze_reason = 'r', courses_k = 3")
        self.refused("UPDATE ranking_versions SET catalog_version = 'other'")
        c.execute("UPDATE ranking_versions SET status = 'frozen', frozen_at = 5, freeze_reason = 'retirement'")
        self.refused("UPDATE ranking_versions SET status = 'open', frozen_at = NULL, freeze_reason = NULL")      # never back
        self.refused("UPDATE ranking_versions SET freeze_reason = 'again'")
        self.refused("DELETE FROM ranking_versions")
        with self.assertRaises(sqlite3.IntegrityError):                                                          # same (family, catalog, rules): no re-creation
            c.execute("INSERT INTO ranking_versions (id, family, catalog_version, rules_version, snapshot_digest, courses_k, denominator_total, "
                      "created_at, status) VALUES ('v2', 'official', 'cat1', 'r2', ?, 2, '12', 1, 'open')", (SHA,))

    def test_version_constraints_and_immutable_snapshot_tables(self):
        c = self.conn
        c.execute(*self.version())
        for bad in ("'12a'", "''", "'-5'", "'1.5'"):
            self.refused("INSERT INTO ranking_versions (id, family, catalog_version, rules_version, snapshot_digest, courses_k, denominator_total, "
                         f"created_at, status) VALUES ('vx', 'official', 'c9', 'r2', '{SHA}', 2, {bad}, 1, 'open')")
        self.refused(f"INSERT INTO ranking_versions (id, family, catalog_version, rules_version, snapshot_digest, courses_k, denominator_total, "
                     f"created_at, status) VALUES ('vy', 'official', 'c8', 'r2', '{SHA}', 0, '1', 1, 'open')")
        self.refused(f"INSERT INTO ranking_versions (id, family, catalog_version, rules_version, snapshot_digest, courses_k, denominator_total, "
                     f"created_at, status, frozen_at, freeze_reason) VALUES ('vz', 'official', 'c7', 'r2', '{SHA}', 2, '1', 1, 'open', 1, 'x')")
        c.execute("INSERT INTO ranking_version_courses (version_id, course_id, pool_size) VALUES ('v1', 'a', 3)")
        c.execute("INSERT INTO ranking_version_members (version_id, course_id, question_id, content_sha256) VALUES ('v1', 'a', 'q1', ?)", (SHA,))
        for table in ("ranking_version_courses", "ranking_version_members"):
            self.refused(f"DELETE FROM {table}")
        self.refused("UPDATE ranking_version_courses SET pool_size = 4")
        self.refused("UPDATE ranking_version_members SET content_sha256 = ?", ("b" * 64,))
        self.refused("INSERT INTO ranking_version_courses (version_id, course_id, pool_size) VALUES ('v1', 'b', 0)")
        self.refused("INSERT INTO ranking_version_courses (version_id, course_id, pool_size) VALUES ('nope', 'b', 1)")

    def test_standings_keys_are_digits_only_and_belong_to_a_version(self):
        c = self.conn
        c.execute(*self.version())
        c.execute("INSERT INTO users (id, created_at, status, deletion_at) VALUES ('u', 1, 'active', NULL)")
        c.execute("INSERT INTO scoring_principals (id, user_id, created_at, last_activity, state) VALUES ('p', 'u', 1, 1, 'active')")
        base = "INSERT INTO standings (version_id, scope, window_key, principal_id, numerator, score_key, answered, last_accepted_at) VALUES "
        c.execute(base + "('v1', 'total', 'all', 'p', '5', '0000017', 1, 1)")
        for bad in ("('v1', 'course:a', 'all', 'p', '5', '00x', 1, 1)", "('v1', 'course:a', 'all', 'p', '5', '', 1, 1)",
                    "('v1', 'course:a', 'all', 'p', '5', '-17', 1, 1)", "('nope', 'total', 'all', 'p', '5', '1', 1, 1)",
                    "('v1', 'total', 'all', 'ghost', '5', '1', 1, 1)", "('v1', 'total', 'all', 'p', '5', '1', 1, 1)"):
            self.refused(base + bad)
        c.execute("INSERT INTO board_revisions (version_id, window_key, revision) VALUES ('v1', 'all', 0)")
        self.refused("INSERT INTO board_revisions (version_id, window_key, revision) VALUES ('v1', 'all', 1)")
        self.refused("INSERT INTO board_revisions (version_id, window_key, revision) VALUES ('v1', 'week', -1)")

    def test_challenge_sets_are_solo_only_and_audit_is_append_only(self):
        c = self.fresh()
        base = ("INSERT INTO challenge_sets (id, kind, family, catalog_version, rules_version, algorithm_version, seed_ref, lock_policy, created_at) "
                "VALUES (?, ?, 'official', 'c', 'r2', 'a1', 'seed', ?, 1)")
        c.execute(base, ("s1", "solo", "global_first_attempt"))
        for kind in ("daily", "season", "sprint", "duel", ""):
            self.refused(base, (f"s-{kind}", kind, "global_first_attempt"))
        self.refused(base, ("s2", "solo", "anything"))
        c.execute("INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) VALUES ('s1', 0, 'q1', ?)", (SHA,))
        self.refused("INSERT INTO challenge_set_questions (set_id, position, question_id, content_sha256) VALUES ('s1', 1, 'q1', ?)", (SHA,))
        c.execute("INSERT INTO scoring_moderation_audit (at, operator, principal_id, family, action, reason_code, dry_run) "
                  "VALUES (1, 'op', 'p', 'free', 'hide', 'abuse', 0)")
        self.refused("INSERT INTO scoring_moderation_audit (at, operator, principal_id, family, action, reason_code, dry_run) "
                     "VALUES (1, 'op', 'p', 'free', 'delete', 'abuse', 0)")
        self.refused("UPDATE scoring_moderation_audit SET reason_code = 'x'")
        self.refused("DELETE FROM scoring_moderation_audit")


class PurgeTests(OriginTests):
    def test_purging_a_principal_removes_the_phase_1_rows_without_breaking_a_foreign_key(self):
        c = self.principals()
        c.execute("INSERT INTO ranking_versions (id, family, catalog_version, rules_version, snapshot_digest, courses_k, denominator_total, "
                  "created_at, status) VALUES ('v1', 'free', 'c', 'r2', ?, 1, '3', 1, 'open')", (SHA,))
        c.execute("INSERT INTO standings (version_id, scope, window_key, principal_id, numerator, score_key, answered, last_accepted_at) "
                  "VALUES ('v1', 'total', 'all', 'p', '2', '5', 1, 1)")
        c.execute("INSERT INTO course_units (version_id, window_key, principal_id, course_id, units, answered, last_accepted_at) "
                  "VALUES ('v1', 'all', 'p', 'c', 2, 1, 1)")
        c.execute("INSERT INTO anonymous_units (version_id, window_key, principal_id, course_id, units, answered, last_accepted_at) "
                  "VALUES ('v1', 'all', 'p2', 'c', 2, 1, 1)")
        c.execute("INSERT INTO question_exposure (principal_id, question_id, family, first_exposed_at, via, rank_blocking) VALUES ('p', 'q', 'free', 1, 'issue', 0)")
        c.execute("INSERT INTO question_issues (id, principal_id, question_id, family, catalog_version, revision, option_map, issued_at, expires_at) "
                  "VALUES ('i1', 'p', 'q', 'free', 'c', 'r', '{}', 1, 2)")
        c.execute("INSERT INTO challenge_sets (id, kind, family, catalog_version, rules_version, algorithm_version, seed_ref, lock_policy, created_at) "
                  "VALUES ('s1', 'solo', 'free', 'c', 'r2', 'a1', 'seed', 'global_first_attempt', 1)")
        c.execute("INSERT INTO set_participants (set_id, principal_id, started_at, status) VALUES ('s1', 'p', 1, 'active')")
        c.execute("INSERT INTO set_attempts (set_id, principal_id, question_id, issue_id) VALUES ('s1', 'p', 'q', 'i1')")
        class Db:                                   # the repository only needs a connection holder
            def connect(self_inner):
                return c
        repo = ScoringRepository(Db())
        repo.purge_principals(["p", "p2"])
        for table in ("standings", "course_units", "anonymous_units", "question_exposure", "set_attempts", "set_participants", "question_issues", "scoring_principals"):
            self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM ranking_versions").fetchone()[0], 1, "the version itself stays")


if __name__ == "__main__":
    unittest.main()
