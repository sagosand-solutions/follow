# SPDX-License-Identifier: MIT
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from validator import core
from validator import trace
from validator.runner import RunResult


class LocalTestRunner:
    name = "synthetic-trace-test-double"

    def run(self, code, stdin, limits):
        try:
            flags = ("-s", "-P") if limits.python_hash_seed is not None else ("-I",)
            result = subprocess.run([sys.executable, *flags, "-c", code], input=stdin,
                                    capture_output=True, text=True, timeout=limits.wall_seconds,
                                    env={"LANG": "C.UTF-8", **({"PYTHONHASHSEED": limits.python_hash_seed}
                                                                   if limits.python_hash_seed is not None else {})})
        except subprocess.TimeoutExpired as exc:
            return RunResult("timeout", stdout=exc.stdout or "", stderr=exc.stderr or "")
        return RunResult("completed" if result.returncode == 0 else "error",
                         stdout=result.stdout, stderr=result.stderr,
                         exit_code=result.returncode)


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "schema").mkdir()
        shutil.copyfile(core.ROOT / "schema/trace.schema.json", self.root / "schema/trace.schema.json")
        (self.root / "public/data/questions").mkdir(parents=True)
        self.qpath = self.root / "public/data/questions/test-question.json"
        self.q = {"id": "test-question", "snippets": [{"id": "main", "code": "print(3)"}],
                  "verification": {"cases": [{"snippet_id": "main", "stdin": "",
                                                "expected": {"stdout": "3\n"}}]}}
        self.write(self.qpath, self.q)

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def trace_one(self, source, stdin=""):
        q = json.loads(json.dumps(self.q))
        q["snippets"][0]["code"] = source
        q["verification"]["cases"][0]["stdin"] = stdin
        q["verification"]["cases"][0].pop("expected", None)
        return trace._trace_question(q, self.qpath, LocalTestRunner())["traces"][0]

    def test_function_call_return_and_local_filter(self):
        record = self.trace_one("def add(x):\n    y = x + 1\n    return y\nprint(add(2))")
        self.assertEqual(record["stdout"], "3\n")
        events = [step["event"] for step in record["steps"]]
        self.assertIn("call", events)
        self.assertIn("return", events)
        returned = next(step for step in record["steps"] if step["event"] == "return" and step["function"] == "add")
        self.assertEqual(returned["value"], "3")
        self.assertTrue(all(not name.startswith("__") for step in record["steps"] for name in step["locals"]))
        self.assertEqual(next(step["depth"] for step in record["steps"] if step["function"] == "<module>"), 0)

    def test_exception_and_recursion(self):
        failed = self.trace_one("value = 1\nprint(value / 0)")
        self.assertEqual(failed["exception"]["type"], "ZeroDivisionError")
        self.assertEqual(failed["exception"]["line"], 2)
        recursive = self.trace_one("def f(n):\n    if n: return f(n - 1)\n    return n\nf(3)")
        self.assertGreater(max(step["depth"] for step in recursive["steps"]), 0)

    def test_step_cap_and_determinism(self):
        source = "while True:\n    pass"
        one = self.trace_one(source)
        two = self.trace_one(source)
        self.assertTrue(one["truncated"])
        self.assertEqual(len(one["steps"]), 300)
        payload = {"schema_version": "0.1", "question_id": "test-question", "traces": [one]}
        self.assertEqual(trace._serialize(payload), trace._serialize({**payload, "traces": [two]}))
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            self.skipTest("jsonschema dependency is not installed")
        trace._validate_trace(payload, self.root / "schema/trace.schema.json")

    def test_stdout_len_tracks_output_after_line(self):
        record = self.trace_one("print('a')\nprint('bc')")
        lines = [step for step in record["steps"] if step["event"] == "line"]
        self.assertEqual([step["stdout_len"] for step in lines], [0, 2])

    def test_generation_requires_explicit_trust_and_check_never_runs(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(trace.main(["--root", str(self.root)], runner=LocalTestRunner()), 2)
        self.assertIn("requires --runner", output.getvalue())
        # Create one valid trace, then use an object that fails if check tries to execute.
        with patch.object(trace, "_validate_trace"), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(trace.main(["--root", str(self.root), "--runner", "trusted-local",
                                         "--allow-trusted-code"], runner=LocalTestRunner()), 0)
        poison = type("Poison", (), {"run": lambda *args: (_ for _ in ()).throw(AssertionError("executed"))})()
        with patch.object(trace, "TrustedLocalRunner", return_value=poison):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(trace.main(["--root", str(self.root), "--check"], runner=poison), 0)
        self.assertIn("execution never requested", output.getvalue())
        value = json.loads((self.root / "public/data/traces/test-question.json").read_text())
        self.assertEqual(value["traces"][0]["source_sha256"], hashlib.sha256(b"print(3)").hexdigest())

    def test_check_detects_changed_missing_and_orphan(self):
        output = self.root / "public/data/traces/test-question.json"
        output.parent.mkdir(parents=True)
        self.write(output, {"question_id": "test-question", "traces": [{"snippet_id": "main",
                    "source_sha256": hashlib.sha256(b"print(3)").hexdigest()}]})
        self.assertEqual(trace._check(self.root, [self.qpath]), 0)
        self.q["snippets"][0]["code"] = "print(4)"
        self.write(self.qpath, self.q)
        self.assertEqual(trace._check(self.root, [self.qpath]), 1)
        self.q["snippets"][0]["code"] = "print(3)"
        self.write(self.qpath, self.q)
        output.unlink()
        self.assertEqual(trace._check(self.root, [self.qpath]), 1)
        orphan = self.root / "public/data/traces/orphan.json"
        self.write(orphan, {})
        self.assertEqual(trace._check(self.root, [self.qpath]), 1)

    def test_timeout_and_output_limit_have_no_output_written(self):
        qpath = self.root / "public/data/questions/loop-question.json"
        q = json.loads(json.dumps(self.q))
        q["id"] = "loop-question"
        q["snippets"][0]["code"] = "while True: pass"
        self.write(qpath, q)
        class FailedRunner:
            def __init__(self, status):
                self.status = status
            def run(self, *args):
                return RunResult(self.status)
        for status in ("timeout", "output-limit"):
            with self.subTest(status=status), redirect_stdout(io.StringIO()):
                self.assertEqual(trace.main(["--root", str(self.root), "--runner", "trusted-local",
                                             "--allow-trusted-code", str(qpath)], runner=FailedRunner(status)), 2)
            self.assertFalse((self.root / "public/data/traces/loop-question.json").exists())


if __name__ == "__main__":
    unittest.main()
