# SPDX-License-Identifier: MIT
"""Semantic rules of the answer profile (question 0.2): option count and score distribution, unique ids, misconception and
partial_reason binding, snippet/case references. The legacy 0.1 rules are covered by test_core and must stay as they were."""
import unittest
from copy import deepcopy

from validator.core import validate_question
from validator.tests.helpers import concept_map, question


def option(index, score, **over):
    return {"id": f"o{index}", "text": f"Synthetic option {index}", "score": score, "feedback": "Synthetic feedback",
            "misconception": None if score == 1 else "py.test-error",
            **({"partial_reason": "Synthetic correct and incorrect parts"} if score == 0.5 else {}), **over}


def practice(scores=(1, 0.5, 0, 0)):
    q = question()
    q["schema_version"] = "0.2"
    q["answer_profile"] = "beginner-practice"
    q["options"] = [option(i, score) for i, score in enumerate(scores)]
    return q


def standard(scores=(1, 0.5, 0, 0, 0)):
    q = practice(scores)
    q["answer_profile"] = "standard"
    return q


class AnswerProfileValidatorTests(unittest.TestCase):
    def setUp(self):
        self.map = concept_map()

    def codes(self, q):
        return {i.code for i in validate_question(q, self.map)}

    def issues(self, q):
        return validate_question(q, self.map)

    def test_valid_questions_of_every_kind_pass(self):
        self.assertEqual(self.issues(question()), [], "legacy 0.1")
        self.assertEqual(self.issues(standard()), [], "0.2 standard with five")
        self.assertEqual(self.issues(standard((1, 0.5, 0, 0, 0, 0, 0, 0))), [], "0.2 standard with eight")
        self.assertEqual(self.issues(practice()), [], "0.2 beginner-practice with four")

    def test_the_option_count_follows_the_version_and_profile(self):
        refused = {"legacy four": {**question(), "options": [option(i, s) for i, s in enumerate((1, 0.5, 0, 0))]},
                   "standard four": standard((1, 0.5, 0, 0)),
                   "standard nine": standard((1, 0.5, 0, 0, 0, 0, 0, 0, 0)),
                   "beginner five": practice((1, 0.5, 0, 0, 0)),
                   "beginner three": practice((1, 0.5, 0)),
                   "beginner eight": practice((1, 0.5, 0, 0, 0, 0, 0, 0))}
        for name, q in refused.items():
            with self.subTest(name):
                self.assertIn("schema", self.codes(q))

    def test_missing_unknown_and_misplaced_profiles_and_unknown_versions_are_refused(self):
        no_profile = practice()
        del no_profile["answer_profile"]
        legacy_with_profile = {**question(), "answer_profile": "standard"}
        legacy_with_practice = {**question(), "answer_profile": "beginner-practice"}
        unknown_profile = {**practice(), "answer_profile": "premium"}
        unknown_version = {**practice(), "schema_version": "0.3"}
        for name, q in {"missing": no_profile, "on legacy (standard)": legacy_with_profile, "on legacy (practice)": legacy_with_practice,
                        "unknown profile": unknown_profile, "unknown version": unknown_version}.items():
            with self.subTest(name):
                self.assertIn("schema", self.codes(q))

    def test_the_beginner_distribution_is_exactly_one_full_one_half_two_zero(self):
        refused = {"two halves": (1, 0.5, 0.5, 0), "no half": (1, 0, 0, 0), "two full": (1, 1, 0.5, 0), "no full": (0.5, 0, 0, 0),
                   "one zero": (1, 0.5, 0.5, 0.5)}
        for name, scores in refused.items():
            with self.subTest(name):
                self.assertIn("scores", self.codes(practice(scores)))
        self.assertNotIn("scores", self.codes(practice((0, 0, 1, 0.5))), "the order of the options does not matter")

    def test_a_boolean_is_never_a_score(self):
        for profile, build in (("beginner-practice", practice), ("standard", standard)):
            with self.subTest(profile):
                q = build()
                q["options"][0]["score"] = True
                self.assertTrue(self.codes(q), "True is neither accepted by the schema nor counted as 1")
                q = build()
                q["options"][2]["score"] = False
                self.assertTrue(self.codes(q), "False is never zero")

    def test_the_standard_distribution_rule_is_unchanged(self):
        self.assertIn("scores", self.codes(standard((1, 0.5, 0, 0, 0.5))))
        self.assertIn("scores", self.codes(standard((1, 1, 0, 0, 0))))
        self.assertEqual(self.issues(standard((1, 0.5, 0, 0, 0))), [])
        self.assertIn("scores", self.codes({**question(), "options": [option(i, s) for i, s in enumerate((1, 0.5, 0, 0, 0.5))]}))

    def test_option_ids_must_be_unique_in_the_practice_profile_too(self):
        q = practice()
        q["options"][2]["id"] = q["options"][1]["id"]
        self.assertIn("duplicate", self.codes(q))
        q = practice()
        q["options"][3]["text"] = q["options"][2]["text"]
        self.assertIn("duplicate-text", self.codes(q))

    def test_partial_reason_and_misconception_binding_are_the_same_in_the_practice_profile(self):
        q = practice()
        del q["options"][1]["partial_reason"]
        self.assertTrue(self.codes(q), "half credit needs partial_reason")
        q = practice()
        q["options"][2]["partial_reason"] = "why"
        self.assertTrue(self.codes(q), "partial_reason only belongs on half credit")
        q = practice()
        q["options"][0]["misconception"] = "py.test-error"
        self.assertTrue(self.codes(q), "full credit has no misconception (the schema and the validator both say so)")
        q = practice()
        q["options"][2]["misconception"] = None
        self.assertTrue(self.codes(q), "a wrong option needs a misconception")
        q = practice()
        q["options"][2]["misconception"] = "py.missing"
        self.assertIn("unknown-misconception", self.codes(q))
        q = practice()
        q["options"][3]["misconception"] = "py.other-error"
        self.assertIn("untagged-misconception", self.codes(q), "the misconception must relate to a tagged concept")

    def test_snippet_and_case_references_are_checked_in_the_practice_profile_too(self):
        q = practice()
        q["verification"]["cases"][0]["snippet_id"] = "nowhere"
        self.assertIn("unknown-snippet", self.codes(q))
        q = practice()
        q["snippets"][0]["highlight_lines"] = [9]
        self.assertIn("line-range", self.codes(q))
        q = practice()
        q["snippets"].append({"id": "second", "code": "print(2)"})
        self.assertIn("unverified-snippet", self.codes(q))

    def test_nothing_is_inferred_from_the_other_fields(self):
        for change in ({"level": "beginner"}, {"level": "advanced"}, {"id": "beginner-practice-one"}, {"license": "LicenseRef-Proprietary"}):
            with self.subTest(change=change):
                four = {**question(), **change, "options": [option(i, s) for i, s in enumerate((1, 0.5, 0, 0))]}
                self.assertIn("schema", self.codes(four), "four options without a profile are refused")
                four.update(schema_version="0.2", answer_profile="beginner-practice")
                self.assertEqual(self.issues(four), [])

    def test_the_documents_are_not_modified(self):
        q = practice()
        before = deepcopy(q)
        self.issues(q)
        self.assertEqual(q, before)


if __name__ == "__main__":
    unittest.main()
