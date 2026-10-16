# SPDX-License-Identifier: MIT
"""Guard against the silent `python -m validator.<module>` no-op trap.

`python -m validator.export` / `.trace` / `.cli` must behave exactly like the
supported `python -m validator export|trace` (and the default validate form),
or an empty module would exit 0 without doing anything. These tests run the real
subprocesses; no execution, network or write side effects are involved.
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
QUESTION = "public/data/questions/py-list-last-boundary.json"


def run(*args):
    return subprocess.run([sys.executable, "-m", *args], cwd=REPO_ROOT,
                          capture_output=True, text=True)


def last_line(text):
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


class TestModuleEntrypoints(unittest.TestCase):
    def assert_delegates(self, variants, *, expect_nonzero=False):
        results = [run(*variant) for variant in variants]
        codes = {result.returncode for result in results}
        self.assertEqual(len(codes), 1,
                         [(result.returncode, result.stdout, result.stderr) for result in results])
        if expect_nonzero:
            self.assertNotEqual(results[0].returncode, 0,
                                [(result.stdout, result.stderr) for result in results])
        lines = {last_line(result.stdout) for result in results}
        self.assertEqual(len(lines), 1, lines)
        return results[0]

    def test_export_check_delegates_and_runs(self):
        result = self.assert_delegates([("validator.export", "--check"),
                                        ("validator", "export", "--check")])
        self.assertEqual(result.returncode, 0)
        self.assertIn("export passed", result.stdout)

    def test_export_error_delegates_and_fails_nonzero(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assert_delegates([("validator.export", "--check", "--root", empty),
                                   ("validator", "export", "--check", "--root", empty)],
                                  expect_nonzero=True)

    def test_trace_check_delegates_and_runs(self):
        result = self.assert_delegates([("validator.trace", "--check"),
                                        ("validator", "trace", "--check")])
        self.assertEqual(result.returncode, 0)
        self.assertIn("Trace check passed", result.stdout)

    def test_trace_error_delegates_and_fails_nonzero(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assert_delegates([("validator.trace", "--check", "--root", empty),
                                   ("validator", "trace", "--check", "--root", empty)],
                                  expect_nonzero=True)

    def test_cli_delegates_to_default_validation(self):
        result = self.assert_delegates([("validator.cli", QUESTION),
                                        ("validator", QUESTION)])
        self.assertEqual(result.returncode, 0)
        self.assertIn("VALID", result.stdout)


if __name__ == "__main__":
    unittest.main()
