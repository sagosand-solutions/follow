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


    # ---- format 0.3 (explicit opt-in; synthetic snippets only) ----
    def trace_v3(self, source, stdin=""):
        q = json.loads(json.dumps(self.q))
        q["snippets"][0]["code"] = source
        q["verification"]["cases"][0]["stdin"] = stdin
        q["verification"]["cases"][0].pop("expected", None)
        return trace._trace_question(q, self.qpath, LocalTestRunner(), "0.3")["traces"][0]

    def test_default_generation_is_unchanged_0_2(self):
        record = self.trace_one("x = 1\nprint(x)")
        self.assertTrue(all(set(step) <= {"line", "event", "depth", "function", "locals", "stdout_len", "value", "exception"}
                            for step in record["steps"]))

    def test_v3_global_mutation_inside_a_function_is_visible(self):
        record = self.trace_v3("events = []\ndef log(x):\n    events.append(x)\nlog(1)\nlog(2)")
        in_log = [s for s in record["steps"] if s["function"] == "log" and s["event"] == "return"]
        self.assertEqual([s["globals"]["events"] for s in in_log], ["[1]", "[1, 2]"])
        self.assertTrue(all(s["scope"] == "function" for s in in_log))
        self.assertNotIn("events", in_log[0]["locals"], "a global is not a local")

    def test_v3_global_shadowed_by_local_is_kept_apart(self):
        record = self.trace_v3("x = 'global'\ndef f():\n    x = 'local'\n    return x\nf()")
        ret = next(s for s in record["steps"] if s["function"] == "f" and s["event"] == "return")
        self.assertEqual(ret["locals"]["x"], "'local'")
        self.assertEqual(ret["globals"]["x"], "'global'")

    def test_v3_module_scope_has_empty_locals_and_only_user_data_globals(self):
        record = self.trace_v3("import math\nvalue = 3\ndef f(): pass\nclass K: pass\nname = 'a'\nprint(len(name))")
        last = record["steps"][-1]
        self.assertEqual(last["scope"], "module")
        self.assertEqual(last["locals"], {})
        self.assertEqual(last["globals"], {"name": "'a'", "value": "3"})
        self.assertEqual((last["locals_omitted"], last["globals_omitted"]), (0, 0), "functions/classes/modules are never counted")
        names = {n for s in record["steps"] for n in s["globals"]}
        self.assertFalse(names & {"__builtins__", "__name__", "data", "steps", "tracer", "snippet_stdin", "V3", "user_globals"})

    def test_v3_shared_alias_is_two_plain_values_with_no_identity_claim(self):
        record = self.trace_v3("a = [1]\nb = a\nb.append(2)")
        last = record["steps"][-1]
        self.assertEqual((last["globals"]["a"], last["globals"]["b"]), ("[1, 2]", "[1, 2]"))
        self.assertFalse(any(key in last for key in ("refs", "id", "alias")))

    def test_v3_two_generators_same_function_keep_distinct_frames(self):
        record = self.trace_v3("def g():\n    yield 1\n    yield 2\na = g()\nb = g()\nnext(a)\nnext(b)\nnext(a)")
        gen = [s for s in record["steps"] if s["function"] == "g"]
        by_frame = {}
        for step in gen:
            by_frame.setdefault(step["frame_id"], []).append(step["event"])
        self.assertEqual(len(by_frame), 2)
        first, second = sorted(by_frame)
        self.assertEqual(by_frame[first], ["call", "line", "yield", "resume", "line", "yield"])
        self.assertEqual(by_frame[second], ["call", "line", "yield"])

    def test_v3_recursion_ids_are_unique_and_never_reused_after_return(self):
        record = self.trace_v3("def f(n):\n    if n:\n        return f(n - 1)\n    return n\nf(2)\nf(1)")
        calls = [s["frame_id"] for s in record["steps"] if s["event"] == "call"]
        self.assertEqual(len(calls), len(set(calls)), "a returned frame's id is not handed out again")
        self.assertEqual(calls, sorted(calls))
        returns = [s["frame_id"] for s in record["steps"] if s["event"] == "return"]
        self.assertEqual(sorted(returns), sorted(calls))

    def test_v3_unwind_closes_the_frame_and_exception_keeps_identity(self):
        record = self.trace_v3("def f():\n    raise ValueError('x')\ntry:\n    f()\nexcept ValueError:\n    pass")
        inner = [s for s in record["steps"] if s["function"] == "f"]
        self.assertEqual({s["frame_id"] for s in inner}, {inner[0]["frame_id"]})
        self.assertEqual(inner[-1]["event"], "unwind")

    def test_v3_truncated_serialization_reports_omitted_without_counting_noise(self):
        names = "\n".join(f"v{i} = {i}" for i in range(45))
        record = self.trace_v3(names + "\ndef f(): pass\n")
        last = record["steps"][-1]
        self.assertEqual(len(last["globals"]), 40)
        self.assertEqual(last["globals_omitted"], 5, "5 data values over the 40-name limit; the function is not counted")
        big = self.trace_v3("a = 'x' * 70\nb = 'y' * 70\nc = 'z' * 70\nd = 'w' * 70\n" + "".join(f"e{i} = 'v' * 70\n" for i in range(25)) + "z = 1")
        end = big["steps"][-1]
        size = sum(len(k.encode()) + len(v.encode()) for k, v in {**end["locals"], **end["globals"]}.items())
        self.assertLessEqual(size, 2048)
        self.assertGreater(end["globals_omitted"], 0)

    def test_v3_locals_and_globals_share_one_budget(self):
        record = self.trace_v3("g1 = 'a' * 75\ng2 = 'b' * 75\ndef f(p, q, r, s, t, u):\n    return 1\n"
                               + "f(*(['c' * 75] * 6))")
        inner = next(s for s in record["steps"] if s["function"] == "f" and s["event"] == "return")
        total = sum(len(k) + len(v) for k, v in {**inner["locals"], **inner["globals"]}.items())
        self.assertLessEqual(total, 2048)

    def test_v3_generation_is_deterministic_and_matches_its_schema(self):
        source = "total = 0\ndef add(n):\n    global total\n    total += n\nfor i in range(3):\n    add(i)"
        first, second = self.trace_v3(source), self.trace_v3(source)
        self.assertEqual(first, second)
        shutil.copyfile(core.ROOT / "schema/trace-0.3.schema.json", self.root / "schema/trace-0.3.schema.json")
        data = {"schema_version": "0.3", "question_id": "test-question", "traces": [{
            "snippet_id": "main", "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "case_id": "case",
            "stdin": "", "stdin_sha256": hashlib.sha256(b"").hexdigest(), **first}]}
        trace._validate_trace(data, self.root / "schema/trace-0.3.schema.json")

    def test_check_accepts_0_2_and_0_3_files_and_rejects_unknown_versions(self):
        shutil.copyfile(core.ROOT / "schema/trace-0.3.schema.json", self.root / "schema/trace-0.3.schema.json")
        out = self.root / "public/data/traces/test-question.json"
        for version in ("0.2", "0.3", "0.2"):
            data = trace._trace_question(self.q, self.qpath, LocalTestRunner(), version)
            self.write(out, data)
            self.assertEqual(trace._check(self.root, [self.qpath]), 0, version)
        data["schema_version"] = "0.4"
        self.write(out, data)
        self.assertEqual(trace._check(self.root, [self.qpath]), 1)
        data = trace._trace_question(self.q, self.qpath, LocalTestRunner(), "0.3")
        data["schema_version"] = "0.2"          # 0.3 steps under a 0.2 envelope: 0.2 schema forbids extra fields
        self.write(out, data)
        self.assertEqual(trace._check(self.root, [self.qpath]), 1)

    def test_trace_format_flag_selects_the_schema_and_defaults_to_0_2(self):
        shutil.copyfile(core.ROOT / "schema/trace-0.3.schema.json", self.root / "schema/trace-0.3.schema.json")
        args = ["--root", str(self.root), "--runner", "trusted-local", "--allow-trusted-code"]
        out = self.root / "public/data/traces/test-question.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(trace.main(args + [str(self.qpath)], runner=LocalTestRunner()), 0)
            self.assertEqual(json.loads(out.read_text())["schema_version"], "0.2")
            self.assertEqual(trace.main(args + ["--trace-format", "0.3", str(self.qpath)], runner=LocalTestRunner()), 0)
            self.assertEqual(json.loads(out.read_text())["schema_version"], "0.3")
            self.assertEqual(trace.main(["--root", str(self.root), "--check"], runner=LocalTestRunner()), 0)


if __name__ == "__main__":
    unittest.main()
