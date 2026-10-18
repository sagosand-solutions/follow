# SPDX-License-Identifier: AGPL-3.0-or-later
"""The client's scoring-scope helper and the server's course-scope regex agree on one shared matrix.

The matrix lives in ``web-tests/fixtures/scope-cases.json`` and is read by both ``web-tests/scopes.test.mjs`` and this
test, so there is no second hand-maintained copy to drift. ``total`` is a literal, not a course id, so it is checked
separately here.
"""
import json
import unittest
from pathlib import Path

from server.scoring_service import _COURSE_SCOPE_RE

CASES = json.loads((Path(__file__).parents[2] / "web-tests" / "fixtures" / "scope-cases.json").read_text(encoding="utf-8"))


class ScopeParityTests(unittest.TestCase):
    def test_the_server_regex_agrees_with_the_shared_matrix(self):
        for scope, expected in CASES:
            if scope == "total":
                continue                                  # a literal, not a course id
            with self.subTest(scope=scope):
                self.assertEqual(_COURSE_SCOPE_RE.fullmatch(scope) is not None, expected)

    def test_total_is_not_a_course_scope(self):
        self.assertIsNone(_COURSE_SCOPE_RE.fullmatch("total"))

    def test_the_matrix_has_the_documented_edge_cases(self):
        scopes = [scope for scope, _ in CASES]
        self.assertIn("course:" + "a" + "b" * 63, scopes)          # 64 characters: the maximum
        self.assertIn("course:" + "a" * 65, scopes)                # one too many
        self.assertIn("course:a\n", scopes)                        # a trailing line break is refused


if __name__ == "__main__":
    unittest.main()
