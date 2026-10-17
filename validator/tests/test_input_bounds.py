# SPDX-License-Identifier: MIT
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from validator import ContentError, InputLimitError, SetupError, core, load_json
from validator import cli, export as exporter
from helpers import concept_map, question


class InputBoundsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "input.json"
        self.root = self.directory / "repository"

    def write(self, path, value, size=None):
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if size is not None:
            self.assertLessEqual(len(raw), size)
            raw += b" " * (size - len(raw))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def make_root(self, count=1, size=None):
        schema = self.root / "schema"
        schema.mkdir(parents=True)
        for name in ("question.schema.json", "concept-map.schema.json", "course.schema.json"):
            shutil.copyfile(core.ROOT / "schema" / name, schema / name)
        self.write(schema / "concepts/python-v0.json", concept_map())
        paths, identifiers = [], []
        for i in range(count):
            q = question()
            q["id"] = f"test-{i:03}"
            path = self.root / "public/data/questions" / (q["id"] + ".json")
            self.write(path, q, size)
            paths.append(path)
            identifiers.append(q["id"])
        self.write(self.root / "public/data/courses/python.json", {
            "id": "python", "title": "Synthetic course", "language": "python", "levels": [
                {"level": "beginner", "title": "Synthetic level", "lessons": [
                    {"id": "lesson", "title": "Synthetic lesson", "builds_on": [], "questions": identifiers}]}]})
        return paths

    def invoke_cli(self, paths, *, verify=False, runner=None):
        args = ["--format", "json", "--concept-map", str(self.root / "schema/concepts/python-v0.json")]
        if verify:
            args += ["--verify", "--runner", "trusted-local", "--allow-trusted-code"]
        out = io.StringIO()
        with patch.object(core, "SCHEMA_DIR", self.root / "schema"), redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(args + list(map(str, paths)), runner=runner)
        return code, json.loads(out.getvalue())

    def invoke_export(self, check=False):
        out = io.StringIO()
        with redirect_stdout(out):
            code = exporter.main(["--root", str(self.root)] + (["--check"] if check else []))
        return code, out.getvalue()

    def sentinels(self):
        paths = [self.root / "public/data" / name for name in ("index.json", "concepts.json")]
        for path in paths:
            path.write_bytes(b"existing output\n")
        return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

    def assert_unchanged(self, snapshot):
        self.assertEqual(snapshot, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in snapshot})
        self.assertEqual(list((self.root / "public/data").glob(".export-*")), [])

    def test_reviewed_constants(self):
        self.assertEqual((core.MAX_JSON_BYTES, core.MAX_QUESTION_FILES, core.MAX_QUESTION_BYTES, core.MAX_JSON_DEPTH),
                         (262144, 512, 16777216, 32))

    def test_file_exact_cap_and_cap_plus_one(self):
        self.write(self.path, "valid", core.MAX_JSON_BYTES)
        self.assertEqual(load_json(self.path), "valid")
        with self.path.open("ab") as handle:
            handle.write(b" ")
        with patch.object(core.json, "loads", side_effect=AssertionError("oversized input parsed")) as parse:
            with self.assertRaisesRegex(InputLimitError, "file-bytes.*262144"):
                load_json(self.path)
        parse.assert_not_called()

    def test_utf8_byte_not_codepoint_budget(self):
        value = "å" * ((core.MAX_JSON_BYTES - 2) // 2)
        self.write(self.path, value)
        self.assertEqual(self.path.stat().st_size, core.MAX_JSON_BYTES)
        self.assertEqual(load_json(self.path), value)
        with self.path.open("ab") as handle:
            handle.write(b" ")
        with self.assertRaises(InputLimitError):
            load_json(self.path)

    def test_oversized_read_stops_at_cap_plus_one(self):
        class Observed(io.BytesIO):
            def __init__(self, value):
                super().__init__(value)
                self.requests, self.consumed = [], 0
            def read(self, size=-1):
                self.requests.append(size)
                if size < 0:
                    raise AssertionError("unbounded read")
                result = super().read(size)
                self.consumed += len(result)
                return result
        stream = Observed(b" " * (core.MAX_JSON_BYTES + 1024))
        with patch.object(Path, "open", return_value=stream) as opened:
            with self.assertRaises(InputLimitError):
                load_json(self.path)
        opened.assert_called_once_with("rb", buffering=0)
        self.assertEqual(stream.requests, [core.MAX_JSON_BYTES + 1])
        self.assertEqual(stream.consumed, core.MAX_JSON_BYTES + 1)

    def test_short_reads_do_not_bypass_size_limit(self):
        class ShortReads(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(1024, size))
        stream = ShortReads(b" " * (core.MAX_JSON_BYTES + 1))
        with patch.object(Path, "open", return_value=stream):
            with self.assertRaises(InputLimitError):
                load_json(self.path)

    def test_mixed_depth_exact_cap_and_plus_one_before_parser(self):
        def nested(depth):
            text = "0"
            for i in range(depth):
                text = '{"x":' + text + "}" if i % 2 else "[" + text + "]"
            return text
        self.path.write_text(nested(32))
        self.assertEqual(load_json(self.path), json.loads(nested(32)))
        for text in (nested(33), "[" * 33 + "0" + "]" * 33, '{"x":' * 33 + "0" + "}" * 33):
            self.path.write_text(text)
            with patch.object(core.json, "loads", side_effect=AssertionError("deep input parsed")) as parse:
                with self.assertRaisesRegex(InputLimitError, "json-depth.*32"):
                    load_json(self.path)
            parse.assert_not_called()

    def test_string_brackets_escaped_quotes_and_backslashes_are_not_depth(self):
        for value in ("[" * 1000, "}" * 1000, '[{ \\" ]} \\ ' * 40, 'quote" and backslash\\', "[\\u005b]"):
            self.write(self.path, value)
            with self.subTest(value=value[:30]):
                self.assertEqual(load_json(self.path), value)
        # Root scalar is depth0; root array/object is depth1.
        for value in (0, True, None, "[]{}"):
            self.write(self.path, value)
            self.assertEqual(load_json(self.path), value)
        with patch.object(core, "MAX_JSON_DEPTH", 0):
            self.write(self.path, [])
            with self.assertRaises(InputLimitError):
                load_json(self.path)

    def test_malformed_json_stays_invalid_and_strict_utf8_stays_setup_error(self):
        for text in ("[}", '{"x":]', '"unterminated', '"bad\\q"', '{"x":true}junk', '{"x":1,"x":2}', 'NaN', 'Infinity'):
            self.path.write_text(text)
            with self.subTest(text=text), self.assertRaises(ContentError):
                load_json(self.path)
        self.path.write_bytes(b'"\xff"')
        with self.assertRaises(SetupError):
            load_json(self.path)

    def test_parser_recursion_and_memory_failures_are_controlled(self):
        self.path.write_text("0")
        for error in (RecursionError, MemoryError):
            with self.subTest(error=error), patch.object(core.json, "loads", side_effect=error("injected")):
                with self.assertRaisesRegex(InputLimitError, "json-parser-resource"):
                    load_json(self.path)

    def test_actual_aggregate_bytes_before_decode_and_parse_not_stat(self):
        self.path.write_bytes(b'"\xc3\xa5"')
        size = len(self.path.read_bytes())
        budget = core.QuestionBudget(core.MAX_QUESTION_BYTES - size)
        with patch.object(Path, "stat", side_effect=AssertionError("stat budget is not authoritative")):
            self.assertEqual(load_json(self.path, budget=budget), "å")
        self.assertEqual(budget.bytes_read, core.MAX_QUESTION_BYTES)
        self.path.write_bytes(b"0")
        with patch.object(core.json, "loads", side_effect=AssertionError("overflow parsed")) as parse:
            with self.assertRaisesRegex(InputLimitError, "question-bytes.*16777216"):
                load_json(self.path, budget=budget)
        parse.assert_not_called()
        self.assertEqual(budget.bytes_read, core.MAX_QUESTION_BYTES + 1)
        # Overflow is refused even before UTF-8 decoding of the crossing file.
        self.path.write_bytes(b"\xff")
        with self.assertRaises(InputLimitError):
            load_json(self.path, budget=core.QuestionBudget(core.MAX_QUESTION_BYTES))
        self.path.write_bytes(b"0")
        self.assertEqual(load_json(self.path, budget=core.QuestionBudget()), 0)

    def test_repeated_file_reads_are_charged_again(self):
        self.path.write_bytes(b"0")
        budget = core.QuestionBudget(core.MAX_QUESTION_BYTES - 2)
        self.assertEqual(load_json(self.path, budget=budget), 0)
        self.assertEqual(load_json(self.path, budget=budget), 0)
        self.assertEqual(budget.bytes_read, core.MAX_QUESTION_BYTES)
        with self.assertRaises(InputLimitError):
            load_json(self.path, budget=budget)

    def test_partial_io_failure_still_charges_actual_question_bytes(self):
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.read.side_effect = [b"0", OSError("injected later read failure")]
        budget = core.QuestionBudget(core.MAX_QUESTION_BYTES - 1)
        with patch.object(Path, "open", return_value=stream):
            with self.assertRaises(SetupError):
                load_json(self.path, budget=budget)
        self.assertEqual(budget.bytes_read, core.MAX_QUESTION_BYTES)
        self.path.write_bytes(b"0")
        with self.assertRaises(InputLimitError):
            load_json(self.path, budget=budget)

    def test_cli_count_exact_cap_and_plus_one_before_any_read(self):
        paths = [f"test-{i:03}.json" for i in range(512)]
        def loaded(path, **kwargs):
            if str(path).endswith("python-v0.json"):
                return concept_map()
            return {**question(), "id": Path(path).stem}
        with patch.object(cli, "load_json", side_effect=loaded) as read, patch.object(cli, "validate_map", return_value=[]), patch.object(cli, "validate_question", return_value=[]):
            code, report = self.invoke_cli(paths)
            self.assertEqual((code, report["questions"]), (0, 512))
            self.assertEqual(read.call_count, 513)
        with patch.object(cli, "load_json", side_effect=AssertionError("count gate did not run first")) as read:
            runner = SimpleNamespace(run=Mock(side_effect=AssertionError("must not run")))
            code, report = self.invoke_cli(paths + ["extra.json"], verify=True, runner=runner)
        self.assertEqual(code, 1)
        self.assertEqual(report["questions"], 0)
        self.assertEqual(report["issues"][0]["code"], "input-limit")
        self.assertIn("question-count", report["issues"][0]["message"])
        self.assertEqual(report["execution"], "unavailable")
        read.assert_not_called()
        runner.run.assert_not_called()

    def test_cli_duplicate_inputs_count_and_keep_duplicate_id_rejection(self):
        with patch.object(cli, "load_json", side_effect=lambda path, **kw: concept_map() if str(path).endswith("python-v0.json") else question()), patch.object(cli, "validate_map", return_value=[]), patch.object(cli, "validate_question", return_value=[]):
            code, report = self.invoke_cli(["same.json"] * 512)
        self.assertEqual(code, 1)
        self.assertTrue(all(i["code"] == "duplicate-question" for i in report["issues"]))
        code, report = self.invoke_cli(["same.json"] * 513)
        self.assertEqual(code, 1)
        self.assertEqual(report["issues"][0]["code"], "input-limit")

    def test_streaming_directory_count_stops_at_513_matches(self):
        def scan(count):
            consumed = []
            def entries():
                for i in range(count):
                    consumed.append(i)
                    if i > 512:
                        raise AssertionError("discovery continued beyond 513 matches")
                    yield SimpleNamespace(name=f"test-{i:03}.json")
            manager = Mock()
            manager.__enter__ = Mock(return_value=entries())
            manager.__exit__ = Mock(return_value=False)
            return manager, consumed
        manager, consumed = scan(512)
        with patch.object(exporter.os, "scandir", return_value=manager):
            result = exporter._question_paths(self.directory)
        self.assertEqual(len(result), 512)
        self.assertEqual(result, sorted(result))
        self.assertEqual(len(consumed), 512)
        manager, consumed = scan(1000)
        with patch.object(exporter.os, "scandir", return_value=manager):
            with self.assertRaisesRegex(InputLimitError, "question-count"):
                exporter._question_paths(self.directory)
        self.assertEqual(len(consumed), 513)
        manager.__exit__.assert_called_once()

    def test_export_count_limit_before_read_or_replace(self):
        self.make_root()
        snapshot = self.sentinels()
        manager = Mock()
        manager.__enter__ = Mock(return_value=iter([SimpleNamespace(name=f"test-{i:03}.json") for i in range(513)]))
        manager.__exit__ = Mock(return_value=False)
        with patch.object(exporter.os, "scandir", return_value=manager), patch.object(exporter, "load_json", side_effect=AssertionError("must not read")) as read, patch.object(exporter.os, "replace", side_effect=AssertionError("must not replace")) as replace:
            code, output = self.invoke_export()
        self.assertEqual(code, 1)
        self.assertIn("input-limit", output)
        read.assert_not_called()
        replace.assert_not_called()
        self.assert_unchanged(snapshot)

    def test_file_and_depth_limits_block_all_execution_and_writes(self):
        paths = self.make_root()
        original = paths[0].read_bytes()
        snapshot = self.sentinels()
        runner = SimpleNamespace(run=Mock(side_effect=AssertionError("must not execute")))
        for raw, policy in ((b" " * (core.MAX_JSON_BYTES + 1), "file-bytes"), (b"[" * 33 + b"0" + b"]" * 33, "json-depth")):
            paths[0].write_bytes(raw)
            code, report = self.invoke_cli(paths, verify=True, runner=runner)
            self.assertEqual(code, 1)
            self.assertEqual(report["issues"][0]["code"], "input-limit")
            self.assertIn(policy, report["issues"][0]["message"])
            self.assertEqual(report["issues"][0]["file"], str(paths[0]))
            for check in (False, True):
                with patch.object(exporter.os, "replace", side_effect=AssertionError("must not replace")) as replace:
                    code, output = self.invoke_export(check)
                self.assertEqual(code, 1)
                self.assertIn("input-limit", output)
                replace.assert_not_called()
                self.assert_unchanged(snapshot)
        runner.run.assert_not_called()
        paths[0].write_bytes(original)
        self.assertEqual(self.invoke_cli(paths)[0], 0)

    def test_schema_map_and_course_limit_failures_stay_input_errors(self):
        paths = self.make_root()
        snapshot = self.sentinels()
        targets = [self.root / "schema" / name for name in ("question.schema.json", "concept-map.schema.json", "course.schema.json", "concepts/python-v0.json")]
        targets.append(self.root / "public/data/courses/python.json")
        for target in targets:
            original = target.read_bytes()
            for raw in (b" " * (core.MAX_JSON_BYTES + 1), b"[" * 33 + b"0" + b"]" * 33):
                target.write_bytes(raw)
                with self.subTest(target=target), patch.object(exporter.os, "replace", side_effect=AssertionError("must not replace")):
                    code, output = self.invoke_export()
                    self.assertEqual(code, 1, output)
                    self.assertIn("input-limit", output)
                    self.assertIn(str(target), output)
                if target.name != "course.schema.json" and target.name != "python.json":
                    code, report = self.invoke_cli(paths)
                    self.assertEqual(code, 1)
                    self.assertIn("input-limit", [i["code"] for i in report["issues"]])
                self.assert_unchanged(snapshot)
            target.write_bytes(original)

    def test_real_16mib_boundary_overflow_gate_and_fresh_invocation(self):
        paths = self.make_root(64, core.MAX_JSON_BYTES)
        self.assertEqual(sum(p.stat().st_size for p in paths), core.MAX_QUESTION_BYTES)
        # Exact boundary accepted independently twice: no process-global exhaustion.
        self.assertEqual(self.invoke_cli(paths)[0], 0)
        self.assertEqual(self.invoke_cli(paths)[0], 0)
        self.assertEqual(self.invoke_export()[0], 0)
        self.assertEqual(self.invoke_export(True)[0], 0)
        snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.root / "public/data/index.json", self.root / "public/data/concepts.json")}
        extra = paths[0].with_name("zz-extra.json")
        extra.write_bytes(b"0")
        paths.append(extra)
        later = extra.with_name("zzz-later.json")
        later.write_bytes(b"0")
        paths.append(later)
        original_loads, original_read = json.loads, core.read_json_bytes
        parsed_zero, read_paths = [], []
        def tracked(text, *args, **kwargs):
            if text == "0":
                parsed_zero.append(True)
            return original_loads(text, *args, **kwargs)
        def read(path, **kwargs):
            read_paths.append(Path(path))
            return original_read(path, **kwargs)
        runner = SimpleNamespace(run=Mock(side_effect=AssertionError("must not run")))
        with patch.object(core.json, "loads", side_effect=tracked), patch.object(core, "read_json_bytes", side_effect=read):
            code, report = self.invoke_cli(paths, verify=True, runner=runner)
            self.assertEqual(code, 1)
            self.assertEqual(report["questions"], 64)
            self.assertIn("question-bytes", report["issues"][0]["message"])
            for check in (False, True):
                with patch.object(exporter.os, "replace", side_effect=AssertionError("must not replace")):
                    code, output = self.invoke_export(check)
                self.assertEqual(code, 1)
                self.assertIn("question-bytes", output)
                self.assert_unchanged(snapshot)
        self.assertEqual(parsed_zero, [])
        self.assertNotIn(later, read_paths)
        runner.run.assert_not_called()
        extra.unlink()
        later.unlink()
        self.assertEqual(self.invoke_export(True)[0], 0)

    def test_default_corpus_reexports_idempotently_without_changing_inputs(self):
        for relative in ("schema/question.schema.json", "schema/concept-map.schema.json",
                         "schema/course.schema.json", "schema/catalog.schema.json", "schema/concepts/python-v0.json"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(core.ROOT / relative, target)
        shutil.copytree(core.ROOT / "public/data", self.root / "public/data")
        outputs = [self.root / "public/data" / name for name in ("index.json", "concepts.json")]
        before = {path: path.read_bytes() for path in self.root.rglob("*.json") if path not in outputs}
        with patch("subprocess.Popen", side_effect=AssertionError("must stay static")):
            self.assertEqual(self.invoke_export()[0], 0)
            generated = {path: path.read_bytes() for path in outputs}
            self.assertEqual(self.invoke_export()[0], 0)
            self.assertEqual(self.invoke_export(True)[0], 0)
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*.json") if path not in outputs})
        self.assertEqual(generated, {path: path.read_bytes() for path in outputs})

    def test_compact_valid_map_expands_over_cap_before_any_output_staging(self):
        self.make_root()
        snapshot = self.sentinels()
        mapping = concept_map()
        mapping["misconceptions"][0]["description"] = ""
        compact_size = len(json.dumps(mapping, ensure_ascii=False, separators=(",", ":")).encode())
        mapping["misconceptions"][0]["description"] = "x" * (core.MAX_JSON_BYTES - compact_size)
        path = self.root / "schema/concepts/python-v0.json"
        self.write(path, mapping)
        self.assertEqual(path.stat().st_size, core.MAX_JSON_BYTES)
        self.assertEqual(core.validate_map(load_json(path), schema_dir=self.root / "schema"), [])
        self.assertGreater(len(exporter.serialize(mapping)), core.MAX_JSON_BYTES)
        for check in (False, True):
            with patch.object(exporter.tempfile, "NamedTemporaryFile", side_effect=AssertionError("must validate BOTH payloads before staging")) as stage, patch.object(exporter.os, "replace", side_effect=AssertionError("must not replace")) as replace:
                code, output = self.invoke_export(check)
            self.assertEqual(code, 1)
            self.assertIn("input-limit", output)
            self.assertIn("concepts.json", output)
            self.assertIn("generated JSON after formatting", output)
            stage.assert_not_called()
            replace.assert_not_called()
            self.assert_unchanged(snapshot)

    def test_generated_comparison_and_payload_bytes_are_bounded(self):
        self.make_root()
        self.assertEqual(self.invoke_export()[0], 0)
        target = self.root / "public/data/index.json"
        target.write_bytes(b" " * (core.MAX_JSON_BYTES + 1))
        snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (target, target.with_name("concepts.json"))}
        code, output = self.invoke_export(True)
        self.assertEqual(code, 1)
        self.assertIn("input-limit", output)
        self.assert_unchanged(snapshot)
        with patch.object(exporter, "serialize", return_value=b" " * (core.MAX_JSON_BYTES + 1)), patch.object(exporter.os, "replace", side_effect=AssertionError("must not replace")):
            code, output = self.invoke_export()
        self.assertEqual(code, 1)
        self.assertIn("input-limit", output)
        self.assert_unchanged(snapshot)
        # Normal export can repair an old oversized output, without reading it.
        with patch.object(exporter, "read_json_bytes", side_effect=AssertionError("normal export must not read old outputs")):
            self.assertEqual(self.invoke_export()[0], 0)
        self.assertEqual(self.invoke_export(True)[0], 0)


if __name__ == "__main__":
    unittest.main()
