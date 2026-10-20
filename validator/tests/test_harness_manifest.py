# SPDX-License-Identifier: MIT
"""Static tests for manifest acceptance: both digest links, the receipt and the semantic rules (no execution)."""
import hashlib
import unittest

from validator.harness import canonical, manifest, plan


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
                    "stdout_sha256": h("stdout"), "stdout_bytes": 1, "stderr_sha256": h(""), "stderr_bytes": 0,
                    "budget": dict(plan.BUDGET), "isolation": {"mechanism": "bwrap+cgroup-v2", "proof": "tested", "network": "denied"},
                    "usage": {**{key: 0 for key in manifest.USAGE_KEYS}, "wall_ms": 1, "stdout_bytes": 1, "artifact_bytes": 1},
                    "limit_hit": [], "status": "ok"},
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

    def test_the_manifest_version_and_licence_gate_are_fixed(self):
        self.assertIn("manifest-version", accepted(lambda v: v.update(manifest_schema_version="9.9")))
        self.assertIn("manifest-version", accepted(lambda v: v.update(binding_projection_version="2")))
        self.assertIn("licence", accepted(lambda v: v["licence"].update(gate="release")))
        self.assertIn("licence", accepted(lambda v: v["licence"].pop("license")))

    def test_canonicalisation_is_one_of_the_two_declared_modes(self):
        self.assertIn("canonicalisation", accepted(lambda v: v.update(canonicalisation={"mode": "bogus"})))
        self.assertIn("canonicalisation-none", accepted(lambda v: v.update(canonicalisation={"mode": "none", "name": "x"})))

    def test_exit_code_budget_and_usage_are_checked(self):
        self.assertIn("receipt-exit-code", accepted(lambda v: v["receipt"].update(exit_code=1)))
        self.assertIn("receipt-budget", accepted(lambda v: v["receipt"]["budget"].pop("pids")))
        self.assertIn("receipt-budget", accepted(lambda v: v["receipt"]["budget"].update(pids=64)))
        self.assertIn("receipt-usage", accepted(lambda v: v["receipt"]["usage"].pop("cpu_ms")))

    def test_case_hashes_shapes_and_tests_are_checked(self):
        self.assertIn("case-0-source_sha256", accepted(lambda v: v["cases"][0].update(source_sha256="0" * 64)))
        self.assertIn("case-0-stdout-hash", accepted(lambda v: v["cases"][0]["actual"].update(stdout_sha256="0" * 64)))
        self.assertIn("case-0-id", accepted(lambda v: v["cases"][0].update(case_id={})))
        self.assertIn("test-shape", accepted(lambda v: v["test_report"]["tests"].append("x")))

    def test_bindings_argv_and_question_ids_are_required(self):
        self.assertIn("bindings-argv", accepted(lambda v: v["bindings"].update(argv=[])))
        self.assertIn("bindings-argv", accepted(lambda v: v["bindings"].pop("argv")))
        self.assertIn("bindings-question-ids", accepted(lambda v: v["bindings"].update(question_ids=[])))

    def test_usage_must_stay_within_the_budget_and_match_the_receipt(self):
        self.assertIn("usage-stdout_bytes", accepted(lambda v: v["receipt"]["usage"].update(stdout_bytes=1048577)))
        self.assertIn("usage-wall_ms", accepted(lambda v: v["receipt"]["usage"].update(wall_ms=11000)))
        self.assertIn("usage-memory_peak_bytes", accepted(lambda v: v["receipt"]["usage"].update(memory_peak_bytes=268435457)))
        self.assertIn("usage-receipt-mismatch", accepted(lambda v: v["receipt"]["usage"].update(stdout_bytes=0)))
        self.assertIn("usage-artifact-sum", accepted(lambda v: v["receipt"]["usage"].update(artifact_bytes=0)))

    def test_started_duration_and_receipt_hashes_are_checked(self):
        self.assertIn("receipt-started-at", accepted(lambda v: v["receipt"].update(started_at="")))
        self.assertIn("receipt-duration", accepted(lambda v: v["receipt"].update(duration_ms=-1)))
        self.assertIn("receipt-hashes", accepted(lambda v: v["receipt"].update(stdout_sha256="0" * 64)))

    def test_an_incomplete_artifact_still_needs_a_real_hash_and_a_string_id(self):
        self.assertIn("artifact-0-hash", accepted(lambda v: v["artifacts"][0].update(complete=False, sha256="0" * 64)))
        self.assertIn("artifact-0-id", accepted(lambda v: v["artifacts"][0].update(artifact_id={})))

    def test_a_shapeless_input_path_is_a_finding_not_a_crash(self):
        self.assertIn("input-0-path", accepted(lambda v: v["inputs"][0].update(path=[])))

    def test_the_budget_has_exactly_the_contract_keys(self):
        self.assertIn("receipt-budget", accepted(lambda v: v["receipt"]["budget"].update(extra=1)))

    def test_named_canonicalisation_has_exactly_its_four_keys(self):
        self.assertIn("canonicalisation-named", accepted(lambda v: v.update(canonicalisation={
            "mode": "named", "name": "n", "version": "1", "transform_sha256": h("t"), "extra": 1})))

    def test_an_optional_schema_is_applied_when_given(self):
        value = valid()
        raw = canonical.dumps_canonical(value)
        expected = canonical.manifest_sha256(raw)
        codes = manifest.accept(value, raw, expected_manifest_sha256=expected,
                                schema={"type": "object", "required": ["no_such_field"]})
        self.assertTrue(any(code.startswith("schema:") for code in codes))
        self.assertFalse(any(code.startswith("schema:") for code in
                             manifest.accept(value, raw, expected_manifest_sha256=expected, schema={"type": "object"})))

    def test_a_zero_byte_stream_must_carry_the_hash_of_no_bytes(self):
        def zero_stdout(v):
            v["receipt"]["stdout_bytes"] = 0
            v["receipt"]["usage"]["stdout_bytes"] = 0
        self.assertIn("receipt-empty-hash", accepted(zero_stdout))

        def zero_stderr_with_a_hash(v):
            v["receipt"]["stderr_bytes"] = 0
            v["receipt"]["usage"]["stderr_bytes"] = 0
            v["receipt"]["stderr_sha256"] = h("not empty")
        self.assertIn("receipt-empty-hash", accepted(zero_stderr_with_a_hash))

        def zero_stdout_with_the_empty_hash(v):
            zero_stdout(v)
            v["receipt"]["stdout_sha256"] = h("")
        self.assertNotIn("receipt-empty-hash", accepted(zero_stdout_with_the_empty_hash))

    def test_the_manifest_s_own_binding_digest_must_match(self):
        def changed(v):
            v["binding_sha256"] = h("wrong")
        self.assertIn("binding-digest-mismatch", accepted(changed, rebind=False))


if __name__ == "__main__":
    unittest.main()
