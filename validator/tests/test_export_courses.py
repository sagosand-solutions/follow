# SPDX-License-Identifier: MIT
"""Multi-course export: catalog, per-course vocabularies, prefix rules, derived files, Python byte-identity.

Fixtures are built from the REAL vocabularies (schema/concepts/*.json) and synthetic question bodies."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from validator import core
from validator import export as exporter
from validator.cli import main as cli_main
from helpers import question

SCHEMAS = ("question.schema.json", "concept-map.schema.json", "course.schema.json", "catalog.schema.json")


def make_question(map_data, qid, concept, level="beginner"):
    """A schema-valid synthetic question tagged with `concept`, its wrong options using that vocabulary's misconceptions."""
    related = [m["id"] for m in map_data["misconceptions"] if concept in m["concepts"]]
    assert len(related) >= 4, concept
    q = question()
    q.update(id=qid, level=level, concepts=[concept])
    wrong = iter(related)
    for option in q["options"]:
        option["misconception"] = None if option["score"] == 1 else next(wrong)
    return q


class CourseFixture(unittest.TestCase):
    PICK = {"python": ("py.values", "py-first", "beginner"), "production": ("prod.timeouts-retries", "prod-first", "intermediate"), "sql": ("sql.querying", "sql-first", "beginner")}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in SCHEMAS:
            self.copy(core.ROOT / "schema" / name, self.root / "schema" / name)
        self.maps = {}
        for cid in ("python", "production", "sql"):
            src = core.ROOT / "schema/concepts" / f"{cid}-v0.json"
            self.copy(src, self.root / "schema/concepts" / src.name)
            self.maps[cid] = json.loads(src.read_text(encoding="utf-8"))
        self.data = self.root / "public/data"
        (self.data / "questions").mkdir(parents=True)
        self.catalog = {"data_version": 1, "courses": []}
        self.courses = {}
        for cid, (concept, qid, level) in self.PICK.items():
            self.add_course(cid, [(qid, concept, level)])

    @staticmethod
    def copy(src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def add_course(self, cid, items):
        """items: [(question id, concept, level)] one lesson each, ascending levels."""
        levels, previous = {}, None
        for qid, concept, level in items:
            self.write(self.data / "questions" / f"{qid}.json", make_question(self.maps[cid], qid, concept, level))
            lesson = {"id": f"l-{qid}", "title": f"Lesson {qid}", "builds_on": [previous] if previous else [], "questions": [qid]}
            levels.setdefault(level, []).append(lesson)
            previous = lesson["id"]
        order = [name for name in ("beginner", "intermediate", "advanced") if name in levels]
        course = {"id": cid, "title": f"{cid} course", "language": "python", "levels": [{"level": n, "title": n.title(), "lessons": levels[n]} for n in order]}
        self.courses[cid] = course
        self.write(self.data / "courses" / f"{cid}.json", course)
        entry = {"id": cid, "title": f"{cid} course", "description": f"About {cid}.", "language": "python", "free_questions": len(items),
                 "premium": {"status": "coming-soon", "questions": 7}, "start_question": items[0][0], "concept_map": f"schema/concepts/{cid}-v0.json"}
        self.catalog["courses"] = [c for c in self.catalog["courses"] if c["id"] != cid] + [entry]
        self.write(self.data / "catalog.json", self.catalog)

    def run_export(self, *args):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = exporter.main(["--root", str(self.root), *args])
        return code, stream.getvalue()

    def sha(self, name):
        return hashlib.sha256((self.data / name).read_bytes()).hexdigest()


class MultiCourseExportTests(CourseFixture):
    def test_three_courses_write_and_check_all_derived_files(self):
        code, output = self.run_export()
        self.assertEqual(code, 0, output)
        names = {p.name for p in self.data.glob("*.json")}
        self.assertTrue({"index.json", "concepts.json", "index-production.json", "concepts-production.json", "index-sql.json", "concepts-sql.json", "catalog.json"} <= names)
        self.assertEqual(self.run_export("--check")[0], 0)
        index = json.loads((self.data / "index-production.json").read_text(encoding="utf-8"))
        self.assertEqual(index["onboarding_question"], "prod-first")
        self.assertEqual([q["id"] for q in index["questions"]], ["prod-first"])
        self.assertEqual(json.loads((self.data / "concepts-sql.json").read_text(encoding="utf-8")), self.maps["sql"])
        # each course's index lists only its own questions
        self.assertEqual([q["id"] for q in json.loads((self.data / "index.json").read_text(encoding="utf-8"))["questions"]], ["py-first"])

    def test_python_files_are_byte_identical_with_or_without_the_other_courses(self):
        # python only, legacy mode (no catalog at all)
        (self.data / "catalog.json").unlink()
        for cid in ("production", "sql"):
            (self.data / "courses" / f"{cid}.json").unlink()
            (self.data / "questions" / f"{self.PICK[cid][1]}.json").unlink()
        self.assertEqual(self.run_export()[0], 0)
        legacy = (self.sha("index.json"), self.sha("concepts.json"))
        self.assertFalse((self.data / "index-sql.json").exists(), "legacy mode derives python only")
        # now the full three-course catalog: python's bytes must not move
        self.setUp()
        self.assertEqual(self.run_export()[0], 0)
        self.assertEqual((self.sha("index.json"), self.sha("concepts.json")), legacy)

    def test_check_detects_a_missing_or_stale_derived_file_of_any_course(self):
        self.run_export()
        (self.data / "index-sql.json").unlink()
        code, output = self.run_export("--check")
        self.assertEqual(code, 1)
        self.assertIn("index-sql.json", output)
        self.run_export()
        (self.data / "concepts-production.json").write_text("{}\n", encoding="utf-8")
        code, output = self.run_export("--check")
        self.assertEqual(code, 1)
        self.assertIn("concepts-production.json: stale-export", output)

    def test_onboarding_must_be_beginner_only_for_python(self):
        self.assertEqual(self.run_export()[0], 0, "production starts at intermediate")
        self.add_course("python", [("py-first", "py.values", "intermediate")])
        code, output = self.run_export()
        self.assertEqual(code, 1)
        self.assertIn("onboarding-level", output)

    def test_a_question_is_validated_against_its_own_course_vocabulary(self):
        # A sql question may not use a production concept, and a python question may not use a sql one.
        bad = make_question(self.maps["production"], "sql-first", "prod.timeouts-retries", "beginner")
        self.write(self.data / "questions/sql-first.json", bad)
        code, output = self.run_export()
        self.assertEqual(code, 1)
        self.assertTrue("course-prefix" in output or "unknown" in output.lower(), output)

    def test_prefix_rules_per_course(self):
        for cid, ok in (("python", {"py"}), ("production", {"prod", "py"}), ("sql", {"sql", "py"})):
            self.assertEqual(set(exporter.COURSE_PREFIXES[cid]), ok)
        q = {"concepts": ["sql.querying", "py.values"], "options": [{"misconception": "prod.x"}, {"misconception": None}]}
        found = exporter.prefix_issues("sql", question=q)
        self.assertEqual([i.path for i in found], ["/options/0/misconception"])
        self.assertEqual(exporter.prefix_issues("python", question={"concepts": ["py.a"], "options": []}), [])
        self.assertTrue(exporter.prefix_issues("python", map_data={"concepts": [{"id": "prod.x", "prerequisites": []}], "misconceptions": []}))
        self.assertEqual(exporter.prefix_issues("production", map_data=self.maps["production"]), [])
        self.assertEqual(exporter.prefix_issues("sql", map_data=self.maps["sql"]), [])
        self.assertEqual(exporter.prefix_issues("python", map_data=self.maps["python"]), [])

    def test_a_question_cannot_belong_to_two_courses(self):
        extra = {"id": "l-dup", "title": "Dup", "builds_on": [], "questions": ["py-first"]}
        production = deepcopy(self.courses["production"])
        production["levels"][0]["lessons"].append(extra)
        self.write(self.data / "courses/production.json", production)
        code, output = self.run_export()
        self.assertEqual(code, 1)
        self.assertIn("question-in-two-courses", output)

    def test_lesson_ids_are_unique_across_courses(self):
        production = deepcopy(self.courses["production"])
        production["levels"][0]["lessons"][0]["id"] = self.courses["sql"]["levels"][0]["lessons"][0]["id"]
        self.write(self.data / "courses/production.json", production)
        code, output = self.run_export()
        self.assertEqual(code, 1)
        self.assertIn("lesson-in-two-courses", output)
        self.assertIn("lesson ids must be unique across courses", output)
        # the same id inside ONE course is still the course's own duplicate-lesson error, not this one
        self.setUp()
        python = deepcopy(self.courses["python"])
        python["levels"][0]["lessons"].append(deepcopy(python["levels"][0]["lessons"][0]))
        self.write(self.data / "courses/python.json", python)
        self.assertNotIn("lesson-in-two-courses", self.run_export()[1])

    def test_a_question_in_no_course_is_an_orphan(self):
        self.write(self.data / "questions/py-orphan.json", make_question(self.maps["python"], "py-orphan", "py.values"))
        code, output = self.run_export()
        self.assertEqual(code, 1)
        self.assertIn("orphan-question", output)

    def test_a_course_without_a_vocabulary_or_course_file_is_an_error(self):
        (self.root / "schema/concepts/sql-v0.json").unlink()
        code, output = self.run_export()
        self.assertNotEqual(code, 0, output)
        self.setUp()
        (self.data / "courses/sql.json").unlink()
        code, output = self.run_export()
        self.assertNotEqual(code, 0, output)

    def test_catalog_must_match_the_data(self):
        self.catalog["courses"][1]["free_questions"] = 9
        self.write(self.data / "catalog.json", self.catalog)
        code, output = self.run_export()
        self.assertEqual(code, 1)
        self.assertIn("catalog-count", output)
        self.setUp()
        self.catalog["courses"][1]["start_question"] = "py-first"
        self.write(self.data / "catalog.json", self.catalog)
        self.assertIn("catalog-start", self.run_export()[1])

    def test_catalog_shape_rules(self):
        cases = {
            "duplicate-course": lambda c: c["courses"].append(deepcopy(c["courses"][0])),
            "catalog-map": lambda c: c["courses"][0].update(concept_map="schema/concepts/sql-v0.json"),
            "catalog-python": lambda c: c["courses"].__delitem__(0),
        }
        for code_word, mutate in cases.items():
            with self.subTest(code_word):
                self.setUp()
                catalog = deepcopy(self.catalog)
                if code_word == "catalog-python":
                    catalog["courses"] = [c for c in catalog["courses"] if c["id"] != "python"]
                else:
                    mutate(catalog)
                self.write(self.data / "catalog.json", catalog)
                code, output = self.run_export()
                self.assertEqual(code, 1, output)
                self.assertIn(code_word, output)

    def test_course_file_id_must_match_its_catalog_entry(self):
        course = deepcopy(self.courses["sql"])
        course["id"] = "production"
        self.write(self.data / "courses/sql.json", course)
        code, output = self.run_export()
        self.assertEqual(code, 1)
        self.assertIn("course-id", output)

    def test_only_public_licence_is_exported_for_every_course(self):
        q = json.loads((self.data / "questions/prod-first.json").read_text(encoding="utf-8"))
        q["license"] = "LicenseRef-Proprietary"
        self.write(self.data / "questions/prod-first.json", q)
        code, output = self.run_export()
        self.assertEqual(code, 1)
        self.assertIn("non-public-license", output)

    def test_export_never_executes_code(self):
        from unittest.mock import patch
        with patch("subprocess.Popen", side_effect=AssertionError("export must stay static")):
            self.assertEqual(self.run_export()[0], 0)


class ExportIsTheGateTests(CourseFixture):
    """CI relies on export --check instead of the generic CLI line: it must catch everything that line caught."""

    def cli_ok(self, course, qid):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = cli_main(["--concept-map", str(self.root / f"schema/concepts/{course}-v0.json"), str(self.data / "questions" / f"{qid}.json")])
        return code == 0

    def test_valid_data_passes_both_and_each_defect_the_cli_finds_fails_the_export(self):
        self.assertEqual(self.run_export()[0], 0)
        for course, (_, qid, _) in self.PICK.items():
            self.assertTrue(self.cli_ok(course, qid), course)
        defects = {
            "missing required field": lambda q: q.pop("explanation"),
            "unknown concept": lambda q: q.update(concepts=["py.no-such-concept"]),
            "misconception not related to the tagged concept": lambda q, m=None: q["options"][2].update(misconception=m),
            "score shape": lambda q: q["options"][0].update(score=0.7),
            "extra field": lambda q: q.update(unexpected=True),
        }
        for course, (_, qid, _) in self.PICK.items():
            original = json.loads((self.data / "questions" / f"{qid}.json").read_text(encoding="utf-8"))
            for name, mutate in defects.items():
                with self.subTest(course=course, defect=name):
                    broken = deepcopy(original)
                    if name.startswith("misconception"):
                        unrelated = next(m["id"] for m in self.maps[course]["misconceptions"] if original["concepts"][0] not in m["concepts"])
                        mutate(broken, unrelated)
                    else:
                        mutate(broken)
                    self.write(self.data / "questions" / f"{qid}.json", broken)
                    self.assertFalse(self.cli_ok(course, qid), "the CLI rejects it")
                    self.assertEqual(self.run_export()[0], 1, "so does the export")
                    self.write(self.data / "questions" / f"{qid}.json", original)

    def test_export_adds_what_the_cli_cannot_see(self):
        qid = self.PICK["sql"][1]
        original = json.loads((self.data / "questions" / f"{qid}.json").read_text(encoding="utf-8"))
        wrong_file = self.data / "questions" / "renamed.json"
        (self.data / "questions" / f"{qid}.json").rename(wrong_file)
        self.assertTrue(self.cli_ok("sql", "renamed"), "the CLI is happy with any file name")
        code, output = self.run_export()
        self.assertEqual(code, 1)
        self.assertIn("filename", output)
        wrong_file.rename(self.data / "questions" / f"{qid}.json")
        original["license"] = "LicenseRef-Proprietary"
        self.write(self.data / "questions" / f"{qid}.json", original)
        self.assertTrue(self.cli_ok("sql", qid), "the CLI does not look at the licence")
        self.assertIn("non-public-license", self.run_export()[1])

    def test_the_workflow_runs_the_export_check_and_not_the_single_vocabulary_line(self):
        workflow = (core.ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("python -m validator export --check", workflow)
        self.assertNotIn("python -m validator public/data/questions", workflow)
        readme = (core.ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("--concept-map schema/concepts/sql-v0.json", readme, "author examples per course are documented")


class RealRepositoryTests(unittest.TestCase):
    def test_the_real_maps_are_closed_and_share_python_entries_verbatim(self):
        python = json.loads((core.ROOT / "schema/concepts/python-v0.json").read_text(encoding="utf-8"))
        py_c = {c["id"]: c for c in python["concepts"]}
        py_m = {m["id"]: m for m in python["misconceptions"]}
        for cid, counts in (("production", (13, 19)), ("sql", (11, 20))):
            data = json.loads((core.ROOT / f"schema/concepts/{cid}-v0.json").read_text(encoding="utf-8"))
            self.assertEqual((len(data["concepts"]), len(data["misconceptions"])), counts, cid)
            ids = {c["id"] for c in data["concepts"]}
            for c in data["concepts"]:
                self.assertTrue(set(c["prerequisites"]) <= ids, f"{cid}: {c['id']} prerequisites are not in the map")
                if c["id"].startswith("py."):
                    self.assertEqual(py_c.get(c["id"]), c, f"{cid}: {c['id']} differs from python-v0")
            for m in data["misconceptions"]:
                self.assertTrue(set(m["concepts"]) <= ids, f"{cid}: misconception {m['id']} refers outside the map")
                if m["id"].startswith("py."):
                    self.assertEqual(py_m.get(m["id"]), m)
            self.assertEqual(core.validate_map(data), [])
            self.assertEqual(exporter.prefix_issues(cid, map_data=data), [])

    def test_the_real_catalog_and_export_are_consistent(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = exporter.main(["--check"])
        self.assertEqual(code, 0, stream.getvalue())


if __name__ == "__main__":
    unittest.main()
