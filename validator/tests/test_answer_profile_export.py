# SPDX-License-Identifier: MIT
"""The answer profile through export: the question document keeps its version and profile, the public index states the profile
explicitly for every question (index data_version 2), a legacy bank is exported as before except for that explicit field, and every
unknown or inconsistent combination blocks all writes."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from validator import core
from validator import export as exporter
from helpers import concept_map, question


def option(index, score):
    return {"id": f"o{index}", "text": f"Synthetic option {index}", "score": score, "feedback": "Synthetic feedback",
            "misconception": None if score == 1 else "py.test-error",
            **({"partial_reason": "Synthetic correct and incorrect parts"} if score == 0.5 else {})}


def practice(question_id="practice-question"):
    q = question()
    q.update(id=question_id, schema_version="0.2", answer_profile="beginner-practice", level="beginner")
    q["options"] = [option(i, s) for i, s in enumerate((1, 0.5, 0, 0))]
    return q


def standard(question_id="standard-question"):
    q = question()
    q.update(id=question_id, schema_version="0.2", answer_profile="standard", level="beginner")
    return q


class AnswerProfileExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        schema = self.root / "schema"
        schema.mkdir()
        for name in ("question.schema.json", "concept-map.schema.json", "course.schema.json"):
            shutil.copyfile(core.ROOT / "schema" / name, schema / name)
        self.data = self.root / "public/data"
        (self.data / "questions").mkdir(parents=True)
        self.map_path = schema / "concepts/python-v0.json"
        self.index = self.data / "index.json"
        self.concepts = self.data / "concepts.json"

    def build(self, questions):
        (self.data / "questions").mkdir(parents=True, exist_ok=True)
        legacy = question()
        legacy["id"] = "legacy-question"
        everything = [legacy, *questions]
        self.map_path.parent.mkdir(parents=True, exist_ok=True)
        self.map_path.write_text(json.dumps(concept_map()), encoding="utf-8")
        for q in everything:
            (self.data / "questions" / f"{q['id']}.json").write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")
        course = {"id": "python", "title": "Synthetic course", "language": "python", "levels": [
            {"level": "beginner", "title": "First", "lessons": [
                {"id": "first", "title": "First lesson", "builds_on": [], "questions": [q["id"] for q in everything]}]}]}
        (self.data / "courses").mkdir(parents=True, exist_ok=True)
        (self.data / "courses/python.json").write_text(json.dumps(course), encoding="utf-8")
        return everything

    def invoke(self, *args):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = exporter.main(["--root", str(self.root), *args])
        return code, stream.getvalue()

    def test_profiles_are_kept_in_the_documents_and_stated_explicitly_in_the_index(self):
        questions = self.build([practice(), standard()])
        code, output = self.invoke()
        self.assertEqual(code, 0, output)
        index = json.loads(self.index.read_bytes())
        self.assertEqual(index["data_version"], 2)
        self.assertEqual({q["id"]: q["answer_profile"] for q in index["questions"]},
                         {"legacy-question": "standard", "practice-question": "beginner-practice", "standard-question": "standard"},
                         "every entry states its profile; a legacy 0.1 document is standard by its contract")
        for entry in index["questions"]:
            self.assertEqual(sorted(entry), ["answer_profile", "concepts", "difficulty", "id", "level", "type"])
        for q in questions:                                           # the documents keep their exact version and profile
            stored = json.loads((self.data / "questions" / f"{q['id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, q)
        self.assertEqual(self.invoke("--check")[0], 0)

    def test_a_legacy_only_bank_exports_the_same_entries_plus_the_explicit_standard_profile(self):
        questions = self.build([])
        self.assertEqual(self.invoke()[0], 0)
        index = json.loads(self.index.read_bytes())
        expected = {field: questions[0][field] for field in ("id", "type", "level", "difficulty", "concepts")}
        self.assertEqual(index["questions"], [{**expected, "answer_profile": "standard"}])

    def test_the_index_is_deterministic_and_stale_bytes_fail_the_check(self):
        self.build([practice(), standard()])
        self.assertEqual(self.invoke()[0], 0)
        first = self.index.read_bytes()
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(first, self.index.read_bytes())
        old = json.loads(first)
        old["data_version"] = 1                                        # an index of the previous contract
        for entry in old["questions"]:
            del entry["answer_profile"]
        self.index.write_text(json.dumps(old, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(self.invoke("--check")[0], 1, "an index without the profiles is stale")

    def test_every_unknown_or_inconsistent_combination_blocks_all_writes(self):
        def mutated(base, **change):
            q = deepcopy(base)
            q.update(change)
            return q
        legacy_four = question()
        legacy_four.update(id="bad-question")
        legacy_four["options"] = [option(i, s) for i, s in enumerate((1, 0.5, 0, 0))]
        cases = {
            "legacy with four options": legacy_four,
            "profile on the legacy version": mutated(question(), id="bad-question", answer_profile="standard"),
            "practice on the legacy version": mutated(question(), id="bad-question", answer_profile="beginner-practice"),
            "missing profile in 0.2": {k: v for k, v in practice("bad-question").items() if k != "answer_profile"},
            "unknown profile": mutated(practice("bad-question"), answer_profile="premium"),
            "unknown version": mutated(practice("bad-question"), schema_version="0.3"),
            "practice with five options": mutated(practice("bad-question"), options=[option(i, s) for i, s in enumerate((1, 0.5, 0, 0, 0))]),
            "standard with four options": mutated(standard("bad-question"), options=[option(i, s) for i, s in enumerate((1, 0.5, 0, 0))]),
            "practice with a wrong distribution": mutated(practice("bad-question"), options=[option(i, s) for i, s in enumerate((1, 0.5, 0.5, 0))]),
        }
        for name, bad in cases.items():
            with self.subTest(name):
                shutil.rmtree(self.data)
                self.build([bad])
                for path in (self.index, self.concepts):
                    path.write_bytes(b"existing-output\n")
                before = {p: p.read_bytes() for p in (self.index, self.concepts)}
                code, output = self.invoke()
                self.assertEqual(code, 1, output)
                self.assertEqual(before, {p: p.read_bytes() for p in (self.index, self.concepts)}, "an invalid question writes nothing")


if __name__ == "__main__":
    unittest.main()
