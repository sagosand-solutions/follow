# SPDX-License-Identifier: MIT
"""Semantic tests for validator/exam.py. All fixtures are synthetic."""
import contextlib
import copy
import hashlib
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from validator.exam import (main, validate_attempt, validate_attempt_transition, validate_exam)

PURPOSES = ("purpose", "flow", "state", "failure", "risk", "transfer")
CODE = ("def total(items):\n    result = 0\n    for item in items:\n        result += item\n"
        "    return result\nprint(total([1, 2, 3]))\n")
HASH = hashlib.sha256(CODE.encode("utf-8")).hexdigest()

MAP = {
    "schema_version": "0.1",
    "language": "python",
    "concepts": [{"id": "py.functions", "title": "Functions", "level": "beginner", "prerequisites": []}],
    "misconceptions": [{"id": "py.test-error", "title": "Test error",
                        "description": "Synthetic error", "concepts": ["py.functions"]}],
}


def exam():
    parts = []
    for purpose in PURPOSES:
        parts.append({
            "id": purpose,
            "kind": "explanation",
            "purpose": purpose,
            "prompt": f"Explain the {purpose} of the program.",
            "model_answer": "SECRET_MODEL_ANSWER with literal < and `backticks`.",
            "rubric": [
                {"score": 0, "criteria": "Does not address the point."},
                {"score": 1, "criteria": "Partly addresses the point."},
                {"score": 2, "criteria": "Fully addresses the point."},
            ],
            "max_points": 2,
            "concepts": ["py.functions"],
            "evidence": [{"source_hash": HASH, "snippet_id": "source", "start_line": 1, "end_line": 6}],
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
        "source": {"id": "source", "code": CODE, "sha256": HASH,
                   "environment": "CPython 3.14, no stdin.",
                   "execution_policy": {"mode": "static-only"}},
        "parts": parts,
        "study_refs": [],
        "author": {"name": "Sample Author"},
        "license": "CC-BY-SA-4.0",
    }


def attempt(exam_data=None, status="in_progress"):
    exam_data = exam_data or exam()
    return {
        "schema_version": "0.1",
        "attempt_id": "attempt-one",
        "exam_id": exam_data["id"],
        "exam_version": exam_data["version"],
        "source_hash": exam_data["source"]["sha256"],
        "rubric_version": exam_data["rubric_version"],
        "status": status,
        "assisted": False,
        "assistance_events": [],
        "parts": [{"part_id": part["id"], "status": "unanswered", "value": None}
                  for part in exam_data["parts"]],
    }


def codes(issues):
    return {issue.code for issue in issues}


class ExamTests(unittest.TestCase):
    def test_valid_exam_has_no_issues(self):
        self.assertEqual(validate_exam(exam(), MAP), [])

    def test_code_policy(self):
        variants = [
            ("code-characters", {"code": CODE.replace("\n", "\r\n")}),
            ("code-characters", {"code": CODE + "\x85"}),
            ("code-characters", {"code": CODE + "a\u2028b"}),
            ("code-characters", {"code": CODE + "\x00"}),
            ("empty-code", {"code": " \t\n"}),
            ("code-lines", {"code": "\n".join("x" for _ in range(101))}),
        ]
        for code, change in variants:
            with self.subTest(code=code, change=change):
                data = exam()
                data["source"].update(change)
                self.assertIn(code, codes(validate_exam(data, MAP)))
        # A trailing LF does not add a line, and 100 lines is accepted.
        data = exam()
        data["source"]["code"] = "\n".join("x" for _ in range(100))
        data["source"]["sha256"] = hashlib.sha256(data["source"]["code"].encode()).hexdigest()
        for part in data["parts"]:
            part["evidence"][0]["source_hash"] = data["source"]["sha256"]
        self.assertEqual(validate_exam(data, MAP), [])

    def test_source_hash_binding(self):
        data = exam()
        data["source"]["sha256"] = "0" * 64
        self.assertIn("source-hash", codes(validate_exam(data, MAP)))

    def test_part_semantics(self):
        checks = [
            ("duplicate", lambda d: d["parts"][1].update(id=d["parts"][0]["id"])),
            ("missing-purpose", lambda d: [p.update(purpose="state") for p in d["parts"]]),
            ("unknown-concept", lambda d: d["parts"][0].update(concepts=["py.missing"])),
            ("rubric-order", lambda d: d["parts"][0]["rubric"].reverse()),
            ("evidence-hash", lambda d: d["parts"][0]["evidence"][0].update(source_hash="0" * 64)),
            ("evidence-snippet", lambda d: d["parts"][0]["evidence"][0].update(snippet_id="other")),
            ("evidence-lines", lambda d: d["parts"][0]["evidence"][0].update(start_line=6, end_line=2)),
            ("evidence-lines", lambda d: d["parts"][0]["evidence"][0].update(end_line=99)),
            ("blank", lambda d: d["parts"][0].update(model_answer="   ")),
            ("blank", lambda d: d.update(title=" ")),
        ]
        for code, mutate in checks:
            with self.subTest(code=code):
                data = exam()
                mutate(data)
                self.assertIn(code, codes(validate_exam(data, MAP)))
        # All six pilot purposes present; removing flow and risk must be flagged.
        data = exam()
        for part in data["parts"]:
            part["purpose"] = "purpose"
        self.assertIn("missing-purpose", codes(validate_exam(data, MAP)))

    def test_study_refs_need_explicit_keys(self):
        data = exam()
        data["study_refs"] = ["concept:py.functions"]
        self.assertIn("unknown-study-ref", codes(validate_exam(data, MAP)))
        self.assertEqual(validate_exam(data, MAP, known_study_keys={"concept:py.functions"}), [])

    def test_schema_errors_short_circuit(self):
        issues = validate_exam({"schema_version": "0.1"}, MAP)
        self.assertTrue(issues)
        self.assertIn("schema", codes(issues))


class AttemptTests(unittest.TestCase):
    def test_valid_attempt(self):
        self.assertEqual(validate_attempt(attempt(), exam()), [])

    def test_binding(self):
        data = exam()
        checks = [
            ("exam-id", lambda a: a.update(exam_id="other")),
            ("exam-version", lambda a: a.update(exam_version=2)),
            ("source-hash", lambda a: a.update(source_hash="0" * 64)),
            ("rubric-version", lambda a: a.update(rubric_version=2)),
            ("part-set", lambda a: a["parts"][0].update(part_id="other")),
            ("part-set", lambda a: a["parts"].reverse()),
            ("assisted-events", lambda a: a["assistance_events"].append({"kind": "study", "resource_id": "r"})),
        ]
        for code, mutate in checks:
            with self.subTest(code=code):
                a = attempt(data)
                mutate(a)
                self.assertIn(code, codes(validate_attempt(a, data)))

    def test_unknown_and_unanswered_are_not_zero(self):
        data = exam()
        a = attempt(data)
        a["parts"][0] = {"part_id": a["parts"][0]["part_id"], "status": "unknown", "value": None}
        a["parts"][1] = {"part_id": a["parts"][1]["part_id"], "status": "answered", "value": {"text": "answer"}}
        self.assertEqual(validate_attempt(a, data), [])
        a["parts"][2] = {"part_id": a["parts"][2]["part_id"], "status": "answered", "value": {"text": "   "}}
        self.assertIn("blank-answer", codes(validate_attempt(a, data)))


class TransitionTests(unittest.TestCase):
    def assert_code(self, code, mutate):
        data = exam()
        previous = attempt(data)
        nxt = attempt(data)
        mutate(previous, nxt)
        self.assertIn(code, codes(validate_attempt_transition(previous, nxt, data)))

    def test_allowed_transitions(self):
        data = exam()
        previous = attempt(data)
        # in_progress -> in_progress with edited parts
        nxt = attempt(data)
        nxt["parts"][0] = {"part_id": nxt["parts"][0]["part_id"], "status": "answered", "value": {"text": "x"}}
        self.assertEqual(validate_attempt_transition(previous, nxt, data), [])
        # in_progress -> submitted, in_progress -> abandoned
        for status in ("submitted", "abandoned"):
            with self.subTest(status=status):
                final = attempt(data, status=status)
                self.assertEqual(validate_attempt_transition(previous, final, data), [])
        # identical idempotent save on a terminal snapshot
        submitted = attempt(data, status="submitted")
        self.assertEqual(validate_attempt_transition(submitted, copy.deepcopy(submitted), data), [])

    def test_terminal_immutable(self):
        data = exam()
        submitted = attempt(data, status="submitted")
        reopened = attempt(data)
        self.assertIn("terminal-mutation", codes(validate_attempt_transition(submitted, reopened, data)))

    def test_identity_and_assistance(self):
        self.assert_code("identity-change", lambda p, n: n.update(attempt_id="attempt-two"))
        self.assert_code("assisted-reset", lambda p, n: (p.update(assisted=True), n.update(assisted=False)))
        self.assert_code("assistance-rewrite",
                         lambda p, n: (p.update(assisted=True),
                                       p["assistance_events"].append({"kind": "study", "resource_id": "a"}),
                                       n.update(assisted=True),
                                       n["assistance_events"].append({"kind": "study", "resource_id": "b"})))
        self.assert_code("assistance-drop",
                         lambda p, n: (p.update(assisted=True),
                                       p["assistance_events"].append({"kind": "study", "resource_id": "a"})))
        # Appending help must be assisted and stay in_progress.
        self.assert_code("assistance-flag",
                         lambda p, n: n["assistance_events"].append({"kind": "study", "resource_id": "a"}))
        self.assert_code("assistance-submit",
                         lambda p, n: (n.update(assisted=True, status="submitted"),
                                       n["assistance_events"].append({"kind": "study", "resource_id": "a"})))
        # The honest order in_progress clean -> in_progress assisted -> submitted is allowed.
        data = exam()
        previous = attempt(data)
        aided = attempt(data)
        aided["assisted"] = True
        aided["assistance_events"] = [{"kind": "study", "resource_id": "note-a"}]
        self.assertEqual(validate_attempt_transition(previous, aided, data), [])
        submitted = attempt(data, status="submitted")
        submitted["assisted"] = True
        submitted["assistance_events"] = [{"kind": "study", "resource_id": "note-a"}]
        self.assertEqual(validate_attempt_transition(aided, submitted, data), [])


class SafetyTests(unittest.TestCase):
    def test_no_process_network_or_write_on_valid_or_invalid(self):
        blocked = patch("subprocess.Popen", side_effect=AssertionError("process"))
        with blocked, patch("subprocess.run", side_effect=AssertionError("process")), \
                patch("os.system", side_effect=AssertionError("process")), \
                patch("socket.socket", side_effect=AssertionError("network")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            self.assertEqual(validate_exam(exam(), MAP), [])
            broken = exam()
            broken["source"]["sha256"] = "0" * 64
            self.assertTrue(validate_exam(broken, MAP))
            self.assertEqual(validate_attempt(attempt(), exam()), [])
            self.assertTrue(validate_attempt({"bad": 1}, exam()))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.map_path = self.root / "map.json"
        self.map_path.write_text(json.dumps(MAP), encoding="utf-8")
        self.exam_path = self.root / "exam.json"
        self.exam_path.write_text(json.dumps(exam()), encoding="utf-8")
        self.attempt_path = self.root / "attempt.json"
        self.attempt_path.write_text(json.dumps(attempt()), encoding="utf-8")

    def run_cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def test_valid_exam_and_attempt(self):
        code, out = self.run_cli([str(self.exam_path), "--map", str(self.map_path)])
        self.assertEqual(code, 0)
        code, _ = self.run_cli([str(self.exam_path), "--map", str(self.map_path),
                                "--attempt", str(self.attempt_path)])
        self.assertEqual(code, 0)
        self.assertNotIn("SECRET_MODEL_ANSWER", out)

    def test_invalid_exam_is_exit_one(self):
        broken = exam()
        broken["source"]["sha256"] = "0" * 64
        path = self.root / "broken.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        code, out = self.run_cli([str(path), "--map", str(self.map_path)])
        self.assertEqual(code, 1)
        self.assertIn("source-hash", out)

    def test_setup_failures_are_exit_two(self):
        code, _ = self.run_cli([str(self.root / "missing.json"), "--map", str(self.map_path)])
        self.assertEqual(code, 2)
        code, _ = self.run_cli([str(self.exam_path), "--map", str(self.root / "missing-map.json")])
        self.assertEqual(code, 2)
        code, _ = self.run_cli([str(self.exam_path)])
        self.assertEqual(code, 2)

    def test_input_limit_is_exit_one_not_two(self):
        oversized = self.root / "oversized.json"
        oversized.write_text(json.dumps({"pad": "x" * (270 * 1024)}), encoding="utf-8")
        code, out = self.run_cli([str(oversized), "--map", str(self.map_path)])
        self.assertEqual(code, 1)
        self.assertIn("input-limit", out)

    def test_cli_accepts_only_empty_study_refs(self):
        data = exam()
        data["study_refs"] = ["concept:py.functions"]
        path = self.root / "study.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        code, out = self.run_cli([str(path), "--map", str(self.map_path)])
        self.assertEqual(code, 1)
        self.assertIn("unknown-study-ref", out)

    def test_cli_writes_no_files(self):
        before = sorted(p.name for p in self.root.iterdir())
        self.run_cli([str(self.exam_path), "--map", str(self.map_path), "--attempt", str(self.attempt_path)])
        self.assertEqual(before, sorted(p.name for p in self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
