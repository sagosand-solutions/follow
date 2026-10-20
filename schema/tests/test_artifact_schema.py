# SPDX-License-Identifier: MIT
"""Schema-only checks for text artifacts (schema_version 0.3): what the JSON Schema itself decides, without the semantic validator.

The server's pool builder checks questions with the schema alone, so these rules must hold here and not only in the validator."""
import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "question.schema.json").read_text(encoding="utf-8"))
HEX = "a" * 64


def options():
    return [{"id": f"o{i}", "text": f"Option {i}", "score": score, "feedback": "f", "misconception": None if score == 1 else "py.some-misconception",
             **({"partial_reason": "p"} if score == 0.5 else {})} for i, score in enumerate([1, 0.5, 0, 0, 0])]


def artifact(**over):
    value = {"id": "change", "kind": "diff", "format": "unified-diff", "text": "-a\n+b\n", "sha256": HEX,
             "provenance": {"fixture_id": "fixture-one", "fixture_output_sha256": HEX}}
    value.update(over)
    return value


def base(version="0.3", language="text"):
    q = {"schema_version": version, "answer_profile": "standard", "language": language, "id": "diff-reading-one", "locale": "en", "type": "output",
         "level": "beginner", "difficulty": 0.2, "concepts": ["py.values"], "prompt": "p", "options": options(), "explanation": "e",
         "verification": {"status": "specified", "cases": [], "rationale": "r"}, "author": {"name": "A"}, "license": "CC-BY-SA-4.0"}
    if version == "0.1":
        del q["answer_profile"]
    return q


def text_only():
    q = base()
    q["artifacts"] = [artifact()]
    return q


def mixed():
    q = text_only()
    q.update(language="python", snippets=[{"id": "main", "code": "print(1)"}])
    q["verification"]["cases"] = [{"id": "one", "snippet_id": "main", "stdin": "", "expected": {"stdout": "1\n", "exit_code": 0}}]
    return q


class ArtifactSchemaTests(unittest.TestCase):
    def setUp(self):
        Draft202012Validator.check_schema(SCHEMA)
        self.validator = Draft202012Validator(SCHEMA)

    def errors(self, q):
        return list(self.validator.iter_errors(q))

    def mutated(self, base_question, change):
        q = copy.deepcopy(base_question)
        change(q)
        return q

    def test_text_only_and_mixed_are_valid(self):
        self.assertEqual(self.errors(text_only()), [])
        self.assertEqual(self.errors(mixed()), [])

    def test_versions_and_the_artifacts_field(self):
        for version in ("0.1", "0.2"):
            q = mixed()
            q["schema_version"] = version
            if version == "0.1":
                del q["answer_profile"]
            self.assertTrue(self.errors(q), f"{version} with artifacts")
        self.assertTrue(self.errors(self.mutated(text_only(), lambda q: q.update(schema_version="0.4"))))
        self.assertTrue(self.errors(self.mutated(text_only(), lambda q: q.pop("answer_profile"))), "0.3 needs an explicit profile")

    def test_language_decides_whether_code_is_there(self):
        self.assertTrue(self.errors(self.mutated(text_only(), lambda q: q.update(language="python"))), "python without snippets")
        self.assertTrue(self.errors(self.mutated(mixed(), lambda q: q.update(language="text"))), "text with snippets")
        self.assertTrue(self.errors(self.mutated(text_only(), lambda q: q.pop("artifacts"))), "text without artifacts")
        self.assertTrue(self.errors(self.mutated(text_only(), lambda q: q.update(language="java"))))
        # `text` is only for 0.3
        for version in ("0.1", "0.2"):
            q = base(version, "text")
            self.assertTrue(self.errors(q), f"{version} with language text")
        legacy = base("0.2", "python")
        legacy["snippets"] = [{"id": "main", "code": "print(1)"}]
        legacy["verification"]["cases"] = mixed()["verification"]["cases"]
        self.assertEqual(self.errors(legacy), [], "0.2 python stays valid")
        self.assertTrue(self.errors(self.mutated(legacy, lambda q: q.pop("snippets"))), "0.2 without snippets")

    def test_verification_cases_are_required_with_snippets_and_optional_without(self):
        self.assertTrue(self.errors(self.mutated(mixed(), lambda q: q["verification"].update(cases=[]))))
        self.assertEqual(self.errors(text_only()), [])

    def test_artifact_shape(self):
        bad = {
            "upper case hash": artifact(sha256=HEX.upper()),
            "short hash": artifact(sha256="a" * 63),
            "binding hash upper case": artifact(provenance={"fixture_id": "f", "fixture_output_sha256": HEX.upper()}),
            "provenance extra key": artifact(provenance={"fixture_id": "f", "fixture_output_sha256": HEX, "path": "/x"}),
            "provenance missing": {k: v for k, v in artifact().items() if k != "provenance"},
            "extra key": artifact(url="https://example.com/"),
            "unknown kind": artifact(kind="python"),
            "diff in the wrong format": artifact(format="plain"),
            "transcript in the wrong format": artifact(kind="http-transcript", format="plain"),
            "plain kind in a special format": artifact(kind="policy", format="unified-diff"),
            "too long": artifact(text="x" * 4001),
            "empty": artifact(text=""),
            "control character": artifact(text="a\u0007b"),
            "carriage return": artifact(text="a\r\nb"),
            "line separator": artifact(text="a b"),
            "bad id": artifact(id="Bad Id"),
            "long label": artifact(label="x" * 81),
        }
        for name, item in bad.items():
            with self.subTest(name):
                q = text_only()
                q["artifacts"] = [item]
                self.assertTrue(self.errors(q), name)
        for count in (0, 4):
            q = text_only()
            q["artifacts"] = [artifact(id=f"a-{i}", text=f"{i}\n") for i in range(count)]
            self.assertTrue(self.errors(q), f"{count} artifacts")
        q = text_only()
        q["artifacts"] = [artifact(id=f"a-{i}", text=f"{i}\n") for i in range(3)]
        self.assertEqual(self.errors(q), [])
        edge = text_only()
        edge["artifacts"] = [artifact(text="x" * 4000, kind="text", format="plain")]
        self.assertEqual(self.errors(edge), [])


if __name__ == "__main__":
    unittest.main()
