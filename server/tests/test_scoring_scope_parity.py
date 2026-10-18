# SPDX-License-Identifier: AGPL-3.0-or-later
"""The client's scoring-scope helper and the server's course-scope regex agree on one shared matrix.

The same list lives in ``web-tests/scopes.test.mjs``; the client only ever sends a scope that has the server's shape.
``total`` is a literal, not a course id, so it is checked separately here.
"""
import unittest

from server.scoring_service import _COURSE_SCOPE_RE

# (scope, is a valid course:... scope). Identical to SCOPE_CASES in web-tests/scopes.test.mjs.
SCOPE_CASES = [
    ("course:a", True),
    ("course:a-1", True),
    ("course:abc-123", True),
    ("course:" + "a" + "b" * 63, True),                 # the 64-character maximum
    ("", False),
    ("Total", False),
    ("total ", False),
    (" total", False),
    ("course", False),
    ("course:", False),
    ("course:A", False),
    ("course:1a", False),
    ("course:a_b", False),
    ("course:a.b", False),
    ("course:-a", False),
    ("course:a/b", False),
    ("course:" + "a" * 65, False),                       # 65 characters: one too many
    ("course:a\n", False),
    ("course:a\r", False),
    ("course:a\u2028", False),
]


class ScopeParityTests(unittest.TestCase):
    def test_the_server_regex_agrees_with_the_client_matrix(self):
        for scope, expected in SCOPE_CASES:
            with self.subTest(scope=scope):
                self.assertEqual(_COURSE_SCOPE_RE.fullmatch(scope) is not None, expected)

    def test_total_is_not_a_course_scope(self):
        self.assertIsNone(_COURSE_SCOPE_RE.fullmatch("total"))


if __name__ == "__main__":
    unittest.main()
