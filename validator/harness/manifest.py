# SPDX-License-Identifier: MIT
"""Acceptance of an offline-harness manifest: both digest links, the parent-owned receipt and the semantic rules.

Nothing here executes anything. The trusted author gate owns the input spec and the receipt; a manifest that recomputed
its own digest is not an authorization, so ``expected_manifest_sha256`` must come from the binding file, never from the
manifest itself.
"""
from __future__ import annotations

import hashlib
import re

from . import canonical


class ManifestError(ValueError):
    """The manifest cannot be accepted as the canonical object at all."""


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PLACEHOLDER = re.compile(r"(.)\1{63}\Z")
ROLES = frozenset({"fixture", "source", "stdin", "config"})


def _hash(value) -> bool:
    """A real 64-hex digest, never a zero or repeated-character placeholder."""
    return isinstance(value, str) and bool(_HEX.fullmatch(value)) and not _PLACEHOLDER.fullmatch(value)


def accept(manifest: dict, raw_bytes: bytes, *, expected_manifest_sha256: str,
           expected_binding_sha256: str | None = None) -> list:
    """Findings (empty means accepted). Raises ManifestError only when no canonical binding can be computed."""
    found = []

    def add(code: str) -> None:
        found.append(code)

    try:
        binding = canonical.binding_sha256(manifest)
    except canonical.CanonicalError as exc:
        raise ManifestError(str(exc)) from None

    # Both links: the content binding and the whole-file digest that the binding file owns.
    if manifest.get("binding_sha256") != binding:
        add("binding-digest-mismatch")
    if expected_binding_sha256 is not None and expected_binding_sha256 != binding:
        add("binding-not-approved")
    if not _hash(expected_manifest_sha256) or canonical.manifest_sha256(raw_bytes) != expected_manifest_sha256:
        add("manifest-not-approved")

    # The receipt is outside the binding by design, but a run is accepted only when it is clean, isolated and complete.
    receipt = manifest.get("receipt")
    if not isinstance(receipt, dict):
        add("receipt-missing")
    else:
        if receipt.get("status") != "ok":
            add("receipt-status")
        if receipt.get("limit_hit") != []:
            add("receipt-limit-hit")
        isolation = receipt.get("isolation")
        if (not isinstance(isolation, dict) or not isolation.get("mechanism") or not isolation.get("proof")
                or isolation.get("network") != "denied"):
            add("receipt-isolation")
        if not isinstance(receipt.get("budget"), dict) or not receipt["budget"]:
            add("receipt-budget")

    cases = manifest.get("cases") if isinstance(manifest.get("cases"), list) else []
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
    case_ids = [c.get("case_id") for c in cases if isinstance(c, dict)]
    artifact_ids = [a.get("artifact_id") for a in artifacts if isinstance(a, dict)]
    if len(set(case_ids)) != len(case_ids):
        add("duplicate-case-id")
    if len(set(artifact_ids)) != len(artifact_ids):
        add("duplicate-artifact-id")

    report = manifest.get("test_report") if isinstance(manifest.get("test_report"), dict) else {}
    tests = report.get("tests") if isinstance(report.get("tests"), list) else []
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    test_ids = [t.get("test_id") for t in tests if isinstance(t, dict)]
    outcomes = [t.get("outcome") for t in tests if isinstance(t, dict)]
    if len(set(test_ids)) != len(test_ids):
        add("duplicate-test-id")
    if counts.get("total") != len(tests) or counts.get("total") != len(cases):
        add("counts-total")
    for name in ("passed", "failed", "skipped", "xfail", "xpass"):
        if counts.get(name) != outcomes.count(name):
            add(f"counts-{name}")
    if not cases or not tests:
        add("no-cases")
    if any(outcome != "passed" for outcome in outcomes) or any(c.get("outcome") != "passed" for c in cases if isinstance(c, dict)):
        add("not-a-verified-facit")

    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            add(f"case-{index}-shape")
            continue
        expected, actual = case.get("expected"), case.get("actual")
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            add(f"case-{index}-projection")
            continue
        exception = expected.get("exception") or {}
        actual_exception = actual.get("exception") or {}
        if case.get("outcome") == "passed":
            if expected.get("stdout_sha256") != actual.get("stdout_sha256"):
                add(f"case-{index}-stdout-mismatch")
            if (exception.get("type"), exception.get("message")) != (actual_exception.get("type"), actual_exception.get("message")):
                add(f"case-{index}-exception-mismatch")
            if case.get("truncated") or not case.get("complete"):
                add(f"case-{index}-incomplete")

    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            add(f"artifact-{index}-shape")
            continue
        kind, fmt = artifact.get("type"), artifact.get("format")
        if kind == "trace":
            if fmt != "json" or artifact.get("is_trace") is not True or artifact.get("executable") is not False:
                add(f"artifact-{index}-trace-rules")
        elif artifact.get("is_trace") is not False:
            add(f"artifact-{index}-not-trace")
        if fmt == "text" and artifact.get("executable") is not False:
            add(f"artifact-{index}-text-executable")
        if artifact.get("truncated") and artifact.get("complete"):
            add(f"artifact-{index}-truncated-and-complete")
        if artifact.get("complete") and not _hash(artifact.get("sha256")):
            add(f"artifact-{index}-hash")

    bindings = manifest.get("bindings") if isinstance(manifest.get("bindings"), dict) else {}
    question_ids = bindings.get("question_ids")
    if isinstance(question_ids, list) and manifest.get("question_id") is not None and manifest["question_id"] not in question_ids:
        add("question-id-not-bound")
    inputs = manifest.get("inputs") if isinstance(manifest.get("inputs"), list) else []
    pairs = [(item.get("role"), item.get("path")) for item in inputs if isinstance(item, dict)]
    if len(set(pairs)) != len(pairs):
        add("duplicate-input")
    for index, item in enumerate(inputs):
        if not isinstance(item, dict) or item.get("role") not in ROLES or not _hash(item.get("sha256")):
            add(f"input-{index}")

    config = manifest.get("config")
    if isinstance(config, dict):
        digest = hashlib.sha256(canonical.dumps_canonical(config)).hexdigest()
        if bindings.get("config_digest") != digest:
            add("config-digest-mismatch")
    else:
        add("config-missing")
    return sorted(set(found))
