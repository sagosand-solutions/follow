# SPDX-License-Identifier: MIT
"""Static study validation and manifest-core tests. No execution or writing."""

from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from validator import core
from validator import study

CONCEPT_IDS = {"py.complexity", "py.iteration"}
LESSON_IDS = {"lists"}
QUESTION_IDS = {"abc"}


def make_note(scope="concept", target="py.complexity", status="reviewed"):
    return {
        "schema_version": "0.1",
        "language": "python",
        "locale": "en",
        scope: target,
        "title": "Algorithmic complexity",
        "summary": "How the cost of an algorithm grows with the input size.",
        "related": ["py.iteration"],
        "sections": [
            {
                "id": "what",
                "heading": "What it measures",
                "body": "Plain text with a literal < and a `code` span.",
                "deeper": False,
                "example": {
                    "language": "python",
                    "caption": "A tiny example",
                    "code": "print(1)\n",
                    "execution_policy": {"mode": "static-only"},
                },
            }
        ],
        "sources": [
            {
                "id": "cormen",
                "title": "Introduction to Algorithms",
                "url": "https://example.org/book",
                "locator": "3rd ed., section 3.1",
                "accessed_on": "2026-10-16",
            }
        ],
        "status": status,
        "author": {"name": "Test Author"},
        "license": "CC-BY-SA-4.0",
    }


def encode(note):
    return (json.dumps(note, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_note(root, relative, note):
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = encode(note)
    path.write_bytes(raw)
    return raw


def large_note(target):
    return {
        "schema_version": "0.1",
        "language": "python",
        "locale": "en",
        "concept": target,
        "title": "x" * 120,
        "summary": "x" * 280,
        "sections": [
            {
                "id": f"s{i}",
                "heading": "x" * 120,
                "body": "x" * 6000,
                "deeper": bool(i % 2),
                "formula": "x" * 1000,
                "example": {
                    "language": "python",
                    "caption": "x" * 280,
                    "code": "x" * 8000,
                    "execution_policy": {"mode": "static-only"},
                },
            }
            for i in range(12)
        ],
        "sources": [
            {
                "id": f"src{i}",
                "title": "x" * 200,
                "url": "https://example.org/" + "a" * 2020,
                "locator": "x" * 300,
                "accessed_on": "2026-10-16",
            }
            for i in range(20)
        ],
        "status": "reviewed",
        "author": {"name": "Test Author"},
        "license": "CC-BY-SA-4.0",
    }


def snapshot(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            result[str(path.relative_to(root))] = (stat.st_mtime_ns, stat.st_size)
    return result


class StudyTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "study"
        self.root.mkdir()

    def validate(self, note):
        return study.validate_study(note, concept_ids=CONCEPT_IDS, lesson_ids=LESSON_IDS,
                                    question_ids=QUESTION_IDS)

    def codes(self, note):
        return {issue.code for issue in self.validate(note)}


class TestValidateStudy(StudyTestCase):
    def test_valid_scopes_and_status(self):
        for scope, target in (("concept", "py.complexity"), ("lesson", "lists"), ("question", "abc")):
            for status in ("draft", "reviewed"):
                with self.subTest(scope=scope, status=status):
                    self.assertEqual(self.validate(make_note(scope, target, status)), [])

    def test_unknown_targets(self):
        self.assertIn("unknown-concept", self.codes(make_note("concept", "py.nope")))
        self.assertIn("unknown-lesson", self.codes(make_note("lesson", "nope")))
        self.assertIn("unknown-question", self.codes(make_note("question", "nope")))

    def test_unknown_related_and_existing_concept(self):
        note = make_note()
        note["related"] = ["py.iteration", "py.missing"]
        self.assertIn("unknown-concept", self.codes(note))

    def test_duplicate_section_and_source_ids(self):
        note = make_note()
        note["sections"].append(dict(note["sections"][0]))
        self.assertIn("duplicate", self.codes(note))
        note = make_note()
        note["sources"].append(dict(note["sources"][0]))
        self.assertIn("duplicate", self.codes(note))

    def test_blank_and_wrong_type_and_extra(self):
        note = make_note()
        note["summary"] = "   "
        self.assertIn("blank", self.codes(note))
        note = make_note()
        note["title"] = 5
        self.assertIn("schema", self.codes(note))
        note = make_note()
        note["unexpected"] = 1
        self.assertIn("schema", self.codes(note))

    def test_url_semantics(self):
        for url in ("http://example.org", "https:///nohost", "https://user:pw@example.org",
                    "https://example.org/\x01", "https://example.org/%0a", "https://exa mple.org",
                    "https://example.org/\x85", "https://example.org/\x9f"):
            with self.subTest(url=url):
                note = make_note()
                note["sources"][0]["url"] = url
                self.assertIn("url", self.codes(note))

    def test_date_semantics(self):
        for value in ("2026-02-30", "2026-13-01", "2025-00-10", "2026-1-1"):
            with self.subTest(value=value):
                note = make_note()
                note["sources"][0]["accessed_on"] = value
                self.assertTrue(self.codes(note))

    def test_example_policy_and_language(self):
        note = make_note()
        note["sections"][0]["example"]["execution_policy"] = {"mode": "trusted-local"}
        self.assertIn("schema", self.codes(note))
        note = make_note()
        note["sections"][0]["example"]["language"] = "bash"
        self.assertIn("schema", self.codes(note))
        note = make_note()
        note["sections"][0]["example"]["cases"] = [{"id": "c"}]
        self.assertIn("schema", self.codes(note))

    def test_section_and_source_bounds(self):
        note = make_note()
        note["sections"] = [dict(note["sections"][0], id=f"s{i}") for i in range(12)]
        self.assertEqual(self.validate(note), [])
        note["sections"] = [dict(note["sections"][0], id=f"s{i}") for i in range(13)]
        self.assertIn("schema", self.codes(note))
        note = make_note()
        note["sources"] = [dict(note["sources"][0], id=f"src{i}") for i in range(20)]
        self.assertEqual(self.validate(note), [])
        note["sources"] = [dict(note["sources"][0], id=f"src{i}") for i in range(21)]
        self.assertIn("schema", self.codes(note))

    def test_codepoint_limits(self):
        note = make_note()
        note["title"] = "x" * 120
        self.assertEqual(self.validate(note), [])
        note["title"] = "x" * 121
        self.assertIn("schema", self.codes(note))

    def test_body_allows_literal_markup(self):
        note = make_note()
        note["sections"][0]["body"] = "a < b\n`code` and an unbalanced ` tick"
        self.assertEqual(self.validate(note), [])


class TestBuildManifest(StudyTestCase):
    def approval(self, relative, raw=None):
        if raw is None:
            raw = (self.root / relative).read_bytes()
        return {"path": f"data/study/{relative}", "sha256": hashlib.sha256(raw).hexdigest()}

    def build(self, approvals, **kw):
        concept_ids = kw.pop("concept_ids", CONCEPT_IDS)
        return study.build_manifest(self.root, approvals, concept_ids=concept_ids,
                                    lesson_ids=LESSON_IDS, question_ids=QUESTION_IDS, **kw)

    def test_empty_manifest(self):
        self.assertEqual(self.build([]), {"data_version": 1, "notes": []})

    def test_valid_manifest_sorted(self):
        write_note(self.root, "py-complexity.json", make_note("concept", "py.complexity"))
        write_note(self.root, "lessons/lists.json", make_note("lesson", "lists"))
        write_note(self.root, "questions/abc.json", make_note("question", "abc"))
        approvals = [self.approval("questions/abc.json"), self.approval("py-complexity.json"),
                     self.approval("lessons/lists.json")]
        manifest = self.build(approvals)
        self.assertEqual(manifest["data_version"], 1)
        self.assertEqual([(n["scope"], n["target_id"]) for n in manifest["notes"]],
                         [("concept", "py.complexity"), ("lesson", "lists"), ("question", "abc")])
        for note in manifest["notes"]:
            self.assertEqual(len(note["sha256"]), 64)
            self.assertTrue(note["path"].startswith("data/study/"))

    def test_hash_mismatch(self):
        write_note(self.root, "py-complexity.json", make_note())
        with self.assertRaises(core.ContentError):
            self.build([{"path": "data/study/py-complexity.json", "sha256": "0" * 64}])

    def test_changed_source_bytes(self):
        write_note(self.root, "py-complexity.json", make_note())
        approval = self.approval("py-complexity.json")
        (self.root / "py-complexity.json").write_bytes(encode(make_note()) + b"\n")
        with self.assertRaises(core.ContentError):
            self.build([approval])

    def test_invalid_sha_forms(self):
        write_note(self.root, "py-complexity.json", make_note())
        for sha in ("A" * 64, "a" * 63, "g" * 64, 5, None):
            with self.subTest(sha=sha):
                with self.assertRaises(core.ContentError):
                    self.build([{"path": "data/study/py-complexity.json", "sha256": sha}])

    def test_symlink_is_rejected(self):
        write_note(self.root, "real.json", make_note())
        (self.root / "link.json").symlink_to(self.root / "real.json")
        raw = (self.root / "real.json").read_bytes()
        with self.assertRaises(core.SetupError):
            self.build([{"path": "data/study/link.json", "sha256": hashlib.sha256(raw).hexdigest()}])

    def test_bad_paths(self):
        write_note(self.root, "py-complexity.json", make_note())
        raw = (self.root / "py-complexity.json").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        for path in ("/abs/x.json", "data/study/../escape.json", "data\\study\\x.json",
                     "data/study/x%2f.json", "data/study/x.json?v=1", "data/study/x.json#f",
                     "study/x.json", "data/study/x.txt", "data/study//x.json", "data/study/./x.json"):
            with self.subTest(path=path):
                with self.assertRaises(core.ContentError):
                    self.build([{"path": path, "sha256": digest}])

    def test_duplicate_path_and_target(self):
        write_note(self.root, "a.json", make_note("concept", "py.complexity"))
        write_note(self.root, "b.json", make_note("concept", "py.complexity"))
        approval = self.approval("a.json")
        with self.assertRaises(core.ContentError):
            self.build([approval, approval])
        with self.assertRaises(core.ContentError):
            self.build([self.approval("a.json"), self.approval("b.json")])

    def test_draft_is_not_publishable(self):
        write_note(self.root, "draft.json", make_note(status="draft"))
        with self.assertRaises(core.ContentError):
            self.build([self.approval("draft.json")])

    def test_unknown_reference_is_rejected(self):
        write_note(self.root, "bad.json", make_note("concept", "py.nope"))
        with self.assertRaises(core.ContentError):
            self.build([self.approval("bad.json")])

    def test_no_partial_manifest_on_last_error(self):
        write_note(self.root, "ok.json", make_note("concept", "py.complexity"))
        write_note(self.root, "bad.json", make_note("concept", "py.nope"))
        with self.assertRaises(core.ContentError):
            self.build([self.approval("ok.json"), self.approval("bad.json")])

    def test_determinism(self):
        write_note(self.root, "py-complexity.json", make_note())
        approval = self.approval("py-complexity.json")
        self.assertEqual(self.build([approval]), self.build([approval]))

    def test_no_side_effects(self):
        write_note(self.root, "py-complexity.json", make_note())
        approval = self.approval("py-complexity.json")
        before = snapshot(self.root)
        self.build([approval])
        with self.assertRaises(core.ContentError):
            self.build([{"path": "data/study/py-complexity.json", "sha256": "0" * 64}])
        self.assertEqual(snapshot(self.root), before)

    def test_trailing_lf_rejected_in_path_and_sha(self):
        write_note(self.root, "py-complexity.json", make_note())
        raw = (self.root / "py-complexity.json").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        with self.assertRaises(core.ContentError):
            self.build([{"path": "data/study/py-complexity.json\n", "sha256": digest}])
        with self.assertRaises(core.ContentError):
            self.build([{"path": "data/study/py-complexity.json", "sha256": digest + "\n"}])

    def test_symlinked_ancestor_is_rejected(self):
        base = Path(self.tmp.name)
        real = base / "real"
        (real / "study").mkdir(parents=True)
        raw = write_note(real / "study", "py-complexity.json", make_note())
        digest = hashlib.sha256(raw).hexdigest()
        link = base / "link"
        link.symlink_to(real, target_is_directory=True)
        approvals = [{"path": "data/study/py-complexity.json", "sha256": digest}]
        absolute = link / "study"
        with self.assertRaises(core.SetupError):
            study.build_manifest(absolute, approvals, concept_ids=CONCEPT_IDS,
                                 lesson_ids=LESSON_IDS, question_ids=QUESTION_IDS)
        relative = os.path.relpath(absolute, os.getcwd())
        with self.assertRaises(core.SetupError):
            study.build_manifest(relative, approvals, concept_ids=CONCEPT_IDS,
                                 lesson_ids=LESSON_IDS, question_ids=QUESTION_IDS)

    def test_dotdot_root_over_symlink_is_rejected(self):
        base = Path(self.tmp.name)
        (base / "a").mkdir()
        target = base / "elsewhere"
        (target / "study").mkdir(parents=True)
        raw = write_note(target / "study", "py-complexity.json", make_note())
        digest = hashlib.sha256(raw).hexdigest()
        (base / "a" / "link").symlink_to(target, target_is_directory=True)
        approvals = [{"path": "data/study/py-complexity.json", "sha256": digest}]
        for evil in (base / "a" / "link" / ".." / "study", base / "a" / ".." / "a" / "study"):
            with self.subTest(root=str(evil)):
                with self.assertRaises(core.SetupError):
                    study.build_manifest(evil, approvals, concept_ids=CONCEPT_IDS,
                                         lesson_ids=LESSON_IDS, question_ids=QUESTION_IDS)

    def test_count_cap(self):
        approvals = [{"path": f"data/study/x{i}.json", "sha256": "a" * 64}
                     for i in range(study.MAX_MANIFEST_ENTRIES + 1)]
        with self.assertRaises(core.InputLimitError):
            self.build(approvals)

    def test_per_file_cap(self):
        path = self.root / "big.json"
        path.write_bytes(b"{" + b" " * core.MAX_JSON_BYTES)
        approval = {"path": "data/study/big.json",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        with self.assertRaises(core.InputLimitError):
            self.build([approval])

    def test_depth_cap(self):
        path = self.root / "deep.json"
        path.write_bytes(b"[" * (core.MAX_JSON_DEPTH + 1))
        approval = {"path": "data/study/deep.json",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        with self.assertRaises(core.InputLimitError):
            self.build([approval])

    def test_aggregate_budget_and_new_budget_per_call(self):
        approvals = []
        concept_ids = set()
        total = 0
        index = 0
        while total <= core.MAX_QUESTION_BYTES:
            target = f"py.big{index}"
            raw = write_note(self.root, f"big{index}.json", large_note(target))
            total += len(raw)
            concept_ids.add(target)
            approvals.append({"path": f"data/study/big{index}.json",
                              "sha256": hashlib.sha256(raw).hexdigest()})
            index += 1
        with self.assertRaises(core.InputLimitError):
            self.build(approvals, concept_ids=concept_ids)
        small = [{"path": "data/study/" + name, "sha256": hashlib.sha256(
            (self.root / name).read_bytes()).hexdigest()} for name in ("big0.json", "big1.json")]
        first = self.build(small, concept_ids=concept_ids)
        second = self.build(small, concept_ids=concept_ids)
        self.assertEqual(first, second)


class TestStudyCli(StudyTestCase):
    def cli(self, argv):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = study.main(argv)
        return code, stream.getvalue()

    def setUp(self):
        super().setUp()
        self.map = Path(self.tmp.name) / "map.json"
        self.course = Path(self.tmp.name) / "course.json"
        self.index = Path(self.tmp.name) / "index.json"
        self.map.write_text(json.dumps({"concepts": [{"id": "py.complexity"}, {"id": "py.iteration"}]}),
                            encoding="utf-8")
        self.course.write_text(json.dumps({"levels": [{"lessons": [{"id": "lists"}]}]}), encoding="utf-8")
        self.index.write_text(json.dumps({"questions": [{"id": "abc"}]}), encoding="utf-8")

    def args(self, note):
        return [str(note), "--map", str(self.map), "--course", str(self.course), "--index", str(self.index)]

    def test_valid_note_exit_zero(self):
        note = Path(self.tmp.name) / "note.json"
        note.write_bytes(encode(make_note()))
        code, output = self.cli(self.args(note))
        self.assertEqual(code, 0)
        self.assertIn("passed", output)

    def test_content_error_exit_one(self):
        note = Path(self.tmp.name) / "note.json"
        note.write_bytes(encode(make_note("concept", "py.nope")))
        code, output = self.cli(self.args(note))
        self.assertEqual(code, 1)
        self.assertIn("unknown-concept", output)

    def test_setup_error_exit_two(self):
        code, output = self.cli(self.args(Path(self.tmp.name) / "missing.json"))
        self.assertEqual(code, 2)
        self.assertIn("setup", output)


if __name__ == "__main__":
    unittest.main()
