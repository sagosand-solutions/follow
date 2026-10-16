# SPDX-License-Identifier: MIT
"""Static study-note validation and deterministic availability-manifest core.

This module never executes code, never fetches a URL and never writes or
exports anything. It is the deliberately small first slice of the study
contract: schema + static semantics for one note, plus a manifest *core* that
hashes and validates explicitly approved notes. The full publication pipeline
(validate-all-before-write, output caps, per-file atomicity and ``--check``)
is a later task; do not treat this module as a publication pipeline.
"""
from datetime import date
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

from . import core
from .core import Issue

DATA_VERSION = 1
MANIFEST_PREFIX = "data/study/"
MAX_MANIFEST_ENTRIES = 512

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_PATH = re.compile(r"^data/study/[A-Za-z0-9._/-]+\.json$")
_SCOPES = ("concept", "lesson", "question")


def _duplicate_ids(items, path, field="id"):
    issues, seen = [], set()
    for index, item in enumerate(items):
        value = item.get(field) if isinstance(item, dict) else None
        if value in seen:
            issues.append(Issue("duplicate", f"/{path}/{index}/{field}", f"Duplicate {field}: {value}"))
        seen.add(value)
    return issues


_URL_ENCODED_CONTROL = re.compile(r"%(?:0[0-9a-fA-F]|1[0-9a-fA-F]|7[fF])")


def _url_problem(value):
    """Return a reason string if ``value`` is not a safe https URL, else None."""
    if any(ord(character) <= 0x20 or ord(character) == 0x7f for character in value):
        return "URL must not contain whitespace or control characters"
    if _URL_ENCODED_CONTROL.search(value):
        return "URL must not contain percent-encoded control characters"
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        return f"URL cannot be parsed: {exc}"
    if parts.scheme != "https":
        return "URL must use the https scheme"
    if parts.hostname is None or not parts.netloc:
        return "URL must include a hostname"
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return "URL must not include credentials"
    return None


def _date_problem(value):
    try:
        date.fromisoformat(value)
    except ValueError:
        return f"accessed_on is not a real calendar date: {value}"
    return None


def validate_study(note, *, concept_ids, lesson_ids, question_ids, schema_dir=None):
    """Return static issues for one study note. Never executes or fetches.

    ``concept_ids``, ``lesson_ids`` and ``question_ids`` are the existing
    reference sets; a target outside them, an unresolved ``related`` concept or
    a duplicate section/source id is an issue. JSON Schema already guarantees
    the exclusive concept/lesson/question scope and the static-only policy.
    """
    issues = core._schema(note, "study.schema.json", schema_dir=schema_dir)
    if issues:
        return core._ordered(issues)
    issues = core._blanks(note)
    known = {"concept": concept_ids, "lesson": lesson_ids, "question": question_ids}
    scope = next(name for name in _SCOPES if name in note)
    target = note[scope]
    if target not in known[scope]:
        issues.append(Issue(f"unknown-{scope}", f"/{scope}", f"Unknown {scope}: {target}"))
    for index, ref in enumerate(note.get("related", [])):
        if ref not in concept_ids:
            issues.append(Issue("unknown-concept", f"/related/{index}", f"Unknown concept: {ref}"))
    issues += _duplicate_ids(note["sections"], "sections")
    issues += _duplicate_ids(note["sources"], "sources")
    for index, source in enumerate(note["sources"]):
        problem = _url_problem(source["url"])
        if problem:
            issues.append(Issue("url", f"/sources/{index}/url", problem))
        problem = _date_problem(source["accessed_on"])
        if problem:
            issues.append(Issue("date", f"/sources/{index}/accessed_on", problem))
    return core._ordered(issues)


def _manifest_relative(path):
    """Validate one manifest-relative path and return its notes-root suffix."""
    if not isinstance(path, str):
        raise core.ContentError(f"manifest path must be a string: {path!r}")
    if path.startswith("/"):
        raise core.ContentError(f"{path}: manifest path must be relative, not absolute")
    if "\\" in path:
        raise core.ContentError(f"{path}: manifest path must not contain a backslash")
    if "?" in path or "#" in path:
        raise core.ContentError(f"{path}: manifest path must not contain a query or fragment")
    if "%" in path:
        raise core.ContentError(f"{path}: manifest path must not contain a percent escape")
    if not _MANIFEST_PATH.match(path):
        raise core.ContentError(f"{path}: manifest path must be data/study/<name>.json with ASCII path characters")
    relative = path[len(MANIFEST_PREFIX):]
    segments = relative.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise core.ContentError(f"{path}: manifest path has an empty, dot or dotdot segment")
    return relative


def _resolve_within(notes_root, relative):
    """Resolve below notes_root, rejecting symlinks in any component."""
    if notes_root.is_symlink():
        raise core.SetupError(f"{notes_root}: notes root must not be a symlink")
    current = notes_root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise core.SetupError(f"{current}: symlinks are not supported for study notes")
    return current


def build_manifest(notes_root, approvals, *, concept_ids, lesson_ids, question_ids, schema_dir=None):
    """Return the deterministic manifest for explicitly approved notes.

    ``approvals`` is a manually prepared list of ``{path, sha256}`` entries
    with manifest-relative ``data/study/...json`` paths. The authored ``reviewed``
    status is never trusted on its own: approval is the external release gate.
    Every entry must validate before anything is returned; a failure raises and
    never produces a partial manifest. No byte is re-serialized for hashing.
    """
    notes_root = Path(notes_root)
    if not isinstance(approvals, (list, tuple)):
        raise core.SetupError("approvals must be a list of {path, sha256} entries")
    if len(approvals) > MAX_MANIFEST_ENTRIES:
        raise core.InputLimitError(notes_root, f"manifest-count: {len(approvals)} approvals exceeds {MAX_MANIFEST_ENTRIES} per invocation")
    if not approvals:
        return {"data_version": DATA_VERSION, "notes": []}
    budget = core.QuestionBudget()
    entries, seen_paths, seen_targets = [], set(), set()
    for approval in approvals:
        if not isinstance(approval, dict):
            raise core.ContentError(f"manifest approval must be an object: {approval!r}")
        path = approval.get("path")
        expected = approval.get("sha256")
        relative = _manifest_relative(path)
        if relative in seen_paths:
            raise core.ContentError(f"{path}: duplicate manifest path")
        seen_paths.add(relative)
        if not isinstance(expected, str) or not _SHA256.match(expected):
            raise core.ContentError(f"{path}: sha256 must be 64 lowercase hex characters")
        full = _resolve_within(notes_root, relative)
        raw = core.read_json_bytes(full, budget=budget)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected:
            raise core.ContentError(f"{path}: sha256 mismatch: manifest has {expected}, bytes hash to {digest}")
        note = core.parse_json_bytes(raw, full)
        issues = validate_study(note, concept_ids=concept_ids, lesson_ids=lesson_ids,
                                question_ids=question_ids, schema_dir=schema_dir)
        if issues:
            first = issues[0]
            raise core.ContentError(f"{path}: invalid study note: {first.path}: {first.code}: {first.message}")
        if note["status"] != "reviewed":
            raise core.ContentError(f"{path}: note status is {note['status']!r}; only reviewed notes may be published")
        scope = next(name for name in _SCOPES if name in note)
        target = note[scope]
        key = f"{scope}:{target}"
        if key in seen_targets:
            raise core.ContentError(f"{path}: duplicate manifest target {key}")
        seen_targets.add(key)
        entries.append({"scope": scope, "target_id": target, "path": path, "sha256": digest})
    entries.sort(key=lambda entry: (entry["scope"], entry["target_id"]))
    manifest = {"data_version": DATA_VERSION, "notes": entries}
    issues = core._schema(manifest, "study-manifest.schema.json", schema_dir=schema_dir)
    if issues:
        first = issues[0]
        raise core.SetupError(f"generated manifest failed its own schema: {first.path}: {first.message}")
    return manifest


def _reference_sets(map_data, course, index):
    concept_ids = {concept["id"] for concept in map_data["concepts"]}
    lesson_ids = {lesson["id"] for level in course["levels"] for lesson in level["lessons"]}
    question_ids = {question["id"] for question in index["questions"]}
    return concept_ids, lesson_ids, question_ids


def main(argv=None):
    import argparse

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise core.SetupError(message)

    parser = Parser(description="Statically validate one study note. No execution, no export and no publication.")
    parser.add_argument("note")
    parser.add_argument("--map", default=str(core.ROOT / "schema/concepts/python-v0.json"))
    parser.add_argument("--course", default=str(core.ROOT / "public/data/courses/python.json"))
    parser.add_argument("--index", default=str(core.ROOT / "public/data/index.json"))
    issues = []
    exit_code = 0
    try:
        args = parser.parse_args(argv)
        map_data = core.load_json(args.map)
        course = core.load_json(args.course)
        index = core.load_json(args.index)
        concept_ids, lesson_ids, question_ids = _reference_sets(map_data, course, index)
        note = core.load_json(args.note)
        issues = validate_study(note, concept_ids=concept_ids, lesson_ids=lesson_ids,
                                question_ids=question_ids)
        exit_code = 1 if issues else 0
    except core.InputLimitError as exc:
        issues = [Issue("input-limit", "", str(exc))]
        exit_code = 1
    except core.ContentError as exc:
        issues = [Issue("json", "", str(exc))]
        exit_code = 1
    except core.SetupError as exc:
        issues = [Issue("setup", "", str(exc))]
        exit_code = 2
    except (KeyError, TypeError, AttributeError) as exc:
        issues = [Issue("setup", "", f"invalid reference structure: {exc}")]
        exit_code = 2
    for issue in issues:
        print(f"{issue.path}: {issue.code}: {issue.message}")
    print(f"Static study validation {'passed' if exit_code == 0 else 'failed'}; no execution, export or publication approval.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
