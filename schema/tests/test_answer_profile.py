"""Question contract 0.2: the explicit answer profile (standard | beginner-practice).

The legacy 0.1 document is unchanged (five to eight options, no answer_profile). 0.2 needs an explicit profile and the
option count follows it. Nothing is inferred from a course name, level, id prefix, option count or licence, and an unknown
version, an unknown or missing profile or an inconsistent combination is refused (never a silent fallback). Score
distribution, unique ids and the misconception rules are the semantic validator's and are tested there.
"""
import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA = Path(__file__).resolve().parents[1] / "question.schema.json"
EXAMPLE = Path(__file__).resolve().parents[2] / "content" / "examples" / "independent-if.json"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def option(index, score):
    return {"id": f"extra-{index}", "text": f"Synthetic option {index}.", "score": score, "feedback": f"Synthetic feedback {index}.",
            "misconception": None if score == 1 else "py.independent-if-is-elif",
            **({"partial_reason": "Synthetic partial reason."} if score == 0.5 else {})}


class AnswerProfileSchemaTests(unittest.TestCase):
    def setUp(self):
        schema = load(SCHEMA)
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema)
        self.legacy = load(EXAMPLE)                                  # 0.1, five options

    def valid(self, question):
        return list(self.validator.iter_errors(question)) == []

    def with_options(self, version, profile, count):
        question = copy.deepcopy(self.legacy)
        question["schema_version"] = version
        if profile is not None:
            question["answer_profile"] = profile
        base = question["options"][:4]                                  # full, half, zero, zero
        scores = [o["score"] for o in base]
        question["options"] = base + [option(i, 0) for i in range(max(0, count - 4))]
        question["options"] = question["options"][:count]
        assert [o["score"] for o in base] == scores
        return question

    def test_the_legacy_document_is_unchanged_and_still_valid(self):
        self.assertEqual((self.legacy["schema_version"], "answer_profile" in self.legacy, len(self.legacy["options"])), ("0.1", False, 5))
        self.assertTrue(self.valid(self.legacy))
        for count in (5, 6, 7, 8):
            self.assertTrue(self.valid(self.with_options("0.1", None, count)), count)

    def test_a_valid_standard_question_in_the_new_version_has_five_to_eight_options(self):
        for count in (5, 6, 7, 8):
            self.assertTrue(self.valid(self.with_options("0.2", "standard", count)), count)

    def test_a_valid_beginner_practice_question_has_exactly_four_options(self):
        self.assertTrue(self.valid(self.with_options("0.2", "beginner-practice", 4)))

    def test_the_option_count_follows_the_version_and_profile(self):
        refused = [
            ("legacy with four", self.with_options("0.1", None, 4)),
            ("legacy with nine", self.with_options("0.1", None, 9)),
            ("standard with four", self.with_options("0.2", "standard", 4)),
            ("standard with three", self.with_options("0.2", "standard", 3)),
            ("standard with nine", self.with_options("0.2", "standard", 9)),
            ("beginner with five", self.with_options("0.2", "beginner-practice", 5)),
            ("beginner with eight", self.with_options("0.2", "beginner-practice", 8)),
            ("beginner with three", self.with_options("0.2", "beginner-practice", 3)),
            ("beginner with none", self.with_options("0.2", "beginner-practice", 0)),
        ]
        for name, question in refused:
            with self.subTest(name):
                self.assertFalse(self.valid(question))

    def test_the_new_version_needs_an_explicit_known_profile(self):
        missing = self.with_options("0.2", None, 5)
        self.assertFalse(self.valid(missing), "a missing profile is never defaulted")
        self.assertFalse(self.valid(self.with_options("0.2", None, 4)), "four options do not imply the practice profile")
        for bad in ("premium", "Standard", "BEGINNER-PRACTICE", "beginner_practice", "", " standard", "standard ", None, True, 1, ["standard"], {"name": "standard"}):
            with self.subTest(profile=bad):
                question = self.with_options("0.2", "standard", 5)
                question["answer_profile"] = bad
                self.assertFalse(self.valid(question))

    def test_a_profile_on_the_legacy_version_is_refused(self):
        for profile in ("standard", "beginner-practice"):
            for count in (4, 5):
                with self.subTest(profile=profile, count=count):
                    self.assertFalse(self.valid(self.with_options("0.1", profile, count)))

    def test_unknown_or_missing_versions_are_refused(self):
        for version in ("0.4", "0.10", "0.2.0", "1", "1.0", "", " 0.2", "0.2 ", 0.2, 0.1, None, True, ["0.2"]):
            with self.subTest(version=version):
                question = self.with_options("0.2", "standard", 5)
                question["schema_version"] = version
                self.assertFalse(self.valid(question))
        question = self.with_options("0.2", "standard", 5)
        del question["schema_version"]
        self.assertFalse(self.valid(question))

    def test_nothing_is_inferred_from_course_level_id_licence_or_count(self):
        for field, value in (("level", "beginner"), ("level", "advanced"), ("id", "beginner-practice-one"), ("id", "b1-first-step"),
                             ("license", "LicenseRef-Proprietary"), ("license", "CC-BY-SA-4.0")):
            with self.subTest(field=field, value=value):
                question = self.with_options("0.2", None, 4)
                question[field] = value
                self.assertFalse(self.valid(question), "four options without a profile are refused whatever the other fields say")
                question["answer_profile"] = "beginner-practice"
                self.assertTrue(self.valid(question))

    def test_the_other_fields_keep_their_constraints_in_the_new_version(self):
        base = self.with_options("0.2", "beginner-practice", 4)
        for name, change in (("extra top-level field", {"extra": 1}), ("wrong language", {"language": "javascript"}),
                             ("wrong locale", {"locale": "sv"}), ("unknown type", {"type": "debug"})):
            with self.subTest(name):
                question = copy.deepcopy(base)
                question.update(change)
                self.assertFalse(self.valid(question))
        question = copy.deepcopy(base)
        question["options"][0]["score"] = True
        self.assertFalse(self.valid(question), "a boolean is never a score")
        question = copy.deepcopy(base)
        question["options"][1].pop("partial_reason")
        self.assertFalse(self.valid(question), "half credit still needs partial_reason")

    def test_the_schema_documents_both_versions_without_changing_the_legacy_branch(self):
        schema = load(SCHEMA)
        self.assertEqual(schema["properties"]["schema_version"], {"enum": ["0.1", "0.2", "0.3"]},
                         "0.3 adds text artifacts and keeps the 0.2 profile rules")
        self.assertEqual(schema["properties"]["answer_profile"], {"enum": ["standard", "beginner-practice"]})
        self.assertNotIn("minItems", schema["properties"]["options"], "the bounds live in the version/profile branches, not globally")


if __name__ == "__main__":
    unittest.main()
