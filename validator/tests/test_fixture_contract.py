# SPDX-License-Identifier: MIT
"""Fixture contract: static binding of a 0.3 question to one harness manifest (schema v1.5).

All fixtures are synthetic; no process, network, runner or trace generator is used."""
from copy import deepcopy
import difflib
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from validator import core
from validator import fixture_contract as fc

DIFF = ("--- a/total.py\n+++ b/total.py\n@@ -1,2 +1,2 @@\n"
        " def total(xs):\n-    return sum(xs)\n+    return sum(xs) + 1\n")
CODE = "print(1)\n"
STDOUT = "1\n"
TEXT = "A short inert text.\n"
CC = "CC-BY-SA-4.0"
PROPRIETARY = "LicenseRef-Proprietary"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def sha_text(text):
    return sha(text.encode("utf-8"))


TRACE = json.dumps({
    "schema_version": "0.3",
    "question_id": "q-1",
    "traces": [{
        "snippet_id": "snip-1", "source_sha256": sha_text(CODE),
        "case_id": "case-1", "stdin": "", "stdin_sha256": sha_text(""),
        "steps": [], "stdout": STDOUT, "exception": None, "truncated": False,
    }],
}, separators=(",", ":")).encode()


def question_python():
    return {
        "schema_version": "0.3",
        "id": "q-1",
        "language": "python",
        "license": CC,
        "snippets": [{"id": "snip-1", "code": CODE}],
        "verification": {"status": "specified", "cases": [{
            "id": "case-1", "snippet_id": "snip-1", "stdin": "",
            "expected": {"stdout": STDOUT, "exit_code": 0},
        }]},
        "artifacts": [{
            "id": "change", "kind": "diff", "format": "unified-diff", "text": DIFF,
            "sha256": sha_text(DIFF),
            "provenance": {"fixture_id": "fixture-one",
                           "fixture_output_sha256": sha_text(DIFF)},
        }],
    }


def question_text():
    return {
        "schema_version": "0.3", "id": "q-1", "language": "text", "license": CC,
        "verification": {"status": "specified", "cases": []},
        "artifacts": [{
            "id": "passage", "kind": "text", "format": "plain", "text": TEXT,
            "sha256": sha_text(TEXT),
            "provenance": {"fixture_id": "fixture-one",
                           "fixture_output_sha256": sha_text(TEXT)},
        }],
    }


def manifest(question):
    config = {"env": {"TZ": "UTC", "LC_ALL": "C"}, "settings": ["core.autocrlf=false"]}
    text_mode = question["language"] == "text"
    artifact_bytes = TEXT.encode() if text_mode else DIFF.encode()
    artifact = {
        "artifact_id": "text-1" if text_mode else "diff-1",
        "type": "text" if text_mode else "diff",
        "format": "text",
        "media_type": "text/plain" if text_mode else "text/x-diff",
        "encoding": "utf-8", "executable": False, "is_trace": False,
        "sha256": sha(artifact_bytes), "bytes": len(artifact_bytes),
        "truncated": False, "complete": True,
    }
    artifacts = [artifact]
    if not text_mode:
        artifacts.append({
            "artifact_id": "trace-1", "type": "trace", "format": "json",
            "media_type": "application/json", "encoding": "utf-8",
            "executable": False, "is_trace": True,
            "sha256": sha(TRACE), "bytes": len(TRACE),
            "truncated": False, "complete": True,
        })
    case = {
        "case_id": "case-1" if not text_mode else "case-text",
        "snippet_id": None if text_mode else "snip-1",
        "source_sha256": sha(b"fixture-bytes") if text_mode else sha_text(CODE),
        "stdin_sha256": sha(b""),
        "expected": {"stdout_sha256": sha_text(STDOUT) if not text_mode else sha(artifact_bytes),
                     "exception": None},
        "actual": {"stdout_sha256": sha_text(STDOUT) if not text_mode else sha(artifact_bytes),
                   "exception": None},
        "outcome": "passed", "truncated": False, "complete": True,
    }
    artifact_total = sum(item["bytes"] for item in artifacts)
    value = {
        "manifest_schema_version": "1.0.0",
        "binding_projection_version": "1",
        "batch_id": "git-pilot-1",
        "question_id": "q-1",
        "bindings": {
            "question_ids": ["q-1"],
            "harness_version": "0.1.0-template",
            "tool_versions": {"git": "2.45.0", "python": "3.12.0", "pytest": "8.2.0"},
            "runtime_image_sha256": sha(b"runtime"),
            "script_sha256": sha(b"script"),
            "config_digest": fc.compute_config_digest(config),
            "fixture_sha256": sha(b"fixture-bytes"),
            "argv": ["git", "-C", "/work/repo", "diff", "--no-color"],
        },
        "inputs": [
            {"role": "fixture", "path": "fixtures/repo.tar", "sha256": sha(b"fixture-bytes"),
             "bytes": len(b"fixture-bytes")},
            {"role": "source", "path": "src/snippet.py", "sha256": sha_text(CODE),
             "bytes": len(CODE.encode())},
            {"role": "stdin", "path": "stdin/case-1.txt", "sha256": sha(b""), "bytes": 0},
            {"role": "config", "path": "config/pinned.json",
             "sha256": sha(fc.canonical_json_bytes(config)),
             "bytes": len(fc.canonical_json_bytes(config))},
        ],
        "config": config,
        "canonicalisation": {"mode": "none"},
        "cases": [case],
        "artifacts": artifacts,
        "test_report": {
            "schema_version": "1.0.0", "tool": "pytest", "tool_version": "8.2.0",
            "counts": {"total": 1, "passed": 1, "failed": 0, "skipped": 0,
                       "xfail": 0, "xpass": 0},
            "tests": [{"test_id": "test_1", "outcome": "passed"}],
        },
        "licence": {"license": CC, "gate": "draft-only"},
        "receipt": {
            "started_at": "1970-01-01T00:00:00Z", "duration_ms": 0, "exit_code": 0,
            "stdout_sha256": fc.EMPTY_HASH, "stdout_bytes": 0,
            "stderr_sha256": fc.EMPTY_HASH, "stderr_bytes": 0,
            "budget": {"wall_seconds": 10, "cpu_soft_seconds": 4, "cpu_hard_seconds": 5,
                       "memory_bytes": 268435456, "pids": 32, "output_bytes_per_stream": 1048576,
                       "artifact_bytes": 4194304, "artifact_files": 64, "scratch_bytes": 67108864,
                       "scratch_inodes": 4096, "open_fds": 64, "core_bytes": 0},
            "isolation": {"mechanism": "bwrap+cgroup-v2",
                          "namespaces": ["user", "mount", "pid", "net", "ipc", "uts"],
                          "capabilities": "none", "no_new_privileges": True,
                          "network": "denied", "kill": "whole-cgroup"},
            "usage": {"wall_ms": 0, "cpu_ms": 0, "memory_peak_bytes": 0, "pids_peak": 0,
                      "stdout_bytes": 0, "stderr_bytes": 0, "artifact_bytes": artifact_total,
                      "scratch_bytes": 0, "scratch_inodes": 0, "open_fds_peak": 0},
            "limit_hit": [], "status": "ok",
        },
    }
    value["binding_sha256"] = fc.compute_binding_sha256(value)
    return value


def contract(question=None):
    question = question or question_python()
    value = manifest(question)
    binding = {
        "schema_version": "1.0.0",
        "manifest_uri": "batch/manifest.json",
        "manifest_sha256": sha(json.dumps(value).encode()),
        "artifact_uris": {
            item["artifact_id"]: f"batch/artifacts/{item['artifact_id']}"
            for item in value["artifacts"]
        },
        "questions": [{
            "question_uri": "content/questions/q-1.json",
            "question_sha256": sha(json.dumps(question).encode()),
            "license": CC,
            "artifact_ids": [item["artifact_id"] for item in value["artifacts"]]
            if question["language"] == "text" else [],
            "cases": [] if question["language"] == "text" else [{
                "case_id": "case-1", "case_index": 0,
                "artifact_ids": ["diff-1"], "trace_artifact_id": "trace-1",
            }],
        }],
    }
    artifacts = {item["artifact_id"]: (TEXT if item["artifact_id"] == "text-1" else
                                       DIFF if item["artifact_id"] == "diff-1" else TRACE)
                 for item in value["artifacts"]}
    return {
        "manifest": value, "question": question, "binding": binding,
        "artifact_bytes": {key: value.encode() if isinstance(value, str) else value
                           for key, value in artifacts.items()},
        "fixture_bytes": lambda fixture_id: (TEXT if question["language"] == "text"
                                             else DIFF).encode() if fixture_id == "fixture-one" else None,
    }


def review_contract():
    """A V1/V4/V6-style Review pilot: two Python snippets, one explicit diff artifact."""
    case = contract()
    question_value = case["question"]
    before = "def fee(n):\n    return 0 if n == 0 else 4\n"
    after = "def fee(count):\n    return 4\n"
    question_value["snippets"] = [
        {"id": "fee-before", "code": before},
        {"id": "fee-after", "code": after},
    ]
    question_value["verification"]["cases"] = [
        {"id": "case-before", "snippet_id": "fee-before", "stdin": "",
         "expected": {"stdout": "0\n", "exit_code": 0}},
        {"id": "case-after", "snippet_id": "fee-after", "stdin": "",
         "expected": {"stdout": "4\n", "exit_code": 0}},
    ]
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        fromfile="a/fee.py", tofile="b/fee.py"))
    artifact = question_value["artifacts"][0]
    artifact.update({"kind": "diff", "format": "unified-diff", "text": diff,
                     "sha256": sha_text(diff)})
    artifact["provenance"]["fixture_output_sha256"] = sha_text(diff)

    manifest_value = case["manifest"]
    content = next(item for item in manifest_value["artifacts"] if item["type"] != "trace")
    content.update({"sha256": sha_text(diff), "bytes": len(diff.encode())})

    def case_entry(case_id, snippet_id, source, stdout):
        projection = {"stdout_sha256": sha_text(stdout), "exception": None}
        return {"case_id": case_id, "snippet_id": snippet_id,
                "source_sha256": sha(source.encode("utf-8")), "stdin_sha256": sha(b""),
                "expected": dict(projection), "actual": dict(projection),
                "outcome": "passed", "truncated": False, "complete": True}

    manifest_value["cases"] = [
        case_entry("case-before", "fee-before", before, "0\n"),
        case_entry("case-after", "fee-after", after, "4\n"),
    ]
    manifest_value["test_report"].update({
        "counts": {"total": 2, "passed": 2, "failed": 0, "skipped": 0,
                   "xfail": 0, "xpass": 0},
        "tests": [{"test_id": "test_before", "outcome": "passed"},
                  {"test_id": "test_after", "outcome": "passed"}],
    })
    manifest_value["receipt"]["usage"]["artifact_bytes"] = len(diff.encode()) + len(TRACE)
    manifest_value["binding_sha256"] = fc.compute_binding_sha256(manifest_value)

    entry = case["binding"]["questions"][0]
    entry["cases"] = [
        {"case_id": "case-before", "case_index": 0, "artifact_ids": ["diff-1"],
         "trace_artifact_id": None},
        {"case_id": "case-after", "case_index": 1, "artifact_ids": ["diff-1"],
         "trace_artifact_id": None},
    ]
    entry["review"] = {"before_snippet_id": "fee-before", "after_snippet_id": "fee-after",
                       "diff_artifact_id": "change", "fromfile": "a/fee.py",
                       "tofile": "b/fee.py"}
    entry["gate"] = {"chapter": "V1", "objective": "V1.O2",
                     "pilot": "review-pilot-1", "status": "draft-pilot"}
    case["artifact_bytes"]["diff-1"] = diff.encode()
    case["fixture_bytes"] = (lambda fixture_id: diff.encode()
                             if fixture_id == "fixture-one" else None)
    case["diff"] = diff
    return case


def run(case, *, sync_manifest=True, sync_question=True):
    manifest_value = case["manifest"]
    question_value = case["question"]
    binding = case["binding"]
    mraw = json.dumps(manifest_value).encode()
    qraw = json.dumps(question_value).encode()
    if sync_manifest:
        binding["manifest_sha256"] = sha(mraw)
    if sync_question:
        binding["questions"][0]["question_sha256"] = sha(qraw)
    braw = json.dumps(binding).encode()
    return fc.validate(manifest_value, question_value, binding, manifest_bytes=mraw,
                       question_bytes=qraw, artifact_bytes=case["artifact_bytes"],
                       fixture_bytes=case["fixture_bytes"], binding_bytes=braw,
                       expected_binding_sha256=sha(braw))


def codes(result):
    return {issue.code for issue in result.issues}


def replace_artifact(case, text, kind, fmt, k3):
    """Replace the single content artifact and keep both sides byte-identical."""
    question = case["question"]
    artifact = question["artifacts"][0]
    artifact.update({"kind": kind, "format": fmt, "text": text,
                     "sha256": sha_text(text)})
    artifact["provenance"]["fixture_output_sha256"] = sha_text(text)
    manifest_value = case["manifest"]
    content = next(item for item in manifest_value["artifacts"] if item["type"] != "trace")
    content.update({"type": k3[0], "format": k3[1], "media_type": k3[2],
                    "sha256": sha_text(text), "bytes": len(text.encode())})
    if question["language"] == "text":
        manifest_value["cases"][0]["expected"]["stdout_sha256"] = sha_text(text)
        manifest_value["cases"][0]["actual"]["stdout_sha256"] = sha_text(text)
    manifest_value["receipt"]["usage"]["artifact_bytes"] = sum(
        item["bytes"] for item in manifest_value["artifacts"])
    manifest_value["binding_sha256"] = fc.compute_binding_sha256(manifest_value)
    case["artifact_bytes"][content["artifact_id"]] = text.encode()
    case["fixture_bytes"] = (lambda fixture_id: text.encode()
                             if fixture_id == "fixture-one" else None)


def replace_trace(case, raw):
    trace = next(item for item in case["manifest"]["artifacts"] if item["type"] == "trace")
    old = trace["bytes"]
    trace.update({"sha256": sha(raw), "bytes": len(raw)})
    case["artifact_bytes"]["trace-1"] = raw
    case["manifest"]["receipt"]["usage"]["artifact_bytes"] += len(raw) - old
    case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])


def exception_contract():
    case = contract()
    question_case = case["question"]["verification"]["cases"][0]
    question_case["expected"] = {"stdout": "", "exit_code": 1,
                                 "stderr_contains": "bad literal"}
    manifest_case = case["manifest"]["cases"][0]
    stderr = "bad literal\n"
    case["manifest"]["artifacts"].append({
        "artifact_id": "stderr-1", "type": "text", "format": "text",
        "media_type": "text/plain", "encoding": "utf-8", "executable": False,
        "is_trace": False, "sha256": sha_text(stderr), "bytes": len(stderr.encode()),
        "truncated": False, "complete": True})
    case["manifest"]["receipt"]["usage"]["artifact_bytes"] += len(stderr.encode())
    case["artifact_bytes"]["stderr-1"] = stderr.encode()
    case["binding"]["questions"][0]["cases"][0]["artifact_ids"].append("stderr-1")
    case["binding"]["questions"][0]["cases"][0]["trace_artifact_id"] = None
    exception = {"type": "ValueError", "message": "bad literal",
                 "stderr_artifact_id": "stderr-1"}
    manifest_case["expected"] = {"stdout_sha256": sha_text(""), "exception": exception}
    manifest_case["actual"] = {"stdout_sha256": sha_text(""), "exception": dict(exception)}
    manifest_case["stdin_sha256"] = sha_text("")
    case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
    return case


def add_stderr_artifact(case, artifact_id="stderr-2", text="bad literal\n"):
    raw = text.encode()
    case["manifest"]["artifacts"].append({
        "artifact_id": artifact_id, "type": "text", "format": "text",
        "media_type": "text/plain", "encoding": "utf-8", "executable": False,
        "is_trace": False, "sha256": sha(raw), "bytes": len(raw),
        "truncated": False, "complete": True})
    case["manifest"]["receipt"]["usage"]["artifact_bytes"] += len(raw)
    case["artifact_bytes"][artifact_id] = raw
    case["binding"]["questions"][0]["cases"][0]["artifact_ids"].append(artifact_id)
    return case["manifest"]["artifacts"][-1]


class ValidContractTest(unittest.TestCase):
    def test_git_diff_contract_is_verified(self):
        result = run(contract())
        self.assertTrue(result.verified, [issue.to_dict() for issue in result.issues])
        self.assertEqual(result.licences[0]["license"], CC)
        self.assertEqual(result.licences[0]["gate"], "draft-only")

    def test_text_contract_is_verified_without_trace(self):
        result = run(contract(question_text()))
        self.assertTrue(result.verified, [issue.to_dict() for issue in result.issues])
        self.assertEqual(result.licences[0]["license"], CC)

class CourseFixtureTest(unittest.TestCase):
    """One representative static fixture per course; none of them runs a fixture."""

    def test_http_transcript_is_a_text_artifact(self):
        transcript = ("GET /items HTTP/1.1\nHost: example.test\n\n"
                      "HTTP/1.1 200 OK\nContent-Type: application/json\n\n[]\n")
        case = contract(question_text())
        replace_artifact(case, transcript, "http-transcript", "http-1.1-transcript",
                         ("http-transcript", "text", "text/plain"))
        self.assertTrue(run(case).verified)

    def test_testing_report_is_display_text_not_json(self):
        report = "PASS test_a\nFAIL test_b: assert 3 == 4\n1 passed, 1 failed\n"
        case = contract(question_text())
        replace_artifact(case, report, "test-report", "plain",
                         ("test-report", "text", "text/plain"))
        self.assertTrue(run(case).verified)

    def test_review_diff_is_the_unified_diff_of_bound_bytes(self):
        before = "def total(xs):\n    return sum(xs)\n"
        after = "def total(xs):\n    return sum(xs) + 1\n"
        diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                             fromfile="a/total.py", tofile="b/total.py"))
        case = contract()
        replace_artifact(case, diff, "diff", "unified-diff",
                         ("diff", "text", "text/x-diff"))
        self.assertEqual(case["question"]["artifacts"][0]["text"], diff)
        self.assertTrue(run(case).verified)

    def test_expected_exception_is_explicit(self):
        case = exception_contract()
        self.assertTrue(run(case).verified, [issue.to_dict() for issue in run(case).issues])

    def test_unexpected_exception_is_not_a_pass(self):
        case = contract()
        question_case = case["question"]["verification"]["cases"][0]
        question_case["expected"] = {"stdout": "", "exit_code": 1,
                                     "stderr_contains": "bad literal"}
        manifest_case = case["manifest"]["cases"][0]
        manifest_case["expected"]["stdout_sha256"] = sha_text("")
        manifest_case["actual"]["stdout_sha256"] = sha_text("")
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-missing", codes(run(case)))


class ExactControlTest(unittest.TestCase):
    """One minimal deviation per control; the assertion names the exact issue code."""

    def content_artifact(self, case):
        return next(item for item in case["manifest"]["artifacts"] if item["type"] != "trace")

    def test_question_artifact_hash_is_checked_on_its_own(self):
        case = contract()
        case["question"]["artifacts"][0]["sha256"] = fc.ZERO_HASH
        self.assertEqual(codes(run(case)), {"question-artifact-hash"})

    def test_question_provenance_hash_is_checked_on_its_own(self):
        case = contract()
        case["question"]["artifacts"][0]["provenance"]["fixture_output_sha256"] = fc.ZERO_HASH
        self.assertEqual(codes(run(case)), {"question-provenance-hash"})

    def test_ambiguous_manifest_artifact_is_unbound(self):
        case = contract()
        manifest_value = case["manifest"]
        duplicate = deepcopy(self.content_artifact(case))
        duplicate["artifact_id"] = "diff-2"
        manifest_value["artifacts"].append(duplicate)
        manifest_value["receipt"]["usage"]["artifact_bytes"] += duplicate["bytes"]
        manifest_value["binding_sha256"] = fc.compute_binding_sha256(manifest_value)
        case["binding"]["questions"][0]["cases"][0]["artifact_ids"].append("diff-2")
        case["artifact_bytes"]["diff-2"] = DIFF.encode()
        self.assertEqual(codes(run(case)), {"question-artifact-unbound"})

    def test_question_artifact_must_be_referenced(self):
        case = contract()
        case["binding"]["questions"][0]["cases"][0]["artifact_ids"] = ["trace-1"]
        self.assertEqual(codes(run(case)), {"question-artifact-not-referenced"})

    def test_question_artifact_mapping_checks_the_type(self):
        case = contract()
        self.content_artifact(case).update(type="text", format="text", media_type="text/plain")
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertEqual(codes(run(case)), {"question-artifact-mapping"})

    def test_question_artifact_mapping_checks_the_format(self):
        case = contract()
        self.content_artifact(case).update(format="json", media_type="application/json")
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertEqual(codes(run(case)), {"artifact-format", "question-artifact-mapping"})

    def test_question_artifact_mapping_checks_the_media_type(self):
        case = contract()
        self.content_artifact(case)["media_type"] = "text/plain"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertEqual(codes(run(case)), {"question-artifact-mapping"})

    def test_question_artifact_byte_count_is_checked(self):
        case = contract()
        self.content_artifact(case)["bytes"] += 1
        case["manifest"]["receipt"]["usage"]["artifact_bytes"] += 1
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertEqual(codes(run(case)), {"question-artifact-bytes", "artifact-bytes-mismatch"})

    def test_test_report_artifact_cannot_be_json(self):
        case = contract(question_text())
        artifact = case["question"]["artifacts"][0]
        artifact["kind"], artifact["format"] = "test-report", "plain"
        self.content_artifact(case).update(type="test-report", format="json",
                                           media_type="application/json")
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertEqual(codes(run(case)),
                         {"question-artifact-mapping", "test-report-artifact-format"})

    def test_text_question_with_a_case_is_rejected(self):
        case = contract(question_text())
        case["binding"]["questions"][0]["cases"] = [{
            "case_id": "case-text", "case_index": 0,
            "artifact_ids": ["text-1"], "trace_artifact_id": None,
        }]
        found = codes(run(case))
        self.assertIn("text-question-cases", found)

    def test_question_artifact_kind_is_checked(self):
        case = contract()
        case["question"]["artifacts"][0]["kind"] = "html"
        self.assertEqual(codes(run(case)), {"question-artifact-kind"})

    def test_question_artifact_lone_surrogate_is_rejected(self):
        case = contract()
        case["question"]["artifacts"][0]["text"] = "\ud800"
        self.assertEqual(codes(run(case)), {"question-artifact-encoding"})

    def test_text_question_needs_an_artifact(self):
        case = contract(question_text())
        case["question"]["artifacts"] = []
        self.assertEqual(codes(run(case)), {"question-text-artifacts"})


class SchemaParityTest(unittest.TestCase):
    def test_every_question_kind_format_pair_has_a_k3_mapping(self):
        schema = json.loads((core.ROOT / "schema/question.schema.json").read_text(encoding="utf-8"))
        pairs = {
            (clause["if"]["properties"]["kind"]["const"],
             clause["then"]["properties"]["format"]["const"])
            for clause in schema["$defs"]["artifact"]["allOf"]
        }
        self.assertEqual(pairs, set(fc.QUESTION_ARTIFACT_TO_MANIFEST))


class ReviewDiffTest(unittest.TestCase):
    def test_exact_unified_diff_is_accepted(self):
        case = review_contract()
        result = run(case)
        self.assertTrue(result.verified, [issue.to_dict() for issue in result.issues])

    def test_diff_must_match_the_bound_sources(self):
        case = review_contract()
        text = case["diff"] + "extra\n"
        artifact = case["question"]["artifacts"][0]
        artifact.update({"text": text, "sha256": sha_text(text)})
        artifact["provenance"]["fixture_output_sha256"] = sha_text(text)
        content = next(item for item in case["manifest"]["artifacts"] if item["type"] != "trace")
        content.update({"sha256": sha_text(text), "bytes": len(text.encode())})
        case["manifest"]["receipt"]["usage"]["artifact_bytes"] = len(text.encode()) + len(TRACE)
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        case["artifact_bytes"]["diff-1"] = text.encode()
        case["fixture_bytes"] = lambda fixture_id: text.encode()
        found = codes(run(case))
        self.assertIn("review-diff-mismatch", found)

    def test_both_sources_must_be_bound_cases(self):
        case = review_contract()
        case["binding"]["questions"][0]["cases"] = \
            case["binding"]["questions"][0]["cases"][:1]
        found = codes(run(case))
        self.assertIn("review-after-case", found)

    def test_before_source_must_also_be_bound(self):
        case = review_contract()
        case["binding"]["questions"][0]["cases"] = \
            case["binding"]["questions"][0]["cases"][1:]
        self.assertIn("review-before-case", codes(run(case)))

    def test_before_and_after_must_be_different_snippets(self):
        case = review_contract()
        case["binding"]["questions"][0]["review"]["after_snippet_id"] = "fee-before"
        self.assertIn("review-same-snippet", codes(run(case)))

    def test_review_snippets_must_end_with_a_newline(self):
        case = review_contract()
        case["question"]["snippets"][0]["code"] = \
            case["question"]["snippets"][0]["code"].rstrip("\n")
        self.assertIn("review-snippet-newline", codes(run(case)))

    def test_after_snippet_must_also_end_with_a_newline(self):
        case = review_contract()
        case["question"]["snippets"][1]["code"] = \
            case["question"]["snippets"][1]["code"].rstrip("\n")
        self.assertIn("review-snippet-newline", codes(run(case)))

    def test_diff_must_match_exactly_one_manifest_artifact(self):
        case = review_contract()
        duplicate = deepcopy(next(item for item in case["manifest"]["artifacts"]
                                    if item["type"] != "trace"))
        duplicate["artifact_id"] = "diff-2"
        case["manifest"]["artifacts"].append(duplicate)
        diff_bytes = case["diff"].encode()
        case["artifact_bytes"]["diff-2"] = diff_bytes
        case["manifest"]["receipt"]["usage"]["artifact_bytes"] += len(diff_bytes)
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("review-diff-unbound", codes(run(case)))

    def test_diff_artifact_must_be_a_diff(self):
        case = review_contract()
        case["question"]["artifacts"][0].update({"kind": "text", "format": "plain"})
        found = codes(run(case))
        self.assertIn("review-diff-artifact", found)

    def test_diff_must_be_referenced(self):
        case = review_contract()
        for item in case["binding"]["questions"][0]["cases"]:
            item["artifact_ids"] = ["trace-1"]
        found = codes(run(case))
        self.assertIn("review-diff-not-referenced", found)

    def test_header_injection_is_rejected(self):
        case = review_contract()
        case["binding"]["questions"][0]["review"]["fromfile"] = "a/fee.py\nX: y"
        found = codes(run(case))
        self.assertIn("review-header", found)


class GateTest(unittest.TestCase):
    def test_gate_is_part_of_the_receipt(self):
        result = run(review_contract())
        self.assertTrue(result.verified, [issue.to_dict() for issue in result.issues])
        self.assertEqual(result.gate["chapter"], "V1")
        self.assertEqual(result.gate["objective"], "V1.O2")
        self.assertEqual(result.to_dict()["gate"]["pilot"], "review-pilot-1")

    def test_review_needs_an_explicit_gate(self):
        case = review_contract()
        del case["binding"]["questions"][0]["gate"]
        self.assertIn("gate-missing", codes(run(case)))

    def test_gate_objective_must_match_the_chapter(self):
        case = review_contract()
        case["binding"]["questions"][0]["gate"]["objective"] = "V4.O2"
        self.assertIn("gate-objective", codes(run(case)))

    def test_gate_rejects_an_invented_course_tag(self):
        case = review_contract()
        case["binding"]["questions"][0]["gate"]["chapter"] = "CXX"
        self.assertIn("gate-chapter", codes(run(case)))

    def test_gate_is_draft_only(self):
        case = review_contract()
        case["binding"]["questions"][0]["gate"]["status"] = "released"
        self.assertIn("gate-status", codes(run(case)))


class StaleBytesTest(unittest.TestCase):
    def test_stale_source(self):
        case = contract()
        case["question"]["snippets"][0]["code"] += "# changed\n"
        self.assertIn("source-stale", codes(run(case)))

    def test_stale_stdin(self):
        case = contract()
        case["question"]["verification"]["cases"][0]["stdin"] = "changed"
        self.assertIn("stdin-stale", codes(run(case)))

    def test_stale_stdout(self):
        case = contract()
        case["question"]["verification"]["cases"][0]["expected"]["stdout"] = "2\n"
        case["question"]["verification"]["cases"][0]["expected"]["exit_code"] = 0
        self.assertIn("stdout-stale", codes(run(case)))

    def test_changed_artifact_text_no_longer_matches_the_manifest(self):
        case = contract()
        artifact = case["question"]["artifacts"][0]
        artifact["text"] = DIFF.replace("+1", "+2")
        artifact["sha256"] = sha_text(artifact["text"])
        artifact["provenance"]["fixture_output_sha256"] = sha_text(artifact["text"])
        case["fixture_bytes"] = lambda fixture_id: artifact["text"].encode()
        self.assertIn("question-artifact-unbound", codes(run(case)))

    def test_fixture_output_must_be_byte_identical(self):
        case = contract()
        case["fixture_bytes"] = lambda fixture_id: b"different\n"
        self.assertIn("fixture-bytes", codes(run(case)))

    def test_changed_manifest_bytes(self):
        case = contract()
        case["binding"]["manifest_sha256"] = sha(b"other")
        self.assertIn("manifest-hash-mismatch", codes(run(case, sync_manifest=False)))

    def test_changed_question_bytes(self):
        case = contract()
        case["binding"]["questions"][0]["question_sha256"] = sha(b"other")
        self.assertIn("question-hash-mismatch", codes(run(case, sync_question=False)))


class HashChainTest(unittest.TestCase):
    def test_binding_sha256_is_recomputed(self):
        case = contract()
        case["manifest"]["batch_id"] = "changed"
        self.assertIn("binding-hash-mismatch", codes(run(case)))

    def test_binding_sha256_cannot_be_zero(self):
        case = contract()
        case["manifest"]["binding_sha256"] = fc.ZERO_HASH
        self.assertIn("binding-hash", codes(run(case)))

    def test_config_digest_is_recomputed(self):
        case = contract()
        case["manifest"]["config"]["env"]["TZ"] = "Europe/Stockholm"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("config-digest-mismatch", codes(run(case)))

    def test_receipt_status_fails_even_with_unchanged_binding_hash(self):
        case = contract()
        case["manifest"]["receipt"]["status"] = "resource-exceeded"
        before = case["manifest"]["binding_sha256"]
        self.assertEqual(fc.compute_binding_sha256(case["manifest"]), before)
        self.assertIn("receipt-status", codes(run(case)))

    def test_receipt_limit_hit_fails_even_with_unchanged_binding_hash(self):
        case = contract()
        case["manifest"]["receipt"]["limit_hit"] = ["wall"]
        self.assertIn("receipt-limit-hit", codes(run(case)))

    def test_receipt_isolation_is_required(self):
        case = contract()
        case["manifest"]["receipt"]["isolation"]["network"] = "allowed"
        self.assertIn("receipt-isolation-network", codes(run(case)))


class CompletenessTest(unittest.TestCase):
    def test_truncated_case_cannot_be_passed(self):
        case = contract()
        item = case["manifest"]["cases"][0]
        item["truncated"], item["complete"] = True, False
        self.assertIn("case-truncated-passed", codes(run(case)))

    def test_truncated_artifact_cannot_be_complete(self):
        case = contract()
        item = case["manifest"]["artifacts"][0]
        item["truncated"], item["complete"] = True, False
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        found = codes(run(case))
        self.assertIn("artifact-not-complete", found)
        self.assertIn("question-artifact-complete", found)

    def test_failed_case_is_not_verified(self):
        case = contract()
        item = case["manifest"]["cases"][0]
        item["outcome"] = "failed"
        case["manifest"]["test_report"]["counts"].update({"passed": 0, "failed": 1})
        case["manifest"]["test_report"]["tests"][0]["outcome"] = "failed"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("case-not-passed", codes(run(case)))

    def test_all_skipped_is_not_facit(self):
        case = contract()
        item = case["manifest"]["cases"][0]
        item["outcome"] = "skipped"
        case["manifest"]["test_report"]["counts"].update({"passed": 0, "skipped": 1})
        case["manifest"]["test_report"]["tests"][0]["outcome"] = "skipped"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        found = codes(run(case))
        self.assertIn("case-not-passed", found)
        self.assertIn("test-passed", found)


class ArtifactAndTraceTest(unittest.TestCase):
    def test_test_report_counts_must_match_tests(self):
        case = contract()
        case["manifest"]["test_report"]["counts"]["passed"] = 2
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("test-count-mismatch", codes(run(case)))

    def test_test_report_total_must_match_tests(self):
        case = contract()
        case["manifest"]["test_report"]["counts"]["total"] = 2
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("test-total-mismatch", codes(run(case)))

    def test_trace_must_be_json(self):
        case = contract()
        trace = next(item for item in case["manifest"]["artifacts"] if item["type"] == "trace")
        trace["format"], trace["media_type"] = "text", "text/plain"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("trace-format", codes(run(case)))

    def test_text_question_cannot_bind_a_trace(self):
        case = contract()
        case["question"]["language"] = "text"
        self.assertIn("trace-text-question", codes(run(case)))

    def test_text_artifact_is_never_a_trace(self):
        case = contract(question_text())
        artifact = case["manifest"]["artifacts"][0]
        artifact["is_trace"] = True
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("artifact-trace-flag", codes(run(case)))

    def test_referenced_artifact_bytes_are_required(self):
        case = contract()
        case["artifact_bytes"].pop("diff-1")
        case["artifact_bytes"].pop("trace-1")
        self.assertIn("artifact-bytes-unavailable", codes(run(case)))

    def test_referenced_artifact_hash_is_checked(self):
        case = contract()
        case["artifact_bytes"]["diff-1"] = b"tampered"
        self.assertIn("artifact-hash-mismatch", codes(run(case)))


    def test_trace_must_parse_as_json(self):
        case = contract()
        replace_trace(case, b"not json")
        self.assertIn("trace-json", codes(run(case)))

    def test_trace_source_must_match_the_snippet(self):
        case = contract()
        doc = json.loads(TRACE)
        doc["traces"][0]["source_sha256"] = "0" * 64
        replace_trace(case, json.dumps(doc).encode())
        self.assertIn("trace-source", codes(run(case)))

    def test_trace_case_must_match_the_bound_case(self):
        case = contract()
        doc = json.loads(TRACE)
        doc["traces"][0]["case_id"] = "other-case"
        replace_trace(case, json.dumps(doc).encode())
        self.assertIn("trace-case", codes(run(case)))

    def test_trace_stdin_must_match_the_question(self):
        case = contract()
        doc = json.loads(TRACE)
        doc["traces"][0]["stdin"] = "x"
        doc["traces"][0]["stdin_sha256"] = sha_text("x")
        replace_trace(case, json.dumps(doc).encode())
        self.assertIn("trace-stdin", codes(run(case)))

    def test_trace_language_must_be_python(self):
        case = contract()
        case["question"]["language"] = "text"
        self.assertIn("trace-language", codes(run(case)))

    def test_truncated_trace_is_not_a_complete_trace(self):
        case = contract()
        doc = json.loads(TRACE)
        doc["traces"][0]["truncated"] = True
        replace_trace(case, json.dumps(doc).encode())
        self.assertIn("trace-truncated", codes(run(case)))

    def test_exception_stderr_must_be_referenced(self):
        case = exception_contract()
        case["binding"]["questions"][0]["cases"][0]["artifact_ids"].remove("stderr-1")
        self.assertIn("exception-stderr-unreferenced", codes(run(case)))

    def test_exception_stderr_must_exist(self):
        case = exception_contract()
        for name in ("expected", "actual"):
            case["manifest"]["cases"][0][name]["exception"]["stderr_artifact_id"] = "missing"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-stderr-unknown", codes(run(case)))

    def test_exception_stderr_bytes_must_match(self):
        case = exception_contract()
        case["artifact_bytes"]["stderr-1"] = b"tampered"
        self.assertIn("exception-stderr-bytes", codes(run(case)))

    def test_actual_exception_must_name_the_same_stderr_artifact(self):
        case = exception_contract()
        case["manifest"]["cases"][0]["actual"]["exception"]["stderr_artifact_id"] = None
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-stderr-missing", codes(run(case)))


    def test_trace_schema_version_is_checked(self):
        case = contract()
        doc = json.loads(TRACE)
        doc["schema_version"] = "0.4"
        replace_trace(case, json.dumps(doc).encode())
        self.assertIn("trace-schema", codes(run(case)))

    def test_trace_schema_rejects_malformed_steps(self):
        case = contract()
        doc = json.loads(TRACE)
        doc["traces"][0]["steps"] = [{}]
        replace_trace(case, json.dumps(doc).encode())
        self.assertIn("trace-schema", codes(run(case)))

    def test_trace_question_must_match(self):
        case = contract()
        doc = json.loads(TRACE)
        doc["question_id"] = "q-2"
        replace_trace(case, json.dumps(doc).encode())
        self.assertIn("trace-question", codes(run(case)))

    def test_trace_stdout_must_match_the_manifest(self):
        case = contract()
        doc = json.loads(TRACE)
        doc["traces"][0]["stdout"] = "2\n"
        replace_trace(case, json.dumps(doc).encode())
        self.assertIn("trace-stdout", codes(run(case)))

    def test_trace_exception_must_match_the_manifest(self):
        case = contract()
        doc = json.loads(TRACE)
        doc["traces"][0]["exception"] = {"type": "ValueError", "message": "x", "line": 1}
        replace_trace(case, json.dumps(doc).encode())
        self.assertIn("trace-exception", codes(run(case)))

    def test_trace_steps_must_be_a_list(self):
        case = contract()
        doc = json.loads(TRACE)
        doc["traces"][0]["steps"] = "not-a-list"
        replace_trace(case, json.dumps(doc).encode())
        self.assertIn("trace-steps", codes(run(case)))

    def test_trace_source_must_match_the_manifest_case(self):
        case = contract()
        case["manifest"]["cases"][0]["source_sha256"] = "0" * 64
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("trace-source", codes(run(case)))

    def test_trace_source_must_be_utf8(self):
        case = contract()
        case["question"]["snippets"][0]["code"] = "\ud800"
        self.assertIn("trace-source", codes(run(case)))

    def test_exception_stderr_must_be_text(self):
        case = exception_contract()
        artifact = next(item for item in case["manifest"]["artifacts"] if item["artifact_id"] == "stderr-1")
        artifact["type"] = "binary"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-stderr-type", codes(run(case)))

    def test_exception_stderr_must_be_complete(self):
        case = exception_contract()
        artifact = next(item for item in case["manifest"]["artifacts"] if item["artifact_id"] == "stderr-1")
        artifact["complete"] = False
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-stderr-complete", codes(run(case)))

    def test_actual_stderr_bytes_are_checked(self):
        case = exception_contract()
        add_stderr_artifact(case, "stderr-2", "bad literal\n")
        case["manifest"]["cases"][0]["actual"]["exception"]["stderr_artifact_id"] = "stderr-2"
        case["artifact_bytes"]["stderr-2"] = b"tampered"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-stderr-bytes", codes(run(case)))

    def test_exception_stderr_must_be_utf8(self):
        case = exception_contract()
        raw = b"\xff"
        artifact = next(item for item in case["manifest"]["artifacts"] if item["artifact_id"] == "stderr-1")
        old = artifact["bytes"]
        artifact.update({"sha256": sha(raw), "bytes": len(raw)})
        case["manifest"]["receipt"]["usage"]["artifact_bytes"] += len(raw) - old
        case["artifact_bytes"]["stderr-1"] = raw
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-stderr-encoding", codes(run(case)))

    def test_exception_stderr_content_is_checked(self):
        case = exception_contract()
        raw = b"other\n"
        artifact = next(item for item in case["manifest"]["artifacts"] if item["artifact_id"] == "stderr-1")
        old = artifact["bytes"]
        artifact.update({"sha256": sha(raw), "bytes": len(raw)})
        case["manifest"]["receipt"]["usage"]["artifact_bytes"] += len(raw) - old
        case["artifact_bytes"]["stderr-1"] = raw
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-stderr-content", codes(run(case)))

    def test_actual_exception_is_required(self):
        case = exception_contract()
        case["manifest"]["cases"][0]["actual"]["exception"] = None
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-actual-missing", codes(run(case)))

    def test_expected_and_actual_stderr_must_be_the_same_artifact(self):
        case = exception_contract()
        add_stderr_artifact(case, "stderr-2", "bad literal\n")
        case["manifest"]["cases"][0]["actual"]["exception"]["stderr_artifact_id"] = "stderr-2"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-stderr-mismatch", codes(run(case)))

    def test_exception_message_is_checked(self):
        case = exception_contract()
        for name in ("expected", "actual"):
            case["manifest"]["cases"][0][name]["exception"]["message"] = "other"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-message", codes(run(case)))

    def test_exception_shape_is_checked(self):
        case = exception_contract()
        for name in ("expected", "actual"):
            case["manifest"]["cases"][0][name]["exception"] = "not-an-object"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("exception-shape", codes(run(case)))

    def test_exception_is_unexpected_on_zero_exit(self):
        case = exception_contract()
        case["question"]["verification"]["cases"][0]["expected"]["exit_code"] = 0
        self.assertIn("exception-unexpected", codes(run(case)))


class ReadBudgetTest(unittest.TestCase):
    def write_root(self, case):
        return ValidateFilesTest.write_root(self, case)

    def test_oversized_binding_is_rejected_before_parsing(self):
        root, binding = self.write_root(contract())
        binding.write_bytes(b"x" * (fc.MAX_JSON_BYTES + 1))
        with self.assertRaises(fc.ContractError):
            fc.validate_files(binding)

    def test_fifo_binding_is_rejected_without_blocking(self):
        root, binding = self.write_root(contract())
        binding.unlink()
        os.mkfifo(binding)
        with self.assertRaises(fc.ContractError):
            fc.validate_files(binding)

    def test_binding_that_grows_past_limit_is_rejected(self):
        root, binding = self.write_root(contract())
        from unittest import mock
        with mock.patch.object(fc.os, "read", return_value=b"x" * (fc.MAX_JSON_BYTES + 1)):
            with self.assertRaises(fc.ContractError):
                fc.validate_files(binding)

    def test_stale_binding_hash_fails_before_follow_up_reads(self):
        root, binding = self.write_root(contract())
        (root / "batch/manifest.json").unlink()
        result = fc.validate_files(binding, expected_binding_sha256="0" * 64)
        self.assertEqual(codes(result), {"binding-file-mismatch"})

    def test_artifact_uri_count_is_budgeted_before_reading(self):
        root, binding = self.write_root(contract())
        raw = json.loads(binding.read_text(encoding="utf-8"))
        raw["artifact_uris"] = {f"artifact-{index}": "batch/artifacts/diff-1"
                                for index in range(fc.MAX_ARTIFACT_FILES + 1)}
        binding.write_text(json.dumps(raw), encoding="utf-8")
        self.assertIn("artifact-count", codes(fc.validate_files(binding)))

    def test_artifact_total_is_budgeted_before_reading(self):
        root, binding = self.write_root(contract())
        (root / "batch/artifacts/big-a").write_bytes(b"a" * (3 * 1024 * 1024))
        (root / "batch/artifacts/big-b").write_bytes(b"b" * (3 * 1024 * 1024))
        raw = json.loads(binding.read_text(encoding="utf-8"))
        raw["artifact_uris"] = {"big-a": "batch/artifacts/big-a",
                                "big-b": "batch/artifacts/big-b"}
        binding.write_text(json.dumps(raw), encoding="utf-8")
        self.assertIn("artifact-total-bytes", codes(fc.validate_files(binding)))


    def test_remaining_artifact_budget_stops_post_size_growth(self):
        root, binding = self.write_root(contract())
        (root / "batch/artifacts/big-a").write_bytes(b"a" * (3 * 1024 * 1024))
        (root / "batch/artifacts/big-b").write_bytes(b"b" * (3 * 1024 * 1024))
        raw = json.loads(binding.read_text(encoding="utf-8"))
        raw["artifact_uris"] = {"big-a": "batch/artifacts/big-a",
                                "big-b": "batch/artifacts/big-b"}
        binding.write_text(json.dumps(raw), encoding="utf-8")
        from unittest import mock
        with mock.patch.object(fc.SafeRoot, "size", return_value=1):
            with self.assertRaises(fc.ContractError):
                fc.validate_files(binding)


class PublicApiBindingTest(unittest.TestCase):
    def test_manifest_object_must_match_its_bytes(self):
        case = contract()
        manifest = case["manifest"]
        question = case["question"]
        binding = deepcopy(case["binding"])
        other = deepcopy(manifest)
        other["batch_id"] = "other-batch"
        mraw = json.dumps(other).encode()
        qraw = json.dumps(question).encode()
        binding["manifest_sha256"] = sha(mraw)
        binding["questions"][0]["question_sha256"] = sha(qraw)
        result = fc.validate(manifest, question, binding, manifest_bytes=mraw,
                             question_bytes=qraw, artifact_bytes=case["artifact_bytes"],
                             fixture_bytes=case["fixture_bytes"])
        self.assertIn("manifest-object-mismatch", codes(result))

    def test_fixture_provider_is_required(self):
        case = contract()
        mraw = json.dumps(case["manifest"]).encode()
        qraw = json.dumps(case["question"]).encode()
        binding = deepcopy(case["binding"])
        binding["manifest_sha256"] = sha(mraw)
        binding["questions"][0]["question_sha256"] = sha(qraw)
        result = fc.validate(case["manifest"], case["question"], binding,
                             manifest_bytes=mraw, question_bytes=qraw,
                             artifact_bytes=case["artifact_bytes"], fixture_bytes=None)
        self.assertIn("fixture-provider", codes(result))

    def test_direct_gate_validate_cannot_claim_verified(self):
        case = review_contract()
        mraw = json.dumps(case["manifest"]).encode()
        qraw = json.dumps(case["question"]).encode()
        binding = deepcopy(case["binding"])
        binding["manifest_sha256"] = sha(mraw)
        binding["questions"][0]["question_sha256"] = sha(qraw)
        result = fc.validate(case["manifest"], case["question"], binding,
                             manifest_bytes=mraw, question_bytes=qraw,
                             artifact_bytes=case["artifact_bytes"],
                             fixture_bytes=case["fixture_bytes"])
        self.assertIn("binding-file-unbound", codes(result))
        self.assertFalse(result.verified)


    def test_question_object_must_match_its_bytes(self):
        case = contract()
        question = case["question"]
        other = deepcopy(question)
        other["id"] = "q-2"
        qraw = json.dumps(other).encode()
        mraw = json.dumps(case["manifest"]).encode()
        binding = deepcopy(case["binding"])
        binding["manifest_sha256"] = sha(mraw)
        binding["questions"][0]["question_sha256"] = sha(qraw)
        result = fc.validate(question, question, binding, manifest_bytes=mraw,
                             question_bytes=qraw, artifact_bytes=case["artifact_bytes"],
                             fixture_bytes=case["fixture_bytes"])
        self.assertIn("question-object-mismatch", codes(result))

    def test_invalid_manifest_json_is_reported(self):
        case = contract()
        qraw = json.dumps(case["question"]).encode()
        raw = b"not json"
        binding = deepcopy(case["binding"])
        binding["manifest_sha256"] = sha(raw)
        binding["questions"][0]["question_sha256"] = sha(qraw)
        result = fc.validate(case["manifest"], case["question"], binding, manifest_bytes=raw,
                             question_bytes=qraw, artifact_bytes=case["artifact_bytes"],
                             fixture_bytes=case["fixture_bytes"])
        self.assertIn("manifest-json", codes(result))

    def test_invalid_question_json_is_reported(self):
        case = contract()
        mraw = json.dumps(case["manifest"]).encode()
        raw = b"not json"
        binding = deepcopy(case["binding"])
        binding["manifest_sha256"] = sha(mraw)
        binding["questions"][0]["question_sha256"] = sha(raw)
        result = fc.validate(case["manifest"], case["question"], binding, manifest_bytes=mraw,
                             question_bytes=raw, artifact_bytes=case["artifact_bytes"],
                             fixture_bytes=case["fixture_bytes"])
        self.assertIn("question-json", codes(result))

    def test_binding_object_must_match_its_bytes(self):
        case = contract()
        question = case["question"]
        binding = deepcopy(case["binding"])
        mraw = json.dumps(case["manifest"]).encode()
        qraw = json.dumps(question).encode()
        binding["manifest_sha256"] = sha(mraw)
        binding["questions"][0]["question_sha256"] = sha(qraw)
        other = deepcopy(binding)
        other["extra"] = True
        result = fc.validate(case["manifest"], question, binding, manifest_bytes=mraw,
                             question_bytes=qraw, artifact_bytes=case["artifact_bytes"],
                             fixture_bytes=case["fixture_bytes"],
                             binding_bytes=json.dumps(other).encode())
        self.assertIn("binding-object-mismatch", codes(result))

    def test_invalid_binding_json_is_reported(self):
        case = contract()
        question = case["question"]
        binding = deepcopy(case["binding"])
        mraw = json.dumps(case["manifest"]).encode()
        qraw = json.dumps(question).encode()
        binding["manifest_sha256"] = sha(mraw)
        binding["questions"][0]["question_sha256"] = sha(qraw)
        result = fc.validate(case["manifest"], question, binding, manifest_bytes=mraw,
                             question_bytes=qraw, artifact_bytes=case["artifact_bytes"],
                             fixture_bytes=case["fixture_bytes"], binding_bytes=b"not json")
        self.assertIn("binding-json", codes(result))


class LicenceTest(unittest.TestCase):
    def test_mixed_licences_require_one_manifest_per_question(self):
        case = contract()
        second = deepcopy(case["binding"]["questions"][0])
        second["question_uri"] = "content/questions/q-2.json"
        second["license"] = PROPRIETARY
        case["binding"]["questions"].append(second)
        self.assertIn("mixed-licence", codes(run(case)))

    def test_question_licence_must_match_binding(self):
        case = contract()
        case["question"]["license"] = PROPRIETARY
        self.assertIn("licence-question", codes(run(case)))

    def test_manifest_licence_must_match_binding(self):
        case = contract()
        case["manifest"]["licence"]["license"] = PROPRIETARY
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("licence-manifest", codes(run(case)))

    def test_draft_only_gate_is_required(self):
        case = contract()
        case["manifest"]["licence"]["gate"] = "published"
        case["manifest"]["binding_sha256"] = fc.compute_binding_sha256(case["manifest"])
        self.assertIn("licence-gate", codes(run(case)))


class SafeRootTest(unittest.TestCase):
    def test_reads_a_regular_file_below_the_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_bytes(b"hello")
            self.assertEqual(fc.SafeRoot(tmp).read_bytes("a.txt"), b"hello")

    def test_rejects_parent_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(fc.ContractError):
                fc.SafeRoot(tmp).read_bytes("../a.txt")

    def test_rejects_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(fc.ContractError):
                fc.SafeRoot(tmp).read_bytes("/etc/passwd")

    def test_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_bytes(b"hello")
            os.symlink(Path(tmp, "a.txt"), Path(tmp, "link"))
            with self.assertRaises((fc.ContractError, OSError)):
                fc.SafeRoot(tmp).read_bytes("link")

    def test_rejects_non_regular_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.mkfifo(Path(tmp, "fifo"))
            with self.assertRaises(fc.ContractError):
                fc.SafeRoot(tmp).read_bytes("fifo")


class MalformedInputTest(unittest.TestCase):
    def test_empty_documents_return_issues_instead_of_crashing(self):
        self.assertFalse(fc.validate({}, {}, {}).verified)
        self.assertFalse(fc.validate(None, None, None).verified)

    def test_wrong_shapes_are_reported(self):
        case = contract()
        case["manifest"]["cases"] = "not-a-list"
        case["manifest"]["artifacts"] = None
        case["question"]["verification"]["cases"] = None
        self.assertFalse(run(case).verified)


class CanonicalJsonTest(unittest.TestCase):
    def test_sorted_keys_compact_utf8(self):
        self.assertEqual(fc.canonical_json_bytes({"b": 1, "a": "å"}),
                         '{"a":"å","b":1}'.encode("utf-8"))

    def test_non_finite_numbers_are_rejected(self):
        with self.assertRaises(ValueError):
            fc.canonical_json_bytes({"x": float("nan")})

    def test_lone_surrogate_is_rejected(self):
        with self.assertRaises(UnicodeEncodeError):
            fc.canonical_json_bytes({"x": "\ud800"})

    def test_duplicate_keys_are_rejected_by_the_parser(self):
        with self.assertRaises(core.ContentError):
            core.parse_json_bytes(b'{"a":1,"a":2}', "x")

    def test_non_finite_json_is_rejected_by_the_parser(self):
        with self.assertRaises(core.ContentError):
            core.parse_json_bytes(b'{"a":NaN}', "x")


class ValidateFilesTest(unittest.TestCase):
    def write_root(self, case, *, fixture="file"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "batch/artifacts").mkdir(parents=True)
        (root / "content/questions").mkdir(parents=True)
        (root / "content/fixtures/artifacts").mkdir(parents=True)
        manifest_raw = json.dumps(case["manifest"], indent=2).encode()
        question_raw = json.dumps(case["question"], indent=2).encode()
        (root / "batch/manifest.json").write_bytes(manifest_raw)
        (root / "content/questions/q-1.json").write_bytes(question_raw)
        payload = case["fixture_bytes"]("fixture-one") or DIFF.encode()
        (root / "batch/artifacts/diff-1").write_bytes(payload)
        (root / "batch/artifacts/text-1").write_bytes(TEXT.encode())
        (root / "batch/artifacts/trace-1").write_bytes(TRACE)
        binding = deepcopy(case["binding"])
        binding["manifest_sha256"] = sha(manifest_raw)
        binding["questions"][0]["question_sha256"] = sha(question_raw)
        (root / "binding.json").write_text(json.dumps(binding), encoding="utf-8")
        fixture_path = root / "content/fixtures/artifacts/fixture-one.out"
        if fixture == "file":
            fixture_path.write_bytes(payload)
        elif fixture == "symlink":
            target = root / "outside.out"
            target.write_bytes(payload)
            fixture_path.symlink_to(target)
        elif fixture == "fifo":
            os.mkfifo(fixture_path)
        elif fixture == "large":
            fixture_path.write_bytes(b"x" * (fc.MAX_ARTIFACT_BYTES + 1))
        return root, root / "binding.json"

    def test_reads_binding_manifest_question_artifacts_and_fixture(self):
        root, binding = self.write_root(contract())
        result = fc.validate_files(binding)
        self.assertTrue(result.verified, [issue.to_dict() for issue in result.issues])

    def test_missing_fixture_is_reported_as_missing(self):
        root, binding = self.write_root(contract(), fixture="absent")
        found = codes(fc.validate_files(binding))
        self.assertIn("fixture-missing", found)

    def test_symlink_fixture_is_unreadable_with_a_reason(self):
        root, binding = self.write_root(contract(), fixture="symlink")
        result = fc.validate_files(binding)
        self.assertIn("fixture-unreadable", codes(result))
        self.assertTrue(any("symlink" in issue.message for issue in result.issues))

    def test_special_file_fixture_is_unreadable_with_a_reason(self):
        root, binding = self.write_root(contract(), fixture="fifo")
        result = fc.validate_files(binding)
        self.assertIn("fixture-unreadable", codes(result))
        self.assertTrue(any("regular file" in issue.message for issue in result.issues))

    def test_oversized_fixture_is_unreadable_with_a_reason(self):
        root, binding = self.write_root(contract(), fixture="large")
        result = fc.validate_files(binding)
        self.assertIn("fixture-unreadable", codes(result))
        self.assertTrue(any("larger than" in issue.message for issue in result.issues))

    def test_binding_file_hash_is_reported_and_bound(self):
        root, binding = self.write_root(review_contract())
        digest = hashlib.sha256(binding.read_bytes()).hexdigest()
        result = fc.validate_files(binding, expected_binding_sha256=digest)
        self.assertTrue(result.verified, [issue.to_dict() for issue in result.issues])
        self.assertIsNotNone(result.gate)
        self.assertEqual(result.binding_file_sha256, digest)
        self.assertEqual(result.to_dict()["binding_file_sha256"], digest)

    def test_binding_file_hash_covers_a_trailing_newline(self):
        root, binding = self.write_root(contract())
        binding.write_bytes(binding.read_bytes() + b"\n")
        digest = hashlib.sha256(binding.read_bytes()).hexdigest()
        result = fc.validate_files(binding, expected_binding_sha256=digest)
        self.assertTrue(result.verified, [issue.to_dict() for issue in result.issues])
        self.assertEqual(result.binding_file_sha256, digest)

    def test_a_gate_binding_without_the_frozen_file_hash_is_red(self):
        root, binding = self.write_root(review_contract())
        self.assertIn("binding-file-unbound", codes(fc.validate_files(binding)))

    def test_swapping_a_gate_behind_the_same_references_is_red(self):
        root, binding = self.write_root(review_contract())
        digest = hashlib.sha256(binding.read_bytes()).hexdigest()
        raw = json.loads(binding.read_text(encoding="utf-8"))
        raw["questions"][0]["gate"]["pilot"] = "review-pilot-2"
        binding.write_text(json.dumps(raw), encoding="utf-8")
        found = codes(fc.validate_files(binding, expected_binding_sha256=digest))
        self.assertIn("binding-file-mismatch", found)


if __name__ == "__main__":
    unittest.main()
