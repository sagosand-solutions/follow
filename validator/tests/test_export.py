# SPDX-License-Identifier: MIT
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from validator import core
from validator import export as exporter
from validator.__main__ import main as dispatch
from helpers import concept_map, question


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.schema = self.root / "schema"
        self.schema.mkdir()
        for name in ("question.schema.json", "concept-map.schema.json", "course.schema.json"):
            shutil.copyfile(core.ROOT / "schema" / name, self.schema / name)
        self.data = self.root / "public/data"
        self.qdir = self.data / "questions"
        self.qdir.mkdir(parents=True)
        self.map = concept_map()
        self.beginner, self.intermediate = question(), question()
        self.beginner["id"] = "zulu-question"
        self.intermediate.update(id="alpha-question", level="intermediate")
        self.course = {"id": "python", "title": "Synthetic course", "language": "python", "levels": [
            {"level": "beginner", "title": "First", "lessons": [{"id": "first", "title": "First lesson", "builds_on": [], "questions": ["zulu-question"]}]},
            {"level": "intermediate", "title": "Next", "lessons": [{"id": "second", "title": "Next lesson", "builds_on": ["first"], "questions": ["alpha-question"]}]}]}
        self.map_path = self.schema / "concepts/python-v0.json"
        self.course_path = self.data / "courses/python.json"
        self.save_inputs()
        self.outputs = [self.data / "index.json", self.data / "concepts.json"]

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def save_inputs(self):
        self.write(self.map_path, self.map)
        self.write(self.qdir / "zulu-question.json", self.beginner)
        self.write(self.qdir / "alpha-question.json", self.intermediate)
        self.write(self.course_path, self.course)

    def invoke(self, *args):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = exporter.main(["--root", str(self.root), *args])
        return code, stream.getvalue()

    def snapshot(self):
        return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.outputs if path.exists()}

    def assert_invalid_unchanged(self, expected, expected_code=1):
        for path in self.outputs:
            path.write_bytes(b"existing-output\n")
        before = self.snapshot()
        code, output = self.invoke()
        self.assertEqual(code, expected_code, output)
        self.assertIn(expected, output)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(list(self.data.glob(".export-*")), [])

    def test_deterministic_complete_output(self):
        self.map["concepts"][0]["title"] = "Värden"
        self.write(self.map_path, self.map)
        with patch("subprocess.Popen", side_effect=AssertionError("must stay static")):
            code, output = self.invoke()
        self.assertEqual(code, 0, output)
        self.assertIn("Static-only", output)
        self.assertIn("execution skipped", output)
        index = json.loads(self.outputs[0].read_bytes())
        expected = {"data_version": 2, "onboarding_question": "zulu-question", "questions": [
            {**{field: q[field] for field in ("id", "type", "level", "difficulty", "concepts")}, "answer_profile": "standard"}
            for q in (self.intermediate, self.beginner)]}
        self.assertEqual(index, expected)
        self.assertEqual(json.loads(self.outputs[1].read_bytes()), self.map)
        self.assertIn("Värden".encode(), self.outputs[1].read_bytes())
        first = [p.read_bytes() for p in self.outputs]
        self.assertTrue(all(b.endswith(b"\n") for b in first))
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(first, [p.read_bytes() for p in self.outputs])
        self.assertTrue(all(p.stat().st_mode & 0o044 for p in self.outputs))

    def test_proprietary_license_blocks_public_export(self):
        for path in self.outputs:
            path.write_bytes(b"existing-output\n")
        before = self.snapshot()
        self.beginner["license"] = "LicenseRef-Proprietary"
        self.write(self.qdir / "zulu-question.json", self.beginner)
        for args in ((), ("--check",)):
            with self.subTest(args=args):
                code, output = self.invoke(*args)
                self.assertEqual(code, 1, output)
                self.assertIn("non-public-license", output)
                self.assertIn("/license", output)
                self.assertEqual(before, self.snapshot())
                self.assertEqual(list(self.data.glob(".export-*")), [])

    def test_proprietary_license_keeps_absent_outputs_absent(self):
        self.beginner["license"] = "LicenseRef-Proprietary"
        self.write(self.qdir / "zulu-question.json", self.beginner)
        for args in ((), ("--check",)):
            with self.subTest(args=args):
                code, output = self.invoke(*args)
                self.assertEqual(code, 1, output)
                self.assertIn("non-public-license", output)
                self.assertFalse(any(path.exists() for path in self.outputs))
                self.assertEqual(list(self.data.glob(".export-*")), [])

    def test_check_success_is_read_only(self):
        self.assertEqual(self.invoke()[0], 0)
        before = self.snapshot()
        self.assertEqual(self.invoke("--check")[0], 0)
        self.assertEqual(before, self.snapshot())

    def test_check_missing_and_noncanonical_does_not_write(self):
        self.assertEqual(self.invoke("--check")[0], 1)
        self.assertFalse(any(p.exists() for p in self.outputs))
        self.assertEqual(list(self.data.glob(".export-*")), [])
        self.assertEqual(self.invoke()[0], 0)
        # Valid JSON but different whitespace/order still requires regeneration.
        self.outputs[0].write_text(json.dumps(json.loads(self.outputs[0].read_bytes())))
        before = self.snapshot()
        code, output = self.invoke("--check")
        self.assertEqual(code, 1, output)
        self.assertIn("stale-export", output)
        self.assertEqual(before, self.snapshot())
        self.outputs[0].unlink()
        before = self.snapshot()
        self.assertEqual(self.invoke("--check")[0], 1)
        self.assertEqual(before, self.snapshot())

    def test_missing_empty_inputs_and_setup(self):
        for relative in ("schema/question.schema.json", "schema/concept-map.schema.json", "schema/course.schema.json",
                         "schema/concepts/python-v0.json", "public/data/courses/python.json"):
            path = self.root / relative
            original = path.read_bytes()
            path.unlink()
            with self.subTest(path=relative):
                self.assertEqual(self.invoke()[0], 2)
            path.write_bytes(original)
        for path in self.qdir.glob("*.json"):
            path.unlink()
        code, output = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("empty export", output)
        self.qdir.rmdir()
        self.assertEqual(self.invoke()[0], 2)

    def test_check_absent_data_directory_never_creates_it(self):
        shutil.rmtree(self.root / "public")
        self.assertEqual(self.invoke("--check")[0], 2)
        self.assertFalse((self.root / "public").exists())

    def test_invalid_question_and_map_block_every_write(self):
        self.beginner["options"][2]["misconception"] = "py.missing"
        self.save_inputs()
        self.assert_invalid_unchanged("unknown-misconception")
        self.beginner = question()
        self.beginner["id"] = "zulu-question"
        self.map["concepts"][0]["prerequisites"] = ["py.values"]
        self.save_inputs()
        self.assert_invalid_unchanged("self-edge")

    def test_no_execution_even_for_nonmatching_declared_observations(self):
        self.beginner["snippets"][0]["code"] = "raise AssertionError('must not execute')"
        self.save_inputs()
        with patch("validator.runner.TrustedLocalRunner.run", side_effect=AssertionError("must not execute")) as run:
            self.assertEqual(self.invoke()[0], 0)
        run.assert_not_called()

    def test_filename_duplicates_and_orphan(self):
        original = self.qdir / "zulu-question.json"
        renamed = self.qdir / "renamed-question.json"
        original.rename(renamed)
        self.assert_invalid_unchanged("filename")
        original.write_bytes(renamed.read_bytes())
        self.assert_invalid_unchanged("duplicate-question")
        renamed.unlink()
        self.course["levels"] = self.course["levels"][:1]
        self.write(self.course_path, self.course)
        self.assert_invalid_unchanged("orphan-question")

    def test_course_titles_and_unknown_fields(self):
        for path in ((), ("levels", 0), ("levels", 0, "lessons", 0)):
            for field, value, code in (("title", " \t", "blank"), ("extra", 1, "schema")):
                course = deepcopy(self.course)
                target = course
                for key in path:
                    target = target[key]
                target[field] = value
                self.write(self.course_path, course)
                with self.subTest(path=path, field=field):
                    self.assert_invalid_unchanged(code)

    def test_lesson_summary_is_optional_preserved_and_not_exported(self):
        self.assertEqual(self.invoke()[0], 0)
        baseline = [path.read_bytes() for path in self.outputs]
        lesson = self.course["levels"][0]["lessons"][0]
        lesson["summary"] = "An introduction to independent choices."
        self.save_inputs()
        course_bytes = self.course_path.read_bytes()
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual([path.read_bytes() for path in self.outputs], baseline)
        self.assertEqual(self.course_path.read_bytes(), course_bytes)
        before = self.snapshot()
        self.assertEqual(self.invoke("--check")[0], 0)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.course_path.read_bytes(), course_bytes)

    def test_invalid_lesson_summary_blocks_writes_and_execution(self):
        forbidden = tuple(map(chr, (10, 13, 0, 31, 127, 159, 0x2028, 0x2029))) + (
            chr(13) + chr(10),
        )
        invalid = (("", None, 1, [], {}, " " * 200, "x" * 201,
                    "a\nb", "a\rb", "a\tb", "a\x00b", "a\x1fb",
                    "a\x7fb", "a\x9fb", "a\u2028b", "a\u2029b")
                   + tuple(character + "Intro" for character in forbidden)
                   + tuple("Intro" + character for character in forbidden))
        for summary in invalid:
            with self.subTest(summary=repr(summary)):
                self.course["levels"][0]["lessons"][0]["summary"] = summary
                self.save_inputs()
                expected = "blank" if isinstance(summary, str) and summary and summary.strip() == "" else "schema"
                with patch("validator.runner.TrustedLocalRunner.run", side_effect=AssertionError("must not execute")) as run:
                    self.assert_invalid_unchanged(expected)
                run.assert_not_called()

    def test_summary_is_rejected_outside_lesson(self):
        for path in ((), ("levels", 0)):
            course = deepcopy(self.course)
            target = course
            for part in path:
                target = target[part]
            target["summary"] = "Not allowed here."
            self.write(self.course_path, course)
            with self.subTest(path=path):
                self.assert_invalid_unchanged("schema")

    def test_course_structural_contract(self):
        variants = [None, [], {**self.course, "id": "other"}, {**self.course, "language": "other"},
                    {**self.course, "levels": []}, {**self.course, "levels": self.course["levels"] * 2}]
        for path in ((), ("levels", 0), ("levels", 0, "lessons", 0)):
            original = self.course
            for key in path:
                original = original[key]
            for field in original:
                course = deepcopy(self.course)
                target = course
                for key in path:
                    target = target[key]
                del target[field]
                variants.append(course)
        for field, value in (("id", "bad\n"), ("builds_on", ["first\n"]), ("questions", ["zulu-question\n"]),
                             ("questions", []), ("builds_on", ["first", "first"])):
            course = deepcopy(self.course)
            course["levels"][0]["lessons"][0][field] = value
            variants.append(course)
        for course in variants:
            with self.subTest(course=course):
                self.write(self.course_path, course)
                self.assert_invalid_unchanged("schema")

    def test_course_reference_and_dependency_failures(self):
        changes = [
            (lambda c: c["levels"][1]["lessons"][0].update(id="first"), "duplicate-lesson"),
            (lambda c: c["levels"][1]["lessons"][0].update(builds_on=["missing"]), "unknown-lesson"),
            (lambda c: c["levels"][1]["lessons"][0].update(builds_on=["second"]), "self-edge"),
            (lambda c: c["levels"][0]["lessons"][0].update(builds_on=["second"]), "lesson-cycle"),
            (lambda c: c["levels"][0]["lessons"][0].update(questions=["missing-question"]), "unknown-question"),
            (lambda c: c["levels"][0]["lessons"][0].update(questions=["alpha-question"]), "question-level"),
            (lambda c: c["levels"][1]["lessons"][0].update(questions=["zulu-question"]), "duplicate-assignment"),
            (lambda c: c["levels"].append(deepcopy(c["levels"][0])), "duplicate-level"),
            (lambda c: c["levels"].reverse(), "level-order"),
            (lambda c: c["levels"].reverse(), "onboarding-level"),
        ]
        for change, expected in changes:
            course = deepcopy(self.course)
            change(course)
            self.write(self.course_path, course)
            with self.subTest(expected=expected):
                self.assert_invalid_unchanged(expected)

    def test_valid_subset_and_empty_dependency_list(self):
        (self.qdir / "alpha-question.json").unlink()
        self.course["levels"] = self.course["levels"][:1]
        self.write(self.course_path, self.course)
        self.assertEqual(self.invoke()[0], 0)
        index = json.loads(self.outputs[0].read_bytes())
        self.assertEqual(len(index["questions"]), 1)

    def test_malformed_json_and_schema_errors(self):
        for path in (self.qdir / "zulu-question.json", self.map_path, self.course_path):
            saved = path.read_bytes()
            for text in ('{"x": NaN}', '{"x": 1, "x": 2}', '{broken'):
                path.write_text(text)
                with self.subTest(path=path, text=text):
                    self.assert_invalid_unchanged("json")
            path.write_bytes(saved)
        (self.schema / "course.schema.json").write_text('{broken')
        self.assert_invalid_unchanged("schema", 2)

    def test_unsafe_input_and_output_links(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "external.json"
            external.write_bytes(b"outside sentinel")
            for target in (self.qdir / "zulu-question.json", self.map_path, self.course_path,
                           self.schema / "question.schema.json", *self.outputs):
                saved = target.read_bytes() if target.exists() else None
                target.unlink(missing_ok=True)
                target.symlink_to(external)
                with self.subTest(target=target):
                    self.assertEqual(self.invoke()[0], 2)
                    self.assertEqual(external.read_bytes(), b"outside sentinel")
                target.unlink()
                if saved is not None:
                    target.write_bytes(saved)
            original = self.qdir.with_name("saved-questions")
            self.qdir.rename(original)
            self.qdir.symlink_to(Path(outside), target_is_directory=True)
            self.assertEqual(self.invoke()[0], 2)
            self.qdir.unlink()
            original.rename(self.qdir)

    def test_stage_failure_keeps_outputs_and_cleans_temporary_files(self):
        for path in self.outputs:
            path.write_bytes(b"original")
        before = self.snapshot()
        original = exporter.tempfile.NamedTemporaryFile
        calls = 0
        def failing_stage(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected staging failure")
            return original(*args, **kwargs)
        with patch.object(exporter.tempfile, "NamedTemporaryFile", side_effect=failing_stage):
            self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(list(self.data.glob(".export-*")), [])

    def test_root_schemas_are_used_without_global_mutation(self):
        before = core.SCHEMA_DIR
        with patch.object(core, "SCHEMA_DIR", Path("/nonexistent-default-schema-directory")):
            self.assertEqual(self.invoke()[0], 0)
            self.assertEqual(core.SCHEMA_DIR, Path("/nonexistent-default-schema-directory"))
        self.assertEqual(core.SCHEMA_DIR, before)

    def test_two_roots_with_different_schemas_do_not_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory) / "repository"
            shutil.copytree(self.root, other)
            path = other / "schema/question.schema.json"
            schema = json.loads(path.read_bytes())
            schema["properties"]["language"] = {"const": "javascript"}
            self.write(path, schema)
            self.assertEqual(self.invoke()[0], 0)
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(exporter.main(["--root", str(other)]), 1)
            self.assertIn("schema", stdout.getvalue())
            self.assertEqual(self.invoke("--check")[0], 0)
            self.assertFalse((other / "public/data/index.json").exists())
            self.assertEqual(core.SCHEMA_DIR, core.ROOT / "schema")

    def test_dispatch_and_invocation_outside_repository(self):
        with patch("validator.export.main", return_value=7) as export_main:
            self.assertEqual(dispatch(["export", "--check", "--root", str(self.root)]), 7)
        export_main.assert_called_once_with(["--check", "--root", str(self.root)])
        with patch("validator.cli.main", return_value=5) as validate_main:
            self.assertEqual(dispatch(["question.json"]), 5)
        validate_main.assert_called_once_with(["question.json"])
        env = dict(os.environ, PYTHONPATH=str(core.ROOT))
        completed = subprocess.run([sys.executable, "-m", "validator", "export", "--root", str(self.root)],
                                   cwd="/tmp", env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        previous = os.getcwd()
        try:
            os.chdir("/tmp")
            with patch.object(exporter, "ROOT", self.root), redirect_stdout(io.StringIO()):
                self.assertEqual(exporter.main(["--check"]), 0)
        finally:
            os.chdir(previous)

    def test_cli_bad_arguments_and_dependency_missing(self):
        self.assertEqual(self.invoke("--unknown")[0], 2)
        with patch.dict("sys.modules", {"jsonschema": None}):
            code, output = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("requirements", output)


if __name__ == "__main__":
    unittest.main()
