# SPDX-License-Identifier: MIT
"""--verify and text artifacts: text is never run, so it never earns "execution: passed"."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from validator import RunResult
from validator.cli import main
from validator.runner import verify_question

from test_artifacts import mixed, text_only


class CountingRunner:
    """Runs nothing for real; answers the one case of the code question and records every call."""
    name = "counting"

    def __init__(self):
        self.calls = []

    def run(self, code, stdin, limits):
        self.calls.append(code)
        return RunResult("completed", "1\n", "", 0)


class ForbiddenRunner:
    name = "forbidden"

    def run(self, code, stdin, limits):
        raise AssertionError("text must never reach the runner")


FLAGS = ["--verify", "--runner", "trusted-local", "--allow-trusted-code"]


class VerifyCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def write(self, name, question):
        path = self.dir / name
        path.write_text(json.dumps(question), encoding="utf-8")
        return str(path)

    def invoke(self, files, runner, json_format=True, flags=FLAGS):
        out, err = io.StringIO(), io.StringIO()
        argv = (["--format", "json"] if json_format else []) + list(flags) + list(files)
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv, runner=runner)
        return code, (json.loads(out.getvalue()) if json_format else out.getvalue())

    def test_a_text_only_question_is_not_applicable_and_the_runner_is_never_called(self):
        code, report = self.invoke([self.write("t.json", text_only())], ForbiddenRunner())
        self.assertEqual(code, 0)
        self.assertTrue(report["valid"], "statically valid")
        self.assertEqual(report["execution"], "not-applicable")
        self.assertEqual((report["executed_cases"], report["text_only_questions"], report["artifacts"]), (0, 1, 1))

    def test_a_text_only_question_prints_it_in_words(self):
        code, text = self.invoke([self.write("t.json", text_only())], ForbiddenRunner(), json_format=False)
        self.assertEqual(code, 0)
        self.assertIn("execution: not-applicable", text)
        self.assertIn("0 case(s) run; 1 text-only question(s) not run", text)
        self.assertIn("Execution not applicable", text)
        self.assertNotIn("execution: passed", text)
        self.assertIn("Text artifacts: 1", text)
        self.assertIn("fixture bytes are verified by the export", text)

    def test_a_mixed_batch_runs_only_the_code_and_says_how_much(self):
        runner = CountingRunner()
        files = [self.write("a.json", text_only()), self.write("b.json", mixed(id="code-reading-one"))]
        code, report = self.invoke(files, runner)
        self.assertEqual(code, 0)
        self.assertEqual(report["execution"], "passed")
        self.assertEqual(runner.calls, ["print(1)"], "only the code snippet ran")
        self.assertEqual((report["executed_cases"], report["text_only_questions"], report["artifacts"]), (1, 1, 2))
        _, text = self.invoke(files, CountingRunner(), json_format=False)
        self.assertIn("execution: passed (1 case(s) run; 1 text-only question(s) not run)", text)

    def test_a_mixed_question_runs_its_snippet_and_not_its_artifact(self):
        runner = CountingRunner()
        code, report = self.invoke([self.write("m.json", mixed())], runner)
        self.assertEqual((code, report["execution"], report["executed_cases"], report["text_only_questions"]), (0, "passed", 1, 0))
        self.assertEqual(runner.calls, ["print(1)"])

    def test_old_statuses_are_unchanged(self):
        for result, expected_code, execution in [(RunResult("completed", "1\n", "", 0), 0, "passed"),
                                                 (RunResult("completed", "wrong", "", 0), 1, "failed"),
                                                 (RunResult("timeout"), 2, "unavailable")]:
            runner = CountingRunner()
            runner.run = lambda code, stdin, limits, result=result: result
            code, report = self.invoke([self.write("m.json", mixed())], runner)
            self.assertEqual((code, report["execution"]), (expected_code, execution))

    def test_static_validation_of_text_claims_no_run_either(self):
        code, report = self.invoke([self.write("t.json", text_only())], ForbiddenRunner(), flags=[])
        self.assertEqual((code, report["execution"], report["executed_cases"]), (0, "not-requested", 0))

    def test_an_invalid_batch_still_blocks_all_execution(self):
        bad = text_only()
        bad["artifacts"][0]["sha256"] = "0" * 64
        runner = CountingRunner()
        code, report = self.invoke([self.write("bad.json", bad), self.write("code.json", mixed(id="code-reading-one"))], runner)
        self.assertEqual(code, 1)
        self.assertEqual(runner.calls, [], "no snippet runs while any input has static errors")
        self.assertNotEqual(report["execution"], "passed")

    def test_verify_question_itself_refuses_text(self):
        with self.assertRaises(ValueError):
            verify_question(text_only(), ForbiddenRunner())


if __name__ == "__main__":
    unittest.main()
