# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scoring-catalog builder refuses every question that is not a standard-profile question, before anything is written.

All content is synthetic. A rejected build never touches an existing artifact; a valid legacy or 0.2 ``standard`` question
builds exactly as before; a practice question is refused even when it is otherwise valid and even through the CLI.
"""
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from server import scoring_catalog_build as builder
from server.scoring_catalog_build import CatalogBuildError
from server.tests.test_scoring_catalog_build import BuilderFixture, make_options, make_question


def practice_options():
    options = make_options(4)                         # full, half, zero, zero
    return options


class ProfileBuildTests(BuilderFixture):
    def build_with(self, data, *, family="official"):
        license = "LicenseRef-Proprietary" if family == "official" else "CC-BY-SA-4.0"
        data = dict(data, license=license)
        self.entries = []                                  # every case is a fresh manifest
        digest = self.add_question("py-alpha", data=data)
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]}, family=family)
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        return release, exposures

    def refused(self, data, *, family="official"):
        release, exposures = self.build_with(data, family=family)
        with self.assertRaises(CatalogBuildError) as caught:
            self.build(release, exposures)
        return str(caught.exception)

    def test_a_valid_beginner_practice_question_is_refused_with_a_clear_error(self):
        data = make_question("py-alpha", schema_version="0.2", answer_profile="beginner-practice")
        data["options"] = practice_options()
        for family in ("official", "free"):
            with self.subTest(family):
                message = self.refused(data, family=family)
                self.assertIn("not eligible for a scoring pool", message)
                self.assertIn("practice questions are never pooled", message)
                self.assertIn("beginner-practice", message)

    def test_every_other_ineligible_combination_is_refused(self):
        def doc(**over):
            data = make_question("py-alpha", **over)
            return data
        four = make_question("py-alpha")
        four["options"] = practice_options()
        cases = {
            "legacy with four options": four,
            "profile on the legacy version (standard)": doc(answer_profile="standard"),
            "profile on the legacy version (practice)": doc(answer_profile="beginner-practice"),
            "missing profile in 0.2": doc(schema_version="0.2"),
            "unknown profile": doc(schema_version="0.2", answer_profile="premium"),
            "unknown version": doc(schema_version="0.3", answer_profile="standard"),
            "standard with four options": dict(doc(schema_version="0.2", answer_profile="standard"), options=practice_options()),
            "profile that is not a string": doc(schema_version="0.2", answer_profile=["standard"]),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertIn("not eligible for a scoring pool", self.refused(data))

    def test_nothing_is_inferred_from_the_other_fields(self):
        data = make_question("py-alpha", schema_version="0.2", answer_profile="beginner-practice", level="beginner")
        data["options"] = practice_options()
        data["id"] = "py-alpha"
        data["concepts"] = ["py.example"]
        for change in ({"level": "advanced"}, {"license": "CC-BY-SA-4.0"}):
            with self.subTest(change=change):
                self.assertIn("not eligible", self.refused(dict(data, **change)))

    def test_the_standard_questions_still_build_and_the_artifact_is_unchanged_by_the_profile(self):
        legacy = make_question("py-alpha")
        release, exposures = self.build_with(legacy)
        legacy_bytes = self.build(release, exposures)
        standard = dict(make_question("py-alpha"), schema_version="0.2", answer_profile="standard")
        for count in (5, 8):
            with self.subTest(count=count):
                data = dict(standard, options=make_options(count))
                self.assertTrue(self.build(*self.build_with(data)))
        decoded = json.loads(legacy_bytes)
        self.assertNotIn("answer_profile", legacy_bytes.decode("utf-8"), "the frozen artifact format carries no profile")
        self.assertEqual(decoded["courses"][0]["questions"][0]["question_id"], "py-alpha")

    def test_the_check_runs_before_and_independently_of_the_schema(self):
        # A trusted-input schema override that no longer bounds the option count cannot admit a practice question.
        loose = self.root / "loose.schema.json"
        loose.write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}), encoding="utf-8")
        data = make_question("py-alpha", schema_version="0.2", answer_profile="beginner-practice")
        data["options"] = practice_options()
        release, exposures = self.build_with(data)
        with self.assertRaises(CatalogBuildError) as caught:
            builder.build_artifact(self.content, self.freeze_manifest(), release, exposures, question_schema=loose)
        self.assertIn("not eligible for a scoring pool", str(caught.exception))

    def test_a_practice_label_alone_is_enough_to_refuse_even_with_a_standard_looking_body(self):
        # Five options with a valid standard distribution under a loose schema override: only the profile says "practice".
        loose = self.root / "loose.schema.json"
        loose.write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}), encoding="utf-8")
        data = make_question("py-alpha", schema_version="0.2", answer_profile="beginner-practice")
        release, exposures = self.build_with(data)
        with self.assertRaises(CatalogBuildError) as caught:
            builder.build_artifact(self.content, self.freeze_manifest(), release, exposures, question_schema=loose)
        self.assertIn("beginner-practice", str(caught.exception))

    def test_a_rejection_leaves_the_old_artifact_untouched_through_the_cli(self):
        digest = self.add_question("py-alpha")
        release = self.write_release({"python": [("py-alpha", "python-lifetime")]})
        exposures = self.write_exposures(self.self_and_safe({"py-alpha": digest}))
        manifest = self.freeze_manifest()
        out = self.root / "artifact.json"
        argv = ["--content-root", str(self.content), "--manifest", str(manifest), "--release", str(release),
                "--exposures", str(exposures), "--out", str(out)]
        with redirect_stdout(StringIO()):
            self.assertEqual(builder.main(argv), 0)
        before = out.read_bytes()
        practice = make_question("py-alpha", schema_version="0.2", answer_profile="beginner-practice", license="LicenseRef-Proprietary")
        practice["options"] = practice_options()
        digest2 = self.add_question("py-beta", data=dict(practice, id="py-beta"))
        release2 = self.write_release({"python": [("py-alpha", "python-lifetime"), ("py-beta", "python-lifetime")]}, catalog_version="rel-2")
        exposures2 = self.write_exposures(self.self_and_safe({"py-alpha": digest, "py-beta": digest2}))
        manifest2 = self.freeze_manifest()
        argv2 = ["--content-root", str(self.content), "--manifest", str(manifest2), "--release", str(release2),
                 "--exposures", str(exposures2), "--out", str(out)]
        err = StringIO()
        with redirect_stderr(err):
            self.assertEqual(builder.main(argv2), 2)
        self.assertIn("not eligible for a scoring pool", err.getvalue())
        self.assertEqual(out.read_bytes(), before, "the previous artifact is byte-identical")
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".") or p.suffix == ".tmp"], [], "no temporary file is left")


if __name__ == "__main__":
    unittest.main()
