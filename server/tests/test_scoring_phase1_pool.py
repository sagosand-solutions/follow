# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoring phase 1, step 3: the question pool lifecycle, the issue gate and the operator tool.

Negative first: a retired, free, unknown or frozen question is never issued, an open issue never outlives its retirement or
freeze, and a lifecycle step that needs another step first is refused whole. All data is synthetic.
"""
import hashlib
import io
import json
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from server import pool
from server.models import ContentError
from server.scoring_repo import ScoringRepository
from server.tests.scoring_support import build_scoring_fixture
from server.tests.support import json_body

REFS = dict(clone_audit_ref="audit-clone-1", license_ok_ref="licence-ok-1", exposure_audit_ref="exposure-1")


class Base(unittest.TestCase):
    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=("u1", "u2"), free=(("py-free", 9),))
        self.addCleanup(tmp.cleanup)
        self.now = self.fx.clock[0]

    def db(self):
        db = self.fx.db()
        self.addCleanup(db.close)
        return db

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
        """The open version start-up created for the family's catalog."""
        return self.rows("SELECT id FROM ranking_versions WHERE family = ?", (family,))[0][0]

    def with_board(self, vid):
        self.run_sql("INSERT OR IGNORE INTO board_revisions (version_id, window_key, revision) VALUES (?, 'all', 0)", (vid,))

    def quiet_freeze(self, vid):
        """Freeze without the operator tool's side effects (no withdrawal, no revision bump)."""
        self.run_sql("UPDATE ranking_versions SET status = 'frozen', frozen_at = 1, freeze_reason = 'manual' WHERE id = ?", (vid,))

    def add_version(self, vid, family="free", catalog="later", members=()):
        """A second, open version (a later catalog) holding the given questions."""
        self.run_sql("INSERT INTO ranking_versions (id, family, catalog_version, rules_version, snapshot_digest, courses_k, denominator_total, "
                     "created_at, status) VALUES (?, ?, ?, 'r2', ?, 1, '3', 1, 'open')", (vid, family, catalog, "a" * 64))
        for qid in members:
            sha = self.rows("SELECT content_sha256 FROM pool_questions WHERE question_id = ?", (qid,))[0][0]
            self.run_sql("INSERT INTO ranking_version_members (version_id, course_id, question_id, content_sha256) VALUES (?, 'c', ?, ?)",
                         (vid, qid, sha))

    def retire(self, qid, freeze=True, **over):
        """Retire a question; by default first freeze every open version that holds it, as the operator must."""
        kw = dict(release_id="rel-2", reason_code="exposed", operator_id="op-1", now=self.now + 10)
        kw.update(over)
        if freeze:
            for (vid,) in self.rows("SELECT v.id FROM ranking_versions v JOIN ranking_version_members m ON m.version_id = v.id "
                                    "WHERE m.question_id = ? AND v.status = 'open'", (qid,)):
                pool.freeze(self.db(), vid, reason_code="retirement", now=self.now + 9)
        return pool.retire(self.db(), qid, **kw)

    def release(self, qid, **over):
        kw = dict(release_id="rel-3", reason_code="reviewed", operator_id="op-1", now=self.now + 20, **REFS)
        kw.update(over)
        return pool.free(self.db(), qid, **kw)

    def state(self, qid):
        return self.rows("SELECT state FROM pool_questions WHERE question_id = ?", (qid,))[0][0]

    def issue(self, qid, user="u1"):
        response = self.fx.post(f"/scoring/questions/{qid}/issue", {}, user)
        return response, (json_body(response) if response[0] == 200 else None)


class SyncTests(Base):
    def test_start_up_registers_every_catalog_question_as_ranked_once(self):
        count = sum(len(c.questions) for c in self.fx.catalogs.values())
        self.assertEqual(self.rows("SELECT COUNT(*), COUNT(DISTINCT state), MIN(state), MIN(operator_id) FROM pool_questions"),
                         [(count, 1, "ranked", "startup")])
        entries = [(pq.question_id, f, pq.content_sha256) for f, c in self.fx.catalogs.items() for pq in c.questions.values()]
        db = self.db()
        with db.transaction(immediate=True):
            self.assertEqual(ScoringRepository(db).sync_pool(entries, self.now + 1, "rel-9"), 0, "a second start-up registers nothing new")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM pool_lifecycle_events"), [(0,)])

    def test_each_family_is_registered_under_its_own_catalog_version(self):
        for family, catalog in self.fx.catalogs.items():
            self.assertEqual(self.rows("SELECT DISTINCT release_id FROM pool_questions WHERE family = ?", (family,)), [(catalog.catalog_version,)])
        self.assertNotEqual(self.fx.catalogs["free"].catalog_version, self.fx.catalogs["official"].catalog_version)

    def test_a_changed_or_cloned_question_stops_start_up_without_leaking_hashes(self):
        db = self.db()
        repo = ScoringRepository(db)
        known = self.rows("SELECT question_id, content_sha256 FROM pool_questions WHERE family = 'free' ORDER BY question_id")
        with self.assertRaises(ContentError) as changed:
            with db.transaction(immediate=True):
                repo.sync_pool([(known[0][0], "free", "b" * 64)], self.now, "rel")
        with self.assertRaises(ContentError) as clone:
            with db.transaction(immediate=True):
                repo.sync_pool([("py-free-brand-new", "free", known[1][1])], self.now, "rel")
        for exc in (changed.exception, clone.exception):
            self.assertNotIn(known[0][1], str(exc))
            self.assertNotIn(known[1][1], str(exc))
        with self.assertRaises(ContentError):                       # the same id moved to the other family
            with db.transaction(immediate=True):
                repo.sync_pool([(known[0][0], "official", known[0][1])], self.now, "rel")


class IssueGateTests(Base):
    def test_a_ranked_question_is_issued_and_every_other_state_is_unavailable(self):
        self.assertEqual(self.issue("py-free-f00")[0][0], 200)
        self.retire("py-free-f01")
        self.retire("py-free-f02")
        self.release("py-free-f02")
        for qid in ("py-free-f01", "py-free-f02", "py-free-f03"):
            self.assertEqual(self.issue(qid)[0][0], 503, qid)       # retired, free, and a ranked one of the now frozen version
        self.assertEqual(self.rows("SELECT COUNT(*) FROM question_issues WHERE question_id IN ('py-free-f01', 'py-free-f02', 'py-free-f03')"), [(0,)])

    def test_a_question_that_is_not_registered_is_never_issued(self):
        self.run_sql("DROP TRIGGER pool_questions_no_delete")
        self.run_sql("DELETE FROM pool_questions WHERE question_id = 'py-free-f03'")
        self.assertEqual(self.issue("py-free-f03")[0][0], 503)

    def test_a_question_that_is_not_in_an_open_version_with_this_exact_content_is_never_issued(self):
        self.run_sql("DROP TRIGGER ranking_version_members_immutable")
        self.run_sql("UPDATE ranking_version_members SET content_sha256 = ? WHERE question_id = 'py-free-f00'", ("c" * 64,))
        self.assertEqual(self.issue("py-free-f00")[0][0], 503, "same id, different content")
        self.run_sql("DROP TRIGGER ranking_version_members_no_delete")
        self.run_sql("DELETE FROM ranking_version_members WHERE question_id = 'py-free-f01'")
        self.assertEqual(self.issue("py-free-f01")[0][0], 503, "not a member")
        self.assertEqual(self.issue("py-free-f02")[0][0], 200)

    def test_a_frozen_version_issues_nothing_for_its_family_and_catalog(self):
        v1 = self.version("free")
        self.assertEqual(self.issue("py-free-f00")[0][0], 200)
        pool.freeze(self.db(), v1, reason_code="season-end", now=self.now + 5)
        self.assertEqual(self.issue("py-free-f04")[0][0], 503, "the whole version stops, not just one question")
        self.assertEqual(self.issue("py-q00")[0][0], 200, "the other family is unaffected")

    def test_a_locked_reader_still_sees_their_result_after_retirement(self):
        _, issue = self.issue("py-free-f00")
        self.assertEqual(self.fx.answer(issue, "Right", user="u1", key="a" * 16)[0], 200)
        self.retire("py-free-f00")
        response, _ = self.issue("py-free-f00")
        self.assertEqual((response[0], json_body(response)["error"]["code"]), (409, "locked"))


class RetirementTests(Base):
    def test_retiring_withdraws_open_issues_and_a_late_answer_writes_nothing(self):
        _, mine = self.issue("py-free-f00")
        _, theirs = self.issue("py-free-f01")
        self.quiet_freeze(self.version("free"))                      # the version is frozen first, as required; issues still open
        self.assertEqual(self.retire("py-free-f00", freeze=False), 1, "only the retired question's own issue is withdrawn by retire")
        response = self.fx.answer(mine, "Right", user="u1", key="b" * 16)
        self.assertEqual((response[0], json_body(response)["error"]["code"]), (409, "expired"))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM question_issues WHERE consumed_at IS NULL"), [(1,)])

    def test_an_accepted_answer_still_replays_after_retirement_and_stays_in_place(self):
        _, issue = self.issue("py-free-f00")
        first = self.fx.answer(issue, "Right", user="u1", key="d" * 16)
        self.retire("py-free-f00")
        again = self.fx.answer(issue, "Right", user="u1", key="d" * 16)
        self.assertEqual((first[0], again[0], json_body(again)["replayed"]), (200, 200, True))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts"), [(1,)])

    def test_a_guard_at_answer_time_holds_even_if_the_issue_was_not_withdrawn(self):
        _, issue = self.issue("py-free-f00")
        self.run_sql("DROP TRIGGER pool_questions_monotone")
        self.run_sql("UPDATE pool_questions SET state = 'retired' WHERE question_id = 'py-free-f00'")
        self.assertEqual(self.fx.answer(issue, "Right", user="u1", key="e" * 16)[0], 409)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts"), [(0,)])

    def test_the_lifecycle_moves_one_way_and_records_who_and_why(self):
        self.retire("py-free-f00", clone_audit_ref="audit-7")
        self.release("py-free-f00")
        self.assertEqual(self.state("py-free-f00"), "free")
        self.assertEqual(self.rows("SELECT from_state, to_state, release_id, operator_id, reason_code FROM pool_lifecycle_events ORDER BY id"),
                         [("ranked", "retired", "rel-2", "op-1", "exposed"), ("retired", "free", "rel-3", "op-1", "reviewed")])
        for call in (lambda: self.retire("py-free-f00"), lambda: self.release("py-free-f00")):
            with self.assertRaises(pool.PoolError):
                call()
        with self.assertRaises(pool.PoolError):
            self.release("py-free-f05")                                   # still ranked: cannot skip retired
        with self.assertRaises(pool.PoolError):
            self.retire("no-such-question")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM pool_lifecycle_events"), [(2,)])

    def test_freeing_checks_the_same_short_codes(self):
        self.retire("py-free-f00")
        for over in ({"operator_id": "someone@example.org"}, {"reason_code": "No Good"}, {"release_id": "bad id"}):
            with self.subTest(over=over):
                with self.assertRaises(pool.PoolError):
                    self.release("py-free-f00", **over)
        self.assertEqual(self.state("py-free-f00"), "retired")

    def test_freeing_needs_all_three_references(self):
        self.retire("py-free-f00")
        for missing in REFS:
            with self.subTest(missing=missing):
                with self.assertRaises(pool.PoolError):
                    self.release("py-free-f00", **{missing: ""})
                with self.assertRaises(pool.PoolError):
                    self.release("py-free-f00", **{missing: None})
        self.assertEqual(self.state("py-free-f00"), "retired")

    def test_an_open_version_blocks_retirement_and_release_until_it_is_frozen(self):
        v1 = self.version("free")
        with self.assertRaises(pool.PoolError) as blocked:
            self.retire("py-free-f00", freeze=False)
        self.assertIn(v1, str(blocked.exception))
        self.assertEqual(self.state("py-free-f00"), "ranked")
        pool.freeze(self.db(), v1, reason_code="retirement", now=self.now + 1)
        self.retire("py-free-f00", freeze=False)
        self.add_version("v3", "free", catalog="later")
        self.run_sql("INSERT INTO ranking_version_members (version_id, course_id, question_id, content_sha256) "
                     "SELECT 'v3', 'c', question_id, content_sha256 FROM pool_questions WHERE question_id = 'py-free-f00'")
        with self.assertRaises(pool.PoolError):
            self.release("py-free-f00")                                   # a later open version holds it
        self.assertEqual(self.state("py-free-f00"), "retired")

    def test_a_failure_in_the_audit_row_rolls_the_whole_retirement_back(self):
        _, issue = self.issue("py-free-f00")
        self.quiet_freeze(self.version("free"))
        self.run_sql("CREATE TRIGGER fail_audit BEFORE INSERT ON pool_lifecycle_events BEGIN SELECT RAISE(ABORT, 'boom'); END")
        with self.assertRaises(Exception):
            self.retire("py-free-f00", freeze=False)
        self.assertEqual(self.state("py-free-f00"), "ranked")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM question_issues WHERE consumed_at IS NOT NULL"), [(0,)], "the withdrawal was rolled back too")

    def test_operator_reason_release_and_references_are_short_codes_not_personal_data(self):
        for over in ({"operator_id": "someone@example.org"}, {"operator_id": "two words"}, {"operator_id": ""}, {"operator_id": "x" * 65},
                     {"reason_code": "Because I said so"}, {"reason_code": ""}, {"release_id": "bad id"}, {"clone_audit_ref": "a b"}):
            with self.subTest(over=over):
                with self.assertRaises(pool.PoolError):
                    self.retire("py-free-f00", **over)
        self.assertEqual(self.state("py-free-f00"), "ranked")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM pool_lifecycle_events"), [(0,)])


class FreezeTests(Base):
    def test_freeze_withdraws_only_that_family_and_catalog_and_raises_the_board_revision(self):
        v1 = self.version("free")
        self.with_board(v1)
        _, free_issue = self.issue("py-free-f00")
        _, official_issue = self.issue("py-q00")
        self.run_sql("INSERT INTO question_issues (id, principal_id, question_id, family, catalog_version, revision, option_map, issued_at, "
                     "expires_at) SELECT 'older-issue', principal_id, 'py-free-f05', 'free', 'older', 'r', '{}', 1, 99999999999 "
                     "FROM question_issues WHERE id = ?", (free_issue["issue_id"],))
        self.assertEqual(pool.freeze(self.db(), v1, reason_code="season-end", now=self.now + 3), 1)
        self.assertEqual(self.rows("SELECT status, freeze_reason FROM ranking_versions WHERE id = ?", (v1,)), [("frozen", "season-end")])
        self.assertEqual(self.rows("SELECT consumed_at IS NULL FROM question_issues WHERE catalog_version = 'older'"), [(1,)],
                         "an issue of another catalog version in the same family is not this version's")
        self.assertEqual(self.rows("SELECT revision FROM board_revisions WHERE version_id = ?", (v1,)), [(1,)])
        response = self.fx.answer(free_issue, "Right", user="u1", key="g" * 16)
        self.assertEqual((response[0], json_body(response)["error"]["code"]), (409, "expired"))
        self.assertEqual(self.fx.answer(official_issue, "Right", user="u1", key="h" * 16)[0], 200)

    def test_freeze_refuses_unknown_repeated_and_malformed_requests(self):
        v1 = self.version("free")
        self.with_board(v1)
        with self.assertRaises(pool.PoolError):
            pool.freeze(self.db(), "nope", reason_code="season-end", now=1)
        with self.assertRaises(pool.PoolError):
            pool.freeze(self.db(), v1, reason_code="Bad Reason!", now=1)
        pool.freeze(self.db(), v1, reason_code="season-end", now=2)
        with self.assertRaises(pool.PoolError):
            pool.freeze(self.db(), v1, reason_code="season-end", now=3)
        self.assertEqual(self.rows("SELECT revision FROM board_revisions"), [(1,)], "a refused freeze changes nothing")

    def test_the_answer_guard_holds_when_a_version_is_frozen_without_the_withdrawal(self):
        _, issue = self.issue("py-free-f00")
        self.quiet_freeze(self.version("free"))
        self.assertEqual(self.fx.answer(issue, "Right", user="u1", key="i" * 16)[0], 409)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM first_attempts"), [(0,)])


class StaticAndCliTests(Base):
    def public_root(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, content in files.items():
            path = Path(tmp.name) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return tmp.name

    def test_check_static_finds_ids_hashes_and_copies_of_protected_questions_only(self):
        sha = self.rows("SELECT content_sha256 FROM pool_questions WHERE question_id = 'py-free-f00'")[0][0]
        self.retire("py-free-f01")
        self.retire("py-free-f02")
        self.release("py-free-f02")
        root = self.public_root({
            "data/index.json": '{"questions": [{"id": "py-free-f00"}]}',                 # ranked id
            "data/retired.json": '{"id": "py-free-f01"}',                                  # retired id
            "data/copy.bin": b"x",                                                          # unrelated
            "data/hash.json": f'{{"sha256": "{sha}"}}',                                    # hash of a ranked question
            "data/free.json": '{"id": "py-free-f02"}',                                     # now free: allowed
            "data/near.json": '{"id": "py-free-f000", "also": "xpy-free-f00"}',            # only near misses
            "data/ref.json": '{"path": "questions/py-free-f04.json"}',                      # an id used as a file name
            "py-free-f03.txt": "no mention inside",                                         # id in the file name
        })
        findings = pool.check_static(self.db(), root)
        self.assertEqual(sorted((p, q, how) for p, q, how in findings),
                         [("data/hash.json", "py-free-f00", "content hash"), ("data/index.json", "py-free-f00", "id"),
                          ("data/ref.json", "py-free-f04", "id"), ("data/retired.json", "py-free-f01", "id"),
                          ("py-free-f03.txt", "py-free-f03", "id")])

    def test_check_static_finds_a_byte_identical_copy_of_the_question_file(self):
        data = b'{"id": "something-else"}'
        self.run_sql("INSERT INTO pool_questions (question_id, family, content_sha256, state, release_id, since, reason_code, operator_id) "
                     "VALUES ('copied-q', 'free', ?, 'ranked', 'rel', 1, 'seed', 'op')", (hashlib.sha256(data).hexdigest(),))
        findings = pool.check_static(self.db(), self.public_root({"assets/blob.dat": data}))
        self.assertEqual(findings, [("assets/blob.dat", "copied-q", "content hash")])

    def test_a_file_over_the_scan_limit_is_a_finding_and_one_at_the_limit_is_scanned(self):
        limit = 40
        at_limit = b'{"id": "py-free-f00"}' + b" " * (limit - len('{"id": "py-free-f00"}'))
        over = b"x" * (limit + 1)
        root = self.public_root({"at-limit.json": at_limit, "over.json": over})
        with mock.patch.object(pool, "MAX_SCAN_BYTES", limit):
            findings = pool.check_static(self.db(), root)
            code, _, err = self.run_cli("check-static", "--public-root", root)
        self.assertEqual(sorted(findings), [("at-limit.json", "py-free-f00", "id"), ("over.json", "-", "file too large to scan")])
        self.assertEqual(code, 1)
        self.assertIn("over.json: - (file too large to scan)", err)

    def test_a_symlink_is_a_finding_and_is_never_followed(self):
        outside = self.public_root({"secret.json": '{"id": "py-free-f00"}'})
        root = self.public_root({"ok.json": "{}"})
        Path(root, "link.json").symlink_to(Path(outside, "secret.json"))
        self.assertEqual(pool.check_static(self.db(), root), [("link.json", "-", "symlink is not followed or scanned")])
        Path(root, "link.json").unlink()
        Path(root, "dir-link").symlink_to(outside, target_is_directory=True)
        self.assertEqual(pool.check_static(self.db(), root), [("dir-link", "-", "symlink is not followed or scanned")])

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = pool.main(["--db", self.fx.path, *argv], clock=lambda: self.now + 50)
        return code, out.getvalue(), err.getvalue()

    def test_the_command_line_exit_codes_and_messages(self):
        _, issue = self.issue("py-free-f00")
        self.assertEqual(self.run_cli("freeze", "--version", self.version("free"), "--reason", "retirement")[0], 0)
        code, out, err = self.run_cli("retire", "--question", "py-free-f00", "--release", "rel-2", "--reason", "exposed", "--operator", "op-1")
        self.assertEqual((code, out.strip(), err), (0, "retired py-free-f00; 0 open issue(s) withdrawn", ""))
        code, out, err = self.run_cli("retire", "--question", "py-free-f00", "--release", "rel-2", "--reason", "exposed", "--operator", "op-1")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("refused:", err)
        code, out, _ = self.run_cli("free", "--question", "py-free-f00", "--release", "rel-3", "--reason", "reviewed", "--operator", "op-1",
                                    "--clone-audit", "c1", "--license-ok", "l1", "--exposure-audit", "e1")
        self.assertEqual((code, out.strip()), (0, "released py-free-f00"))
        official = self.version("official")
        code, out, _ = self.run_cli("freeze", "--version", official, "--reason", "season-end")
        self.assertEqual(code, 0)
        self.assertIn(f"frozen {official}", out)
        self.assertEqual(self.run_cli("freeze", "--version", official, "--reason", "season-end")[0], 2)

    def test_check_static_exits_one_with_paths_and_ids_only(self):
        root = self.public_root({"data/leak.json": '{"id": "py-free-f00", "secret": "PRIVATE-SENTINEL-TEXT"}'})
        code, out, err = self.run_cli("check-static", "--public-root", root)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("data/leak.json: py-free-f00 (id)", err)
        self.assertNotIn("PRIVATE-SENTINEL-TEXT", err)
        clean = self.public_root({"data/ok.json": "{}"})
        self.assertEqual(self.run_cli("check-static", "--public-root", clean)[0], 0)
        self.assertEqual(self.run_cli("check-static", "--public-root", clean + "/missing")[0], 2)

    def test_a_missing_database_is_refused_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = pool.main(["--db", tmp + "/nope.db", "check-static", "--public-root", tmp])
            self.assertEqual(code, 2)
            self.assertFalse(Path(tmp, "nope.db").exists())


if __name__ == "__main__":
    unittest.main()
