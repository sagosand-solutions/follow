# SPDX-License-Identifier: MIT
"""Positive license acceptance for both license values (016).

Synthetic fixtures only. The four formats are accepted by static validation for
`CC-BY-SA-4.0` and `LicenseRef-Proprietary`; the public-export and public-manifest
rejections are covered in test_export.py and test_study.py.
"""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from validator import cli
from validator import core
from validator import exam as exam_module
from validator import study
from helpers import concept_map, question

LICENSES = ("CC-BY-SA-4.0", "LicenseRef-Proprietary")
PURPOSES = ("purpose", "flow", "state", "failure", "risk", "transfer")
EXAM_CODE = ("def total(items):\n    result = 0\n    for item in items:\n        result += item\n"
             "    return result\nprint(total([1, 2, 3]))\n")


def valid_exam(license):
    digest = hashlib.sha256(EXAM_CODE.encode("utf-8")).hexdigest()
    parts = [{
        "id": purpose, "kind": "explanation", "purpose": purpose,
        "prompt": f"Explain the {purpose} of the program.",
        "model_answer": "A short model answer with literal < and `backticks`.",
        "rubric": [{"score": 0, "criteria": "Does not address the point."},
                   {"score": 1, "criteria": "Partly addresses the point."},
                   {"score": 2, "criteria": "Fully addresses the point."}],
        "max_points": 2, "concepts": ["py.functions"],
        "evidence": [{"source_hash": digest, "snippet_id": "source", "start_line": 1, "end_line": 6}],
    } for purpose in PURPOSES]
    return {
        "schema_version": "0.1", "id": "c01-sample", "title": "Sample chapter examination",
        "chapter_id": "C01", "language": "python", "locale": "en", "version": 1, "rubric_version": 1,
        "source": {"id": "source", "code": EXAM_CODE, "sha256": digest,
                   "environment": "CPython 3.14, no stdin.",
                   "execution_policy": {"mode": "static-only"}},
        "parts": parts, "study_refs": [], "author": {"name": "Sample Author"}, "license": license,
    }


def valid_study(license):
    return {
        "schema_version": "0.1", "language": "python", "locale": "en", "concept": "py.values",
        "title": "Values", "summary": "A short summary.",
        "sections": [{"id": "s1", "heading": "Heading", "body": "Body text.", "deeper": False}],
        "sources": [{"id": "src", "title": "Source", "url": "https://example.org/x",
                     "locator": "v1, section 1", "accessed_on": "2026-10-16"}],
        "status": "draft", "author": {"name": "Author"}, "license": license,
    }


class TestLicenseAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.map = concept_map()
        self.map_path = self.root / "map.json"

        def write(path, value):
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            return path

        write(self.map_path, self.map)
        self.course_path = write(self.root / "course.json",
                                 {"levels": [{"lessons": [{"id": "first"}]}]})
        self.index_path = write(self.root / "index.json", {"questions": [{"id": "q1"}]})

    def call(self, function, *args):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = function(*args)
        return code, stream.getvalue()

    def test_question_api_and_cli_accept_both_licenses(self):
        for license in LICENSES:
            with self.subTest(license=license, surface="api"):
                item = question()
                item["license"] = license
                self.assertEqual(core.validate_question(item, self.map), [])
            with self.subTest(license=license, surface="cli"):
                item = question()
                item["license"] = license
                path = self.root / f"question-{license}.json"
                path.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
                code, output = self.call(cli.main, ["--concept-map", str(self.map_path), str(path)])
                self.assertEqual(code, 0, output)

    def test_exam_api_and_cli_accept_both_licenses(self):
        for license in LICENSES:
            with self.subTest(license=license, surface="api"):
                self.assertEqual(exam_module.validate_exam(valid_exam(license), self.map), [])
            with self.subTest(license=license, surface="cli"):
                path = self.root / f"exam-{license}.json"
                path.write_text(json.dumps(valid_exam(license), ensure_ascii=False), encoding="utf-8")
                code, output = self.call(exam_module.main, [str(path), "--map", str(self.map_path)])
                self.assertEqual(code, 0, output)

    def test_study_api_and_cli_accept_both_licenses(self):
        for license in LICENSES:
            with self.subTest(license=license, surface="api"):
                note = valid_study(license)
                issues = study.validate_study(note, concept_ids={"py.values"},
                                              lesson_ids={"first"}, question_ids={"q1"})
                self.assertEqual(issues, [])
            with self.subTest(license=license, surface="cli"):
                path = self.root / f"study-{license}.json"
                path.write_text(json.dumps(valid_study(license), ensure_ascii=False), encoding="utf-8")
                code, output = self.call(study.main, [str(path), "--map", str(self.map_path),
                                                      "--course", str(self.course_path),
                                                      "--index", str(self.index_path)])
                self.assertEqual(code, 0, output)


if __name__ == "__main__":
    unittest.main()
