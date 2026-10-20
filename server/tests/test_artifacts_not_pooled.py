# SPDX-License-Identifier: AGPL-3.0-or-later
"""A question with text artifacts (schema_version 0.3) is never scored on the server.

The scoring issue carries snippets only, so a 0.3 question reaching it would lose its artifacts silently. Both gates refuse it
by name instead: the pool builder (before anything is written) and the service's own eligibility check."""
import unittest

from server.scoring_catalog_build import CatalogBuildError, _require_standard_profile
from server.scoring_service import check_scoring_profile
from server.models import ContentError


def question(version="0.3", language="text", with_snippets=False):
    options = [{"id": f"o{i}", "text": f"Option {i}", "score": score} for i, score in enumerate([1, 0.5, 0, 0, 0])]
    q = {"schema_version": version, "answer_profile": "standard", "language": language, "id": "diff-reading-one", "options": options,
         "artifacts": [{"id": "change", "kind": "diff", "format": "unified-diff", "text": "x\n", "sha256": "0" * 64,
                        "provenance": {"fixture_id": "f", "fixture_output_sha256": "0" * 64}}]}
    if with_snippets:
        q["snippets"] = [{"id": "main", "code": "print(1)"}]
    return q


class ArtifactQuestionsAreRefused(unittest.TestCase):
    def test_the_service_refuses_text_only_and_mixed_artifact_questions(self):
        for q in (question(), question(language="python", with_snippets=True)):
            with self.subTest(language=q["language"]), self.assertRaises(ContentError):
                check_scoring_profile(q)

    def test_the_pool_builder_refuses_them_before_anything_is_written(self):
        for q in (question(), question(language="python", with_snippets=True)):
            with self.subTest(language=q["language"]), self.assertRaises(CatalogBuildError) as caught:
                _require_standard_profile(q["id"], q)
            self.assertIn("not eligible", str(caught.exception))

    def test_the_same_question_without_artifacts_on_an_old_version_is_still_eligible(self):
        legacy = question("0.2", "python", with_snippets=True)
        del legacy["artifacts"]
        check_scoring_profile(legacy)
        _require_standard_profile(legacy["id"], legacy)


if __name__ == "__main__":
    unittest.main()
