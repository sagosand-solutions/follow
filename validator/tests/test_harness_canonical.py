# SPDX-License-Identifier: MIT
"""Static tests for the one canonical JSON byte procedure and the two digest links (no execution, no fixtures)."""
import math
import unittest

from validator.harness import canonical


def manifest(**over):
    value = {
        "manifest_schema_version": "1.0.0",
        "binding_projection_version": "1",
        "batch_id": "git-pilot-1",
        "question_id": "git.example-diff",
        "bindings": {"question_ids": ["git.example-diff"], "harness_version": "0.1.0", "tool_versions": {"git": "2.45", "python": "3.14", "pytest": "8.3"},
                     "runtime_image_sha256": "0" * 64, "script_sha256": "1" * 64, "config_digest": "2" * 64,
                     "fixture_sha256": "3" * 64, "argv": ["git", "diff"]},
        "inputs": [{"role": "fixture", "path": "fixtures/repo.tar", "sha256": "4" * 64, "bytes": 0}],
        "config": {"env": {"TZ": "UTC"}, "settings": ["core.autocrlf=false"]},
        "canonicalisation": {"mode": "none"},
        "cases": [{"case_id": "c1", "source_sha256": "5" * 64, "stdin_sha256": "6" * 64,
                   "expected": {"stdout_sha256": "7" * 64, "exception": None},
                   "actual": {"stdout_sha256": "7" * 64, "exception": None},
                   "outcome": "passed", "truncated": False, "complete": True}],
        "artifacts": [{"artifact_id": "a1", "type": "diff", "format": "text", "media_type": "text/plain", "encoding": "utf-8",
                       "executable": False, "is_trace": False, "sha256": "8" * 64, "bytes": 0, "truncated": False, "complete": True}],
        "test_report": {"schema_version": "1.0.0", "tool": "pytest", "tool_version": "8.3",
                        "counts": {"total": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "xpass": 0},
                        "tests": [{"test_id": "t1", "outcome": "passed"}]},
        "licence": {"license": "LicenseRef-Proprietary", "gate": "draft-only"},
        "binding_sha256": "9" * 64,
        "receipt": {"started_at": "1970-01-01T00:00:00Z", "duration_ms": 0, "exit_code": 0,
                    "stdout_sha256": "a" * 64, "stdout_bytes": 0, "stderr_sha256": "b" * 64, "stderr_bytes": 0,
                    "budget": {}, "isolation": {}, "usage": {}, "limit_hit": [], "status": "ok"},
    }
    value.update(over)
    return value


class CanonicalBytesTests(unittest.TestCase):
    def test_sorted_keys_utf8_and_no_whitespace_is_one_exact_byte_form(self):
        self.assertEqual(canonical.dumps_canonical({"b": 1, "a": "å"}), b'{"a":"\xc3\xa5","b":1}')
        self.assertEqual(canonical.dumps_canonical({"a": 1, "b": 2}), canonical.dumps_canonical({"b": 2, "a": 1}))

    def test_duplicate_keys_are_refused(self):
        with self.assertRaisesRegex(canonical.CanonicalError, "duplicate"):
            canonical.loads_strict('{"a":1,"a":2}')

    def test_a_lone_surrogate_is_refused_on_read_and_write(self):
        with self.assertRaises(canonical.CanonicalError):
            canonical.loads_strict('{"a":"\\ud800"}')
        with self.assertRaises(canonical.CanonicalError):
            canonical.dumps_canonical({"a": "\ud800"})

    def test_non_finite_numbers_are_refused(self):
        for raw in ('{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}', '{"a":[1,{"b":NaN}]}'):
            with self.subTest(raw=raw):
                with self.assertRaises(canonical.CanonicalError):
                    canonical.loads_strict(raw)
        for value in (float("nan"), float("inf"), -float("inf"), {"a": [float("nan")]}):
            with self.subTest(value=value):
                with self.assertRaises(canonical.CanonicalError):
                    canonical.dumps_canonical(value)

    def test_a_non_string_key_is_refused_instead_of_silently_stringified(self):
        with self.assertRaises(canonical.CanonicalError):
            canonical.dumps_canonical({1: "x"})
        with self.assertRaises(canonical.CanonicalError):
            canonical.dumps_canonical({"a": {2: 3}})

    def test_a_non_object_is_not_a_manifest(self):
        for raw in ("[]", "1", '"x"', "null"):
            with self.subTest(raw=raw):
                with self.assertRaises(canonical.CanonicalError):
                    canonical.loads_strict(raw)


class BindingDigestTests(unittest.TestCase):
    def test_the_projection_excludes_receipt_and_the_digest_itself(self):
        base = manifest()
        changed = manifest(receipt={**base["receipt"], "status": "resource-exceeded", "duration_ms": 999},
                           binding_sha256="f" * 64)
        self.assertEqual(canonical.binding_sha256(base), canonical.binding_sha256(changed), "drift data never changes the binding")

    def test_a_changed_projection_field_changes_the_binding(self):
        base = manifest()
        other = manifest(batch_id="git-pilot-2")
        self.assertNotEqual(canonical.binding_sha256(base), canonical.binding_sha256(other))

    def test_a_missing_projection_field_is_an_error(self):
        q = manifest()
        del q["inputs"]
        with self.assertRaisesRegex(canonical.CanonicalError, "inputs"):
            canonical.binding_sha256(q)

    def test_the_manifest_digest_is_over_the_raw_bytes_not_the_parsed_object(self):
        raw_a = b'{"a":1}'
        raw_b = b'{"a": 1}'
        self.assertNotEqual(canonical.manifest_sha256(raw_a), canonical.manifest_sha256(raw_b))
        self.assertEqual(canonical.manifest_sha256(raw_a), canonical.manifest_sha256(b'{"a":1}'))

    def test_the_frozen_example_shape_has_a_stable_binding(self):
        # A pinned vector: the procedure is explicit, so this exact projection must always hash to this exact digest.
        self.assertEqual(canonical.binding_sha256(manifest()), "d60ef8dc295ddb840d8e53e56f806b41255011bd345d1b924d3043778511ab1b")


if __name__ == "__main__":
    unittest.main()
