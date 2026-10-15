# SPDX-License-Identifier: MIT
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from validator import ContentError, SetupError, load_json, validate_map, validate_question
from validator import core
from helpers import concept_map, question, schemas


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        schemas(self.temp.name)
        patcher = patch.object(core, "SCHEMA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.map = concept_map()
        self.q = question()

    def codes(self, q=None):
        return {i.code for i in validate_question(self.q if q is None else q, self.map)}

    def test_valid_static_never_invokes_process(self):
        with patch("subprocess.Popen", side_effect=AssertionError("must never run")):
            self.assertEqual(validate_map(self.map), [])
            self.assertEqual(validate_question(self.q, self.map), [])

    def test_structural_errors(self):
        variants = [None, [], {}, 4, "text", {**self.q, "extra": 1}, {**self.q, "schema_version": "1"},
                    {**self.q, "difficulty": True}, {**self.q, "difficulty": float("nan")},
                    {**self.q, "options": self.q["options"][:4]},
                    {**self.q, "options": self.q["options"] * 2}]
        for value in variants:
            with self.subTest(value=value):
                self.assertTrue(validate_question(value, self.map))
        self.q["options"][0]["score"] = True
        self.assertTrue(self.codes())
        for value in (None, [], {}, {**self.map, "concepts": "wrong"}):
            self.assertTrue(validate_map(value))

    def test_each_required_field(self):
        for field in self.q:
            value = deepcopy(self.q)
            del value[field]
            with self.subTest(field=field):
                self.assertIn("schema", self.codes(value))

    def test_scores_and_option_rules(self):
        changes = [(0, {"score": 0.5}, "scores"), (1, {"score": 1}, "scores"),
                   (0, {"misconception": "py.test-error"}, "full-credit"),
                   (0, {"partial_reason": "why"}, "full-credit"),
                   (2, {"misconception": None}, "misconception-required"),
                   (2, {"partial_reason": "why"}, "partial-reason"),
                   (2, {"misconception": "py.missing"}, "unknown-misconception"),
                   (2, {"misconception": "py.other-error"}, "untagged-misconception"),
                   (2, {"text": "  Synthetic option 0  "}, "duplicate-text")]
        for index, update, code in changes:
            q = deepcopy(self.q)
            q["options"][index].update(update)
            with self.subTest(update=update):
                self.assertIn(code, self.codes(q))
        del self.q["options"][1]["partial_reason"]
        self.assertIn("partial-reason", self.codes())

    def test_blank_text(self):
        for field in ("prompt", "explanation"):
            q = deepcopy(self.q)
            q[field] = " \t\n"
            self.assertIn("blank", self.codes(q))
        self.q["snippets"][0]["code"] = " \n"
        self.assertIn("blank", self.codes())

    def test_references_ids_and_lines(self):
        self.q["concepts"] = ["py.missing"]
        self.assertIn("unknown-concept", self.codes())
        for field in ("snippets", "options"):
            q = question()
            q[field][1 if field == "options" else 0]["id"] = q[field][0]["id"]
            if field == "snippets":
                q[field].append(deepcopy(q[field][0]))
            self.assertIn("duplicate", self.codes(q))
        self.q = question()
        self.q["snippets"][0]["code"] += "\n"
        self.q["snippets"][0]["highlight_lines"] = [2]
        self.assertIn("line-range", self.codes())
        self.q["snippets"][0]["highlight_lines"] = [1]
        self.assertEqual(self.codes(), set())

    def test_verification(self):
        self.q["verification"]["cases"] *= 2
        self.assertIn("duplicate", self.codes())
        self.q = question()
        case = self.q["verification"]["cases"][0]
        case["snippet_id"] = "missing"
        self.assertTrue({"unknown-snippet", "unverified-snippet"} <= self.codes())
        case["snippet_id"] = "main"
        case["expected"]["exit_code"] = 1
        self.assertIn("crash-evidence", self.codes())
        case["expected"]["stderr_contains"] = "ValueError"
        self.assertEqual(self.codes(), set())

    def test_map_semantics(self):
        def check(change, code):
            data = deepcopy(self.map)
            change(data)
            self.assertIn(code, {i.code for i in validate_map(data)})
        check(lambda m: m["concepts"].append(deepcopy(m["concepts"][0])), "duplicate")
        check(lambda m: m["misconceptions"].append(deepcopy(m["misconceptions"][0])), "duplicate")
        check(lambda m: m["concepts"][0]["prerequisites"].append("py.missing"), "unknown-concept")
        check(lambda m: m["concepts"][0]["prerequisites"].append("py.values"), "self-edge")
        check(lambda m: m["concepts"][0]["prerequisites"].append("py.functions"), "higher-level")
        check(lambda m: m["concepts"][0]["prerequisites"].append("py.functions"), "cycle")
        check(lambda m: m["misconceptions"][0]["concepts"].append("py.missing"), "unknown-concept")
        check(lambda m: m["concepts"][0].update(title="  "), "blank")

    def test_strict_json(self):
        file = Path(self.temp.name) / "data.json"
        for text in ('{"x": 1, "x": 2}', '{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}', '{"x": 1e999}', '{\n bad}'):
            file.write_text(text)
            with self.subTest(text=text), self.assertRaises(ContentError) as caught:
                load_json(file)
            self.assertIn(str(file), str(caught.exception))
        file.write_text('{"x": 1}')
        self.assertEqual(load_json(file), {"x": 1})
        with self.assertRaises(SetupError):
            load_json(file.with_name("missing"))

    def test_missing_and_remote_schema_fail_closed(self):
        file = Path(self.temp.name) / "question.schema.json"
        file.write_text(json.dumps({"$ref": "https://invalid.example/schema"}))
        with self.assertRaises(SetupError):
            self.codes()
        file.unlink()
        with self.assertRaises(SetupError):
            self.codes()

    def test_deterministic_issues(self):
        self.q["prompt"] = "  "
        self.q["options"][2]["misconception"] = None
        self.assertEqual(validate_question(self.q, self.map), validate_question(self.q, self.map))


if __name__ == "__main__":
    unittest.main()
