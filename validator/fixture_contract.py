# SPDX-License-Identifier: MIT
"""Static K4 binding contract: a 0.3 question, one K3 v1.5 manifest and its artifacts.

This module is a *tool/test-only* validator. It never imports the runner, the
trace generator, ``subprocess``, sockets, ``urllib``/``requests``, a clock or
the network, and it never executes a fixture. It answers one question: do the
bytes the question shows still match the bytes one reviewed, isolated harness
run recorded?

The K3 manifest is the run receipt (schema v1.5); K1's 0.3 question artifacts
are the display side. K4 owns only the binding document between them. The
manifest's ``binding_sha256`` covers the deterministic content projection and
excludes ``receipt``; the binding file additionally hashes the whole manifest
bytes with ``manifest_sha256``. Both chains are required: a failed or
resource-exceeded receipt is rejected even when the content projection (and
therefore ``binding_sha256``) is unchanged.

JSON is parsed by :func:`validator.core.parse_json_bytes` (duplicate keys,
non-finite numbers and lone UTF-8 errors are rejected) and hashed with the one
canonical byte procedure in :func:`canonical_json_bytes`. Raw fixture and
artifact bytes are hashed without normalisation.
"""
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from . import core
from .core import SetupError
from .fixture_read import FixtureProblem, read_fixture

SCHEMA_VERSION = "1.0.0"
PROJECTION_VERSION = "1"
PROJECTION_KEYS = (
    "manifest_schema_version",
    "binding_projection_version",
    "batch_id",
    "question_id",
    "bindings",
    "inputs",
    "config",
    "canonicalisation",
    "cases",
    "artifacts",
    "test_report",
    "licence",
)
ALLOWED_LICENSES = frozenset({"CC-BY-SA-4.0", "LicenseRef-Proprietary"})
ZERO_HASH = "0" * 64
EMPTY_HASH = hashlib.sha256(b"").hexdigest()
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_FILES = 64
MAX_ARTIFACT_TOTAL_BYTES = 64 * 1024 * 1024

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RELATIVE = re.compile(r"^[A-Za-z0-9._/-]+$")

# K1 question artifact kind/format -> K3 artifact type/format/media_type.
K1_TO_K3 = {
    ("text", "plain"): ("text", "text", "text/plain"),
    ("diff", "unified-diff"): ("diff", "text", "text/x-diff"),
    ("http-transcript", "http-1.1-transcript"): ("http-transcript", "text", "text/plain"),
    ("policy", "plain"): ("policy", "text", "text/plain"),
    ("test-report", "plain"): ("test-report", "text", "text/plain"),
}

# Every K3 artifact type and the formats/media types it may use. `test-report`
# has a machine-JSON form for the harness's own report; when a question binds a
# test-report artifact it must still be the plain display text (K1_TO_K3).
K3_FORMATS = {
    "text": {"text": ("text/plain",)},
    "diff": {"text": ("text/plain", "text/x-diff")},
    "status": {"text": ("text/plain",)},
    "graph": {"text": ("text/plain",)},
    "http-transcript": {"text": ("text/plain",)},
    "policy": {"text": ("text/plain",)},
    "test-report": {"text": ("text/plain",), "json": ("application/json",)},
    "trace": {"json": ("application/json",)},
}
K3_TYPES = frozenset(K3_FORMATS)
OUTCOMES = frozenset({"passed", "failed", "skipped", "xfail", "xpass"})
NS = ("user", "mount", "pid", "net", "ipc", "uts")
GATE_CHAPTERS = ("V1", "V2", "V3", "V4", "V5", "V6")
BUDGET = {
    "wall_seconds": 10, "cpu_soft_seconds": 4, "cpu_hard_seconds": 5,
    "memory_bytes": 268435456, "pids": 32, "output_bytes_per_stream": 1048576,
    "artifact_bytes": 4194304, "artifact_files": 64, "scratch_bytes": 67108864,
    "scratch_inodes": 4096, "open_fds": 64, "core_bytes": 0,
}


class ContractError(ValueError):
    """The binding, manifest or question cannot be read or has an unsafe URI."""


class FixtureUnavailable(ContractError):
    """A ``.out`` file is missing (``missing=True``) or unreadable for a stated reason."""

    def __init__(self, reason, *, missing=False):
        super().__init__(reason)
        self.missing = missing


@dataclass(frozen=True)
class ContractResult:
    """The issues found plus the per-question licence report (never a release approval)."""

    issues: tuple
    licences: tuple
    gate: dict | None = None
    binding_file_sha256: str | None = None

    @property
    def verified(self):
        return not self.issues

    def to_dict(self):
        return {
            "verified": self.verified,
            "issues": [issue.to_dict() for issue in self.issues],
            "licences": list(self.licences),
            "gate": self.gate,
            "binding_file_sha256": self.binding_file_sha256,
        }


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256_text(text):
    return sha256_bytes(text.encode("utf-8"))


def canonical_json_bytes(value):
    """The one canonical JSON byte procedure (sorted keys, UTF-8, no whitespace).

    ``allow_nan=False`` rejects non-finite numbers; a lone surrogate cannot be
    encoded as UTF-8 and raises. Callers must not normalise before this step.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def binding_projection(manifest):
    """The exact §11 projection; ``receipt`` and ``binding_sha256`` are excluded."""
    missing = [key for key in PROJECTION_KEYS if key not in manifest]
    if missing:
        raise ContractError(f"manifest lacks projection field(s): {', '.join(missing)}")
    return {key: manifest[key] for key in PROJECTION_KEYS}


def compute_binding_sha256(manifest):
    return sha256_bytes(canonical_json_bytes(binding_projection(manifest)))


def compute_config_digest(config):
    return sha256_bytes(canonical_json_bytes(config))


def relative_segments(uri):
    """A relative, non-empty URI without an absolute path, NUL, empty, ``.`` or ``..`` segment."""
    if not isinstance(uri, str) or not uri:
        raise ContractError("uri must be a non-empty string")
    if "\x00" in uri or uri.startswith("/") or not _RELATIVE.fullmatch(uri):
        raise ContractError(f"unsafe uri: {uri!r}")
    segments = uri.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ContractError(f"unsafe uri segment in {uri!r}")
    return tuple(segments)


class SafeRoot:
    """Read only regular files below one trusted root through fd-relative, no-follow opens.

    The relative URI is a *form* requirement, not isolation: isolation here is
    the trusted root, ``openat``/``dir_fd``, ``O_NOFOLLOW`` on every component
    and a regular-file ``fstat``. No URI is resolved over the network.
    """

    def __init__(self, root):
        path = Path(root)
        if path.is_symlink():
            raise ContractError(f"trusted root must not be a symlink: {path}")
        try:
            self.root = path.resolve(strict=True)
        except OSError as exc:
            raise ContractError(f"trusted root is unavailable: {path}: {exc}") from exc
        if not self.root.is_dir():
            raise ContractError(f"trusted root is not a directory: {self.root}")

    def read_bytes(self, uri, *, limit=MAX_ARTIFACT_BYTES):
        try:
            return self._read_bytes(uri, limit=limit)
        except OSError as exc:
            raise ContractError(f"cannot read {uri}: {exc}") from exc

    def _read_bytes(self, uri, *, limit):
        segments = relative_segments(uri)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory = flags | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        nofollow = flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(self.root, directory)
        try:
            for segment in segments[:-1]:
                next_fd = os.open(segment, directory, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            file_fd = os.open(segments[-1], nofollow, dir_fd=fd)
            try:
                info = os.fstat(file_fd)
                if not stat.S_ISREG(info.st_mode):
                    raise ContractError(f"not a regular file: {uri}")
                if info.st_size > limit:
                    raise ContractError(f"file exceeds {limit} bytes: {uri}")
                chunks, total = [], 0
                while total <= limit:
                    chunk = os.read(file_fd, min(65536, limit + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                if total > limit:
                    raise ContractError(f"file exceeds {limit} bytes: {uri}")
                return b"".join(chunks)
            finally:
                os.close(file_fd)
        finally:
            os.close(fd)


def _issue(issues, code, path, message):
    issues.append(core.Issue(code, path, message))


def _hash_ok(value):
    return isinstance(value, str) and bool(_HEX64.fullmatch(value)) and value != ZERO_HASH


def _require_hash(issues, value, path, code="hash"):
    if not _hash_ok(value):
        _issue(issues, code, path, "must be a non-zero lowercase sha256")


def _exception_key(exception):
    if exception is None:
        return None
    if not isinstance(exception, dict):
        return ("<invalid>",)
    return (exception.get("type"), exception.get("message"))


def _projection_key(projection):
    if not isinstance(projection, dict):
        return ("<invalid>",)
    return (projection.get("stdout_sha256"), _exception_key(projection.get("exception")))


def _check_exception(issues, exception, path):
    if exception is None:
        return
    if not isinstance(exception, dict):
        _issue(issues, "exception-shape", path, "exception must be an object or null")
        return
    if not isinstance(exception.get("type"), str) or not exception.get("type"):
        _issue(issues, "exception-type", path, "exception.type must be a non-empty string")
    if not isinstance(exception.get("message"), str):
        _issue(issues, "exception-message", path, "exception.message must be a string")
    artifact_id = exception.get("stderr_artifact_id")
    if artifact_id is not None and (not isinstance(artifact_id, str) or not artifact_id):
        _issue(issues, "exception-stderr", path, "stderr_artifact_id must be a non-empty string or null")


def manifest_issues(manifest):
    """Structural and fail-closed checks on one K3 v1.5 manifest (no files read)."""
    issues = []
    if not isinstance(manifest, dict):
        return [core.Issue("manifest-shape", "", "manifest must be an object")]

    if manifest.get("manifest_schema_version") != "1.0.0":
        _issue(issues, "manifest-schema", "/manifest_schema_version", "must be 1.0.0")
    if manifest.get("binding_projection_version") != PROJECTION_VERSION:
        _issue(issues, "binding-projection", "/binding_projection_version", "must be 1")
    try:
        recomputed = compute_binding_sha256(manifest)
    except (ContractError, TypeError, ValueError) as exc:
        _issue(issues, "binding-hash", "", f"cannot compute binding_sha256: {exc}")
        recomputed = None
    declared = manifest.get("binding_sha256")
    _require_hash(issues, declared, "/binding_sha256", "binding-hash")
    if recomputed is not None and declared != recomputed:
        _issue(issues, "binding-hash-mismatch", "/binding_sha256",
               "does not match the canonical §11 projection")

    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict):
        _issue(issues, "bindings-shape", "/bindings", "must be an object")
        bindings = {}
    question_ids = bindings.get("question_ids")
    if not isinstance(question_ids, list) or not question_ids:
        _issue(issues, "question-ids", "/bindings/question_ids", "must be a non-empty list")
        question_ids = []
    if len(set(question_ids)) != len(question_ids):
        _issue(issues, "question-ids-duplicate", "/bindings/question_ids", "ids must be unique")
    question_id = manifest.get("question_id")
    if question_id is not None and question_id not in question_ids:
        _issue(issues, "question-id", "/question_id", "must be null or a member of bindings.question_ids")

    for name in ("harness_version", "runtime_image_sha256", "script_sha256",
                 "config_digest", "fixture_sha256"):
        value = bindings.get(name)
        if name == "harness_version":
            if not isinstance(value, str) or not value:
                _issue(issues, "harness-version", f"/bindings/{name}", "must be a non-empty string")
        else:
            _require_hash(issues, value, f"/bindings/{name}", f"{name}-invalid")
    argv = bindings.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        _issue(issues, "argv", "/bindings/argv", "must be a non-empty string list")
    tool_versions = bindings.get("tool_versions")
    if not isinstance(tool_versions, dict) or not all(
            isinstance(tool_versions.get(name), str) and tool_versions.get(name)
            for name in ("git", "python", "pytest")):
        _issue(issues, "tool-versions", "/bindings/tool_versions",
               "git/python/pytest must be non-empty strings")

    canonical = manifest.get("canonicalisation")
    if not isinstance(canonical, dict):
        _issue(issues, "canonicalisation-shape", "/canonicalisation", "must be an object")
    else:
        mode = canonical.get("mode")
        if mode not in ("none", "named"):
            _issue(issues, "canonicalisation-mode", "/canonicalisation/mode", "must be none or named")
        elif mode == "none":
            if any(name in canonical for name in ("name", "version", "transform_sha256")):
                _issue(issues, "canonicalisation-none", "/canonicalisation",
                       "mode none cannot carry a named transform")
        else:
            for name in ("name", "version"):
                if not isinstance(canonical.get(name), str) or not canonical.get(name):
                    _issue(issues, "canonicalisation-named", f"/canonicalisation/{name}",
                           "a named transform needs a non-empty value")
            _require_hash(issues, canonical.get("transform_sha256"),
                          "/canonicalisation/transform_sha256", "canonicalisation-hash")

    config = manifest.get("config")
    if not isinstance(config, dict):
        _issue(issues, "config-shape", "/config", "must be an object")
    else:
        try:
            digest = compute_config_digest(config)
        except (TypeError, ValueError) as exc:
            _issue(issues, "config-digest", "/config", f"cannot hash config: {exc}")
        else:
            if bindings.get("config_digest") != digest:
                _issue(issues, "config-digest-mismatch", "/bindings/config_digest",
                       "does not match the canonical config preimage")

    inputs = manifest.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        _issue(issues, "inputs", "/inputs", "must be a non-empty list")
        inputs = []
    seen_paths = set()
    for index, item in enumerate(inputs):
        path = f"/inputs/{index}"
        if not isinstance(item, dict):
            _issue(issues, "input-shape", path, "must be an object")
            continue
        if item.get("role") not in ("fixture", "source", "stdin", "config"):
            _issue(issues, "input-role", path + "/role", "unknown role")
        try:
            relative_segments(item.get("path"))
        except ContractError as exc:
            _issue(issues, "input-path", path + "/path", str(exc))
        else:
            if item["path"] in seen_paths:
                _issue(issues, "input-path-duplicate", path + "/path", "duplicate input path")
            seen_paths.add(item["path"])
        _require_hash(issues, item.get("sha256"), path + "/sha256", "input-hash")
        size = item.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            _issue(issues, "input-bytes", path + "/bytes", "must be a non-negative integer")

    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        _issue(issues, "cases", "/cases", "must be a non-empty list")
        cases = []
    case_ids = set()
    for index, item in enumerate(cases):
        path = f"/cases/{index}"
        if not isinstance(item, dict):
            _issue(issues, "case-shape", path, "must be an object")
            continue
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            _issue(issues, "case-id", path + "/case_id", "must be a non-empty string")
        elif case_id in case_ids:
            _issue(issues, "case-id-duplicate", path + "/case_id", "case_id must be unique")
        else:
            case_ids.add(case_id)
        snippet_id = item.get("snippet_id")
        if snippet_id is not None and not isinstance(snippet_id, str):
            _issue(issues, "case-snippet-type", path + "/snippet_id",
                   "must be a string or null")
        _require_hash(issues, item.get("source_sha256"), path + "/source_sha256", "source-hash")
        _require_hash(issues, item.get("stdin_sha256"), path + "/stdin_sha256", "stdin-hash")
        expected, actual = item.get("expected"), item.get("actual")
        for name, projection in (("expected", expected), ("actual", actual)):
            if not isinstance(projection, dict):
                _issue(issues, "projection-shape", f"{path}/{name}", "must be an object")
                continue
            _require_hash(issues, projection.get("stdout_sha256"), f"{path}/{name}/stdout_sha256",
                          "stdout-hash")
            _check_exception(issues, projection.get("exception"), f"{path}/{name}/exception")
        outcome = item.get("outcome")
        if outcome not in OUTCOMES:
            _issue(issues, "case-outcome", path + "/outcome", "unknown outcome")
        truncated, complete = item.get("truncated"), item.get("complete")
        if not isinstance(truncated, bool) or not isinstance(complete, bool):
            _issue(issues, "case-completeness", path, "truncated/complete must be booleans")
        else:
            if truncated and complete:
                _issue(issues, "case-truncated-complete", path, "truncated and complete cannot both be true")
            if not truncated and not complete:
                _issue(issues, "case-incomplete", path, "a case must be complete or explicitly truncated")
            if truncated and outcome == "passed":
                _issue(issues, "case-truncated-passed", path, "a truncated case cannot be passed")
        if outcome != "passed":
            _issue(issues, "case-not-passed", path,
                   f"bound case outcome is {outcome!r}; only passed is verified facit")
        if isinstance(expected, dict) and isinstance(actual, dict):
            same = _projection_key(expected) == _projection_key(actual)
            if same and outcome == "failed":
                _issue(issues, "case-outcome-mismatch", path, "identical projections cannot be failed")
            if not same and outcome == "passed":
                _issue(issues, "case-expected-mismatch", path,
                       "outcome is passed but expected and actual differ")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        _issue(issues, "artifacts", "/artifacts", "must be a list")
        artifacts = []
    artifact_ids = set()
    for index, item in enumerate(artifacts):
        path = f"/artifacts/{index}"
        if not isinstance(item, dict):
            _issue(issues, "artifact-shape", path, "must be an object")
            continue
        artifact_id = item.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            _issue(issues, "artifact-id", path + "/artifact_id", "must be a non-empty string")
        elif artifact_id in artifact_ids:
            _issue(issues, "artifact-id-duplicate", path + "/artifact_id", "artifact_id must be unique")
        else:
            artifact_ids.add(artifact_id)
        kind, fmt = item.get("type"), item.get("format")
        if kind not in K3_TYPES:
            _issue(issues, "artifact-type", path + "/type", "unknown artifact type")
        elif fmt not in K3_FORMATS[kind]:
            _issue(issues, "artifact-format", path + "/format", f"{fmt!r} is not valid for {kind!r}")
        else:
            media = item.get("media_type")
            if media not in K3_FORMATS[kind][fmt]:
                _issue(issues, "artifact-media-type", path + "/media_type",
                       f"must be one of {K3_FORMATS[kind][fmt]}")
        if not isinstance(item.get("encoding"), str) or not item.get("encoding"):
            _issue(issues, "artifact-encoding", path + "/encoding", "must be a non-empty string")
        _require_hash(issues, item.get("sha256"), path + "/sha256", "artifact-hash")
        size = item.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            _issue(issues, "artifact-bytes", path + "/bytes", "must be a non-negative integer")
        truncated, complete = item.get("truncated"), item.get("complete")
        if not isinstance(truncated, bool) or not isinstance(complete, bool):
            _issue(issues, "artifact-completeness", path, "truncated/complete must be booleans")
        else:
            if truncated and complete:
                _issue(issues, "artifact-truncated-complete", path,
                       "truncated and complete cannot both be true")
            if not truncated and not complete:
                _issue(issues, "artifact-incomplete", path,
                       "an artifact must be complete or explicitly truncated")
        if item.get("executable") is not False:
            _issue(issues, "artifact-executable", path + "/executable",
                   "artifacts are data; executable must be false")
        if kind == "trace":
            if item.get("is_trace") is not True:
                _issue(issues, "trace-flag", path + "/is_trace", "a trace must have is_trace=true")
            if fmt != "json":
                _issue(issues, "trace-format", path + "/format", "a trace must be JSON")
        elif item.get("is_trace") is not False:
            _issue(issues, "artifact-trace-flag", path + "/is_trace",
                   "a non-trace artifact must have is_trace=false")

    _test_report_issues(issues, manifest.get("test_report"))
    _receipt_issues(issues, manifest.get("receipt"), artifacts)

    licence = manifest.get("licence")
    if not isinstance(licence, dict):
        _issue(issues, "licence-shape", "/licence", "must be an object")
    else:
        if licence.get("license") not in ALLOWED_LICENSES:
            _issue(issues, "licence-license", "/licence/license", "unknown licence identifier")
        if licence.get("gate") != "draft-only":
            _issue(issues, "licence-gate", "/licence/gate", "must be draft-only")

    return issues


def _test_report_issues(issues, report):
    if not isinstance(report, dict):
        _issue(issues, "test-report-shape", "/test_report", "must be an object")
        return
    if report.get("schema_version") != "1.0.0":
        _issue(issues, "test-report-schema", "/test_report/schema_version", "must be 1.0.0")
    if not isinstance(report.get("tool"), str) or not report.get("tool"):
        _issue(issues, "test-report-tool", "/test_report/tool", "must be a non-empty string")
    if not isinstance(report.get("tool_version"), str) or not report.get("tool_version"):
        _issue(issues, "test-report-tool-version", "/test_report/tool_version",
               "must be a non-empty string")
    tests = report.get("tests")
    if not isinstance(tests, list):
        _issue(issues, "test-report-tests", "/test_report/tests", "must be a list")
        tests = []
    seen = set()
    actual = {name: 0 for name in ("passed", "failed", "skipped", "xfail", "xpass")}
    for index, test in enumerate(tests):
        path = f"/test_report/tests/{index}"
        if not isinstance(test, dict):
            _issue(issues, "test-shape", path, "must be an object")
            continue
        test_id = test.get("test_id")
        if not isinstance(test_id, str) or not test_id:
            _issue(issues, "test-id", path + "/test_id", "must be a non-empty string")
        elif test_id in seen:
            _issue(issues, "test-id-duplicate", path + "/test_id", "test_id must be unique")
        else:
            seen.add(test_id)
        outcome = test.get("outcome")
        if outcome not in OUTCOMES:
            _issue(issues, "test-outcome", path + "/outcome", "unknown outcome")
        else:
            actual[outcome] += 1
    counts = report.get("counts")
    if not isinstance(counts, dict):
        _issue(issues, "test-counts-shape", "/test_report/counts", "must be an object")
        return
    total = counts.get("total")
    if not isinstance(total, int) or isinstance(total, bool) or total < 1:
        _issue(issues, "test-total", "/test_report/counts/total", "must be greater than zero")
        total = 0
    if total != len(tests):
        _issue(issues, "test-total-mismatch", "/test_report/counts/total",
               "must equal the number of tests")
    for name, value in actual.items():
        declared = counts.get(name)
        if not isinstance(declared, int) or isinstance(declared, bool) or declared < 0:
            _issue(issues, "test-count", f"/test_report/counts/{name}",
                   "must be a non-negative integer")
        elif declared != value:
            _issue(issues, "test-count-mismatch", f"/test_report/counts/{name}",
                   f"declares {declared} but tests contain {value}")
    if not isinstance(counts.get("passed"), int) or counts.get("passed", 0) < 1:
        _issue(issues, "test-passed", "/test_report/counts/passed",
               "at least one passed test is required")
    if total and sum(actual.values()) != total:
        _issue(issues, "test-outcome-total", "/test_report/counts/total",
               "outcome counts do not cover every test")


def _receipt_issues(issues, receipt, artifacts):
    if not isinstance(receipt, dict):
        _issue(issues, "receipt-shape", "/receipt", "must be an object")
        return
    if receipt.get("status") != "ok":
        _issue(issues, "receipt-status", "/receipt/status",
               "a non-ok receipt is rejected even with an unchanged binding_sha256")
    if receipt.get("limit_hit") != []:
        _issue(issues, "receipt-limit-hit", "/receipt/limit_hit",
               "a resource limit was hit; not a verified run")
    if receipt.get("exit_code") != 0:
        _issue(issues, "receipt-exit", "/receipt/exit_code", "must be 0 for a verified run")
    if not isinstance(receipt.get("started_at"), str) or not receipt.get("started_at"):
        _issue(issues, "receipt-started", "/receipt/started_at", "must be a non-empty string")
    duration = receipt.get("duration_ms")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
        _issue(issues, "receipt-duration", "/receipt/duration_ms",
               "must be a non-negative integer")

    stdout_hash, stderr_hash = receipt.get("stdout_sha256"), receipt.get("stderr_sha256")
    _require_hash(issues, stdout_hash, "/receipt/stdout_sha256", "receipt-hash")
    _require_hash(issues, stderr_hash, "/receipt/stderr_sha256", "receipt-hash")
    for stream in ("stdout", "stderr"):
        size = receipt.get(f"{stream}_bytes")
        digest = receipt.get(f"{stream}_sha256")
        if isinstance(size, int) and size == 0 and isinstance(digest, str) and digest != EMPTY_HASH:
            _issue(issues, "receipt-empty-hash", f"/receipt/{stream}_sha256",
                   "zero bytes require the empty-string hash")

    usage = receipt.get("usage")
    budget = receipt.get("budget")
    if not isinstance(usage, dict):
        _issue(issues, "receipt-usage", "/receipt/usage", "must be an object")
        usage = {}
    for field in ("wall_ms", "cpu_ms", "memory_peak_bytes", "pids_peak", "stdout_bytes",
                  "stderr_bytes", "artifact_bytes", "scratch_bytes", "scratch_inodes",
                  "open_fds_peak"):
        value = usage.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            _issue(issues, "receipt-usage-field", f"/receipt/usage/{field}",
                   "must be a non-negative integer")
    if isinstance(receipt.get("stdout_bytes"), int) and usage.get("stdout_bytes") != receipt["stdout_bytes"]:
        _issue(issues, "receipt-usage-stdout", "/receipt/usage/stdout_bytes",
               "must equal receipt.stdout_bytes")
    if isinstance(receipt.get("stderr_bytes"), int) and usage.get("stderr_bytes") != receipt["stderr_bytes"]:
        _issue(issues, "receipt-usage-stderr", "/receipt/usage/stderr_bytes",
               "must equal receipt.stderr_bytes")
    declared_artifacts = sum(item.get("bytes", 0) for item in artifacts
                             if isinstance(item, dict) and isinstance(item.get("bytes"), int))
    if usage.get("artifact_bytes") != declared_artifacts:
        _issue(issues, "receipt-artifact-bytes", "/receipt/usage/artifact_bytes",
               "must equal the sum of artifact bytes")

    if not isinstance(budget, dict):
        _issue(issues, "receipt-budget", "/receipt/budget", "must be an object")
        budget = {}
    else:
        for name, expected in BUDGET.items():
            if budget.get(name) != expected:
                _issue(issues, "receipt-budget-policy", f"/receipt/budget/{name}",
                       f"must equal the frozen limit {expected}")
    checks = (
        ("wall_ms", "wall_seconds", 1000),
        ("cpu_ms", "cpu_hard_seconds", 1000),
        ("memory_peak_bytes", "memory_bytes", 1),
        ("pids_peak", "pids", 1),
        ("artifact_bytes", "artifact_bytes", 1),
        ("scratch_bytes", "scratch_bytes", 1),
        ("scratch_inodes", "scratch_inodes", 1),
        ("open_fds_peak", "open_fds", 1),
    )
    for used, limit_name, factor in checks:
        limit = budget.get(limit_name)
        value = usage.get(used)
        if isinstance(limit, int) and isinstance(value, int) and value > limit * factor:
            _issue(issues, "receipt-budget-exceeded", f"/receipt/usage/{used}",
                   f"exceeds budget {limit_name}")
    for stream in ("stdout", "stderr"):
        limit = budget.get("output_bytes_per_stream")
        value = usage.get(f"{stream}_bytes")
        if isinstance(limit, int) and isinstance(value, int) and value > limit:
            _issue(issues, "receipt-budget-exceeded", f"/receipt/usage/{stream}_bytes",
                   "exceeds output_bytes_per_stream")

    isolation = receipt.get("isolation")
    if not isinstance(isolation, dict):
        _issue(issues, "receipt-isolation", "/receipt/isolation", "must be an object")
        return
    if isolation.get("mechanism") != "bwrap+cgroup-v2":
        _issue(issues, "receipt-isolation-mechanism", "/receipt/isolation/mechanism",
               "must be the frozen bwrap+cgroup-v2 mechanism")
    namespaces = isolation.get("namespaces")
    if not isinstance(namespaces, list) or not all(ns in namespaces for ns in NS):
        _issue(issues, "receipt-isolation-namespaces", "/receipt/isolation/namespaces",
               f"must include {', '.join(NS)}")
    if isolation.get("capabilities") != "none":
        _issue(issues, "receipt-isolation-capabilities", "/receipt/isolation/capabilities",
               "must be none")
    if isolation.get("no_new_privileges") is not True:
        _issue(issues, "receipt-isolation-privileges", "/receipt/isolation/no_new_privileges",
               "must be true")
    if isolation.get("network") != "denied":
        _issue(issues, "receipt-isolation-network", "/receipt/isolation/network",
               "must be denied")
    if isolation.get("kill") != "whole-cgroup":
        _issue(issues, "receipt-isolation-kill", "/receipt/isolation/kill",
               "must kill the whole cgroup")


def _binding_structure_issues(binding):
    issues = []
    if not isinstance(binding, dict):
        return [core.Issue("binding-shape", "", "binding must be an object")]
    if binding.get("schema_version") != SCHEMA_VERSION:
        _issue(issues, "binding-schema", "/schema_version", f"must be {SCHEMA_VERSION}")
    for field in ("manifest_uri", "manifest_sha256", "questions"):
        if field not in binding:
            _issue(issues, "binding-field", f"/{field}", "is required")
    try:
        relative_segments(binding.get("manifest_uri"))
    except ContractError as exc:
        _issue(issues, "manifest-uri", "/manifest_uri", str(exc))
    _require_hash(issues, binding.get("manifest_sha256"), "/manifest_sha256", "manifest-hash")
    artifact_uris = binding.get("artifact_uris")
    if artifact_uris is not None:
        if not isinstance(artifact_uris, dict):
            _issue(issues, "artifact-uris-shape", "/artifact_uris", "must be an object")
        else:
            for artifact_id, uri in artifact_uris.items():
                if not isinstance(artifact_id, str) or not artifact_id:
                    _issue(issues, "artifact-uri-id", "/artifact_uris",
                           "artifact ids must be non-empty strings")
                try:
                    relative_segments(uri)
                except ContractError as exc:
                    _issue(issues, "artifact-uri", f"/artifact_uris/{artifact_id}", str(exc))
    questions = binding.get("questions")
    if not isinstance(questions, list) or not questions:
        _issue(issues, "binding-questions", "/questions", "must be a non-empty list")
        questions = []
    seen_uris = set()
    for index, entry in enumerate(questions):
        path = f"/questions/{index}"
        if not isinstance(entry, dict):
            _issue(issues, "binding-question-shape", path, "must be an object")
            continue
        uri = entry.get("question_uri")
        try:
            relative_segments(uri)
        except ContractError as exc:
            _issue(issues, "question-uri", path + "/question_uri", str(exc))
        else:
            if uri in seen_uris:
                _issue(issues, "question-uri-duplicate", path + "/question_uri", "duplicate question_uri")
            seen_uris.add(uri)
        _require_hash(issues, entry.get("question_sha256"), path + "/question_sha256",
                      "question-hash")
        if entry.get("license") not in ALLOWED_LICENSES:
            _issue(issues, "binding-licence", path + "/license", "unknown licence identifier")
        gate = entry.get("gate")
        if gate is not None:
            if not isinstance(gate, dict):
                _issue(issues, "gate-shape", path + "/gate", "must be an object")
            else:
                chapter, objective = gate.get("chapter"), gate.get("objective")
                if chapter not in GATE_CHAPTERS:
                    _issue(issues, "gate-chapter", path + "/gate/chapter",
                           "must be one of V1..V6")
                if not isinstance(objective, str) or not re.fullmatch(
                        r"V[1-6]\.O[1-9][0-9]*", objective):
                    _issue(issues, "gate-objective", path + "/gate/objective",
                           "must look like V1.O2")
                elif isinstance(chapter, str) and not objective.startswith(chapter + "."):
                    _issue(issues, "gate-objective", path + "/gate/objective",
                           "objective does not belong to the declared chapter")
                if not isinstance(gate.get("pilot"), str) or not gate.get("pilot"):
                    _issue(issues, "gate-pilot", path + "/gate/pilot", "must be non-empty")
                if gate.get("status") != "draft-pilot":
                    _issue(issues, "gate-status", path + "/gate/status", "must be draft-pilot")
        review = entry.get("review")
        if review is not None and gate is None:
            _issue(issues, "gate-missing", path + "/gate",
                   "a review question needs a gate declaration")
        if review is not None:
            if not isinstance(review, dict):
                _issue(issues, "review-shape", path + "/review", "must be an object")
            else:
                for field in ("before_snippet_id", "after_snippet_id", "diff_artifact_id",
                              "fromfile", "tofile"):
                    value = review.get(field)
                    if not isinstance(value, str) or not value:
                        _issue(issues, "review-field", f"{path}/review/{field}",
                               "must be a non-empty string")
                if review.get("before_snippet_id") == review.get("after_snippet_id"):
                    _issue(issues, "review-same-snippet", path + "/review",
                           "before and after must be different snippets")
                for field in ("fromfile", "tofile"):
                    value = review.get(field)
                    if isinstance(value, str) and ("\n" in value or "\r" in value):
                        _issue(issues, "review-header", f"{path}/review/{field}",
                               "must not contain a line break")
        question_artifacts = entry.get("artifact_ids")
        if question_artifacts is not None:
            if not isinstance(question_artifacts, list) or \
                    not all(isinstance(item, str) and item for item in question_artifacts):
                _issue(issues, "binding-artifact-ids", path + "/artifact_ids",
                       "must be a list of non-empty strings")
            elif len(set(question_artifacts)) != len(question_artifacts):
                _issue(issues, "binding-artifact-ids-duplicate", path + "/artifact_ids",
                       "artifact_ids must be unique")
        cases = entry.get("cases")
        if not isinstance(cases, list):
            _issue(issues, "binding-cases", path + "/cases", "must be a list")
            continue
        case_ids, case_indexes = set(), set()
        for position, case in enumerate(cases):
            case_path = f"{path}/cases/{position}"
            if not isinstance(case, dict):
                _issue(issues, "binding-case-shape", case_path, "must be an object")
                continue
            case_id = case.get("case_id")
            if not isinstance(case_id, str) or not case_id:
                _issue(issues, "binding-case-id", case_path + "/case_id", "must be a non-empty string")
            elif case_id in case_ids:
                _issue(issues, "binding-case-id-duplicate", case_path + "/case_id", "duplicate case_id")
            else:
                case_ids.add(case_id)
            case_index = case.get("case_index")
            if not isinstance(case_index, int) or isinstance(case_index, bool) or case_index < 0:
                _issue(issues, "binding-case-index", case_path + "/case_index",
                       "must be a non-negative integer")
            elif case_index in case_indexes:
                _issue(issues, "binding-case-index-duplicate", case_path + "/case_index",
                       "duplicate case_index")
            else:
                case_indexes.add(case_index)
            artifact_ids = case.get("artifact_ids")
            if not isinstance(artifact_ids, list) or not artifact_ids:
                _issue(issues, "binding-artifact-ids", case_path + "/artifact_ids",
                       "must be a non-empty list")
            elif len(set(artifact_ids)) != len(artifact_ids):
                _issue(issues, "binding-artifact-ids-duplicate", case_path + "/artifact_ids",
                       "artifact_ids must be unique")
            trace_id = case.get("trace_artifact_id")
            if trace_id is not None and (not isinstance(trace_id, str) or not trace_id):
                _issue(issues, "binding-trace-id", case_path + "/trace_artifact_id",
                       "must be a non-empty string or null")
    return issues


def _question_structure_issues(question):
    issues = []
    if not isinstance(question, dict):
        return [core.Issue("question-shape", "", "question must be an object")]
    if question.get("license") not in ALLOWED_LICENSES:
        _issue(issues, "question-licence", "/license", "unknown licence identifier")
    verification = question.get("verification")
    cases = verification.get("cases") if isinstance(verification, dict) else None
    if not isinstance(cases, list):
        _issue(issues, "question-cases", "/verification/cases", "must be a list")
        cases = []
    if question.get("language") == "text" and cases:
        _issue(issues, "question-text-cases", "/verification/cases",
               "a text-only question has no executable cases")
    for index, case in enumerate(cases):
        path = f"/verification/cases/{index}"
        if not isinstance(case, dict):
            _issue(issues, "question-case-shape", path, "must be an object")
            continue
        snippet_id = case.get("snippet_id")
        raw_snippets = question.get("snippets", [])
        if not isinstance(raw_snippets, list):
            raw_snippets = []
        snippets = {item.get("id") for item in raw_snippets if isinstance(item, dict)}
        if snippet_id is not None and snippet_id not in snippets:
            _issue(issues, "question-snippet", path + "/snippet_id", "unknown snippet")
        if not isinstance(case.get("stdin"), str):
            _issue(issues, "question-stdin", path + "/stdin", "must be a string")
        expected = case.get("expected")
        if not isinstance(expected, dict):
            _issue(issues, "question-expected", path + "/expected", "must be an object")
            continue
        if not isinstance(expected.get("stdout"), str):
            _issue(issues, "question-stdout", path + "/expected/stdout", "must be a string")
        exit_code = expected.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool) or not 0 <= exit_code <= 255:
            _issue(issues, "question-exit", path + "/expected/exit_code", "must be 0..255")
        if "stderr_contains" in expected and (not isinstance(expected["stderr_contains"], str)
                                              or not expected["stderr_contains"]):
            _issue(issues, "question-stderr", path + "/expected/stderr_contains",
                   "must be a non-empty string")
    artifacts = question.get("artifacts", [])
    if not isinstance(artifacts, list):
        _issue(issues, "question-artifacts", "/artifacts", "must be a list")
        artifacts = []
    if question.get("language") == "text" and not artifacts:
        _issue(issues, "question-text-artifacts", "/artifacts",
               "a text-only question requires at least one artifact")
    seen = set()
    for index, item in enumerate(artifacts):
        path = f"/artifacts/{index}"
        if not isinstance(item, dict):
            _issue(issues, "question-artifact-shape", path, "must be an object")
            continue
        artifact_id = item.get("id")
        if not isinstance(artifact_id, str) or not artifact_id:
            _issue(issues, "question-artifact-id", path + "/id", "must be a non-empty string")
        elif artifact_id in seen:
            _issue(issues, "question-artifact-id-duplicate", path + "/id",
                   "artifact id must be unique")
        else:
            seen.add(artifact_id)
        if (item.get("kind"), item.get("format")) not in K1_TO_K3:
            _issue(issues, "question-artifact-kind", path, "unknown kind/format")
        if not isinstance(item.get("text"), str):
            _issue(issues, "question-artifact-text", path + "/text", "must be a string")
            continue
        try:
            digest = sha256_text(item["text"])
        except UnicodeEncodeError:
            _issue(issues, "question-artifact-encoding", path + "/text",
                   "not encodable as UTF-8 (lone surrogate)")
            continue
        if item.get("sha256") != digest:
            _issue(issues, "question-artifact-hash", path + "/sha256",
                   "does not match the UTF-8 bytes of text")
        provenance = item.get("provenance")
        if not isinstance(provenance, dict):
            _issue(issues, "question-provenance", path + "/provenance", "must be an object")
        else:
            if provenance.get("fixture_output_sha256") != digest:
                _issue(issues, "question-provenance-hash", path + "/provenance/fixture_output_sha256",
                       "does not match the displayed text")
            try:
                relative_segments(f"content/fixtures/artifacts/{provenance.get('fixture_id')}.out")
            except ContractError:
                _issue(issues, "question-fixture-id", path + "/provenance/fixture_id",
                       "must be a safe fixture id")
    return issues


def validate(manifest, question, binding, *, manifest_bytes=None, question_bytes=None,
             artifact_bytes=None, fixture_bytes=None):
    """Validate one question against one manifest and the K4 binding document.

    ``manifest_bytes``/``question_bytes`` are the exact bytes the caller read;
    when given they are checked against ``manifest_sha256``/``question_sha256``.
    ``artifact_bytes`` maps manifest artifact ids to bytes (for K3-only
    artifacts and traces); ``fixture_bytes`` maps fixture ids to the content of
    ``content/fixtures/artifacts/<fixture_id>.out``. Missing bytes are reported
    as issues, never silently ignored. No file is read here.
    """
    issues = list(_binding_structure_issues(binding))
    issues.extend(manifest_issues(manifest))
    issues.extend(_question_structure_issues(question))
    if not isinstance(binding, dict) or not isinstance(manifest, dict) or not isinstance(question, dict):
        return ContractResult(tuple(issues), ())

    if manifest_bytes is not None and binding.get("manifest_sha256") != sha256_bytes(manifest_bytes):
        _issue(issues, "manifest-hash-mismatch", "/manifest_sha256",
               "does not match the manifest bytes")
    if manifest_bytes is None:
        _issue(issues, "manifest-bytes", "/manifest_sha256", "manifest bytes are required")

    questions = binding.get("questions")
    if not isinstance(questions, list):
        return ContractResult(tuple(issues), ())
    entry = None
    for candidate in questions:
        if isinstance(candidate, dict) and candidate.get("question_uri") and \
                candidate.get("question_sha256") == (sha256_bytes(question_bytes)
                                                     if question_bytes is not None else None):
            entry = candidate
            break
    if entry is None and len(questions) == 1 and isinstance(questions[0], dict):
        entry = questions[0]
    if entry is None:
        _issue(issues, "question-not-bound", "/questions",
               "no binding entry matches this question")
        return ContractResult(tuple(issues), ())

    if question_bytes is not None and entry.get("question_sha256") != sha256_bytes(question_bytes):
        _issue(issues, "question-hash-mismatch", "/questions/0/question_sha256",
               "does not match the question bytes")
    if question_bytes is None:
        _issue(issues, "question-bytes", "/questions/0/question_sha256", "question bytes are required")

    raw_cases = manifest.get("cases")
    raw_cases = raw_cases if isinstance(raw_cases, list) else []
    manifest_cases = {case["case_id"]: case for case in raw_cases
                      if isinstance(case, dict) and isinstance(case.get("case_id"), str)}
    raw_artifacts = manifest.get("artifacts")
    raw_artifacts = raw_artifacts if isinstance(raw_artifacts, list) else []
    manifest_artifacts = {artifact["artifact_id"]: artifact
                          for artifact in raw_artifacts
                          if isinstance(artifact, dict) and isinstance(artifact.get("artifact_id"), str)}
    verification = question.get("verification")
    question_cases = verification.get("cases") if isinstance(verification, dict) else []
    question_cases = question_cases if isinstance(question_cases, list) else []
    raw_snippets = question.get("snippets")
    raw_snippets = raw_snippets if isinstance(raw_snippets, list) else []
    snippets = {item["id"]: item for item in raw_snippets
                if isinstance(item, dict) and isinstance(item.get("id"), str)}
    binding_cases = entry.get("cases") if isinstance(entry.get("cases"), list) else []

    if question.get("language") == "text":
        if binding_cases:
            _issue(issues, "text-question-cases", "/questions/0/cases",
                   "a text-only question has no executable cases to bind")
    elif not binding_cases:
        _issue(issues, "cases-missing", "/questions/0/cases",
               "a question with snippets must bind its verification cases")
    if len(binding_cases) != len(question_cases):
        _issue(issues, "case-count", "/questions/0/cases",
               f"binds {len(binding_cases)} cases but the question has {len(question_cases)}")
    bound_indexes = [case.get("case_index") for case in binding_cases if isinstance(case, dict)]
    if sorted(index for index in bound_indexes if isinstance(index, int)) != list(range(len(question_cases))):
        _issue(issues, "case-index-coverage", "/questions/0/cases",
               "case_index must cover every question case exactly once")

    raw_question_artifacts = question.get("artifacts")
    raw_question_artifacts = raw_question_artifacts if isinstance(raw_question_artifacts, list) else []
    question_artifact_by_id = {item["id"]: item for item in raw_question_artifacts
                               if isinstance(item, dict) and isinstance(item.get("id"), str)}
    referenced = set(entry.get("artifact_ids") or [])
    for artifact_id in referenced:
        if artifact_id not in manifest_artifacts:
            _issue(issues, "artifact-unknown", "/questions/0/artifact_ids",
                   f"{artifact_id!r} is not in the manifest")
    verified_artifact_hashes = set()
    for position, case in enumerate(binding_cases):
        path = f"/questions/0/cases/{position}"
        if not isinstance(case, dict):
            continue
        case_id = case.get("case_id")
        case_index = case.get("case_index")
        manifest_case = manifest_cases.get(case_id)
        if manifest_case is None:
            _issue(issues, "case-unknown", path + "/case_id", "case_id is not in the manifest")
            continue
        if not isinstance(case_index, int) or isinstance(case_index, bool) or \
                not 0 <= case_index < len(question_cases):
            _issue(issues, "case-index", path + "/case_index", "does not address a question case")
            continue
        question_case = question_cases[case_index]
        if not isinstance(question_case, dict):
            continue
        if question_case.get("snippet_id") != manifest_case.get("snippet_id"):
            _issue(issues, "case-snippet", path, "snippet_id differs from the manifest")
        _check_case_bytes(issues, question_case, manifest_case, manifest, path, snippets,
                          case_id)
        for artifact_id in case.get("artifact_ids", []) if isinstance(case.get("artifact_ids"), list) else []:
            referenced.add(artifact_id)
            if artifact_id not in manifest_artifacts:
                _issue(issues, "artifact-unknown", path + "/artifact_ids",
                       f"{artifact_id!r} is not in the manifest")
        trace_id = case.get("trace_artifact_id")
        if trace_id is not None:
            referenced.add(trace_id)
            trace = manifest_artifacts.get(trace_id)
            if trace is None:
                _issue(issues, "trace-unknown", path + "/trace_artifact_id",
                       "trace artifact is not in the manifest")
            elif trace.get("type") != "trace":
                _issue(issues, "trace-type", path + "/trace_artifact_id",
                       "must reference a trace artifact")
            if question.get("language") == "text":
                _issue(issues, "trace-text-question", path + "/trace_artifact_id",
                       "a text-only question has no trace")
            if not question_case.get("snippet_id"):
                _issue(issues, "trace-without-snippet", path + "/trace_artifact_id",
                       "a trace requires a Python snippet")

    for artifact_id, artifact in question_artifact_by_id.items():
        path = f"/artifacts/{artifact_id}"
        try:
            text_bytes = artifact["text"].encode("utf-8")
        except (KeyError, UnicodeEncodeError):
            continue
        digest = sha256_bytes(text_bytes)
        verified_artifact_hashes.add(digest)
        matches = [item for item in manifest_artifacts.values()
                   if item.get("sha256") == digest]
        if len(matches) != 1:
            _issue(issues, "question-artifact-unbound", path,
                   "displayed text must match exactly one manifest artifact")
            continue
        match = matches[0]
        if match["artifact_id"] not in referenced:
            _issue(issues, "question-artifact-not-referenced", path,
                   "the matching manifest artifact is not referenced by any bound case")
        kind, fmt = artifact.get("kind"), artifact.get("format")
        mapped = K1_TO_K3.get((kind, fmt))
        if mapped is None:
            continue
        wanted_type, wanted_format, wanted_media = mapped
        if (match.get("type"), match.get("format"), match.get("media_type")) != \
                (wanted_type, wanted_format, wanted_media):
            _issue(issues, "question-artifact-mapping", path,
                   f"manifest artifact is {match.get('type')}/{match.get('format')}/"
                   f"{match.get('media_type')}, expected {wanted_type}/{wanted_format}/{wanted_media}")
        if match.get("bytes") != len(text_bytes):
            _issue(issues, "question-artifact-bytes", path, "manifest bytes differ from the text")
        if match.get("truncated") or not match.get("complete"):
            _issue(issues, "question-artifact-complete", path,
                   "a question artifact must be complete and not truncated")
        if question.get("language") == "text" and match.get("type") == "trace":
            _issue(issues, "trace-text-question", path,
                   "a text-only question cannot bind a trace")
        if kind == "test-report" and match.get("format") != "text":
            _issue(issues, "test-report-artifact-format", path,
                   "a question test-report artifact is display text, never JSON")
        provenance = artifact.get("provenance", {}) if isinstance(artifact.get("provenance"), dict) else {}
        fixture_id = provenance.get("fixture_id")
        if fixture_id:
            try:
                safe_fixture = relative_segments(f"content/fixtures/artifacts/{fixture_id}.out")
            except ContractError:
                safe_fixture = None
            if safe_fixture is not None and fixture_bytes is not None:
                try:
                    fixture = fixture_bytes(fixture_id)
                except FixtureUnavailable as exc:
                    _issue(issues, "fixture-missing" if exc.missing else "fixture-unreadable",
                           path, str(exc))
                except (ContractError, OSError) as exc:
                    _issue(issues, "fixture-unreadable", path, str(exc))
                else:
                    if fixture is None:
                        _issue(issues, "fixture-missing", path,
                               "content/fixtures/artifacts/<fixture_id>.out is missing")
                    elif fixture != text_bytes:
                        _issue(issues, "fixture-bytes", path,
                               "displayed text is not byte-identical to the fixture output")

    review = entry.get("review")
    if review is not None:
        _review_issues(issues, review, question, manifest_cases, manifest_artifacts,
                       binding_cases, question_artifact_by_id, referenced)

    manifest_licence = manifest.get("licence")
    manifest_licence = manifest_licence if isinstance(manifest_licence, dict) else {}
    if manifest_licence.get("license") != entry.get("license"):
        _issue(issues, "licence-manifest", "/questions/0/license",
               "does not match manifest licence.license")
    if question.get("license") != entry.get("license"):
        _issue(issues, "licence-question", "/questions/0/license",
               "does not match the question's licence")

    all_question_licenses = {item.get("license") for item in questions
                             if isinstance(item, dict)}
    if len(all_question_licenses) > 1:
        _issue(issues, "mixed-licence", "/questions",
               "mixed licences require one manifest per question/licence")

    manifest_bindings = manifest.get("bindings")
    manifest_bindings = manifest_bindings if isinstance(manifest_bindings, dict) else {}
    question_ids = manifest_bindings.get("question_ids", [])
    question_ids = question_ids if isinstance(question_ids, list) else []
    if question.get("id") not in question_ids:
        _issue(issues, "question-not-in-manifest", "/bindings/question_ids",
               "the question id is not bound by the manifest")

    _check_referenced_artifacts(issues, manifest_artifacts, referenced,
                                verified_artifact_hashes, artifact_bytes)
    licences = tuple({"question_id": item.get("id"), "license": entry.get("license"),
                      "gate": "draft-only"} for item in [question])
    gate = entry.get("gate") if isinstance(entry.get("gate"), dict) else None
    return ContractResult(tuple(issues), licences, gate)


def _review_issues(issues, review, question, manifest_cases, manifest_artifacts,
                   binding_cases, question_artifacts, referenced):
    """Review (V1/V4/V6): the diff artifact is the exact unified diff of the bound before/after bytes."""
    if not isinstance(review, dict):
        return
    before_id, after_id = review.get("before_snippet_id"), review.get("after_snippet_id")
    raw_snippets = question.get("snippets")
    raw_snippets = raw_snippets if isinstance(raw_snippets, list) else []
    snippets = {item["id"]: item for item in raw_snippets
                if isinstance(item, dict) and isinstance(item.get("id"), str)}
    before, after = snippets.get(before_id), snippets.get(after_id)
    if before is None or after is None:
        _issue(issues, "review-snippet", "/questions/0/review",
               "before_snippet_id/after_snippet_id must name question snippets")
        return
    if not isinstance(before.get("code"), str) or not isinstance(after.get("code"), str):
        _issue(issues, "review-snippet-code", "/questions/0/review", "snippet code must be text")
        return
    if not before["code"].endswith("\n") or not after["code"].endswith("\n"):
        _issue(issues, "review-snippet-newline", "/questions/0/review",
               "before and after snippet code must end with a newline")
        return

    diff = question_artifacts.get(review.get("diff_artifact_id"))
    if diff is None:
        _issue(issues, "review-diff-artifact", "/questions/0/review/diff_artifact_id",
               "must name a question artifact")
        return
    if (diff.get("kind"), diff.get("format")) != ("diff", "unified-diff"):
        _issue(issues, "review-diff-artifact", "/questions/0/review/diff_artifact_id",
               "the review diff artifact must be kind diff/format unified-diff")

    bound_snippets = set()
    for case in binding_cases:
        if not isinstance(case, dict):
            continue
        manifest_case = manifest_cases.get(case.get("case_id"))
        if isinstance(manifest_case, dict):
            bound_snippets.add(manifest_case.get("snippet_id"))
    if before_id not in bound_snippets:
        _issue(issues, "review-before-case", "/questions/0/review/before_snippet_id",
               "the before snippet is not bound by a manifest case")
    if after_id not in bound_snippets:
        _issue(issues, "review-after-case", "/questions/0/review/after_snippet_id",
               "the after snippet is not bound by a manifest case")

    try:
        expected = "".join(difflib.unified_diff(
            before["code"].splitlines(True), after["code"].splitlines(True),
            fromfile=review.get("fromfile"), tofile=review.get("tofile")))
    except (TypeError, ValueError) as exc:
        _issue(issues, "review-diff", "/questions/0/review", f"cannot build the unified diff: {exc}")
        return
    if diff.get("text") != expected:
        _issue(issues, "review-diff-mismatch", "/questions/0/review/diff_artifact_id",
               "displayed diff is not difflib.unified_diff(before, after)")

    matches = [item for item in manifest_artifacts.values()
               if item.get("sha256") == diff.get("sha256")]
    if len(matches) != 1:
        _issue(issues, "review-diff-unbound", "/questions/0/review/diff_artifact_id",
               "the diff artifact must match exactly one manifest artifact")
    elif matches[0].get("artifact_id") not in referenced:
        _issue(issues, "review-diff-not-referenced", "/questions/0/review/diff_artifact_id",
               "the matching manifest artifact is not referenced by a bound case")


def _check_case_bytes(issues, question_case, manifest_case, manifest, path, snippets, case_id):
    snippet_id = question_case.get("snippet_id")
    if snippet_id is not None:
        snippet = snippets.get(snippet_id)
        if snippet is None:
            _issue(issues, "source-snippet", path, "question snippet is missing")
        else:
            try:
                source = snippet["code"].encode("utf-8")
            except (KeyError, UnicodeEncodeError):
                _issue(issues, "source-encoding", path, "snippet code is not UTF-8")
            else:
                if sha256_bytes(source) != manifest_case.get("source_sha256"):
                    _issue(issues, "source-stale", path + "/source_sha256",
                           "snippet bytes differ from the manifest")
    else:
        raw_inputs = manifest.get("inputs")
        raw_inputs = raw_inputs if isinstance(raw_inputs, list) else []
        fixtures = [item for item in raw_inputs
                    if isinstance(item, dict) and item.get("role") == "fixture"]
        if len(fixtures) != 1:
            _issue(issues, "source-fixture", path,
                   "a non-snippet case needs exactly one fixture input")
        elif manifest_case.get("source_sha256") != fixtures[0].get("sha256"):
            _issue(issues, "source-stale", path + "/source_sha256",
                   "source hash differs from the fixture input")
    stdin = question_case.get("stdin")
    if isinstance(stdin, str) and sha256_text(stdin) != manifest_case.get("stdin_sha256"):
        _issue(issues, "stdin-stale", path + "/stdin_sha256", "stdin bytes differ from the manifest")
    expected = question_case.get("expected")
    expected_projection = manifest_case.get("expected")
    if not isinstance(expected, dict) or not isinstance(expected_projection, dict):
        return
    stdout = expected.get("stdout")
    if isinstance(stdout, str) and sha256_text(stdout) != expected_projection.get("stdout_sha256"):
        _issue(issues, "stdout-stale", path + "/expected/stdout_sha256",
               "expected stdout differs from the manifest")
    exit_code = expected.get("exit_code")
    exception = expected_projection.get("exception")
    if exit_code == 0:
        if exception is not None:
            _issue(issues, "exception-unexpected", path + "/expected/exception",
                   "exit code 0 cannot have an expected exception")
    elif isinstance(exit_code, int):
        if exception is None:
            _issue(issues, "exception-missing", path + "/expected/exception",
                   "a non-zero exit code requires an explicit expected exception")
        else:
            contains = expected.get("stderr_contains")
            message = exception.get("message") if isinstance(exception, dict) else None
            artifact_id = exception.get("stderr_artifact_id") if isinstance(exception, dict) else None
            if contains and isinstance(message, str) and contains not in message:
                _issue(issues, "exception-stderr-mismatch", path + "/expected/exception",
                       "stderr_contains is not in the exception message")
            if contains and not isinstance(message, str):
                if artifact_id is None:
                    _issue(issues, "exception-stderr-missing", path + "/expected/exception",
                           "stderr_contains needs a message or a stderr artifact")


def _check_referenced_artifacts(issues, manifest_artifacts, referenced,
                                verified_artifact_hashes, artifact_bytes):
    artifact_bytes = artifact_bytes or {}
    for artifact_id in sorted(referenced):
        artifact = manifest_artifacts.get(artifact_id)
        if artifact is None:
            continue
        raw = artifact_bytes.get(artifact_id)
        if raw is None:
            if artifact.get("sha256") in verified_artifact_hashes:
                continue
            _issue(issues, "artifact-bytes-unavailable", f"/artifacts/{artifact_id}",
                   "no bytes supplied for a referenced artifact")
            continue
        if sha256_bytes(raw) != artifact.get("sha256"):
            _issue(issues, "artifact-hash-mismatch", f"/artifacts/{artifact_id}",
                   "artifact bytes differ from the manifest hash")
        if len(raw) != artifact.get("bytes"):
            _issue(issues, "artifact-bytes-mismatch", f"/artifacts/{artifact_id}",
                   "artifact byte count differs from the manifest")
        if artifact.get("truncated") or not artifact.get("complete"):
            _issue(issues, "artifact-not-complete", f"/artifacts/{artifact_id}",
                   "a referenced artifact must be complete and not truncated")
    if len(referenced) > MAX_ARTIFACT_FILES:
        _issue(issues, "artifact-count", "/artifacts",
               f"more than {MAX_ARTIFACT_FILES} referenced artifacts")
    total = sum(len(value) for value in artifact_bytes.values())
    if total > MAX_ARTIFACT_TOTAL_BYTES:
        _issue(issues, "artifact-total-bytes", "/artifacts",
               f"artifact bytes exceed {MAX_ARTIFACT_TOTAL_BYTES}")


def validate_files(binding_path, *, root=None, expected_binding_sha256=None):
    """Read the binding, manifest, question, fixture and artifact bytes safely, then validate.

    ``root`` defaults to the binding file's directory. All manifest/question/
    artifact URIs are relative to that root and are read through :class:`SafeRoot`.
    ``expected_binding_sha256`` is the exact binding-file hash from the candidate's
    frozen review receipt; when given, a changed review/gate block is rejected even
    when every referenced manifest/question/artifact is unchanged.
    """
    binding_path = Path(binding_path)
    try:
        binding_raw = binding_path.read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read binding: {binding_path}: {exc}") from exc
    if len(binding_raw) > MAX_JSON_BYTES:
        raise ContractError(f"binding exceeds {MAX_JSON_BYTES} bytes")
    binding = core.parse_json_bytes(binding_raw, binding_path)
    if not isinstance(binding, dict):
        raise ContractError("binding must be a JSON object")
    base = SafeRoot(root if root is not None else binding_path.parent)

    manifest_uri = binding.get("manifest_uri")
    manifest_raw = base.read_bytes(manifest_uri, limit=MAX_JSON_BYTES)
    manifest = core.parse_json_bytes(manifest_raw, manifest_uri)

    questions = binding.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ContractError("binding.questions must be a non-empty list")
    entry = questions[0]
    if len(questions) != 1:
        # One invocation validates one question entry; the file may still bind
        # several, but the caller must name the one it is checking.
        raise ContractError("validate_files expects exactly one question entry")
    question_uri = entry.get("question_uri")
    question_raw = base.read_bytes(question_uri, limit=MAX_JSON_BYTES)
    question = core.parse_json_bytes(question_raw, question_uri)

    artifact_bytes = {}
    artifact_uris = binding.get("artifact_uris")
    if isinstance(artifact_uris, dict):
        for artifact_id, uri in artifact_uris.items():
            if isinstance(uri, str):
                artifact_bytes[artifact_id] = base.read_bytes(uri, limit=MAX_ARTIFACT_BYTES)

    def fixture_bytes(fixture_id):
        uri = f"content/fixtures/artifacts/{fixture_id}.out"
        try:
            return read_fixture(base.root, uri, MAX_ARTIFACT_BYTES)
        except FixtureProblem as exc:
            raise FixtureUnavailable(str(exc), missing=str(exc) == "no such file") from exc
        except SetupError as exc:
            raise FixtureUnavailable(str(exc)) from exc

    result = validate(manifest, question, binding, manifest_bytes=manifest_raw,
                      question_bytes=question_raw, artifact_bytes=artifact_bytes,
                      fixture_bytes=fixture_bytes)
    return _with_binding_file_hash(result, binding_raw, expected_binding_sha256)


def _with_binding_file_hash(result, binding_raw, expected_binding_sha256):
    """Attach the exact binding-file hash; a gate needs a frozen hash and a stale one is a finding."""
    actual = sha256_bytes(binding_raw)
    if expected_binding_sha256 is None and result.gate is not None:
        unbound = core.Issue(
            "binding-file-unbound", "/binding",
            "a gate binding needs the exact binding-file hash from the frozen review receipt")
        return ContractResult(tuple([unbound, *result.issues]), result.licences,
                              result.gate, actual)
    if expected_binding_sha256 is not None and expected_binding_sha256 != actual:
        mismatch = core.Issue(
            "binding-file-mismatch", "/binding",
            "binding file bytes do not match the frozen review receipt")
        return ContractResult(tuple([mismatch, *result.issues]), result.licences,
                              result.gate, actual)
    return ContractResult(result.issues, result.licences, result.gate, actual)


__all__ = ["ContractError", "ContractResult", "FixtureUnavailable", "SafeRoot", "binding_projection",
           "canonical_json_bytes", "compute_binding_sha256", "compute_config_digest",
           "manifest_issues", "relative_segments", "sha256_bytes", "sha256_text",
           "validate", "validate_files"]
