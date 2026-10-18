# SPDX-License-Identifier: MIT
"""The synthetic fixtures the web tests render are valid questions under the real schema, the real validator and the shared
concept map, so the browser chain and the author-side contract are tested against the very same documents."""
import hashlib
import json
from pathlib import Path
import unittest

from validator import core
from validator.core import validate_question

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "web-tests" / "fixtures"
PINNED = {
    "practice-question.json": "c95593206b01286ea4c0a859b15873505827708ce295439661f6cd201bd9606e",
    "standard-question.json": "d6aa020bb56331c06700d4cd8dbac1cd06bcef5e20bfcf287afaaf112d635748",
}


class AnswerProfileFixtureTests(unittest.TestCase):
    def setUp(self):
        self.map = json.loads((ROOT / "schema" / "concepts" / "python-v0.json").read_text(encoding="utf-8"))

    def test_the_fixtures_are_valid_and_pinned(self):
        for name, digest in PINNED.items():
            with self.subTest(name):
                raw = (FIXTURES / name).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), digest, "a fixture changed: update the pin deliberately")
                self.assertEqual(validate_question(json.loads(raw), self.map), [])

    def test_the_fixtures_have_the_profiles_and_option_counts_they_claim(self):
        practice = json.loads((FIXTURES / "practice-question.json").read_text(encoding="utf-8"))
        standard = json.loads((FIXTURES / "standard-question.json").read_text(encoding="utf-8"))
        self.assertEqual((practice["schema_version"], practice["answer_profile"], len(practice["options"])), ("0.2", "beginner-practice", 4))
        self.assertEqual((standard["schema_version"], standard["answer_profile"], len(standard["options"])), ("0.2", "standard", 5))
        self.assertEqual(sorted(o["score"] for o in practice["options"]), [0, 0, 0.5, 1])

    def test_the_fixtures_are_not_part_of_the_site_data(self):
        site = ROOT / "public" / "data" / "questions"
        for name in ("synthetic-practice-question.json", "synthetic-standard-question.json"):
            self.assertFalse((site / name).exists(), "synthetic test questions never ship with the site")


if __name__ == "__main__":
    unittest.main()
