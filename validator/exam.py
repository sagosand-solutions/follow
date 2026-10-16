# SPDX-License-Identifier: MIT
"""Separate chapter-examination and local-attempt contracts.

This module is deliberately static. It never executes, imports or replays
contributor code, never opens a process or the network, and never writes output
files. ``execution_policy`` is structurally fixed to ``static-only`` by the
schema; trusted fixtures, executable cases, generated traces and other
languages are rejected structurally rather than half-implemented.

The first frozen format is explanation-only. Single-select choice, two-stage
responses, line selection, ordering and trusted fixtures are intentionally not
supported here and are refused, not silently accepted.

An examination has exactly one ``source``. Every ``evidence`` anchor therefore
names that source: ``source_hash`` must equal ``source.sha256`` and
``snippet_id`` must equal ``source.id``, with ``1 <= start_line <= end_line``
inside the source's LF line count.
"""
import argparse
import hashlib
import re
import sys

from .core import (ContentError, InputLimitError, Issue, QuestionBudget, SetupError,
                   _ordered, _schema, load_json, validate_map)

EXAM_SCHEMA = "exam.schema.json"
ATTEMPT_SCHEMA = "exam-attempt.schema.json"

# Control characters permitted in source code: horizontal tab and line feed.
# Everything else in C0, plus the Unicode line separators below, is rejected so
# that line counting via str.splitlines() stays unambiguous and LF-only.
_ALLOWED_CONTROLS = frozenset("\t\n")
_FORBIDDEN_SEPARATORS = frozenset("\x85\u2028\u2029")

MAX_CODE_POINTS = 8000
MAX_CODE_LINES = 100
MIN_PARTS = 6
MAX_PARTS = 8

_IDENTITY_FIELDS = ("attempt_id", "exam_id", "exam_version", "source_hash", "rubric_version")
_TERMINAL_STATUSES = ("submitted", "abandoned")


def _prefixed(issue, prefix):
    if not prefix:
        return issue
    return Issue(issue.code, prefix + issue.path, issue.message)


def _schema_issues(value, name, prefix=""):
    return [_prefixed(issue, prefix) for issue in _schema(value, name)]


def _blank(value):
    return isinstance(value, str) and not value.strip()


def _unicode_names(characters):
    return ", ".join("U+%04X" % ord(character) for character in sorted(characters))


def validate_exam(exam, map_data, *, known_study_keys=()):
    """Statically validate one chapter examination. Returns ordered Issues.

    ``known_study_keys`` is the explicit, caller-provided set of study notes that
    exist and have been reviewed. Passing an empty set (the default) accepts only
    an empty ``study_refs`` list; it never probes the filesystem or the network.
    """
    issues = _schema_issues(exam, EXAM_SCHEMA)
    if issues:
        return _ordered(issues)

    issues = []
    source = exam["source"]
    code = source["code"]

    forbidden = {character for character in code
                 if (ord(character) < 0x20 and character not in _ALLOWED_CONTROLS)
                 or character in _FORBIDDEN_SEPARATORS}
    if forbidden:
        issues.append(Issue("code-characters", "/source/code",
                            "Code may contain only LF/TAB control characters; found " + _unicode_names(forbidden)))
    if not code.strip():
        issues.append(Issue("empty-code", "/source/code", "An empty program is not allowed"))

    lines = code.splitlines()
    if not 1 <= len(lines) <= MAX_CODE_LINES:
        issues.append(Issue("code-lines", "/source/code",
                            f"Code must contain 1 to {MAX_CODE_LINES} LF-separated lines"))
    line_total = len(lines)

    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if source["sha256"] != digest:
        issues.append(Issue("source-hash", "/source/sha256",
                            "sha256 must be the UTF-8 SHA-256 of the exact code, without normalisation"))

    seen_ids = set()
    for index, part in enumerate(exam["parts"]):
        if part["id"] in seen_ids:
            issues.append(Issue("duplicate", f"/parts/{index}/id", f"Duplicate part id: {part['id']}"))
        seen_ids.add(part["id"])

    purposes = {part["purpose"] for part in exam["parts"]}
    if "flow" not in purposes:
        issues.append(Issue("missing-purpose", "/parts", "At least one part must have purpose flow"))
    if "risk" not in purposes:
        issues.append(Issue("missing-purpose", "/parts", "At least one part must have purpose risk"))

    concepts = {concept["id"] for concept in map_data.get("concepts", [])}
    for index, part in enumerate(exam["parts"]):
        for position, ref in enumerate(part["concepts"]):
            if ref not in concepts:
                issues.append(Issue("unknown-concept", f"/parts/{index}/concepts/{position}",
                                    f"Unknown concept: {ref}"))
        scores = [item["score"] for item in part["rubric"]]
        if scores != [0, 1, 2]:
            issues.append(Issue("rubric-order", f"/parts/{index}/rubric",
                                "Rubric scores must be exactly 0, 1, 2 in order"))
        for position, evidence in enumerate(part["evidence"]):
            path = f"/parts/{index}/evidence/{position}"
            if evidence["source_hash"] != source["sha256"]:
                issues.append(Issue("evidence-hash", path + "/source_hash",
                                    "Evidence source_hash must match the exam source hash"))
            if evidence["snippet_id"] != source["id"]:
                issues.append(Issue("evidence-snippet", path + "/snippet_id",
                                    "Evidence snippet_id must match the single exam source id"))
            if not 1 <= evidence["start_line"] <= evidence["end_line"] <= line_total:
                issues.append(Issue("evidence-lines", path,
                                    f"Evidence lines must satisfy 1 <= start_line <= end_line <= {line_total}"))

    known = set(known_study_keys)
    for index, ref in enumerate(exam.get("study_refs", [])):
        if ref not in known:
            issues.append(Issue("unknown-study-ref", f"/study_refs/{index}",
                                "Study reference is not among the explicitly provided known study keys"))

    if _blank(exam["title"]):
        issues.append(Issue("blank", "/title", "Text must not be whitespace-only"))
    if _blank(source["environment"]):
        issues.append(Issue("blank", "/source/environment", "Text must not be whitespace-only"))
    if _blank(exam["author"]["name"]):
        issues.append(Issue("blank", "/author/name", "Text must not be whitespace-only"))
    for index, part in enumerate(exam["parts"]):
        if _blank(part["prompt"]):
            issues.append(Issue("blank", f"/parts/{index}/prompt", "Text must not be whitespace-only"))
        if _blank(part["model_answer"]):
            issues.append(Issue("blank", f"/parts/{index}/model_answer", "Text must not be whitespace-only"))
        for position, item in enumerate(part["rubric"]):
            if _blank(item["criteria"]):
                issues.append(Issue("blank", f"/parts/{index}/rubric/{position}/criteria",
                                    "Text must not be whitespace-only"))
    return _ordered(issues)


def _bind_issues(attempt, exam, prefix):
    issues = []

    def add(code, path, message):
        issues.append(_prefixed(Issue(code, path, message), prefix))

    if attempt["exam_id"] != exam["id"]:
        add("exam-id", "/exam_id", "exam_id must match the exam id")
    if attempt["exam_version"] != exam["version"]:
        add("exam-version", "/exam_version", "exam_version must match the exam version")
    if attempt["source_hash"] != exam["source"]["sha256"]:
        add("source-hash", "/source_hash", "source_hash must match the exam source hash")
    if attempt["rubric_version"] != exam["rubric_version"]:
        add("rubric-version", "/rubric_version", "rubric_version must match the exam rubric version")

    exam_part_ids = [part["id"] for part in exam["parts"]]
    attempt_part_ids = [part["part_id"] for part in attempt["parts"]]
    if attempt_part_ids != exam_part_ids:
        add("part-set", "/parts", "Attempt parts must match the exam parts, same ids in the same order")
    else:
        seen = set()
        for index, part in enumerate(attempt["parts"]):
            if part["part_id"] in seen:
                add("duplicate", f"/parts/{index}/part_id", f"Duplicate part_id: {part['part_id']}")
            seen.add(part["part_id"])

    if not attempt["assisted"] and attempt["assistance_events"]:
        add("assisted-events", "/assisted", "Assistance events require assisted=true")
    for index, event in enumerate(attempt["assistance_events"]):
        if _blank(event["resource_id"]):
            add("blank", f"/assistance_events/{index}/resource_id", "Text must not be whitespace-only")
    for index, part in enumerate(attempt["parts"]):
        if part["status"] == "answered":
            value = part["value"]
            text = value.get("text", "") if isinstance(value, dict) else ""
            if not text.strip():
                add("blank-answer", f"/parts/{index}/value",
                    "An answered part needs non-empty text; a blank answer is invalid, not zero")
    return issues


def validate_attempt(attempt, exam):
    """Statically validate one attempt against its exam. Returns ordered Issues."""
    issues = _schema_issues(attempt, ATTEMPT_SCHEMA)
    issues += _schema_issues(exam, EXAM_SCHEMA, "/exam")
    if issues:
        return _ordered(issues)
    return _ordered(_bind_issues(attempt, exam, ""))


def validate_attempt_transition(previous, next_attempt, exam):
    """Statically validate one in-place save step between two attempt snapshots.

    The identity fields of an attempt never change; assistance events are an
    append-only prefix; ``assisted`` is monotonic false-to-true; and submitted or
    abandoned snapshots are immutable (an identical idempotent save is allowed).
    """
    issues = _schema_issues(previous, ATTEMPT_SCHEMA, "/previous")
    issues += _schema_issues(next_attempt, ATTEMPT_SCHEMA, "/next")
    issues += _schema_issues(exam, EXAM_SCHEMA, "/exam")
    if issues:
        return _ordered(issues)

    issues = _bind_issues(previous, exam, "/previous") + _bind_issues(next_attempt, exam, "/next")

    def add(code, path, message):
        issues.append(Issue(code, path, message))

    for field in _IDENTITY_FIELDS:
        if previous[field] != next_attempt[field]:
            add("identity-change", f"/{field}", f"{field} must not change within an attempt")
    if previous["assisted"] and not next_attempt["assisted"]:
        add("assisted-reset", "/assisted", "assisted cannot go from true to false")

    previous_events = previous["assistance_events"]
    next_events = next_attempt["assistance_events"]
    common = min(len(previous_events), len(next_events))
    if previous_events[:common] != next_events[:common]:
        add("assistance-rewrite", "/assistance_events",
            "Assistance events are append-only and cannot be edited in place")
    if len(next_events) < len(previous_events):
        add("assistance-drop", "/assistance_events", "Assistance events cannot be removed")

    if previous["status"] in _TERMINAL_STATUSES:
        if next_attempt != previous:
            add("terminal-mutation", "/status",
                "A submitted or abandoned attempt is immutable; only an identical save is allowed")
        return _ordered(issues)

    if len(next_events) > len(previous_events):
        if not next_attempt["assisted"]:
            add("assistance-flag", "/assisted", "New assistance events require assisted=true")
        if next_attempt["status"] != "in_progress":
            add("assistance-submit", "/status",
                "Assistance cannot be added in the same transition as submission or abandonment")
    return _ordered(issues)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SetupError(message)


def main(argv=None):
    """CLI: ``python -m validator.exam EXAM --map MAP [--attempt ATTEMPT]``.

    Exit 0 valid, 1 invalid content, 2 setup/dependency/input-limit failure.
    Static-only: no output files, verification flags, network or process calls.
    Non-empty study_refs are only accepted through the Python API, which takes an
    explicit ``known_study_keys`` set; the CLI therefore passes an empty set.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    report = {"valid": False, "issues": []}
    exit_code = 0
    exam_path = ""
    attempt_path = ""

    def add(code, path, message, file=""):
        report["issues"].append({"code": code, "path": path, "message": message, "file": str(file)})

    parser = _Parser(prog="python -m validator.exam",
                     description="Statically validate a chapter exam and an optional local attempt. No code is executed.")
    parser.add_argument("exam")
    parser.add_argument("--map", required=True)
    parser.add_argument("--attempt")
    try:
        args = parser.parse_args(argv)
        exam_path = args.exam
        attempt_path = args.attempt or ""
        budget = QuestionBudget()

        map_data = load_json(args.map, budget=budget)
        map_issues = validate_map(map_data)
        if map_issues:
            for issue in map_issues:
                add("map-" + issue.code, "/concept-map" + issue.path, issue.message, args.map)
            exit_code = max(exit_code, 1)
        else:
            exam = load_json(args.exam, budget=budget)
            exam_issues = validate_exam(exam, map_data)
            for issue in exam_issues:
                add(issue.code, issue.path, issue.message, args.exam)
            if exam_issues:
                exit_code = max(exit_code, 1)
            if args.attempt is not None:
                attempt = load_json(args.attempt, budget=budget)
                attempt_issues = validate_attempt(attempt, exam)
                for issue in attempt_issues:
                    add(issue.code, issue.path, issue.message, args.attempt)
                if attempt_issues:
                    exit_code = max(exit_code, 1)
    except InputLimitError as exc:
        add("input-limit", "", str(exc), exc.file)
        exit_code = max(exit_code, 1)
    except ContentError as exc:
        add("json", "", str(exc))
        exit_code = max(exit_code, 1)
    except SetupError as exc:
        add("setup", "", str(exc))
        exit_code = 2

    report["valid"] = exit_code == 0
    report["issues"].sort(key=lambda item: (item["file"], item["path"], item["code"], item["message"]))
    label = "VALID" if report["valid"] else "INVALID"
    target = exam_path
    if attempt_path:
        target += " (with attempt)"
    print(f"{label}: {target}")
    for issue in report["issues"]:
        print(f"{issue['file']}{issue['path']}: {issue['code']}: {issue['message']}")
    print("Static checks only: no code executed, no answer key printed.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
