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


class BeginnerDataTests(CourseFixture):
    """037: catalog labelling (audience, path_step), course ids by rule, and the optional lesson fields of beginner courses."""
    B1 = "read-like-a-computer"

    def add_beginner(self, **entry):
        """The beginner course: its own copy of the shared Python vocabulary, two questions in one lesson."""
        self.copy(core.ROOT / "schema/concepts/python-v0.json", self.root / "schema/concepts" / f"{self.B1}-v0.json")
        self.maps[self.B1] = self.maps["python"]
        self.add_course(self.B1, [("b1-first", "py.values", "beginner"), ("b1-second", "py.values", "beginner")])
        course = self.courses[self.B1]
        course["levels"][0]["lessons"] = [{"id": "b1-l1", "title": "Lesson one", "builds_on": [], "questions": ["b1-first", "b1-second"]}]
        self.write(self.data / "courses" / f"{self.B1}.json", course)
        row = next(c for c in self.catalog["courses"] if c["id"] == self.B1)
        row.update(audience="beginner", path_step=1, **entry)
        row.pop("premium", None)
        self.write(self.data / "catalog.json", self.catalog)

    def lesson(self, **fields):
        course = self.courses[self.B1]
        course["levels"][0]["lessons"][0].update(fields)
        self.write(self.data / "courses" / f"{self.B1}.json", course)

    def refused(self, needle):
        code, output = self.run_export()
        self.assertEqual(code, 1, output)
        self.assertIn(needle, output)

    def intro(self, **fields):
        return {"id": "w1", "before_question_ids": ["b1-first"], "term": "print", "explanation": "Shows a value on the screen.", "example": "print(1)", **fields}

    def test_a_beginner_course_with_labelling_and_lesson_fields_exports(self):
        self.add_beginner()
        self.lesson(practice_summary="reading what `print` shows", introductions=[self.intro(before_question_ids=["b1-first", "b1-second"])])
        code, output = self.run_export()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.run_export("--check")[0], 0)
        self.assertTrue((self.data / f"index-{self.B1}.json").exists())

    def test_labelling_changes_no_derived_file(self):
        self.assertEqual(self.run_export()[0], 0)
        before = {p.name: p.read_bytes() for p in self.data.glob("*.json") if p.name != "catalog.json"}
        self.catalog["courses"][0].update(path_step=3)
        for row in self.catalog["courses"][1:]:
            row["audience"] = "experienced"
        self.write(self.data / "catalog.json", self.catalog)
        self.assertEqual(self.run_export()[0], 0)
        self.assertEqual({p.name: p.read_bytes() for p in self.data.glob("*.json") if p.name != "catalog.json"}, before)

    def test_audience_and_path_step_values_are_closed(self):
        for field, value in (("audience", "advanced"), ("audience", "Beginner"), ("audience", None), ("audience", True),
                             ("path_step", 0), ("path_step", 4), ("path_step", -1), ("path_step", True), ("path_step", "1"), ("path_step", 1.5), ("path_step", None)):
            with self.subTest(field=field, value=value):
                self.setUp()
                self.catalog["courses"][0][field] = value
                self.write(self.data / "catalog.json", self.catalog)
                self.refused(f"/courses/0/{field}")

    def test_a_path_step_belongs_to_one_course(self):
        self.catalog["courses"][0]["path_step"] = 3
        self.catalog["courses"][1]["path_step"] = 3
        self.write(self.data / "catalog.json", self.catalog)
        self.refused("duplicate-path-step")

    def test_course_ids_follow_a_rule_not_a_list(self):
        self.add_beginner()
        self.assertEqual(self.run_export()[0], 0)
        for bad in ("Read", "a--b", "-a", "a-", "a_b", "a b", "../x", "x" * 41, ""):
            with self.subTest(bad):
                self.setUp()
                self.catalog["courses"][1]["id"] = bad
                self.write(self.data / "catalog.json", self.catalog)
                code, output = self.run_export()
                self.assertEqual(code, 1, output)
                self.assertIn("/courses/1/id", output)

    def test_the_catalog_has_room_for_more_courses_but_not_unlimited(self):
        spare = {k: v for k, v in self.catalog["courses"][1].items()}
        self.catalog["courses"] += [{**spare, "id": f"extra-{i}"} for i in range(5)]
        self.write(self.data / "catalog.json", self.catalog)
        code, output = self.run_export()
        self.assertNotIn("/courses:", output)
        self.catalog["courses"] += [{**spare, "id": f"more-{i}"} for i in range(3)]
        self.write(self.data / "catalog.json", self.catalog)
        self.refused("/courses")

    def test_premium_is_optional_for_a_free_beginner_course_but_never_a_promise(self):
        self.add_beginner()
        self.assertEqual(self.run_export()[0], 0)
        row = next(c for c in self.catalog["courses"] if c["id"] == self.B1)
        row["premium"] = {"status": "available", "questions": 3}
        self.write(self.data / "catalog.json", self.catalog)
        self.refused("premium/status")

    def test_a_beginner_course_starts_with_a_beginner_question_and_a_start_question(self):
        self.add_beginner()
        self.add_course(self.B1, [("b1-first", "py.values", "intermediate")])
        row = next(c for c in self.catalog["courses"] if c["id"] == self.B1)
        row.update(audience="beginner", path_step=1)
        self.write(self.data / "catalog.json", self.catalog)
        self.refused("onboarding-level")
        self.setUp()
        self.add_beginner()
        row = next(c for c in self.catalog["courses"] if c["id"] == self.B1)
        del row["start_question"]
        self.write(self.data / "catalog.json", self.catalog)
        self.refused("start_question")

    def test_an_unlisted_course_may_use_only_the_shared_python_vocabulary(self):
        self.add_beginner()
        sql = self.maps["sql"]
        self.write(self.root / "schema/concepts" / f"{self.B1}-v0.json", sql)
        self.maps[self.B1] = sql
        self.add_course(self.B1, [("b1-first", "sql.querying", "beginner")])
        row = next(c for c in self.catalog["courses"] if c["id"] == self.B1)
        row.update(audience="beginner", path_step=1)
        self.write(self.data / "catalog.json", self.catalog)
        self.refused("course-prefix")

    def test_practice_summary_is_short_plain_text(self):
        for bad in ("", "x" * 81, " lead", "trail ", "two\nlines", "tab\there", "\ufefflead", "trail\u00a0", 5, None, ["x"], "\u2028x"):
            with self.subTest(bad):
                self.setUp()
                self.add_beginner()
                self.lesson(practice_summary=bad)
                code, output = self.run_export()
                self.assertEqual(code, 1, output)
                self.assertIn("practice_summary", output)
        self.setUp()
        self.add_beginner()
        self.lesson(practice_summary="x" * 80)
        self.assertEqual(self.run_export()[0], 0)

    def test_lengths_count_the_way_the_client_counts_them(self):
        # JavaScript counts UTF-16 units: an astral character is two. 41 of them are 82 units, over the 80 the client allows.
        self.add_beginner()
        self.lesson(practice_summary="\U0001F600" * 41)
        self.refused("practice_summary")
        self.lesson(practice_summary="\U0001F600" * 40)
        self.assertEqual(self.run_export()[0], 0)
        self.lesson(introductions=[self.intro(term="\U0001F600" * 31)])
        self.refused("term")
        # One unit over each limit, with few enough characters that the schema's own count lets it through.
        edge = "\U0001F600"
        for name, limit in (("practice_summary", 80), ("term", 60), ("explanation", 240), ("example", 240)):
            with self.subTest(name):
                over = edge * (limit // 2) + "a"   # limit + 1 units, fewer than limit code points
                fits = edge * (limit // 2)                # exactly limit units
                for value, ok in ((fits, True), (over, False)):
                    if name == "practice_summary":
                        self.lesson(practice_summary=value, introductions=[self.intro()])
                    else:
                        self.lesson(practice_summary="fine", introductions=[self.intro(**{name: value})])
                    code, output = self.run_export()
                    self.assertEqual(code, 0 if ok else 1, f"{name} {len(value.encode('utf-16-le')) // 2} units: {output}")
                    if not ok:
                        self.assertIn("lesson-text", output)

    def test_an_introduction_has_exactly_its_keys(self):
        for label, mutate in {
            "old single-question key": lambda i: (i.pop("before_question_ids"), i.update(before_question_id="b1-first")),
            "extra key": lambda i: i.update(note="x"),
            "missing term": lambda i: i.pop("term"),
            "missing example": lambda i: i.pop("example"),
            "missing id": lambda i: i.pop("id"),
        }.items():
            with self.subTest(label):
                self.setUp()
                self.add_beginner()
                item = self.intro()
                mutate(item)
                self.lesson(introductions=[item])
                self.refused("introductions")

    def test_introduction_values_are_closed(self):
        cases = {
            "id with a space": {"id": "w 1"}, "id too long": {"id": "a" * 65}, "id leading dot": {"id": ".a"}, "id not a string": {"id": 1},
            "empty list of questions": {"before_question_ids": []}, "question list not a list": {"before_question_ids": "b1-first"},
            "question repeated in the list": {"before_question_ids": ["b1-first", "b1-first"]},
            "term too long": {"term": "x" * 61}, "explanation too long": {"explanation": "x" * 241}, "example too long": {"example": "x" * 241},
            "blank term": {"term": ""}, "edge space": {"explanation": " padded"}, "control character": {"example": "a\u0007b"}, "line break": {"explanation": "a\nb"},
            "not text": {"term": ["print"]},
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.setUp()
                self.add_beginner()
                self.lesson(introductions=[self.intro(**change)])
                code, output = self.run_export()
                self.assertEqual(code, 1, output)
                self.assertIn("introductions", output)
        self.setUp()
        self.add_beginner()
        self.lesson(introductions=[self.intro(term="x" * 60, explanation="y" * 240, example="z" * 240)])
        self.assertEqual(self.run_export()[0], 0)

    def test_an_introduction_belongs_to_questions_of_its_own_lesson(self):
        self.add_beginner()
        self.lesson(introductions=[self.intro(before_question_ids=["b1-first", "py-first"])])
        self.refused("introduction-question")
        self.lesson(introductions=[self.intro(before_question_ids=["b1-nowhere"])])
        self.refused("introduction-question")

    def test_an_introduction_id_is_used_once_in_a_lesson(self):
        self.add_beginner()
        self.lesson(introductions=[self.intro(), self.intro(term="other")])
        self.refused("duplicate-introduction")
        self.lesson(introductions=[self.intro(), self.intro(id="w2", term="other")])
        self.assertEqual(self.run_export()[0], 0)

    def test_introductions_list_is_bounded(self):
        self.add_beginner()
        self.lesson(introductions=[self.intro(id=f"w{i}") for i in range(9)])
        self.refused("introductions")
        self.lesson(introductions=[])
        self.refused("introductions")

    def test_lesson_fields_are_text_only_markup_is_data_not_html(self):
        # The client renders these as text; the data layer keeps them verbatim and does not strip or rewrite them.
        self.add_beginner()
        self.lesson(practice_summary="reading <b>bold</b> & `code`")
        self.assertEqual(self.run_export()[0], 0)
        self.assertIn("<b>bold</b>", (self.data / "courses" / f"{self.B1}.json").read_text(encoding="utf-8"))


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

    def test_the_real_catalog_is_labelled_the_way_the_architect_decided(self):
        catalog = json.loads((core.ROOT / "public/data/catalog.json").read_text(encoding="utf-8"))
        rows = {c["id"]: c for c in catalog["courses"]}
        self.assertEqual((rows["python"].get("audience"), rows["python"].get("path_step")), (None, 3), "python: on the path, no audience pill")
        for cid in ("production", "sql"):
            self.assertEqual((rows[cid].get("audience"), rows[cid].get("path_step")), ("experienced", None), cid)
        self.assertNotIn("read-like-a-computer", rows, "the beginner course is not published before the B1 release is decided")

    def test_the_real_catalog_and_export_are_consistent(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = exporter.main(["--check"])
        self.assertEqual(code, 0, stream.getvalue())


if __name__ == "__main__":
    unittest.main()
