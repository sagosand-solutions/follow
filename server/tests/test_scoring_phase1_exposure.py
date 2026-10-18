# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoring phase 1, step 7: a protected free pool, the exposure gate across both families, and family-bound board bumps.

Negative first: an in-memory free source never runs without the explicit development switch, a free question never falls back
to another source, a changed file stops start-up, and served material stays closed until every protected question it reveals
is locked. All data is synthetic.
"""
import dataclasses
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from server import pool
from server.app import Services, create_app
from server.config import load_config
from server.content_catalog import ContentCatalog
from server.models import ContentError, SetupError
from server.scoring_catalog import parse_catalog
from server.scoring_service import ManifestQuestionSource, ScoringServices
from server.tests.scoring_support import SCORING_ENV, build_scoring_fixture
from server.tests.support import ORIGIN, call, json_body

BASE_ENV = {"APP_ENV": "test", "PUBLIC_ORIGIN": ORIGIN, "DB_PATH": "/tmp/unused.db", "CSRF_SECRET_KEY": "a1" * 32,
            "SESSION_SECRET_KEY": "b2" * 32}


class ConfigTests(unittest.TestCase):
    def load(self, **extra):
        return load_config({**BASE_ENV, **extra})

    def test_the_development_source_needs_scoring_and_is_never_production(self):
        self.assertTrue(self.load(SCORING_ENABLED="true", SCORING_DEV_SOURCE="true").scoring.dev_source)
        self.assertFalse(self.load(SCORING_ENABLED="true").scoring.dev_source)
        with self.assertRaises(SetupError):
            self.load(SCORING_DEV_SOURCE="true")                                           # without SCORING_ENABLED
        with self.assertRaises(SetupError):
            self.load(APP_ENV="production", SCORING_ENABLED="true", SCORING_DEV_SOURCE="true")

    def test_the_private_free_pool_is_set_as_a_pair_and_excludes_the_development_source(self):
        ok = self.load(SCORING_ENABLED="true", SCORING_FREE_CONTENT_ROOT="/srv/private", SCORING_FREE_MANIFEST="/srv/manifest.json")
        self.assertEqual((ok.scoring.free_content_root, ok.scoring.free_manifest), ("/srv/private", "/srv/manifest.json"))
        summary = ok.redacted()["scoring"]
        self.assertTrue(summary["free_pool_private"])
        self.assertNotIn("/srv", repr(ok) + json.dumps(summary), "paths never reach a log line")
        for extra in ({"SCORING_FREE_CONTENT_ROOT": "/srv/private"}, {"SCORING_FREE_MANIFEST": "/srv/manifest.json"}):
            with self.assertRaises(SetupError):
                self.load(SCORING_ENABLED="true", **extra)
        with self.assertRaises(SetupError):
            self.load(SCORING_ENABLED="true", SCORING_DEV_SOURCE="true", SCORING_FREE_CONTENT_ROOT="/a", SCORING_FREE_MANIFEST="/b")
        with self.assertRaises(SetupError):
            self.load(SCORING_FREE_CONTENT_ROOT="/a", SCORING_FREE_MANIFEST="/b")                # scoring is off


class SourceBase(unittest.TestCase):
    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=("u1", "u2"), free=(("py-free", 4),))
        self.addCleanup(tmp.cleanup)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.memory = self.fx.scoring.question_source.free

    def private_free_catalog(self, drop=(), tamper=()):
        root = Path(self.dir.name)
        entries = []
        for qid, raw in self.memory.items():
            if qid in drop:
                continue
            body = raw + b" " if qid in tamper else raw
            (root / f"{qid}.txt").write_bytes(body)
            entries.append({"kind": "question", "id": qid, "product_id": "free-pool", "path": f"{qid}.txt",
                            "sha256": hashlib.sha256(raw if qid not in tamper else body).hexdigest()})
        (root / "free-manifest.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")
        return ContentCatalog.load(root, root / "free-manifest.json")

    def app_with(self, source, *, dev_source):
        scoring = ScoringServices(self.fx.scoring.catalogs, source)
        settings = dataclasses.replace(self.fx.settings, scoring=dataclasses.replace(self.fx.settings.scoring, dev_source=dev_source))
        services = Services(open_database=lambda: self.fx.db(), catalog=self.fx.content_catalog, scoring=scoring,
                            clock=lambda: self.fx.clock[0])
        return create_app(settings, services)


class FreeSourceTests(SourceBase):
    def test_the_memory_source_is_refused_without_the_development_switch(self):
        memory = ManifestQuestionSource(self.fx.content_catalog, self.memory)
        with self.assertRaises(SetupError) as caught:
            self.app_with(memory, dev_source=False)
        self.assertNotIn("py-free", str(caught.exception))
        self.app_with(memory, dev_source=True)

    def test_a_private_catalog_starts_without_the_switch_and_serves_the_pool(self):
        source = ManifestQuestionSource(self.fx.content_catalog, free_catalog=self.private_free_catalog())
        app = self.app_with(source, dev_source=False)                                       # also a restart: sync/versions are idempotent
        self.fx.app = app
        _, issue = self.fx.issue("py-free-f00")
        self.assertEqual(self.fx.answer(issue, "Right", key="p" * 16)[0], 200)

    def test_a_changed_file_or_a_missing_question_stops_start_up_and_never_falls_back_to_memory(self):
        for kwargs in ({"tamper": ("py-free-f01",)}, {"drop": ("py-free-f02",)}):
            with self.subTest(kwargs=kwargs):
                source = ManifestQuestionSource(self.fx.content_catalog, free_catalog=self.private_free_catalog(**kwargs))
                self.assertEqual(source.free, {}, "the private source carries no in-memory copy to fall back to")
                with self.assertRaises(SetupError):
                    self.app_with(source, dev_source=False)

    def test_one_source_for_the_free_pool_not_two(self):
        with self.assertRaises(ContentError):
            ManifestQuestionSource(self.fx.content_catalog, self.memory, free_catalog=self.private_free_catalog())

    def test_the_source_reads_through_the_hash_and_the_id_only(self):
        source = ManifestQuestionSource(self.fx.content_catalog, free_catalog=self.private_free_catalog())
        sha = hashlib.sha256(self.memory["py-free-f00"]).hexdigest()
        self.assertEqual(source.read("free", "py-free-f00", sha)["id"], "py-free-f00")
        for question_id, digest in (("py-free-f00", "0" * 64), ("py-free-nope", sha), ("py-q00", sha)):
            with self.assertRaises(ContentError):
                source.read("free", question_id, digest)


class ExposureGateTests(unittest.TestCase):
    """/content/study/study-a reveals the first two official answers; a free row is added so it also reveals free f00."""

    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=("u1",), free=(("py-free", 4),))
        self.addCleanup(tmp.cleanup)

    def with_free_row(self, sha=None):
        catalog = self.fx.catalogs["free"]
        study = self.fx.scoring.catalogs["official"].exposure("study", "study-a")
        courses = [{"course_id": c, "N_c": catalog.pool_sizes[c], "questions": [
            {"question_id": pq.question_id, "product_id": None, "content_sha256": pq.content_sha256}
            for pq in catalog.questions.values() if pq.course_id == c]} for c in catalog.course_ids]
        exposures = [{"kind": "question", "id": q, "content_sha256": pq.content_sha256, "affected_question_ids": [q]}
                     for q, pq in catalog.questions.items()]
        exposures.append({"kind": "study", "id": "study-a", "content_sha256": sha or study[0], "affected_question_ids": ["py-free-f00"]})
        self.fx.scoring.catalogs["free"] = parse_catalog({"schema_version": 1, "catalog_version": catalog.catalog_version, "rules_version": "r2",
                                                          "family": "free", "courses": courses, "exposures": exposures}, expected_family="free")

    def study(self):
        return self.fx.get("/content/study/study-a")[0]

    def lock(self, *qids):
        for qid in qids:
            _, issue = self.fx.issue(qid)
            self.assertEqual(self.fx.answer(issue, "Right", key=(qid + "k").ljust(16, "k")[:32])[0], 200)

    def test_material_tied_to_both_families_waits_for_every_lock(self):
        self.with_free_row()
        self.assertEqual(self.study(), 404, "paid, but nothing locked")
        self.lock("py-q00", "py-q01")
        self.assertEqual(self.study(), 404, "the official questions are locked but the free one is not")
        self.lock("py-free-f00")
        self.assertEqual(self.study(), 200)

    def test_a_free_row_for_different_bytes_closes_the_material(self):
        self.with_free_row(sha="e" * 64)
        self.lock("py-q00", "py-q01", "py-free-f00")
        self.assertEqual(self.study(), 404)

    def test_a_retired_question_still_needs_its_lock_and_a_freed_one_does_not(self):
        self.with_free_row()
        self.lock("py-q00", "py-q01")
        db = self.fx.db()
        self.addCleanup(db.close)
        free_version = db.connect().execute("SELECT id FROM ranking_versions WHERE family = 'free'").fetchone()[0]
        pool.freeze(db, free_version, reason_code="season-end", now=self.fx.clock[0])
        pool.retire(db, "py-free-f00", release_id="rel-2", reason_code="exposed", operator_id="op-1", now=self.fx.clock[0])
        self.assertEqual(self.study(), 404, "retired is still protected")
        pool.free(db, "py-free-f00", release_id="rel-3", reason_code="reviewed", operator_id="op-1", now=self.fx.clock[0],
                  clone_audit_ref="c1", license_ok_ref="l1", exposure_audit_ref="e1")
        self.assertEqual(self.study(), 200, "a question that is public needs no lock")

    def test_a_question_the_pool_does_not_know_is_treated_as_protected(self):
        self.with_free_row()
        self.lock("py-q00", "py-q01")
        db = self.fx.db()
        self.addCleanup(db.close)
        db.connect().execute("DROP TRIGGER pool_questions_no_delete")
        db.connect().execute("DELETE FROM pool_questions WHERE question_id = 'py-free-f00'")
        self.assertEqual(self.study(), 404, "unknown to the pool and not locked: closed, not assumed public")


class FamilyBumpTests(unittest.TestCase):
    def test_a_free_preference_change_moves_only_the_free_boards(self):
        fx, tmp = build_scoring_fixture(users=("u1",), free=(("py-free", 4),))
        self.addCleanup(tmp.cleanup)
        for qid in ("py-q00", "py-free-f00"):
            _, issue = fx.issue(qid)
            fx.answer(issue, "Right", key=(qid + "k").ljust(16, "k")[:32])
        revisions = lambda family: sorted(tuple(r) for r in _rows(fx, "SELECT b.window_key, b.revision FROM board_revisions b "
                                                                       "JOIN ranking_versions v ON v.id = b.version_id WHERE v.family = ?", (family,)))
        self.assertEqual(fx.post("/scoring/preferences", {"family": "free", "opt_in": True, "nickname": "Quiet one"}, "u1")[0], 200)
        official_before = revisions("official")
        free_before = revisions("free")
        self.assertEqual(fx.post("/scoring/preferences", {"family": "free", "opt_in": True, "nickname": "Louder one"}, "u1")[0], 200)
        self.assertEqual(revisions("official"), official_before)
        self.assertEqual([(w, r - dict(free_before)[w]) for w, r in revisions("free")], [(w, 1) for w, _ in free_before])


def _rows(fx, sql, args=()):
    db = fx.db()
    try:
        return [tuple(r) for r in db.connect().execute(sql, args)]
    finally:
        db.close()


if __name__ == "__main__":
    unittest.main()
