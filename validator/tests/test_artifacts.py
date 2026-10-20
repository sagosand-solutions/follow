# SPDX-License-Identifier: MIT
"""Text artifacts (schema_version 0.3): typed, inert display text with an exact hash and a fixture binding.

Fixtures are synthetic. Questions are validated against the REAL schema and the REAL Python vocabulary."""
from copy import deepcopy
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import unittest

from validator import core, validate_question
from validator import artifacts
from validator import export as exporter

from test_export_courses import CourseFixture, make_question

ROOT = core.ROOT
PYTHON_MAP = json.loads((ROOT / "schema/concepts/python-v0.json").read_text(encoding="utf-8"))

KINDS = {"text": "plain", "diff": "unified-diff", "http-transcript": "http-1.1-transcript", "test-report": "plain", "policy": "plain"}
DIFF = "--- a/total.py\n+++ b/total.py\n@@ -1,2 +1,2 @@\n def total(xs):\n-    return sum(xs)\n+    return sum(xs) + 1\n"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def artifact(text=DIFF, kind="diff", id="change", **over):
    value = {"id": id, "kind": kind, "format": KINDS[kind], "text": text, "sha256": sha(text),
             "provenance": {"fixture_id": "fixture-one", "fixture_output_sha256": sha(text)}}
    value.update(over)
    return value


def options():
    related = [m["id"] for m in PYTHON_MAP["misconceptions"] if "py.values" in m["concepts"]]
    wrong = iter(related)
    out = []
    for i, score in enumerate([1, 0.5, 0, 0, 0]):
        option = {"id": f"o{i}", "text": f"Synthetic option {i}", "score": score, "feedback": "Synthetic feedback",
                  "misconception": None if score == 1 else next(wrong)}
        if score == 0.5:
            option["partial_reason"] = "Synthetic correct and incorrect parts"
        out.append(option)
    return out


def text_only(*artifacts_, **over):
    q = {"schema_version": "0.3", "answer_profile": "standard", "language": "text", "id": "diff-reading-one", "locale": "en",
         "type": "output", "level": "beginner", "difficulty": 0.2, "concepts": ["py.values"], "prompt": "Synthetic prompt",
         "artifacts": list(artifacts_) or [artifact()], "options": options(), "explanation": "Synthetic explanation",
         "verification": {"status": "specified", "cases": [], "rationale": "Synthetic rationale"},
         "author": {"name": "Test"}, "license": "CC-BY-SA-4.0"}
    q.update(over)
    return q


def mixed(*artifacts_, **over):
    q = text_only(*artifacts_)
    q.update(language="python", snippets=[{"id": "main", "code": "print(1)"}])
    q["verification"] = {"status": "specified", "rationale": "Synthetic rationale",
                         "cases": [{"id": "one", "snippet_id": "main", "stdin": "", "expected": {"stdout": "1\n", "exit_code": 0}}]}
    q.update(over)
    return q


def codes(q):
    return {issue.code for issue in validate_question(q, PYTHON_MAP)}


class ArtifactAcceptTests(unittest.TestCase):
    def test_text_only_and_mixed_questions_are_valid(self):
        self.assertEqual(validate_question(text_only(), PYTHON_MAP), [])
        self.assertEqual(validate_question(mixed(), PYTHON_MAP), [])

    def test_every_kind_with_its_format(self):
        for kind in KINDS:
            with self.subTest(kind):
                self.assertEqual(validate_question(text_only(artifact("Line one\nLine two\n", kind)), PYTHON_MAP), [])

    def test_three_artifacts_and_the_exact_limits_are_valid(self):
        line = "x" * 49 + "\n"
        edge = line * 80                                   # 80 lines, 4000 code points
        self.assertEqual(len(edge), 4000)
        three = [artifact(edge, "text", "a-one"), artifact("b\n", "policy", "a-two"), artifact("c\n", "test-report", "a-three")]
        self.assertEqual(validate_question(text_only(*three), PYTHON_MAP), [])

    def test_the_hash_is_over_exact_utf8_bytes_with_no_normalisation(self):
        composed, decomposed = "café\n", "café\n"
        self.assertNotEqual(sha(composed), sha(decomposed))
        for text in (composed, decomposed, "tab\there\n", "no final newline"):
            self.assertEqual(validate_question(text_only(artifact(text, "text")), PYTHON_MAP), [], repr(text))
        # the hash of the other form is a different document
        self.assertIn("artifact-hash", codes(text_only(artifact(composed, "text", sha256=sha(decomposed)))))

    def test_legacy_versions_stay_valid_and_cannot_carry_artifacts(self):
        old = json.loads((ROOT / "public/data/questions/m1-assignment-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(old["schema_version"], "0.1")
        self.assertEqual(validate_question(old, PYTHON_MAP), [])
        with_artifacts = deepcopy(old)
        with_artifacts["artifacts"] = [artifact()]
        self.assertNotEqual(validate_question(with_artifacts, PYTHON_MAP), [], "0.1 with artifacts")
        two = deepcopy(with_artifacts)
        two["schema_version"], two["answer_profile"] = "0.2", "standard"
        self.assertNotEqual(validate_question(two, PYTHON_MAP), [], "0.2 with artifacts")


class ArtifactRejectTests(unittest.TestCase):
    def refused(self, q, code=None):
        issues = validate_question(q, PYTHON_MAP)
        self.assertTrue(issues, "expected a refusal")
        if code:
            self.assertIn(code, {i.code for i in issues}, [i.to_dict() for i in issues])

    def test_wrong_hash_binding_or_missing_provenance(self):
        self.refused(text_only(artifact(sha256="0" * 64)), "artifact-hash")
        self.refused(text_only(artifact(provenance={"fixture_id": "fixture-one", "fixture_output_sha256": "1" * 64})), "artifact-binding")
        broken = artifact()
        del broken["provenance"]
        self.refused(text_only(broken))
        for bad in ("A" * 64, "a" * 63, "g" * 64, ""):
            self.refused(text_only(artifact(sha256=bad)))
        self.refused(text_only(artifact(provenance={"fixture_id": "fixture-one"})))
        self.refused(text_only(artifact(provenance={"fixture_id": "fixture-one", "fixture_output_sha256": sha(DIFF), "path": "/tmp/x"})))
        self.refused(text_only(artifact(provenance={"fixture_id": "Bad Id", "fixture_output_sha256": sha(DIFF)})))

    def test_kind_and_format(self):
        wrong_kind = text_only()
        wrong_kind["artifacts"][0]["kind"] = "python"
        self.refused(wrong_kind)
        self.refused(text_only(artifact(format="plain")))                      # a diff is unified-diff
        self.refused(text_only(artifact("t\n", "text", format="unified-diff")))
        self.refused(text_only(artifact("t\n", "http-transcript", format="plain")))

    def test_no_key_can_carry_a_reference_or_a_label_that_runs(self):
        for key, value in (("url", "https://example.com/"), ("src", "x"), ("href", "x"), ("html", "<b>"), ("language", "python"),
                           ("run", True), ("snippet", "main"), ("expected", {"stdout": ""})):
            with self.subTest(key):
                self.refused(text_only(artifact(**{key: value})))

    def test_sizes_counts_and_ids(self):
        self.refused(text_only(artifact("x" * 4001, "text")))
        self.refused(text_only(artifact("a\n" * 81, "text")), "artifact-limits")
        self.refused(text_only(artifact("a\n" * 80 + "b", "text")), "artifact-limits")     # 81 lines, the last one unterminated
        self.assertEqual(validate_question(text_only(artifact("a\n" * 79 + "b", "text")), PYTHON_MAP), [])
        self.refused(text_only(artifact("", "text")))
        self.refused(text_only(artifact("   \n", "text")))
        self.refused(text_only(*[artifact("t\n", "text", f"a-{i}") for i in range(4)]))
        q = text_only()
        q["artifacts"] = []
        self.refused(q)
        self.refused(text_only(artifact("a\n", "text", "same"), artifact("b\n", "text", "same")), "duplicate")
        self.refused(text_only(artifact(id="Bad Id")))
        self.refused(mixed(artifact(id="main")), "artifact-id")                # an id may not repeat a snippet's id

    def test_a_repeated_artifact_id_is_refused_by_every_entry_point(self):
        twin = text_only(artifact("a\n", "text", "same"), artifact("b\n", "text", "same"))
        self.refused(twin, "duplicate")                                            # the whole-question validation
        self.assertEqual([i.code for i in artifacts.artifact_issues(twin)], ["artifact-id"], "and the artifact checks on their own")
        for fixture_bytes in (b"a\n", b"b\n"):                                     # neither twin can be verified as "the first one"
            self.assertEqual([i.code for i in artifacts.verify_artifact_fixture(twin, "same", fixture_bytes)], ["artifact-id"])

    def test_text_content_rules(self):
        for label, text in (("carriage return", "a\r\nb\n"), ("NUL", "a\u0000b"), ("bell", "a\u0007b"), ("DEL", "a\u007fb"),
                            ("C1", "a\u0085b"), ("line separator", "a\u2028b"), ("paragraph separator", "a\u2029b")):
            with self.subTest(label):
                self.refused(text_only(artifact(text, "text")))
        lone = "a\ud800b"                                                       # not encodable as UTF-8
        q = text_only(artifact("placeholder\n", "text", sha256="0" * 64))
        q["artifacts"][0]["text"] = lone
        self.refused(q, "artifact-text")

    def test_the_url_policy(self):
        # The same cases are used by the browser's check (web-tests/artifacts.test.mjs), so the two cannot drift apart.
        cases = json.loads((ROOT / "web-tests/fixtures/artifact-url-cases.json").read_text(encoding="utf-8"))
        for text in cases["reserved"]:
            with self.subTest(reserved=text):
                self.assertEqual(artifacts.url_findings(text), [], text)
                self.assertEqual(validate_question(text_only(artifact(text + "\n", "http-transcript")), PYTHON_MAP), [], text)
        for text in cases["refused"]:
            with self.subTest(refused=text):
                self.assertTrue(artifacts.url_findings(text), text)
                self.refused(text_only(artifact(text + "\n", "http-transcript")), "artifact-url")

    def test_a_label_counts_code_points_and_is_valid_unicode(self):
        astral = "\U0001F600"
        self.assertEqual(validate_question(text_only(artifact(label=astral * 80)), PYTHON_MAP), [], "80 code points, whatever their UTF-16 length")
        self.refused(text_only(artifact(label=astral * 81)))
        q = text_only(artifact())
        q["artifacts"][0]["label"] = "a\ud800b"
        self.refused(q, "artifact-text")
        for bad in ("", "  ", "a\nb", "a\u0007b", "a\u2028b"):
            self.refused(text_only(artifact(label=bad)))

    def test_language_and_snippets_cannot_disguise_text(self):
        self.refused(text_only(language="python"))                              # no Python label on text-only
        self.refused(mixed(language="text"))                                    # language text carries no code
        q = text_only()
        q["snippets"] = [{"id": "main", "code": "print(1)"}]
        self.refused(q)
        no_content = text_only()
        del no_content["artifacts"]
        self.refused(no_content)
        self.refused({k: v for k, v in text_only().items() if k != "answer_profile"})     # 0.3 needs an explicit profile
        self.refused(text_only(schema_version="0.4"))

    def test_a_mixed_question_still_needs_its_snippet_verified(self):
        q = mixed()
        q["verification"]["cases"] = []
        self.refused(q)
        q["verification"]["cases"] = [{"id": "one", "snippet_id": "nowhere", "stdin": "", "expected": {"stdout": "1\n", "exit_code": 0}}]
        self.refused(q, "unknown-snippet")

    def test_no_network_is_ever_touched_for_a_url_named_in_the_text(self):
        import socket
        from unittest import mock
        text = "GET https://example.com/a HTTP/1.1\nHost: localhost\n\nsee http://127.0.0.1:8000/ and http://evil.net/x\n"
        with mock.patch.object(socket, "socket", side_effect=AssertionError("no network")), \
                mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("no DNS")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("no process")):
            issues = validate_question(text_only(artifact(text, "http-transcript")), PYTHON_MAP)
        self.assertEqual([i.code for i in issues], ["artifact-url"], "the unreserved URL is a finding, found without a lookup")

    def test_a_case_cannot_be_bound_to_an_artifact(self):
        q = text_only()
        q["verification"]["cases"] = [{"id": "one", "snippet_id": "change", "stdin": "", "expected": {"stdout": "x", "exit_code": 0}}]
        self.refused(q, "unknown-snippet")


class FixtureBindingTests(unittest.TestCase):
    def test_a_changed_fixture_output_is_a_binding_error(self):
        q = text_only()
        fixture = DIFF.encode("utf-8")
        self.assertEqual(artifacts.verify_artifact_fixture(q, "change", fixture), [])
        changed = artifacts.verify_artifact_fixture(q, "change", fixture.replace(b"+ 1", b"+ 2"))
        self.assertEqual([i.code for i in changed], ["artifact-binding"])
        self.assertEqual([i.code for i in artifacts.verify_artifact_fixture(q, "change", b"")], ["artifact-binding"])
        self.assertEqual([i.code for i in artifacts.verify_artifact_fixture(q, "nope", fixture)], ["artifact-id"])

    def test_fixture_bytes_are_hashed_exactly(self):
        q = text_only(artifact("a\n", "text"))
        self.assertEqual(artifacts.verify_artifact_fixture(q, "change", b"a\n"), [])
        self.assertTrue(artifacts.verify_artifact_fixture(q, "change", b"a\r\n"))        # no newline normalisation
        self.assertTrue(artifacts.verify_artifact_fixture(q, "change", b"a"))
        self.assertTrue(artifacts.verify_artifact_fixture(q, "change", b"\xef\xbb\xbfa\n"))  # a BOM is a different document

    def test_hash_helper_matches_the_definition(self):
        self.assertEqual(artifacts.artifact_sha256("café\n"), sha("café\n"))
        with self.assertRaises(UnicodeEncodeError):
            artifacts.artifact_sha256("a\ud800")


class ExportAndTraceTests(CourseFixture):
    """A text-only question inside a course: exported only against the fixture's real output bytes, indexed, never traced."""
    QID = "diff-reading-one"
    FIXTURE = "content/fixtures/artifacts/fixture-one.out"

    def write_fixture(self, data=DIFF.encode("utf-8"), name="fixture-one"):
        target = self.root / "content/fixtures/artifacts" / f"{name}.out"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def add_text_question(self, fixture=True, **over):
        q = text_only(id=self.QID, **over)
        q["options"] = make_question(self.maps["python"], self.QID, "py.values")["options"]
        self.write(self.data / "questions" / f"{self.QID}.json", q)
        course = self.courses["python"]
        course["levels"][0]["lessons"][0]["questions"].append(self.QID)
        self.write(self.data / "courses/python.json", course)
        row = next(c for c in self.catalog["courses"] if c["id"] == "python")
        row["free_questions"] = 2
        self.write(self.data / "catalog.json", self.catalog)
        if fixture:
            self.write_fixture()
        return q

    def refuses(self, needle, code=1):
        got, output = self.run_export()
        self.assertEqual(got, code, output)
        self.assertIn(needle, output)

    def test_a_text_only_question_exports_and_is_indexed(self):
        self.add_text_question()
        code, output = self.run_export()
        self.assertEqual(code, 0, output)
        index = json.loads((self.data / "index.json").read_text(encoding="utf-8"))
        entry = next(e for e in index["questions"] if e["id"] == self.QID)
        self.assertEqual(entry["answer_profile"], "standard")
        self.assertEqual(self.run_export("--check")[0], 0)

    def test_the_export_reads_the_fixture_bytes_and_nothing_else_is_proof(self):
        # matching hash fields in the question are not enough: with no fixture file the export stops
        self.add_text_question(fixture=False)
        self.refuses("artifact-fixture")
        # the file exists but its bytes are not the recorded output: the smallest change, a line ending, a BOM, a missing newline
        for label, data in {"changed byte": DIFF.replace("+1", "+2").encode(), "CRLF": DIFF.replace("\n", "\r\n").encode(), "BOM": b"\xef\xbb\xbf" + DIFF.encode(),
                            "no final newline": DIFF.rstrip("\n").encode(), "empty": b"", "NFD instead of NFC": "caf\u00e9\n".encode()}.items():
            with self.subTest(label):
                self.write_fixture(data)
                self.refuses("artifact-binding")
        self.write_fixture()
        self.assertEqual(self.run_export()[0], 0, "restoring the real output makes the same question pass")

    def test_the_fixture_is_found_only_in_the_trusted_directory_by_id(self):
        self.add_text_question(fixture=False)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "fixture-one.out").write_bytes(DIFF.encode("utf-8"))
        (self.root / "content/fixtures").mkdir(parents=True, exist_ok=True)
        self.refuses("artifact-fixture")                              # a file outside content/fixtures/artifacts does not count
        # a path in place of an id is a schema error long before any file is opened
        q = text_only(id=self.QID)
        q["artifacts"][0]["provenance"]["fixture_id"] = "../elsewhere/fixture-one"
        q["options"] = make_question(self.maps["python"], self.QID, "py.values")["options"]
        self.write(self.data / "questions" / f"{self.QID}.json", q)
        self.refuses("schema")

    def test_a_symlink_on_the_way_to_the_fixture_stops_the_export(self):
        real = self.root / "real-fixtures"
        real.mkdir()
        (real / "fixture-one.out").write_bytes(DIFF.encode("utf-8"))
        self.add_text_question(fixture=False)
        (self.root / "content/fixtures").mkdir(parents=True)
        (self.root / "content/fixtures/artifacts").symlink_to(real, target_is_directory=True)
        self.refuses("symlink", code=2)
        (self.root / "content/fixtures/artifacts").unlink()
        self.write_fixture()
        self.assertEqual(self.run_export()[0], 0)
        target = self.root / self.FIXTURE
        target.unlink()
        target.symlink_to(real / "fixture-one.out")
        self.refuses("symlink", code=2)

    def test_an_oversized_fixture_is_refused_before_it_is_read_whole(self):
        self.add_text_question()
        self.write_fixture(b"x" * 20001)
        self.refuses("artifact-fixture")

    def test_the_export_refuses_a_wrong_hash_or_binding_or_a_url(self):
        for label, mutate in {
            "hash": lambda q: q["artifacts"][0].update(sha256="0" * 64),
            "binding": lambda q: q["artifacts"][0]["provenance"].update(fixture_output_sha256="1" * 64),
            "url": lambda q: q["artifacts"][0].update(text="GET https://evil.net/x\n", sha256=sha("GET https://evil.net/x\n"),
                                                     provenance={"fixture_id": "fixture-one", "fixture_output_sha256": sha("GET https://evil.net/x\n")}, kind="text", format="plain"),
        }.items():
            with self.subTest(label):
                self.setUp()
                q = self.add_text_question()
                mutate(q)
                self.write(self.data / "questions" / f"{self.QID}.json", q)
                code, output = self.run_export()
                self.assertEqual(code, 1, output)
                self.assertIn({"hash": "artifact-hash", "binding": "artifact-binding", "url": "artifact-url"}[label], output)

    def test_the_export_refuses_two_artifacts_with_one_id(self):
        q = self.add_text_question()
        q["artifacts"] = [artifact("a\n", "text", "same", provenance={"fixture_id": "fixture-one", "fixture_output_sha256": sha("a\n")}),
                          artifact("b\n", "text", "same", provenance={"fixture_id": "fixture-two", "fixture_output_sha256": sha("b\n")})]
        self.write(self.data / "questions" / f"{self.QID}.json", q)
        self.write_fixture(b"a\n", "fixture-one")
        self.write_fixture(b"b\n", "fixture-two")
        code, output = self.run_export()
        self.assertEqual(code, 1, output)
        self.assertIn("/artifacts/1/id", output)

    def test_trace_check_expects_no_trace_for_a_text_only_question_and_calls_a_stray_one_an_orphan(self):
        from validator import trace
        self.add_text_question()
        stream = io.StringIO()
        with redirect_stdout(stream):
            trace.main(["--check", "--root", str(self.root)])
        self.assertNotIn(self.QID, stream.getvalue(), "no missing-trace complaint for a question without code")
        stray = self.data / "traces" / f"{self.QID}.json"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text("{}", encoding="utf-8")
        stream = io.StringIO()
        with redirect_stdout(stream):
            trace.main(["--check", "--root", str(self.root)])
        self.assertIn(f"{stray}: orphan trace", stream.getvalue())


class OnePolicyTwoImplementationsTests(unittest.TestCase):
    """The reserved hosts and the limits exist in Python and in JavaScript; they must be the same lists, not two lists."""

    def js(self):
        return (ROOT / "public/js/artifacts.js").read_text(encoding="utf-8")

    def test_the_reserved_suffixes_and_documentation_nets_are_identical(self):
        import re
        source = self.js()
        suffixes = re.search(r"RESERVED_SUFFIXES = \[(.*?)\];", source).group(1)
        self.assertEqual(re.findall(r"'([^']+)'", suffixes), list(artifacts._RESERVED_SUFFIXES))
        nets = re.search(r"DOCUMENTATION_NETS = \[(.*?)\];\n", source, re.S).group(1)
        js_nets = [tuple(int(n) for n in group.split(",")) for group in re.findall(r"\[([0-9, ]+)\]", nets)]
        py_nets = [tuple(int(o) for o in str(net.network_address).split(".")[:3]) for net in artifacts._DOC_NETS]
        self.assertEqual(js_nets, py_nets)

    def test_the_limits_are_identical(self):
        import re
        source = self.js()
        limits = dict(re.findall(r"(\w+): (\d+)", re.search(r"ARTIFACT_LIMITS = \{(.*?)\}", source).group(1)))
        self.assertEqual(int(limits["lines"]), artifacts.MAX_LINES)
        self.assertEqual(int(limits["chars"]), 4000)
        self.assertEqual(int(limits["count"]), 3)
        schema = json.loads((ROOT / "schema/question.schema.json").read_text(encoding="utf-8"))
        item = schema["$defs"]["artifact"]["properties"]
        self.assertEqual((item["text"]["maxLength"], schema["properties"]["artifacts"]["maxItems"], item["label"]["maxLength"]),
                         (int(limits["chars"]), int(limits["count"]), int(limits["label"])))


if __name__ == "__main__":
    unittest.main()
