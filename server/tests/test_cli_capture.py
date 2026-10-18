# SPDX-License-Identifier: MIT
"""run_cli_captured: garbage from earlier tests never lands in the captured stderr, and the command's own output still does."""
import gc
import sqlite3
import sys
import unittest
import warnings

from server.tests.support import run_cli_captured


def leave_unclosed_connection_in_a_cycle():
    cycle = []
    cycle.append(sqlite3.connect(":memory:"))
    cycle.append(cycle)             # only the garbage collector can free it, at a moment the test does not choose


class CliCaptureTests(unittest.TestCase):
    def test_a_warning_for_garbage_from_an_earlier_test_is_not_in_the_captured_stderr(self):
        with warnings.catch_warnings():
            warnings.simplefilter("always", ResourceWarning)
            leave_unclosed_connection_in_a_cycle()
            # The command lets the collector run while the capture is active, as a real collection might.
            code, out, err = run_cli_captured(lambda argv: (gc.collect(), 0)[1], [])
        self.assertEqual((code, out, err), (0, "", ""))

    def test_the_commands_own_stdout_stderr_and_warnings_are_still_captured(self):
        def main(argv):
            print("hello")
            print("refused: no", file=sys.stderr)
            warnings.warn("the command's own warning", UserWarning)
            return 2
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            code, out, err = run_cli_captured(main, [])
        self.assertEqual((code, out), (2, "hello\n"))
        self.assertIn("refused: no", err)
        self.assertIn("the command's own warning", err, "nothing is filtered out of what the command itself raises")


if __name__ == "__main__":
    unittest.main()
