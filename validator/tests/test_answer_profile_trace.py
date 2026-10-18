# SPDX-License-Identifier: MIT
"""A 0.2 question (either profile) is traced and checked exactly like a legacy one: each snippet's trace stays bound to its
source hash, case id and stdin, and a changed snippet, case or stdin fails the check. The profile plays no part in tracing."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from validator import core
from validator import trace
from test_trace import LocalTestRunner
from helpers import question


def with_profile(profile):
    q = question()
    q.update(id="profile-question", schema_version="0.2", answer_profile=profile)
    if profile == "beginner-practice":
        q["options"] = [dict(o, id=f"o{i}") for i, o in enumerate(q["options"][:3] + q["options"][3:4])]
    q["snippets"] = [{"id": "main", "code": "print(3)"}, {"id": "second", "code": "print(4)"}]
    q["verification"]["cases"] = [{"id": "one", "snippet_id": "main", "stdin": "", "expected": {"stdout": "3\n", "exit_code": 0}},
                                  {"id": "two", "snippet_id": "second", "stdin": "", "expected": {"stdout": "4\n", "exit_code": 0}}]
    return q


class AnswerProfileTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "schema").mkdir()
        shutil.copyfile(core.ROOT / "schema/trace.schema.json", self.root / "schema/trace.schema.json")
        (self.root / "public/data/questions").mkdir(parents=True)
        self.qpath = self.root / "public/data/questions/profile-question.json"
        self.output = self.root / "public/data/traces/profile-question.json"
        self.output.parent.mkdir(parents=True)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_every_snippet_trace_is_bound_to_its_source_hash_in_both_profiles(self):
        for profile in ("standard", "beginner-practice"):
            with self.subTest(profile):
                q = with_profile(profile)
                generated = trace._trace_question(q, self.qpath, LocalTestRunner())
                self.assertEqual([t["snippet_id"] for t in generated["traces"]], ["main", "second"])
                for record, snippet in zip(generated["traces"], q["snippets"]):
                    self.assertEqual(record["source_sha256"], hashlib.sha256(snippet["code"].encode("utf-8")).hexdigest())

    def test_check_passes_and_then_detects_each_change_for_a_practice_question(self):
        q = with_profile("beginner-practice")
        self.write(self.qpath, q)
        self.write(self.output, trace._trace_question(q, self.qpath, LocalTestRunner()))
        self.assertEqual(trace._check(self.root, [self.qpath]), 0)
        for name, change in (("changed source", lambda d: d["snippets"][1].update(code="print(5)")),
                             ("renamed case", lambda d: d["verification"]["cases"][1].update(id="renamed")),
                             ("changed stdin", lambda d: d["verification"]["cases"][0].update(stdin="x"))):
            with self.subTest(name):
                changed = json.loads(json.dumps(q))
                change(changed)
                self.write(self.qpath, changed)
                self.assertEqual(trace._check(self.root, [self.qpath]), 1, "the trace is no longer bound to the question")
                self.write(self.qpath, q)
        self.assertEqual(trace._check(self.root, [self.qpath]), 0)


if __name__ == "__main__":
    unittest.main()
