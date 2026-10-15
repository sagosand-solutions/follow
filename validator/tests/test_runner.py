# SPDX-License-Identifier: MIT
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from validator import Limits, RunResult, TrustedLocalRunner
from validator.runner import verify_question
from helpers import question


def assert_process_stopped(stat, timeout=1):
    """A reaped process or zombie is stopped; a live process must fail the check."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            # Do not check exists() first: /proc can disappear before open/read.
            contents = stat.read_text()
        except (FileNotFoundError, ProcessLookupError):
            # ENOENT at open, or ESRCH if the task vanishes after open but
            # before the procfs read. Both mean the observed process is gone.
            return
        # comm is parenthesized and can contain spaces or closing parentheses.
        state = contents.rsplit(")", 1)[1].split()[0]
        if state == "Z":
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"Process still live at {stat}: state={state}")
        time.sleep(0.01)


class CleanupObservationTests(unittest.TestCase):
    def test_reaped_before_first_read(self):
        for error in (FileNotFoundError, ProcessLookupError):
            with self.subTest(error=error):
                stat = Mock()
                stat.read_text.side_effect = error("process reaped")
                assert_process_stopped(stat)
                stat.read_text.assert_called_once_with()
                stat.exists.assert_not_called()

    def test_reaped_between_observations(self):
        for error in (FileNotFoundError, ProcessLookupError):
            with self.subTest(error=error):
                stat = Mock()
                stat.read_text.side_effect = ["123 (python) S 1", error("process reaped")]
                with patch.object(time, "monotonic", return_value=0), patch.object(time, "sleep") as sleep:
                    assert_process_stopped(stat)
                self.assertEqual(stat.read_text.call_count, 2)
                sleep.assert_called_once_with(0.01)

    def test_live_then_zombie(self):
        stat = Mock()
        stat.read_text.side_effect = ["123 (name with ) spaces) R 1", "123 (name with ) spaces) Z 1"]
        with patch.object(time, "monotonic", return_value=0), patch.object(time, "sleep"):
            assert_process_stopped(stat)
        self.assertEqual(stat.read_text.call_count, 2)

    def test_live_process_is_not_cleanup_success(self):
        stat = Mock()
        stat.read_text.return_value = "123 (python) S 1"
        with patch.object(time, "monotonic", side_effect=[0, 1]):
            with self.assertRaisesRegex(AssertionError, "Process still live.*state=S"):
                assert_process_stopped(stat)

    def test_other_io_errors_are_not_hidden(self):
        stat = Mock()
        stat.read_text.side_effect = PermissionError("not observable")
        with self.assertRaises(PermissionError):
            assert_process_stopped(stat)


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
            with patch("os.geteuid", return_value=0):
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
        assert_process_stopped(stat)


if __name__ == "__main__":
    unittest.main()
