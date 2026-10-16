# SPDX-License-Identifier: MIT
"""Exercise real contract files; optional external checkout during development."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from validator import core, load_json, validate_map, validate_question, TrustedLocalRunner
from validator.cli import main
from validator.runner import verify_question

REPOSITORY = Path(os.environ.get("VALIDATOR_INTEGRATION_ROOT", core.ROOT))
READY = (REPOSITORY / "schema/question.schema.json").is_file() and (REPOSITORY / "content/examples/independent-if.json").is_file()


@unittest.skipUnless(READY, "Contract schema/example checkout not present")
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(core, "SCHEMA_DIR", REPOSITORY / "schema")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.map = load_json(REPOSITORY / "schema/concepts/python-v0.json")
        self.q = load_json(REPOSITORY / "content/examples/independent-if.json")

    def test_real_map_and_question_static(self):
        with patch("subprocess.Popen", side_effect=AssertionError("static execution")):
            self.assertEqual(validate_map(self.map), [])
            self.assertEqual(validate_question(self.q, self.map), [])
        self.assertEqual(len(self.map["concepts"]), 20)
        self.assertGreaterEqual(len(self.map["misconceptions"]), 20)

    def test_prod_prefix_map_and_question_validate_py_data_unchanged(self):
        concept_map = deepcopy(self.map)
        concept_map["concepts"].append({"id": "prod.reading-queries", "title": "Reading queries", "level": "beginner", "prerequisites": []})
        question = deepcopy(self.q)
        question["concepts"] = [*self.q["concepts"], "prod.reading-queries"]  # misconceptions still relate to its py. tags
        self.assertEqual(validate_map(concept_map), [])
        self.assertEqual(validate_question(question, concept_map), [])
        # The same question against a map without the prod concept is a reference error, not a schema pass.
        self.assertTrue(validate_question(question, self.map))
        # Other prefixes stay invalid in the map and the question.
        for prefix in ("sql", "prodx"):
            bad_map = deepcopy(concept_map)
            bad_map["concepts"][-1]["id"] = f"{prefix}.reading-queries"
            bad_question = deepcopy(question)
            bad_question["concepts"] = [*self.q["concepts"], f"{prefix}.reading-queries"]
            with self.subTest(prefix=prefix):
                self.assertTrue(validate_map(bad_map))
                self.assertTrue(validate_question(bad_question, bad_map))
        # py.-only data is untouched.
        self.assertEqual(validate_map(self.map), [])
        self.assertEqual(validate_question(self.q, self.map), [])

    def test_real_schema_rejections(self):
        for field, value in (("schema_version", "bad"), ("locale", "sv"), ("difficulty", True),
                             ("license", "MIT"), ("options", self.q["options"][:4])):
            q = deepcopy(self.q)
            q[field] = value
            with self.subTest(field=field):
                self.assertTrue(validate_question(q, self.map))
        q = deepcopy(self.q)
        q["options"][0]["score"] = True
        self.assertTrue(validate_question(q, self.map))

    def test_default_paths_survive_wrong_cwd(self):
        # ROOT normally denotes this package's repository. Substitute the integration
        # checkout only when those data files have not yet joined this branch.
        previous = os.getcwd()
        stdout = io.StringIO()
        try:
            os.chdir("/tmp")
            with patch("validator.cli.ROOT", REPOSITORY), redirect_stdout(stdout):
                code = main(["--format", "json", str(REPOSITORY / "content/examples/independent-if.json")])
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["execution"], "not-requested")

    @unittest.skipUnless(os.environ.get("VALIDATOR_RUN_TRUSTED_EXAMPLE") == "1",
                         "Requires explicit acknowledgment of inspected example code")
    def test_inspected_example_execution(self):
        # Caller must inspect the current authored example before opting in.
        runner = TrustedLocalRunner(allow_trusted_code=True)
        reason = runner.unavailable_reason()
        if reason:
            self.skipTest(reason)
        issues, infrastructure = verify_question(self.q, runner)
        self.assertEqual((issues, infrastructure), ([], False))


if __name__ == "__main__":
    unittest.main()
