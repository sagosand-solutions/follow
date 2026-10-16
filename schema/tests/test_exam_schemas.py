"""Contract tests for the chapter-exam and attempt schemas.

Run from the repository root::

    pip install 'jsonschema>=4.18,<5'
    python3 -m unittest discover -s schema/tests -v

These tests cover only what JSON Schema decides. Identifier uniqueness, source
hash binding, line bounds, rubric ordering, purpose coverage and study-reference
resolution belong to validator/exam.py.
"""

import copy
import hashlib
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parents[1]
EXAM_SCHEMA_PATH = SCHEMA_DIR / "exam.schema.json"
ATTEMPT_SCHEMA_PATH = SCHEMA_DIR / "exam-attempt.schema.json"

PURPOSES = ("purpose", "flow", "state", "failure", "risk", "transfer")
CODE = ("def total(items):\n    result = 0\n    for item in items:\n        result += item\n"
        "    return result\nprint(total([1, 2, 3]))\n")


def load(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def build_validator(path):
    schema = load(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def source_hash(code=CODE):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def valid_exam():
    digest = source_hash()
    parts = []
    for purpose in PURPOSES:
        parts.append({
            "id": purpose,
            "kind": "explanation",
            "purpose": purpose,
            "prompt": f"Explain the {purpose} of the program.",
            "model_answer": "A short model answer with literal < and `backticks`.",
            "rubric": [
                {"score": 0, "criteria": "Does not address the point."},
                {"score": 1, "criteria": "Partly addresses the point."},
                {"score": 2, "criteria": "Fully addresses the point."},
            ],
            "max_points": 2,
            "concepts": ["py.functions"],
            "evidence": [{"source_hash": digest, "snippet_id": "source", "start_line": 1, "end_line": 6}],
        })
    return {
        "schema_version": "0.1",
        "id": "c01-sample",
        "title": "Sample chapter examination",
        "chapter_id": "C01",
        "language": "python",
        "locale": "en",
        "version": 1,
        "rubric_version": 1,
        "source": {
            "id": "source",
            "code": CODE,
            "sha256": digest,
            "environment": "CPython 3.14, no stdin.",
            "execution_policy": {"mode": "static-only"},
        },
        "parts": parts,
        "study_refs": [],
        "author": {"name": "Sample Author"},
        "license": "CC-BY-SA-4.0",
    }


def valid_attempt(exam=None):
    exam = exam or valid_exam()
    return {
        "schema_version": "0.1",
        "attempt_id": "attempt-one",
        "exam_id": exam["id"],
        "exam_version": exam["version"],
        "source_hash": exam["source"]["sha256"],
        "rubric_version": exam["rubric_version"],
        "status": "in_progress",
        "assisted": False,
        "assistance_events": [],
        "parts": [{"part_id": part["id"], "status": "unanswered", "value": None} for part in exam["parts"]],
    }


def pop_key(*path):
    def mutate(obj):
        target = obj
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return mutate


def set_key(value, *path):
    def mutate(obj):
        target = obj
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def add_extra_key(*path):
    def mutate(obj):
        target = obj
        for key in path:
            target = target[key]
        target["unexpected_extra"] = "x"

    return mutate


class TestExamSchemas(unittest.TestCase):
    def setUp(self):
        self.exam_validator = build_validator(EXAM_SCHEMA_PATH)
        self.attempt_validator = build_validator(ATTEMPT_SCHEMA_PATH)

    def exam_errors(self, exam):
        return sorted(self.exam_validator.iter_errors(exam), key=str)

    def attempt_errors(self, attempt):
        return sorted(self.attempt_validator.iter_errors(attempt), key=str)

    def test_schemas_are_self_contained(self):
        for path in (EXAM_SCHEMA_PATH, ATTEMPT_SCHEMA_PATH):
            schema = load(path)
            self.assertNotIn("$id", schema)
            refs = []

            def collect(node):
                if isinstance(node, dict):
                    if isinstance(node.get("$ref"), str):
                        refs.append(node["$ref"])
                    for value in node.values():
                        collect(value)
                elif isinstance(node, list):
                    for item in node:
                        collect(item)

            collect(schema)
            self.assertTrue(refs)
            for ref in refs:
                self.assertTrue(ref.startswith("#/$defs/"), f"non-local ref: {ref}")

    def test_valid_exam_and_attempt_validate(self):
        exam = valid_exam()
        self.assertEqual(self.exam_errors(exam), [], "\n".join(str(e) for e in self.exam_errors(exam)))
        self.assertEqual(self.attempt_errors(valid_attempt(exam)), [])

    def test_every_object_forbids_extra_properties(self):
        mutations = [
            ("root", add_extra_key()),
            ("source", add_extra_key("source")),
            ("execution_policy", add_extra_key("source", "execution_policy")),
            ("part", add_extra_key("parts", 0)),
            ("rubric_item", add_extra_key("parts", 0, "rubric", 0)),
            ("evidence", add_extra_key("parts", 0, "evidence", 0)),
            ("author", add_extra_key("author")),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                exam = copy.deepcopy(valid_exam())
                mutate(exam)
                self.assertTrue(self.exam_errors(exam), f"exam accepted extra property: {label}")
        attempt = valid_attempt()
        attempt["unexpected_extra"] = "x"
        self.assertTrue(self.attempt_errors(attempt))
        attempt = valid_attempt()
        attempt["assistance_events"].append({"kind": "study", "resource_id": "r", "extra": "x"})
        self.assertTrue(self.attempt_errors(attempt))
        attempt = valid_attempt()
        attempt["parts"][0]["value"] = {"text": "answer", "extra": "x"}
        attempt["parts"][0]["status"] = "answered"
        self.assertTrue(self.attempt_errors(attempt))

    def test_exam_mutations_rejected(self):
        mutations = [
            ("missing-source", pop_key("source")),
            ("wrong-schema_version", set_key("0.2", "schema_version")),
            ("wrong-language", set_key("bash", "language")),
            ("wrong-locale", set_key("sv", "locale")),
            ("wrong-license", set_key("MIT", "license")),
            ("unknown-chapter", set_key("C17", "chapter_id")),
            ("zero-version", set_key(0, "version")),
            ("negative-version", set_key(-1, "version")),
            ("parts-five", set_key(valid_exam()["parts"][:5], "parts")),
            ("parts-nine", set_key(valid_exam()["parts"] + [copy.deepcopy(valid_exam()["parts"][0])], "parts")),
            ("part-kind-choice", set_key("choice", "parts", 0, "kind")),
            ("part-purpose-unknown", set_key("severity", "parts", 0, "purpose")),
            ("part-max-points", set_key(3, "parts", 0, "max_points")),
            ("part-concepts-empty", set_key([], "parts", 0, "concepts")),
            ("part-evidence-empty", set_key([], "parts", 0, "evidence")),
            ("rubric-two", set_key(valid_exam()["parts"][0]["rubric"][:2], "parts", 0, "rubric")),
            ("rubric-score-3", set_key(3, "parts", 0, "rubric", 2, "score")),
            ("study-ref-missing-scope", set_key(["py.functions"], "study_refs")),
            ("study-ref-uppercase", set_key(["concept:Bad"], "study_refs")),
            ("code-empty", set_key("", "source", "code")),
            ("code-8001", set_key("x" * 8001, "source", "code")),
            ("title-121", set_key("x" * 121, "title")),
            ("environment-2001", set_key("x" * 2001, "source", "environment")),
            ("hash-uppercase", set_key("A" * 64, "source", "sha256")),
            ("hash-short", set_key("a" * 63, "source", "sha256")),
            ("hash-trailing-lf", set_key(source_hash() + "\n", "source", "sha256")),
            ("hash-trailing-space", set_key(source_hash() + " ", "source", "sha256")),
            ("evidence-hash-trailing-lf", set_key(source_hash() + "\n", "parts", 0, "evidence", 0, "source_hash")),
            ("id-uppercase", set_key("Bad", "id")),
            ("id-leading-digit", set_key("1bad", "id")),
            ("evidence-start-zero", set_key(0, "parts", 0, "evidence", 0, "start_line")),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                exam = copy.deepcopy(valid_exam())
                mutate(exam)
                self.assertTrue(self.exam_errors(exam), f"exam mutation accepted: {label}")

    def test_well_formed_study_refs_pass(self):
        for ref in ("concept:py.functions", "lesson:values-and-assignment", "question:py-values-001"):
            with self.subTest(ref=ref):
                exam = valid_exam()
                exam["study_refs"] = [ref]
                self.assertEqual(self.exam_errors(exam), [])

    def test_static_only_policy_is_structurally_enforced(self):
        for mode in ("trusted-fixture", "static", "run", "dynamic"):
            with self.subTest(mode=mode):
                exam = valid_exam()
                exam["source"]["execution_policy"] = {"mode": mode}
                self.assertTrue(self.exam_errors(exam))
        for extra in ("cases", "setup", "trace", "script"):
            with self.subTest(extra=extra):
                exam = valid_exam()
                exam["source"][extra] = []
                self.assertTrue(self.exam_errors(exam))

    def test_code_and_text_lengths_count_code_points(self):
        exam = valid_exam()
        exam["source"]["code"] = "x" * 8000
        self.assertEqual(self.exam_errors(exam), [])
        exam["title"] = "😀" * 120
        self.assertEqual(self.exam_errors(exam), [])
        exam["title"] = "😀" * 121
        self.assertTrue(self.exam_errors(exam))

    def test_proprietary_license_is_accepted(self):
        exam = valid_exam()
        exam["license"] = "LicenseRef-Proprietary"
        self.assertEqual(self.exam_errors(exam), [])
        for bad in ("MIT", "CC-BY-SA-4.0 ", " cc-by-sa-4.0", "licenseref-proprietary", "", None):
            with self.subTest(bad=bad):
                exam = valid_exam()
                exam["license"] = bad
                self.assertTrue(self.exam_errors(exam), f"exam license accepted: {bad!r}")

    def test_attempt_mutations_rejected(self):
        mutations = [
            ("missing-parts", pop_key("parts")),
            ("wrong-schema_version", set_key("0.2", "schema_version")),
            ("unknown-status", set_key("done", "status")),
            ("assisted-string", set_key("false", "assisted")),
            ("exam-version-zero", set_key(0, "exam_version")),
            ("source-hash-trailing-lf", set_key(source_hash() + "\n", "source_hash")),
            ("parts-five", set_key(valid_attempt()["parts"][:5], "parts")),
            ("event-unknown-kind", set_key([{"kind": "video", "resource_id": "r"}], "assistance_events")),
            ("answered-null-value", set_key("answered", "parts", 0, "status")),
            ("unanswered-with-value", set_key({"text": "x"}, "parts", 0, "value")),
            ("blank-answer-text", set_key({"text": ""}, "parts", 0, "value")),
            ("value-text-6001", set_key("x" * 6001, "parts", 0, "value")),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                attempt = copy.deepcopy(valid_attempt())
                mutate(attempt)
                self.assertTrue(self.attempt_errors(attempt), f"attempt mutation accepted: {label}")

    def test_attempt_allows_unknown_and_answered_states(self):
        attempt = valid_attempt()
        attempt["parts"][0] = {"part_id": attempt["parts"][0]["part_id"], "status": "unknown", "value": None}
        attempt["parts"][1] = {"part_id": attempt["parts"][1]["part_id"], "status": "answered",
                               "value": {"text": "A complete answer."}}
        self.assertEqual(self.attempt_errors(attempt), [])


if __name__ == "__main__":
    unittest.main()
