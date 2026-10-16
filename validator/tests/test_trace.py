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
                  "verification": {"cases": [{"id": "case", "snippet_id": "main", "stdin": "",
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

    def test_generator_first_entry_resume_and_two_yields(self):
        record = self.trace_one("def g():\n    yield 'one'\n    yield 'two'\n\nit = g()\nnext(it)\nnext(it)")
        events = [step["event"] for step in record["steps"] if step["function"] == "g"]
        self.assertEqual(events, ["call", "line", "yield", "resume", "line", "yield"])

    def test_yield_from_and_send_resume_frames(self):
        source = (
            "def source():\n    value = yield 'ready'\n    yield value\n"
            "def delegate():\n    yield from source()\n"
            "worker = delegate()\nnext(worker)\nworker.send(7)"
        )
        record = self.trace_one(source)
        events = [(step["function"], step["event"]) for step in record["steps"]]
        self.assertIn(("delegate", "call"), events)
        self.assertIn(("delegate", "yield"), events)
        self.assertIn(("delegate", "resume"), events)
        self.assertIn(("source", "call"), events)
        self.assertIn(("source", "yield"), events)
        self.assertIn(("source", "resume"), events)
        self.assertEqual(record["exception"], None)

    def test_trace_generation_requires_a_case_for_each_snippet(self):
        question = json.loads(json.dumps(self.q))
        question["verification"]["cases"][0]["snippet_id"] = "other"

        class PoisonRunner:
            def run(self, *args):
                raise AssertionError("trace ran without a matching verification case")

        with self.assertRaisesRegex(RuntimeError, "no verification case"):
            trace._trace_question(question, self.qpath, PoisonRunner())

    def test_generator_send_question_has_one_resume(self):
        path = core.ROOT / "public/data/questions/py-generator-send-roundtrip.json"
        question = json.loads(path.read_text(encoding="utf-8"))
        record = trace._trace_question(question, path, LocalTestRunner())["traces"][0]
        events = [step["event"] for step in record["steps"] if step["function"] == "scale"]
        self.assertEqual(events.count("call"), 1)
        self.assertEqual(events.count("yield"), 2)
        self.assertEqual(events.count("resume"), 1)
        self.assertEqual(events.count("return"), 0)
        self.assertEqual(record["case_id"], "prime-send-and-receive")
        self.assertEqual(record["stdin"], "")
        self.assertEqual(record["stdin_sha256"], hashlib.sha256(b"").hexdigest())

    def test_finally_unwind_and_caught_or_suppressed_exceptions(self):
        escaping = self.trace_one(
            "def f():\n    try:\n        raise ValueError('x')\n    finally:\n        marker = 1\nf()"
        )
        f_events = [step for step in escaping["steps"] if step["function"] == "f"]
        exception_index = next(i for i, step in enumerate(f_events) if step["event"] == "exception")
        self.assertEqual(f_events[exception_index + 1]["event"], "line")
        self.assertEqual(f_events[-1]["event"], "unwind")
        self.assertEqual(f_events[-1]["exception"]["type"], "ValueError")
        self.assertEqual(escaping["exception"]["type"], "ValueError")

        nested = self.trace_one(
            "def inner():\n    raise ValueError('x')\ndef outer():\n    inner()\nouter()"
        )
        unwrapped = [step for step in nested["steps"] if step["event"] == "unwind"]
        self.assertEqual(sum(step["function"] == "inner" for step in unwrapped), 1)
        self.assertEqual(sum(step["function"] == "outer" for step in unwrapped), 1)

        caught = self.trace_one(
            "def f():\n    try:\n        raise ValueError('x')\n    except ValueError:\n        pass\n    return None\nf()"
        )
        caught_f = [step for step in caught["steps"] if step["function"] == "f"]
        self.assertEqual(caught_f[-1]["event"], "return")
        self.assertEqual(caught_f[-1]["value"], "None")
        self.assertNotIn("unwind", [step["event"] for step in caught_f])

        suppressed = self.trace_one(
            "def f():\n    try:\n        raise ValueError('x')\n    finally:\n        return None\nf()"
        )
        suppressed_f = [step for step in suppressed["steps"] if step["function"] == "f"]
        self.assertEqual(suppressed_f[-1]["event"], "return")
        self.assertEqual(suppressed_f[-1]["value"], "None")
        self.assertEqual(suppressed["exception"], None)

    def test_step_cap_and_determinism(self):
        source = "while True:\n    pass"
        one = self.trace_one(source)
        two = self.trace_one(source)
        self.assertTrue(one["truncated"])
        self.assertEqual(len(one["steps"]), 300)
        self.assertEqual(one["truncated_at_step"], 300)
        payload = {"schema_version": "0.2", "question_id": "test-question", "traces": [one]}
        self.assertEqual(trace._serialize(payload), trace._serialize({**payload, "traces": [two]}))
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            self.skipTest("jsonschema dependency is not installed")
        trace._validate_trace(payload, self.root / "schema/trace.schema.json")

    def test_repr_addresses_are_stable_and_compiler_locals_are_hidden(self):
        source = "functions = [lambda: 1]\ngen = (value for value in [1])\nprint(1)"
        first = self.trace_one(source)
        second = self.trace_one(source)
        self.assertEqual(trace._serialize(first), trace._serialize(second))
        names = {name for step in first["steps"] for name in step["locals"]}
        self.assertNotIn(".0", names)
        self.assertNotIn("<lambda> at 0x7", " ".join(str(step["locals"]) for step in first["steps"]))

    def test_stdout_len_tracks_output_after_line(self):
        record = self.trace_one("print('a')\nprint('bc')")
        lines = [step for step in record["steps"] if step["event"] == "line"]
        self.assertEqual([step["stdout_len"] for step in lines], [0, 2])

    def test_stdout_len_counts_unicode_code_points(self):
        record = self.trace_one("print('😀x')\nprint('z')")
        self.assertEqual(record["stdout"], "😀x\nz\n")
        lines = [step for step in record["steps"] if step["event"] == "line"]
        self.assertEqual([step["stdout_len"] for step in lines], [0, 3])
        self.assertEqual(record["steps"][-1]["stdout_len"], 5)

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

    def test_check_detects_changed_source_case_stdin_missing_and_orphan(self):
        output = self.root / "public/data/traces/test-question.json"
        output.parent.mkdir(parents=True)
        generated = trace._trace_question(self.q, self.qpath, LocalTestRunner())
        self.write(output, generated)
        self.assertEqual(trace._check(self.root, [self.qpath]), 0)

        self.q["snippets"][0]["code"] = "print(4)"
        self.write(self.qpath, self.q)
        self.assertEqual(trace._check(self.root, [self.qpath]), 1)
        self.q["snippets"][0]["code"] = "print(3)"
        self.q["verification"]["cases"][0]["id"] = "renamed-case"
        self.write(self.qpath, self.q)
        self.assertEqual(trace._check(self.root, [self.qpath]), 1)
        self.q["verification"]["cases"][0]["id"] = "case"
        self.q["verification"]["cases"][0]["stdin"] = "changed input"
        self.write(self.qpath, self.q)
        self.assertEqual(trace._check(self.root, [self.qpath]), 1)

        self.q["verification"]["cases"][0]["stdin"] = ""
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
