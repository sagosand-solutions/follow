"""Tests for the generic scoring-catalog builder (task 024).

All content here is synthetic: no private question, path or metadata is read or
written. Run from the repository root::

    python -m unittest discover -s server/tests
"""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from server import scoring_catalog_build as builder
from server.scoring_catalog_build import CatalogBuildError


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_question(question_id, *, license="LicenseRef-Proprietary", options=5, **overrides):
    option_list = [
        {
            "id": f"opt-{index}",
            "text": f"answer {index}",
            "score": 1 if index == 0 else 0,
            "feedback": f"feedback {index}",
            "misconception": None if index == 0 else "py.example",
        }
        for index in range(options)
    ]
    question = {
        "schema_version": "0.1",
        "id": question_id,
        "language": "python",
        "locale": "en",
        "type": "output",
        "level": "beginner",
        "difficulty": 0.3,
        "concepts": ["py.example"],
        "prompt": "What does this print?",
        "snippets": [{"id": "s1", "code": "print(1)"}],
        "options": option_list,
        "explanation": "Because.",
        "verification": {"status": "specified", "cases": [{"id": "c1", "snippet_id": "s1",
                         "stdin": "", "expected": {"stdout": "1", "exit_code": 0}}],
                         "rationale": "checked"},
        "author": {"name": "synthetic"},
        "license": license,
    }
    question.update(overrides)
    return question


class BuilderFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.content = self.root / "content"
        self.content.mkdir()
        self.entries = []

    def tearDown(self):
        self._tmp.cleanup()

    # -- input builders ---------------------------------------------------
    def add_question(self, question_id, *, product="python-lifetime", license="LicenseRef-Proprietary",
                     data=None, path=None, entry_sha=None):
        payload = json.dumps(data if data is not None else make_question(question_id, license=license)).encode("utf-8")
        rel = path or f"{question_id}.json"
        (self.content / rel).write_bytes(payload)
        self.entries.append({
            "kind": "question", "id": question_id, "product_id": product, "path": rel,
            "sha256": entry_sha or _sha(payload),
        })
        return _sha(payload)

    def add_material(self, kind, ident, *, product="python-lifetime", payload=b'{"m": 1}'):
        rel = f"{kind}-{ident}.json"
        (self.content / rel).write_bytes(payload)
        self.entries.append({"kind": kind, "id": ident, "product_id": product, "path": rel,
                             "sha256": _sha(payload)})
        return _sha(payload)

    def freeze_manifest(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"entries": self.entries}), encoding="utf-8")
        return path

    def write_release(self, courses, *, family="official", rules="r2", catalog_version="rel-1",
                      previously_exposed=False):
        # courses: {course_id: [(question_id, product_id), ...]}
        path = self.root / "release.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "catalog_version": catalog_version,
            "rules_version": rules,
            "family": family,
            "courses": [{"course_id": cid,
                         "questions": [{"question_id": q, "product_id": p,
                                        "previously_exposed": previously_exposed}
                                       for q, p in questions]}
                        for cid, questions in courses.items()],
        }), encoding="utf-8")
        return path

    def write_exposures(self, rows):
        path = self.root / "exposures.json"
        path.write_text(json.dumps({"schema_version": 1, "exposures": rows}), encoding="utf-8")
        return path

    def self_and_safe(self, question_hashes, *, extras=()):
        rows = [{"kind": "question", "id": qid, "content_sha256": digest,
                 "affected_question_ids": [qid]} for qid, digest in question_hashes.items()]
        rows.extend(extras)
        return rows

    def build(self, release, exposures):
        return builder.build_artifact(self.content, self.freeze_manifest(), release, exposures)


class HappyPathTest(BuilderFixture):
    def test_single_course_artifact_shape(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        artifact = json.loads(self.build(release, exposures))
        self.assertEqual(artifact["schema_version"], 1)
        self.assertEqual(artifact["family"], "official")
        self.assertEqual(artifact["rules_version"], "r2")
        self.assertEqual(artifact["courses"], [{
            "course_id": "python", "N_c": 1,
            "questions": [{"question_id": "py-alpha", "product_id": "python-lifetime",
                           "content_sha256": digest}],
        }])
        self.assertEqual(artifact["exposures"], [{
            "kind": "question", "id": "py-alpha", "content_sha256": digest,
            "affected_question_ids": ["py-alpha"],
        }])

    def test_three_courses_distinct_pool_sizes(self):
        hashes = {}
        courses = {"python": [], "production": [], "sql": []}
        for course, count in (("python", 2), ("production", 3), ("sql", 1)):
            for index in range(count):
                qid = f"{course[:2]}-q{index}"
                hashes[qid] = self.add_question(qid, product=f"{course}-lifetime")
                courses[course].append((qid, f"{course}-lifetime"))
        release = self.write_release(courses)
        exposures = self.write_exposures(self.self_and_safe(hashes))
        artifact = json.loads(self.build(release, exposures))
        sizes = {course["course_id"]: course["N_c"] for course in artifact["courses"]}
        self.assertEqual(sizes, {"production": 3, "python": 2, "sql": 1})

    def test_free_family_allows_null_product(self):
        digest = self.add_question("py-free", product="python-lifetime", license="CC-BY-SA-4.0")
        release = self.write_release({"python": [("py-free", None)]}, family="free")
        exposures = self.write_exposures(self.self_and_safe({"py-free": digest}))
        artifact = json.loads(self.build(release, exposures))
        self.assertEqual(artifact["family"], "free")
        self.assertIsNone(artifact["courses"][0]["questions"][0]["product_id"])

    def test_rules_r1_rejected_for_new_catalogs(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]}, rules="r1")
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        with self.assertRaises(CatalogBuildError):
            self.build(release, exposures)

    def test_non_question_exposure_row_passes_through(self):
        digest = self.add_question("py-alpha")
        study = self.add_material("study", "study-1")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe(
            {"py-alpha": digest},
            extras=[{"kind": "study", "id": "study-1", "content_sha256": study,
                     "affected_question_ids": ["py-alpha"]}],
        ))
        artifact = json.loads(self.build(release, exposures))
        kinds = [(row["kind"], row["id"]) for row in artifact["exposures"]]
        self.assertEqual(kinds, [("question", "py-alpha"), ("study", "study-1")])


class DeterminismTest(BuilderFixture):
    def test_input_order_does_not_change_bytes(self):
        h_a = self.add_question("py-alpha")
        h_b = self.add_question("py-beta")
        release = self.write_release({"python": [("py-beta", "python-lifetime"),
                                                 ("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures([
            {"kind": "question", "id": "py-beta", "content_sha256": h_b, "affected_question_ids": ["py-beta"]},
            {"kind": "question", "id": "py-alpha", "content_sha256": h_a, "affected_question_ids": ["py-alpha"]},
        ])
        first = self.build(release, exposures)

        release2 = self.write_release({"python": [("py-alpha", "python-lifetime"),
                                                  ("py-beta", "python-lifetime")]})
        exposures2 = self.write_exposures([
            {"kind": "question", "id": "py-alpha", "content_sha256": h_a, "affected_question_ids": ["py-alpha"]},
            {"kind": "question", "id": "py-beta", "content_sha256": h_b, "affected_question_ids": ["py-beta"]},
        ])
        second = self.build(release2, exposures2)
        self.assertEqual(first, second)

    def test_unselected_material_does_not_change_pool(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        before = self.build(release, exposures)
        self.add_question("py-extra")  # added to manifest, not selected
        after = self.build(release, exposures)
        self.assertEqual(before, after)


class RejectionTest(BuilderFixture):
    def build_error(self, release, exposures):
        with self.assertRaises(CatalogBuildError):
            self.build(release, exposures)
        # A rejected build never leaves an artifact behind.

    def test_unknown_question(self):
        release = self.write_release({"python": [("py-missing", "python-lifetime")]})
        exposures = self.write_exposures([])
        self.build_error(release, exposures)

    def test_duplicate_question_across_courses(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")],
                                      "sql": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_duplicate_question_within_course(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime"),
                                                 ("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_empty_course_rejected(self):
        release = self.write_release({"python": []})
        exposures = self.write_exposures([])
        self.build_error(release, exposures)

    def test_unknown_rules_version(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]}, rules="r9")
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_official_requires_product(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", None)]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_product_binding_contradiction(self):
        digest = self.add_question("py-alpha", product="python-lifetime")
        release = self.write_release({"python": [("py-alpha", "sql-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_official_rejects_previously_exposed(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]},
                                     previously_exposed=True)
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_official_requires_exposure_attestation(self):
        digest = self.add_question("py-alpha")
        path = self.root / "release.json"
        path.write_text(json.dumps({
            "schema_version": 1, "catalog_version": "rel-1", "rules_version": "r2",
            "family": "official",
            "courses": [{"course_id": "python", "questions": [
                {"question_id": "py-alpha", "product_id": "python-lifetime"}]}],
        }), encoding="utf-8")
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(path, exposures)

    def test_free_question_may_omit_exposure_attestation(self):
        digest = self.add_question("py-free", license="CC-BY-SA-4.0")
        path = self.root / "release.json"
        path.write_text(json.dumps({
            "schema_version": 1, "catalog_version": "rel-1", "rules_version": "r2",
            "family": "free",
            "courses": [{"course_id": "python", "questions": [
                {"question_id": "py-free", "product_id": None}]}],
        }), encoding="utf-8")
        exposures = self.write_exposures(self.self_and_safe({"py-free": digest}))
        artifact = json.loads(self.build(path, exposures))
        self.assertEqual(artifact["family"], "free")

    def test_official_rejects_free_license(self):
        digest = self.add_question("py-alpha", license="CC-BY-SA-4.0")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_unknown_family(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]}, family="premium")
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_hash_mismatch_on_manifest_read(self):
        self.add_question("py-alpha")
        self.entries[-1]["sha256"] = "0" * 64
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": "0" * 64}))
        self.build_error(release, exposures)

    def test_json_id_mismatch(self):
        self.add_question("py-alpha", data=make_question("py-other"))
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        digest = _sha((self.content / "py-alpha.json").read_bytes())
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_question_schema_missing_field(self):
        data = make_question("py-alpha")
        del data["explanation"]
        self.add_question("py-alpha", data=data)
        digest = _sha((self.content / "py-alpha.json").read_bytes())
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_wrong_option_count(self):
        self.add_question("py-alpha", data=make_question("py-alpha", options=4))
        digest = _sha((self.content / "py-alpha.json").read_bytes())
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        self.build_error(release, exposures)

    def test_missing_manifest(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        with self.assertRaises(CatalogBuildError):
            builder.build_artifact(self.content, self.root / "nope.json", release, exposures)

    def test_wrong_exposure_hash(self):
        self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures([
            {"kind": "question", "id": "py-alpha", "content_sha256": "a" * 64,
             "affected_question_ids": ["py-alpha"]},
        ])
        self.build_error(release, exposures)

    def test_missing_self_reference(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures([
            {"kind": "question", "id": "py-alpha", "content_sha256": digest, "affected_question_ids": []},
        ])
        self.build_error(release, exposures)

    def test_exposure_reference_outside_pool(self):
        digest = self.add_question("py-alpha")
        other = self.add_material("study", "study-1")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe(
            {"py-alpha": digest},
            extras=[{"kind": "study", "id": "study-1", "content_sha256": other,
                     "affected_question_ids": ["py-not-selected"]}],
        ))
        self.build_error(release, exposures)

    def test_exposure_row_not_in_manifest(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe(
            {"py-alpha": digest},
            extras=[{"kind": "exam", "id": "exam-1", "content_sha256": "b" * 64,
                     "affected_question_ids": []}],
        ))
        self.build_error(release, exposures)

    def test_symlink_escape_rejected(self):
        outside = self.root / "outside.json"
        outside.write_bytes(b'{"ok": 1}')
        (self.content / "link.json").symlink_to(outside)
        self.entries.append({"kind": "question", "id": "py-alpha", "product_id": "python-lifetime",
                             "path": "link.json", "sha256": _sha(b'{"ok": 1}')})
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": _sha(b'{"ok": 1}')}))
        self.build_error(release, exposures)

    def test_error_messages_carry_no_raw_content(self):
        self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures([
            {"kind": "question", "id": "py-alpha", "content_sha256": "a" * 64,
             "affected_question_ids": ["py-alpha"]},
        ])
        with self.assertRaises(CatalogBuildError) as caught:
            self.build(release, exposures)
        message = str(caught.exception)
        self.assertNotIn("What does this print", message)
        self.assertNotIn(str(self.content), message)
        self.assertNotIn("answer 0", message)


class CheckAndWriteTest(BuilderFixture):
    def test_check_detects_drift_and_missing(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        payload = self.build(release, exposures)
        out = self.root / "artifact.json"
        self.assertFalse(builder.check_artifact(out, payload))  # missing
        out.write_bytes(payload)
        self.assertTrue(builder.check_artifact(out, payload))
        out.write_bytes(payload + b" ")
        self.assertFalse(builder.check_artifact(out, payload))

    def test_write_refuses_same_version_changed_pool(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        payload = self.build(release, exposures)
        out = self.root / "artifact.json"
        self.assertEqual(builder.write_artifact(out, payload), "written")
        self.assertEqual(builder.write_artifact(out, payload), "unchanged")

        digest2 = self.add_question("py-beta")
        release2 = self.write_release({"python": [("py-beta", "python-lifetime")]},
                                      catalog_version="rel-1")
        exposures2 = self.write_exposures(self.self_and_safe({"py-beta": digest2}))
        payload2 = self.build(release2, exposures2)
        with self.assertRaises(CatalogBuildError):
            builder.write_artifact(out, payload2)
        self.assertEqual(out.read_bytes(), payload)  # previous artifact preserved

    def test_new_version_overwrites_atomically(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        payload = self.build(release, exposures)
        out = self.root / "artifact.json"
        builder.write_artifact(out, payload)

        release2 = self.write_release({"python": [("py-alpha", "python-lifetime")]},
                                      catalog_version="rel-2")
        payload2 = self.build(release2, exposures)
        self.assertEqual(builder.write_artifact(out, payload2), "written")
        self.assertEqual(out.read_bytes(), payload2)

    def test_failed_build_leaves_previous_artifact(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        out = self.root / "artifact.json"
        builder.write_artifact(out, self.build(release, exposures))
        before = out.read_bytes()

        bad_release = self.write_release({"python": [("py-missing", "python-lifetime")]})
        with self.assertRaises(CatalogBuildError):
            builder.build_artifact(self.content, self.freeze_manifest(), bad_release, exposures)
        self.assertEqual(out.read_bytes(), before)

    def test_main_writes_and_checks(self):
        digest = self.add_question("py-alpha")
        manifest = self.freeze_manifest()
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        out = self.root / "artifact.json"
        argv = ["--content-root", str(self.content), "--manifest", str(manifest),
                "--release", str(release), "--exposures", str(exposures), "--out", str(out)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(builder.main(argv), 0)
            self.assertTrue(out.exists())
            self.assertEqual(builder.main(argv + ["--check"]), 0)
            out.write_bytes(out.read_bytes() + b"x")
            self.assertEqual(builder.main(argv + ["--check"]), 1)


if __name__ == "__main__":
    unittest.main()
