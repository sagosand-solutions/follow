# SPDX-License-Identifier: MIT
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from validator import core, RunResult
from validator.cli import main
from helpers import concept_map, question, schemas
from test_runner import FakeRunner


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        schemas(self.directory)
        patcher = patch.object(core, "SCHEMA_DIR", self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.map_path = self.write("map.json", concept_map())
        self.path = self.write("question.json", question())

    def write(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value))
        return str(path)

    def invoke(self, extra=(), files=None, runner=None, json_format=True):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--concept-map", self.map_path]
        if json_format:
            argv += ["--format", "json"]
        argv += list(extra) + ([self.path] if files is None else files)
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv, runner=runner)
        return code, json.loads(out.getvalue()) if json_format else out.getvalue(), err.getvalue()

    def test_static_no_execution_even_trust_flag(self):
        runner = FakeRunner(RunResult("completed", "1\n", "", 0))
        for flags in ([], ["--allow-trusted-code"]):
            with patch("subprocess.Popen", side_effect=AssertionError("static executed")):
                code, report, _ = self.invoke(flags, runner=runner)
            self.assertEqual(code, 0)
            self.assertTrue(report["valid"])
            self.assertEqual(report["execution"], "not-requested")
            self.assertEqual(report["runner"], None)
        self.assertEqual(runner.calls, [])
        code, text, _ = self.invoke(json_format=False)
        self.assertIn("Execution skipped", text)
        self.assertIn("not imply human review", text)

    def test_verify_requires_runner_and_trust(self):
        for flags in (["--verify"], ["--verify", "--runner", "trusted-local"],
                      ["--verify", "--allow-trusted-code"]):
            code, report, _ = self.invoke(flags)
            self.assertEqual((code, report["execution"]), (2, "unavailable"))

    def test_verified_and_failed_reports(self):
        flags = ["--verify", "--runner", "trusted-local", "--allow-trusted-code"]
        for result, expected_code, execution in [(RunResult("completed", "1\n", "", 0), 0, "passed"),
                (RunResult("completed", "wrong", "", 0), 1, "failed"),
                (RunResult("timeout"), 2, "unavailable")]:
            code, report, err = self.invoke(flags, runner=FakeRunner(result))
            self.assertEqual((code, report["execution"]), (expected_code, execution))
            self.assertIn("WARNING", err)

    def test_invalid_input_blocks_all_execution(self):
        bad = question()
        bad["prompt"] = " "
        path = self.write("bad.json", bad)
        runner = FakeRunner(RunResult("completed", "1\n", "", 0))
        code, report, _ = self.invoke(["--verify", "--runner", "trusted-local", "--allow-trusted-code"], [self.path, path], runner)
        self.assertEqual(code, 1)
        self.assertFalse(report["valid"])
        self.assertEqual(runner.calls, [])

    def test_duplicates_and_error_precedence(self):
        code, report, _ = self.invoke(files=[self.path, self.path])
        self.assertEqual(code, 1)
        self.assertIn("duplicate-question", [i["code"] for i in report["issues"]])
        code, report, _ = self.invoke(files=[self.path, self.path, str(self.directory / "missing")])
        self.assertEqual(code, 2)
        self.assertEqual(len(report["issues"]), 2)

    def test_malformed_json(self):
        for text in ('{"id": NaN}', '{"id": 1, "id": 2}', '{broken'):
            Path(self.path).write_text(text)
            code, report, _ = self.invoke()
            self.assertEqual(code, 1)
            self.assertEqual(report["issues"][0]["code"], "json")

    def test_cli_setup_errors_still_json(self):
        for argv in (["--format", "json"], ["--format=json", "--unknown"], ["--format", "json", "--runner", "missing", self.path]):
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(argv)
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(out.getvalue())["valid"])

    def test_wrong_cwd_and_missing_schema(self):
        previous = os.getcwd()
        try:
            os.chdir(self.directory)
            self.assertEqual(self.invoke()[0], 0)
        finally:
            os.chdir(previous)
        (self.directory / "question.schema.json").unlink()
        code, report, _ = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("schema", report["issues"][0]["message"])

    def test_dependency_missing_is_setup_error(self):
        with patch.dict("sys.modules", {"jsonschema": None}):
            code, report, _ = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("requirements", report["issues"][0]["message"])


if __name__ == "__main__":
    unittest.main()
