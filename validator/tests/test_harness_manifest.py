# SPDX-License-Identifier: MIT
"""Static tests for manifest acceptance: both digest links, the receipt and the semantic rules (no execution)."""
import hashlib
import unittest

from validator.harness import canonical, manifest


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def valid():
    config = {"env": {"TZ": "UTC"}, "settings": ["core.autocrlf=false"]}
    value = {
        "manifest_schema_version": "1.0.0",
        "binding_projection_version": "1",
        "batch_id": "git-pilot-1",
        "question_id": "git.example-diff",
        "bindings": {
            "question_ids": ["git.example-diff"], "harness_version": "0.1.0",
            "tool_versions": {"git": "2.45", "python": "3.14", "pytest": "8.3"},
            "runtime_image_sha256": h("runtime"), "script_sha256": h("script"),
            "config_digest": hashlib.sha256(canonical.dumps_canonical(config)).hexdigest(),
            "fixture_sha256": h("fixture"), "argv": ["git", "diff"],
        },
        "inputs": [{"role": "fixture", "path": "fixtures/repo.tar", "sha256": h("input"), "bytes": 1}],
        "config": config,
        "canonicalisation": {"mode": "none"},
        "cases": [{"case_id": "c1", "source_sha256": h("source"), "stdin_sha256": h("stdin"),
                   "expected": {"stdout_sha256": h("out"), "exception": None},
                   "actual": {"stdout_sha256": h("out"), "exception": None},
                   "outcome": "passed", "truncated": False, "complete": True}],
        "artifacts": [{"artifact_id": "a1", "type": "diff", "format": "text", "media_type": "text/plain",
                       "encoding": "utf-8", "executable": False, "is_trace": False, "sha256": h("artifact"),
                       "bytes": 1, "truncated": False, "complete": True}],
        "test_report": {"schema_version": "1.0.0", "tool": "pytest", "tool_version": "8.3",
                        "counts": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "xpass": 0},
                        "tests": [{"test_id": "t1", "outcome": "passed"}]},
        "licence": {"license": "LicenseRef-Proprietary", "gate": "draft-only"},
        "receipt": {"started_at": "1970-01-01T00:00:00Z", "duration_ms": 1, "exit_code": 0,
                    "stdout_sha256": h("stdout"), "stdout_bytes": 1, "stderr_sha256": h("stderr"), "stderr_bytes": 0,
                    "budget": {"wall_seconds": 10}, "isolation": {"mechanism": "bwrap+cgroup-v2", "proof": "tested", "network": "denied"},
                    "usage": {"wall_ms": 1}, "limit_hit": [], "status": "ok"},
    }
    value["binding_sha256"] = canonical.binding_sha256(value)
    return value


def accepted(change=None, *, rebind=True, expected=None, binding=None):
    value = valid()
    if change:
        change(value)
    if rebind:
        value["binding_sha256"] = canonical.binding_sha256(value)
    raw = canonical.dumps_canonical(value)
    return manifest.accept(value, raw,
                           expected_manifest_sha256=expected if expected is not None else canonical.manifest_sha256(raw),
                           expected_binding_sha256=binding if binding is not None else value["binding_sha256"])


class AcceptanceTests(unittest.TestCase):
    def test_a_clean_manifest_is_accepted(self):
        self.assertEqual(accepted(), [])

    def test_a_self_declared_digest_is_not_an_authorization(self):
        self.assertIn("manifest-not-approved", accepted(expected=h("something-else")))

    def test_a_failed_or_incomplete_receipt_is_red(self):
        for label, change in {
            "status": lambda v: v["receipt"].update(status="resource-exceeded"),
            "limit_hit": lambda v: v["receipt"].update(limit_hit=["wall"]),
            "isolation": lambda v: v["receipt"].update(isolation={"mechanism": "", "proof": "", "network": "denied"}),
            "budget": lambda v: v["receipt"].update(budget={}),
        }.items():
            with self.subTest(label=label):
                self.assertTrue(any(code.startswith("receipt-") for code in accepted(change)), label)

    def test_duplicate_ids_are_red(self):
        for label, change, code in (
            ("case", lambda v: v["cases"].append({**v["cases"][0]}), "duplicate-case-id"),
            ("artifact", lambda v: v["artifacts"].append({**v["artifacts"][0]}), "duplicate-artifact-id"),
            ("test", lambda v: v["test_report"]["tests"].append({**v["test_report"]["tests"][0]}), "duplicate-test-id"),
        ):
            with self.subTest(label=label):
                self.assertTrue(any(c.startswith(code) for c in accepted(change)), code)

    def test_all_skip_or_xpass_is_not_a_verified_facit(self):
        def skip_all(v):
            v["test_report"]["tests"] = [{"test_id": "t1", "outcome": "skipped"}]
            v["test_report"]["counts"] = {"total": 1, "passed": 0, "failed": 0, "skipped": 1, "xfail": 0, "xpass": 0}
            v["cases"][0]["outcome"] = "skipped"
        self.assertIn("not-a-verified-facit", accepted(skip_all))

    def test_what_passed_must_match_what_was_expected(self):
        def bad_stdout(v):
            v["cases"][0]["actual"]["stdout_sha256"] = h("other")
        self.assertIn("case-0-stdout-mismatch", accepted(bad_stdout))

        def bad_exception(v):
            v["cases"][0]["expected"]["exception"] = {"type": "ValueError", "message": "x"}
        self.assertIn("case-0-exception-mismatch", accepted(bad_exception))

    def test_truncated_or_incomplete_is_never_a_pass(self):
        def truncated(v):
            v["cases"][0].update(truncated=True)
        self.assertIn("case-0-incomplete", accepted(truncated))
        def artifact_both(v):
            v["artifacts"][0].update(truncated=True, complete=True)
        self.assertIn("artifact-0-truncated-and-complete", accepted(artifact_both))

    def test_trace_and_text_artifact_rules(self):
        def trace_wrong(v):
            v["artifacts"][0].update(type="trace", format="text", is_trace=True)
        self.assertIn("artifact-0-trace-rules", accepted(trace_wrong))
        def text_executable(v):
            v["artifacts"][0].update(executable=True)
        self.assertIn("artifact-0-text-executable", accepted(text_executable))

    def test_placeholder_hashes_are_red(self):
        def zero(v):
            v["artifacts"][0]["sha256"] = "0" * 64
        self.assertIn("artifact-0-hash", accepted(zero))
        def zero_input(v):
            v["inputs"][0]["sha256"] = "f" * 64
        self.assertIn("input-0", accepted(zero_input))

    def test_the_config_digest_is_recomputed_from_the_preimage(self):
        def changed(v):
            v["config"]["env"]["TZ"] = "CET"
        self.assertIn("config-digest-mismatch", accepted(changed))

    def test_the_manifest_s_own_binding_digest_must_match(self):
        def changed(v):
            v["binding_sha256"] = h("wrong")
        self.assertIn("binding-digest-mismatch", accepted(changed, rebind=False))


if __name__ == "__main__":
    unittest.main()
