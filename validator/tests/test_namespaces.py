# SPDX-License-Identifier: MIT
"""Concept namespaces (py, prod, sql, git, http, test, rev) and the course -> vocabulary binding.

A namespace is allowed in the schemas for everybody, but a COURSE may use only its own namespace plus the ones it was given
explicitly. Nothing here accepts a record, a course or a pool: it is only the rule about which ids a vocabulary may contain."""
from copy import deepcopy
import io
import json
from contextlib import redirect_stdout

from validator import export as exporter
from test_export_courses import CourseFixture, make_question

NEW = {"git-diffs": ("git", {"git", "py"}), "http-api": ("http", {"http", "py"}), "testing": ("test", {"test", "py"}),
       "review-ai-changes": ("rev", {"rev", "py", "git", "test"})}


def synthetic_map(prefixes):
    """A small closed vocabulary: one concept and five misconceptions per prefix, valid against concept-map.schema.json."""
    concepts, misconceptions = [], []
    for prefix in prefixes:
        concepts.append({"id": f"{prefix}.reading", "title": f"{prefix} reading", "level": "beginner", "prerequisites": []})
        for i in range(5):
            misconceptions.append({"id": f"{prefix}.wrong-{i}", "title": f"{prefix} wrong {i}", "description": "Synthetic error", "concepts": [f"{prefix}.reading"]})
    return {"schema_version": "0.1", "language": "python", "concepts": concepts, "misconceptions": misconceptions}


class RuleTableTests(CourseFixture):
    def test_each_new_course_has_its_own_namespace_and_the_shared_ones_it_was_given(self):
        for cid, (_own, allowed) in NEW.items():
            self.assertEqual(set(exporter.COURSE_PREFIXES[cid]), allowed, cid)
            self.assertIn(cid, exporter.OWN_MAP_COURSES, f"{cid} has a vocabulary of its own")
        for cid, ok in (("python", {"py"}), ("production", {"prod", "py"}), ("sql", {"sql", "py"})):
            self.assertEqual(set(exporter.COURSE_PREFIXES[cid]), ok, "the old courses are unchanged")
        self.assertEqual(exporter.DEFAULT_PREFIXES, ("py",), "a course that is not listed still reads only the shared Python vocabulary")

    def test_no_course_borrows_a_namespace_that_is_not_its_own_unless_it_was_listed(self):
        def issues(cid, prefix):
            return exporter.prefix_issues(cid, question={"concepts": [f"{prefix}.x"], "options": []})
        for cid, (own, allowed) in NEW.items():
            for prefix in ("py", "prod", "sql", "git", "http", "test", "rev"):
                with self.subTest(course=cid, prefix=prefix):
                    self.assertEqual(bool(issues(cid, prefix)), prefix not in allowed)
        for cid in ("python", "production", "sql", "some-unlisted-course"):
            for prefix in ("git", "http", "test", "rev"):
                with self.subTest(course=cid, prefix=prefix):
                    self.assertTrue(issues(cid, prefix), "the old and unlisted courses cannot use the new namespaces")

    def test_misconception_ids_follow_the_same_rule(self):
        q = {"concepts": ["git.a"], "options": [{"misconception": "http.b"}, {"misconception": "py.c"}, {"misconception": None}]}
        found = exporter.prefix_issues("git-diffs", question=q)
        self.assertEqual([i.path for i in found], ["/options/0/misconception"])
        broken = synthetic_map(["git"])
        broken["misconceptions"][0]["id"] = "http.wrong-0"
        self.assertTrue(exporter.prefix_issues("git-diffs", map_data=broken))
        self.assertEqual(exporter.prefix_issues("git-diffs", map_data=synthetic_map(["git", "py"])), [])


class ExportBindingTests(CourseFixture):
    CID = "git-diffs"

    def add_new_course(self, cid="git-diffs", prefixes=("git", "py"), write_map=True, concept=None):
        data = synthetic_map(prefixes)
        if write_map:
            self.write(self.root / "schema/concepts" / f"{cid}-v0.json", data)
        self.maps[cid] = data
        own = cid.split("-")[0] if cid != "testing" else "test"
        own = NEW[cid][0] if cid in NEW else own
        self.add_course(cid, [(f"{cid}-first", concept or f"{own}.reading", "beginner")])

    def refused(self, needle, code=1):
        got, output = self.run_export()
        self.assertEqual(got, code, output)
        self.assertIn(needle, output)

    def test_a_new_course_with_its_own_map_and_namespace_exports(self):
        for cid, (own, allowed) in NEW.items():
            with self.subTest(cid):
                self.setUp()
                self.add_new_course(cid, prefixes=(own, "py"))
                code, output = self.run_export()
                self.assertEqual(code, 0, output)
                self.assertTrue((self.data / f"index-{cid}.json").exists())
                self.assertEqual(self.run_export("--check")[0], 0)

    def test_the_review_course_may_carry_the_git_and_test_vocabulary_it_builds_on(self):
        self.add_new_course("review-ai-changes", prefixes=("rev", "py", "git", "test"), concept="git.reading")
        code, output = self.run_export()
        self.assertEqual(code, 0, output)

    def test_a_namespace_the_course_was_not_given_is_a_course_prefix_error(self):
        self.add_new_course("git-diffs", prefixes=("git", "py", "http"))      # http concepts in the git vocabulary
        self.refused("course-prefix")
        self.setUp()
        self.add_new_course("testing", prefixes=("test", "py", "git"))        # git is review's, not testing's
        self.refused("course-prefix")

    def test_the_course_must_name_its_own_map_and_the_old_borrowing_is_still_refused(self):
        self.add_new_course("git-diffs")
        row = next(c for c in self.catalog["courses"] if c["id"] == "git-diffs")
        row["concept_map"] = "schema/concepts/python-v0.json"                  # a course with a vocabulary of its own cannot borrow Python's
        self.write(self.data / "catalog.json", self.catalog)
        self.refused("catalog-map")

    def test_a_missing_vocabulary_stops_the_export(self):
        self.add_new_course("http-api", prefixes=("http", "py"), write_map=False)
        code, output = self.run_export()
        self.assertNotEqual(code, 0, output)

    def test_the_old_courses_are_untouched_and_a_new_namespace_in_python_is_refused(self):
        before = {n: self.sha(n) for n in ("index.json", "concepts.json")} if self.run_export()[0] == 0 else None
        self.assertIsNotNone(before)
        python_map = deepcopy(self.maps["python"])
        python_map["concepts"].append({"id": "git.reading", "title": "Git reading", "level": "beginner", "prerequisites": []})
        self.write(self.root / "schema/concepts/python-v0.json", python_map)
        self.refused("course-prefix")

    def test_an_unlisted_course_on_the_shared_map_cannot_use_a_new_namespace(self):
        shared = deepcopy(self.maps["python"])
        shared["concepts"].append({"id": "git.reading", "title": "Git reading", "level": "beginner", "prerequisites": []})
        self.write(self.root / "schema/concepts/python-v0.json", shared)
        self.maps["read-like-a-computer"] = shared
        self.add_course("read-like-a-computer", [("b1-first", "py.values", "beginner")])
        row = next(c for c in self.catalog["courses"] if c["id"] == "read-like-a-computer")
        row.update(audience="beginner", path_step=1, concept_map="schema/concepts/python-v0.json")
        row.pop("premium", None)
        self.write(self.data / "catalog.json", self.catalog)
        self.refused("course-prefix")


if __name__ == "__main__":
    import unittest
    unittest.main()
