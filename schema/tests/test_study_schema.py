# SPDX-License-Identifier: MIT
"""Contract tests for the study note and study manifest schemas.

Run from the repository root::

    python3 -m unittest discover -s schema/tests -v

Schema-unique behaviour lives here; cross-file references, real URL/date
semantics and manifest path canonicality belong to ``validator/tests/test_study.py``.
"""

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parents[1]
STUDY_SCHEMA_PATH = SCHEMA_DIR / "study.schema.json"
MANIFEST_SCHEMA_PATH = SCHEMA_DIR / "study-manifest.schema.json"


def load(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def build_validator(path):
    schema = load(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


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


def valid_note(**overrides):
    note = {
        "schema_version": "0.1",
        "language": "python",
        "locale": "en",
        "concept": "py.complexity",
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
        "status": "draft",
        "author": {"name": "Test Author"},
        "license": "CC-BY-SA-4.0",
    }
    note.update(overrides)
    return note


class TestSchemaSelfValidation(unittest.TestCase):
    def test_study_schema_is_valid(self):
        build_validator(STUDY_SCHEMA_PATH)

    def test_manifest_schema_is_valid(self):
        build_validator(MANIFEST_SCHEMA_PATH)

    def test_schemas_are_self_contained(self):
        def collect_refs(node, found):
            if isinstance(node, dict):
                if isinstance(node.get("$ref"), str):
                    found.append(node["$ref"])
                for value in node.values():
                    collect_refs(value, found)
            elif isinstance(node, list):
                for item in node:
                    collect_refs(item, found)

        for path in (STUDY_SCHEMA_PATH, MANIFEST_SCHEMA_PATH):
            schema = load(path)
            self.assertNotIn("$id", schema)
            refs = []
            collect_refs(schema, refs)
            self.assertTrue(refs, f"{path.name} defines no $ref entries")
            for ref in refs:
                self.assertTrue(ref.startswith("#/$defs/"), f"non-local ref: {ref}")


class TestStudyNoteSchema(unittest.TestCase):
    def setUp(self):
        self.validator = build_validator(STUDY_SCHEMA_PATH)

    def errors(self, note):
        return list(self.validator.iter_errors(note))

    def test_valid_note_for_each_scope(self):
        for scope in ("concept", "lesson", "question"):
            with self.subTest(scope=scope):
                note = valid_note()
                note.pop("concept")
                note[scope] = {"concept": "py.complexity", "lesson": "lists", "question": "abc"}[scope]
                self.assertEqual(self.errors(note), [], scope)

    def test_exclusive_scope(self):
        none = valid_note()
        none.pop("concept")
        both = valid_note(lesson="lists")
        every = valid_note(lesson="lists", question="abc")
        self.assertTrue(self.errors(none))
        self.assertTrue(self.errors(both))
        self.assertTrue(self.errors(every))

    def test_related_is_optional_bounded_and_unique(self):
        note = valid_note()
        note.pop("related")
        self.assertEqual(self.errors(note), [])
        self.assertEqual(self.errors(valid_note(related=[f"py.c{i}" for i in range(64)])), [])
        self.assertTrue(self.errors(valid_note(related=["py.a", "py.a"])))
        self.assertTrue(self.errors(valid_note(related=[f"py.c{i}" for i in range(65)])))

    def test_section_and_source_counts(self):
        note = valid_note()
        base_section = note["sections"][0]
        self.assertTrue(self.errors(valid_note(sections=[])))
        self.assertEqual(self.errors(valid_note(sections=[dict(base_section, id=f"s{i}") for i in range(12)])), [])
        self.assertTrue(self.errors(valid_note(sections=[dict(base_section, id=f"s{i}") for i in range(13)])))
        base_source = note["sources"][0]
        self.assertTrue(self.errors(valid_note(sources=[])))
        self.assertEqual(self.errors(valid_note(sources=[dict(base_source, id=f"src{i}") for i in range(20)])), [])
        self.assertTrue(self.errors(valid_note(sources=[dict(base_source, id=f"src{i}") for i in range(21)])))

    def test_text_codepoint_limits(self):
        limits = [
            (("title",), 120),
            (("summary",), 280),
            (("sections", 0, "heading"), 120),
            (("sections", 0, "body"), 6000),
            (("sections", 0, "formula"), 1000),
            (("sections", 0, "example", "caption"), 280),
            (("sections", 0, "example", "code"), 8000),
            (("author", "name"), 120),
        ]
        for path, maximum in limits:
            with self.subTest(path=path):
                ok = valid_note()
                set_key("x" * maximum, *path)(ok)
                self.assertEqual(self.errors(ok), [], str(path))
                too_long = valid_note()
                set_key("x" * (maximum + 1), *path)(too_long)
                self.assertTrue(self.errors(too_long), f"{path} over limit accepted")
        for path, maximum in ((("sources", 0, "title"), 200), (("sources", 0, "url"), 2048),
                              (("sources", 0, "locator"), 300)):
            with self.subTest(path=path):
                ok = valid_note()
                set_key("x" * maximum, *path)(ok)
                self.assertEqual(self.errors(ok), [])
                too_long = valid_note()
                set_key("x" * (maximum + 1), *path)(too_long)
                self.assertTrue(self.errors(too_long))

    def test_text_values_must_be_nonempty_at_schema_level(self):
        for path in (("title",), ("summary",), ("sections", 0, "heading"),
                     ("sections", 0, "body"), ("sections", 0, "example", "caption"),
                     ("sections", 0, "example", "code"), ("sources", 0, "title"),
                     ("sources", 0, "url"), ("sources", 0, "locator"), ("author", "name")):
            with self.subTest(path=path):
                note = valid_note()
                set_key("", *path)(note)
                self.assertTrue(self.errors(note), f"empty text accepted at {path}")

    def test_body_accepts_newlines_angle_and_backticks(self):
        note = valid_note()
        note["sections"][0]["body"] = "line one\nline two <br> `span` and an unbalanced ` tick"
        self.assertEqual(self.errors(note), [])

    def test_required_fields_and_extra_properties(self):
        mutations = [
            ("missing-schema_version", pop_key("schema_version")),
            ("missing-language", pop_key("language")),
            ("missing-locale", pop_key("locale")),
            ("missing-title", pop_key("title")),
            ("missing-summary", pop_key("summary")),
            ("missing-sections", pop_key("sections")),
            ("missing-sources", pop_key("sources")),
            ("missing-status", pop_key("status")),
            ("missing-author", pop_key("author")),
            ("missing-license", pop_key("license")),
            ("missing-section-id", pop_key("sections", 0, "id")),
            ("missing-section-heading", pop_key("sections", 0, "heading")),
            ("missing-section-body", pop_key("sections", 0, "body")),
            ("missing-section-deeper", pop_key("sections", 0, "deeper")),
            ("missing-source-id", pop_key("sources", 0, "id")),
            ("missing-source-title", pop_key("sources", 0, "title")),
            ("missing-source-url", pop_key("sources", 0, "url")),
            ("missing-source-locator", pop_key("sources", 0, "locator")),
            ("missing-source-accessed", pop_key("sources", 0, "accessed_on")),
            ("missing-author-name", pop_key("author", "name")),
            ("wrong-schema_version", set_key("0.2", "schema_version")),
            ("wrong-language", set_key("javascript", "language")),
            ("wrong-locale", set_key("sv", "locale")),
            ("wrong-license", set_key("MIT", "license")),
            ("unknown-status", set_key("published", "status")),
            ("reviewed-status-ok", set_key("reviewed", "status")),
            ("deeper-string", set_key("yes", "sections", 0, "deeper")),
            ("extra-root-property", add_extra_key()),
            ("extra-section-property", add_extra_key("sections", 0)),
            ("extra-source-property", add_extra_key("sources", 0)),
            ("extra-author-property", add_extra_key("author")),
            ("section-id-uppercase", set_key("What", "sections", 0, "id")),
            ("section-id-trailing-lf", set_key("what\n", "sections", 0, "id")),
            ("section-id-leading-space", set_key(" what", "sections", 0, "id")),
            ("source-id-trailing-lf", set_key("cormen\n", "sources", 0, "id")),
            ("concept-id-no-namespace", set_key("complexity", "concept")),
            ("concept-id-uppercase", set_key("py.Complexity", "concept")),
            ("lesson-id-leading-digit", set_key("1lists", "lesson")),
            ("question-id-too-short", set_key("ab", "question")),
            ("accessed-on-slash", set_key("2026/10/16", "sources", 0, "accessed_on")),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                note = valid_note()
                mutate(note)
                errors = self.errors(note)
                if label == "reviewed-status-ok":
                    self.assertEqual(errors, [])
                else:
                    self.assertTrue(errors, f"mutation accepted: {label}")

    def test_example_is_static_only(self):
        base_example = {
            "language": "python",
            "caption": "A tiny example",
            "code": "print(1)\n",
            "execution_policy": {"mode": "static-only"},
        }
        good = valid_note()
        good["sections"][0]["example"] = copy.deepcopy(base_example)
        self.assertEqual(self.errors(good), [])
        for label, mutate in [
            ("missing-execution_policy", pop_key("sections", 0, "example", "execution_policy")),
            ("missing-mode", pop_key("sections", 0, "example", "execution_policy", "mode")),
            ("trusted-mode", set_key("trusted-local", "sections", 0, "example", "execution_policy", "mode")),
            ("unknown-mode", set_key("run", "sections", 0, "example", "execution_policy", "mode")),
            ("other-language", set_key("bash", "sections", 0, "example", "language")),
            ("extra-example-property", add_extra_key("sections", 0, "example")),
            ("case-field", set_key([{"id": "c"}], "sections", 0, "example", "cases")),
            ("setup-field", set_key("setup.py", "sections", 0, "example", "setup")),
            ("extra-policy-property", add_extra_key("sections", 0, "example", "execution_policy")),
        ]:
            with self.subTest(label=label):
                note = valid_note()
                note["sections"][0]["example"] = copy.deepcopy(base_example)
                mutate(note)
                self.assertTrue(self.errors(note), f"example mutation accepted: {label}")


class TestStudyManifestSchema(unittest.TestCase):
    def setUp(self):
        self.validator = build_validator(MANIFEST_SCHEMA_PATH)

    def errors(self, manifest):
        return list(self.validator.iter_errors(manifest))

    def test_empty_manifest_is_valid(self):
        self.assertEqual(self.errors({"data_version": 1, "notes": []}), [])

    def test_valid_entry(self):
        manifest = {
            "data_version": 1,
            "notes": [
                {"scope": "concept", "target_id": "py.complexity",
                 "path": "data/study/py-complexity.json", "sha256": "a" * 64},
                {"scope": "lesson", "target_id": "lists",
                 "path": "data/study/lessons/lists.json", "sha256": "b" * 64},
                {"scope": "question", "target_id": "abc",
                 "path": "data/study/questions/abc.json", "sha256": "c" * 64},
            ],
        }
        self.assertEqual(self.errors(manifest), [])

    def test_mutations_are_rejected(self):
        entry = {"scope": "concept", "target_id": "py.complexity",
                 "path": "data/study/py-complexity.json", "sha256": "a" * 64}
        mutations = [
            ("wrong-data_version", lambda m: m.__setitem__("data_version", 2)),
            ("missing-data_version", lambda m: m.pop("data_version")),
            ("missing-notes", lambda m: m.pop("notes")),
            ("extra-root-property", lambda m: m.__setitem__("extra", 1)),
            ("missing-scope", lambda m: m["notes"][0].pop("scope")),
            ("missing-target_id", lambda m: m["notes"][0].pop("target_id")),
            ("missing-path", lambda m: m["notes"][0].pop("path")),
            ("missing-sha256", lambda m: m["notes"][0].pop("sha256")),
            ("unknown-scope", lambda m: m["notes"][0].__setitem__("scope", "chapter")),
            ("uppercase-sha", lambda m: m["notes"][0].__setitem__("sha256", "A" * 64)),
            ("short-sha", lambda m: m["notes"][0].__setitem__("sha256", "a" * 63)),
            ("nonhex-sha", lambda m: m["notes"][0].__setitem__("sha256", "g" * 64)),
            ("absolute-path", lambda m: m["notes"][0].__setitem__("path", "/data/study/x.json")),
            ("no-prefix", lambda m: m["notes"][0].__setitem__("path", "study/x.json")),
            ("no-json-suffix", lambda m: m["notes"][0].__setitem__("path", "data/study/x.txt")),
            ("query-path", lambda m: m["notes"][0].__setitem__("path", "data/study/x.json?v=1")),
            ("percent-path", lambda m: m["notes"][0].__setitem__("path", "data/study/x%2f.json")),
            ("extra-entry-property", lambda m: m["notes"][0].__setitem__("extra", 1)),
            ("bad-concept-id", lambda m: m["notes"][0].__setitem__("target_id", "bad id")),
            ("path-trailing-lf", lambda m: m["notes"][0].__setitem__("path", "data/study/x.json\n")),
            ("sha-trailing-lf", lambda m: m["notes"][0].__setitem__("sha256", "a" * 64 + "\n")),
            ("sha-trailing-cr", lambda m: m["notes"][0].__setitem__("sha256", "a" * 64 + "\r")),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                manifest = {"data_version": 1, "notes": [copy.deepcopy(entry)]}
                mutate(manifest)
                self.assertTrue(self.errors(manifest), f"manifest mutation accepted: {label}")


if __name__ == "__main__":
    unittest.main()
