# SPDX-License-Identifier: MIT
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from validator import Limits, RunResult, TrustedLocalRunner
from validator.runner import verify_question
from helpers import question


class FakeRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, code, stdin, limits):
        self.calls.append((code, stdin, limits))
        return self.result


class BoundaryTests(unittest.TestCase):
    def test_exact_results(self):
        for result, codes, infrastructure in [
            (RunResult("completed", "1\n", "", 0), set(), False),
            (RunResult("completed", "1", "", 0), {"stdout"}, False),
            (RunResult("completed", "1\n", "warning", 0), {"stderr"}, False),
            (RunResult("completed", "1\n", "", 1), {"exit-code"}, False),
            (RunResult("completed", "1\n", "", -9), {"runner-completed"}, True),
            *[(RunResult(status), {"runner-" + status}, True)
              for status in ("timeout", "memory-limit", "output-limit", "unavailable", "error")],
        ]:
            runner = FakeRunner(result)
            with self.subTest(result=result):
                issues, infra = verify_question(question(), runner)
                self.assertEqual({i.code for i in issues}, codes)
                self.assertEqual(infra, infrastructure)
                self.assertEqual(len(runner.calls), 1)

    def test_expected_crash_substring(self):
        q = question()
        q["verification"]["cases"][0]["expected"] = {"stdout": "", "exit_code": 1, "stderr_contains": "ValueError"}
        self.assertEqual(verify_question(q, FakeRunner(RunResult("completed", "", "trace: ValueError: text", 1))), ([], False))
        self.assertTrue(verify_question(q, FakeRunner(RunResult("completed", "", "TypeError", 1)))[0])

    def test_refuses_by_default_and_unsupported_platform(self):
        with patch("subprocess.Popen", side_effect=AssertionError("must not run")):
            self.assertEqual(TrustedLocalRunner().run("print(1)", "", Limits()).status, "unavailable")
            with patch("sys.platform", "unsupported"):
                self.assertEqual(TrustedLocalRunner(allow_trusted_code=True).run("print(1)", "", Limits()).status, "unavailable")
            with patch("resource.getrlimit", side_effect=OSError("not available")):
                self.assertEqual(TrustedLocalRunner(allow_trusted_code=True).run("print(1)", "", Limits()).status, "unavailable")

    def test_limits_reject_invalid_values(self):
        for values in ({"wall_seconds": 0}, {"wall_seconds": float("nan")}, {"output_bytes": -1}, {"processes": True}):
            with self.assertRaises(ValueError):
                Limits(**values)


@unittest.skipUnless(sys.platform == "linux", "Local execution requires Linux")
class LocalSmokeTests(unittest.TestCase):
    def setUp(self):
        self.runner = TrustedLocalRunner(allow_trusted_code=True)

    def run_code(self, code, stdin="", limits=None):
        return self.runner.run(code, stdin, limits or Limits())

    def test_print_and_stdin(self):
        result = self.run_code("print(input())", "hello\n")
        self.assertEqual((result.status, result.stdout, result.stderr, result.exit_code), ("completed", "hello\n", "", 0))
        self.assertFalse(result.capabilities["network_isolated"])
        self.assertFalse(result.capabilities["host_filesystem_isolated"])
        self.assertIn("no-network-isolation", result.capabilities["isolation"])

    def test_ordinary_exception(self):
        result = self.run_code("raise ValueError('smoke')")
        self.assertEqual((result.status, result.exit_code), ("completed", 1))
        self.assertIn("ValueError: smoke", result.stderr)

    def test_timeout(self):
        result = self.run_code("while True: pass", limits=Limits(wall_seconds=0.15))
        self.assertEqual(result.status, "timeout")
        self.assertLess(result.elapsed_seconds, 2)

    def test_output_bounded_while_reading(self):
        result = self.run_code("while True: print('x' * 1000)", limits=Limits(output_bytes=4096))
        self.assertEqual(result.status, "output-limit")
        self.assertLessEqual(len(result.stdout.encode()) + len(result.stderr.encode()), 4096)

    def test_fresh_temp_interpreter_environment_and_cleanup(self):
        code = "import os, sys\nprint(os.getcwd())\nprint(sys.flags.isolated)\nprint('VALIDATOR_TEST_SECRET' in os.environ)\n"
        with patch.dict(os.environ, {"VALIDATOR_TEST_SECRET": "secret"}):
            first = self.run_code(code)
            second = self.run_code(code)
        self.assertEqual(first.status, "completed")
        a, isolated, leaked = first.stdout.splitlines()
        b = second.stdout.splitlines()[0]
        self.assertNotEqual(a, b)
        self.assertEqual((isolated, leaked), ("1", "False"))
        self.assertFalse(Path(a).exists())
        self.assertFalse(Path(b).exists())

    def test_process_group_cleanup_on_parent_exit(self):
        # Self-written child merely sleeps. No filesystem/network probing by code.
        result = self.run_code("import subprocess, sys\np = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(20)'])\nprint(p.pid, flush=True)", limits=Limits(wall_seconds=0.2, processes=4096))
        self.assertEqual(result.status, "timeout", result.stderr)
        pid = int(result.stdout.strip())
        # Reparented children can remain zombies until the system reaper runs.
        stat = Path(f"/proc/{pid}/stat")
        deadline = time.monotonic() + 1
        while stat.exists() and stat.read_text().split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        if stat.exists():
            self.assertEqual(stat.read_text().split()[2], "Z")


if __name__ == "__main__":
    unittest.main()
