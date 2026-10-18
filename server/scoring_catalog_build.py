# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic scoring-catalog builder (task 024, owner K4).

Builds one frozen internal scoring catalog per family (``official``/``free``) from
an *explicit* release selection over an existing content manifest. Nothing here
scans directories, guesses a course/product/premium status, or reconstructs a
chapter from a filename: every pool question, its owning course and its product
binding come from the release file, and every exposure row comes from a reviewed
exposure map. An ``official`` question must also carry an explicit
``previously_exposed`` attestation; a known-exposed question is refused, and the
builder never claims to detect an unknown leak.

Output is the frozen artifact from ``scoring-server-interfaces-v1.md`` §2 / task
024 and nothing else. It contains ids, product bindings, hashes, exposure
references and versions — never prompts, options, feedback, solutions, source
paths or raw secrets. Runtime ownership (loading, migrations, HTTP) is task 023
and lives in other modules; **runtime code must never import this builder.**

Public tests and fixtures use only synthetic content. Real private release inputs
and artifacts live in the private repo or an internal draft and are never
committed here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .content_catalog import ALLOWED_KINDS, ContentCatalog
from .models import ContentError

# --- frozen vocabulary -------------------------------------------------------

SCHEMA_VERSION = 1

#: Known rules versions and their fixed score units, for error messages and for
#: refusing a typo. The builder never scores. Only ``BUILDABLE_RULES`` may be
#: frozen into a *new* catalog: the 2026-10-18 decision requires r2
#: (+2/+1/0/-1, denominator 2) for new catalogs and forbids reinterpreting r1's
#: quarter points (r1 = +4/+2/0/-1, denominator 4, historical only).
KNOWN_RULES = {
    "r1": {"correct": 4, "partial": 2, "not_sure": 0, "wrong": -1, "denominator": 4},
    "r2": {"correct": 2, "partial": 1, "not_sure": 0, "wrong": -1, "denominator": 2},
}
BUILDABLE_RULES = frozenset({"r2"})

FAMILIES = ("official", "free")

#: Family -> the only accepted content license for that family's pool. Mixing
#: free public samples and protected premium into one score set is forbidden.
LICENSE_BY_FAMILY = {
    "official": "LicenseRef-Proprietary",
    "free": "CC-BY-SA-4.0",
}

_QUESTION_FIELDS = (
    "schema_version", "id", "language", "locale", "type", "level", "difficulty",
    "concepts", "prompt", "snippets", "options", "explanation", "verification",
    "author", "license",
)
_OPTION_FIELDS = ("id", "text", "score", "feedback", "misconception")
_SCORES = (0, 0.5, 1)

#: A release question binding may carry an explicit exposure attestation.
_REQUIRED_QUESTION_KEYS = frozenset({"question_id", "product_id"})
_ALLOWED_QUESTION_KEYS = _REQUIRED_QUESTION_KEYS | {"previously_exposed"}

_ID_RE = re.compile(r"[a-z][a-z0-9.-]{0,79}\Z")
#: The runtime catalog loader uses this exact course-id pattern; the builder must
#: not freeze a course_id (e.g. with ``.``/``_``) that runtime would reject.
_COURSE_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_PRODUCT_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN = ("\x00", "\n", "\r", "/", "\\")


class CatalogBuildError(Exception):
    """A release or exposure input cannot produce a valid frozen catalog.

    Messages never carry file contents, prompts, solutions, absolute paths or
    raw secrets.
    """


# --- parsed inputs -----------------------------------------------------------


@dataclass(frozen=True)
class PoolQuestion:
    course_id: str
    question_id: str
    product_id: str | None
    content_sha256: str


@dataclass(frozen=True)
class Exposure:
    kind: str
    id: str
    content_sha256: str
    affected_question_ids: tuple[str, ...]


@dataclass(frozen=True)
class Release:
    catalog_version: str
    rules_version: str
    family: str
    courses: tuple[tuple[str, tuple[tuple[str, str | None], ...]], ...]


# --- small safe helpers ------------------------------------------------------


def _read_json(path, label: str) -> object:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogBuildError(f"{label} could not be read") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogBuildError(f"{label} is not valid JSON") from exc


def _require_object(raw: object, label: str) -> dict:
    if not isinstance(raw, dict):
        raise CatalogBuildError(f"{label} must be a JSON object")
    return raw


def _require_exact_keys(raw: dict, required: frozenset, label: str) -> None:
    keys = set(raw)
    if keys != required:
        missing = sorted(required - keys)
        extra = sorted(keys - required)
        raise CatalogBuildError(f"{label} fields invalid (missing {missing}, unexpected {extra})")


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CatalogBuildError(f"{label} must be a non-empty string")
    if any(token in value for token in _FORBIDDEN):
        raise CatalogBuildError(f"{label} contains a forbidden character")
    return value


def _require_id(value: object, label: str) -> str:
    text = _require_str(value, label)
    if ".." in text or not _ID_RE.fullmatch(text):
        raise CatalogBuildError(f"{label} is not a valid content id")
    return text


def _require_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise CatalogBuildError(f"{label} must be 64 lowercase hex characters")
    return value


def _require_schema_version(raw: dict, label: str) -> None:
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise CatalogBuildError(f"{label} schema_version must be {SCHEMA_VERSION}")


# --- release parsing ---------------------------------------------------------


def parse_release(raw: object) -> Release:
    data = _require_object(raw, "release")
    _require_schema_version(data, "release")
    _require_exact_keys(
        data,
        frozenset({"schema_version", "catalog_version", "rules_version", "family", "courses"}),
        "release",
    )
    catalog_version = _require_str(data["catalog_version"], "release catalog_version")
    rules_version = data["rules_version"]
    if rules_version not in KNOWN_RULES:
        raise CatalogBuildError(f"unknown rules_version: {rules_version!r}")
    if rules_version not in BUILDABLE_RULES:
        raise CatalogBuildError(
            f"rules_version {rules_version!r} is not allowed for new catalogs; use 'r2'"
        )
    family = data["family"]
    if family not in FAMILIES:
        raise CatalogBuildError(f"unknown family: {family!r}")
    raw_courses = data["courses"]
    if not isinstance(raw_courses, list) or not raw_courses:
        raise CatalogBuildError("release courses must be a non-empty list")

    courses: list[tuple[str, tuple[tuple[str, str | None], ...]]] = []
    seen_courses: set[str] = set()
    for index, raw_course in enumerate(raw_courses):
        course = _require_object(raw_course, f"release courses[{index}]")
        _require_exact_keys(course, frozenset({"course_id", "questions"}), f"release courses[{index}]")
        course_id = _require_str(course["course_id"], f"release courses[{index}] course_id")
        if not _COURSE_RE.fullmatch(course_id):
            raise CatalogBuildError(f"invalid course_id: {course_id!r}")
        if course_id in seen_courses:
            raise CatalogBuildError(f"duplicate course_id: {course_id!r}")
        seen_courses.add(course_id)
        raw_questions = course["questions"]
        if not isinstance(raw_questions, list) or not raw_questions:
            raise CatalogBuildError(f"course {course_id!r} questions must be a non-empty list")
        questions: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        for q_index, raw_question in enumerate(raw_questions):
            label = f"course {course_id!r} questions[{q_index}]"
            question = _require_object(raw_question, label)
            keys = set(question)
            if keys - _ALLOWED_QUESTION_KEYS or _REQUIRED_QUESTION_KEYS - keys:
                missing = sorted(_REQUIRED_QUESTION_KEYS - keys)
                extra = sorted(keys - _ALLOWED_QUESTION_KEYS)
                raise CatalogBuildError(f"{label} fields invalid (missing {missing}, unexpected {extra})")
            if "previously_exposed" in question and not isinstance(question["previously_exposed"], bool):
                raise CatalogBuildError(f"{label} previously_exposed must be a boolean")
            if family == "official":
                if "previously_exposed" not in question:
                    raise CatalogBuildError(
                        f"{label} must attest previously_exposed for an official question"
                    )
                if question["previously_exposed"]:
                    raise CatalogBuildError(
                        f"{label} is attested as previously exposed and cannot enter a protected pool"
                    )
            question_id = _require_id(question["question_id"], f"course {course_id!r} question_id")
            if question_id in seen:
                raise CatalogBuildError(f"duplicate question_id in course {course_id!r}: {question_id!r}")
            seen.add(question_id)
            product_id = question["product_id"]
            if product_id is not None:
                product_id = _require_str(product_id, f"question {question_id!r} product_id")
                if not _PRODUCT_RE.fullmatch(product_id):
                    raise CatalogBuildError(f"invalid product_id for {question_id!r}: {product_id!r}")
            elif family == "official":
                raise CatalogBuildError(f"official question {question_id!r} requires a product_id")
            questions.append((question_id, product_id))
        courses.append((course_id, tuple(questions)))
    return Release(catalog_version=catalog_version, rules_version=rules_version,
                   family=family, courses=tuple(courses))


# --- exposure parsing --------------------------------------------------------


def parse_exposures(raw: object) -> tuple[Exposure, ...]:
    data = _require_object(raw, "exposures")
    _require_schema_version(data, "exposures")
    _require_exact_keys(data, frozenset({"schema_version", "exposures"}), "exposures")
    raw_rows = data["exposures"]
    if not isinstance(raw_rows, list):
        raise CatalogBuildError("exposures must be a list")

    rows: list[Exposure] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_row in enumerate(raw_rows):
        row = _require_object(raw_row, f"exposures[{index}]")
        _require_exact_keys(
            row,
            frozenset({"kind", "id", "content_sha256", "affected_question_ids"}),
            f"exposures[{index}]",
        )
        kind = row["kind"]
        if kind not in ALLOWED_KINDS:
            raise CatalogBuildError(f"exposures[{index}] unknown kind: {kind!r}")
        ident = _require_id(row["id"], f"exposures[{index}] id")
        if (kind, ident) in seen:
            raise CatalogBuildError(f"duplicate exposure row: {kind}/{ident}")
        seen.add((kind, ident))
        digest = _require_hash(row["content_sha256"], f"exposures[{index}] content_sha256")
        raw_affected = row["affected_question_ids"]
        if not isinstance(raw_affected, list):
            raise CatalogBuildError(f"exposures[{index}] affected_question_ids must be a list")
        affected: list[str] = []
        for a_index, value in enumerate(raw_affected):
            affected_id = _require_id(value, f"exposures[{index}] affected_question_ids[{a_index}]")
            affected.append(affected_id)
        if len(set(affected)) != len(affected):
            raise CatalogBuildError(f"exposures[{index}] affected_question_ids contains duplicates")
        rows.append(Exposure(kind=kind, id=ident, content_sha256=digest,
                             affected_question_ids=tuple(sorted(affected))))
    return tuple(sorted(rows, key=lambda row: (row.kind, row.id)))


# --- question contract (focused subset of schema/question.schema.json) -------


def _validate_question(question_id: str, data: object, family: str) -> None:
    question = _require_object(data, f"question {question_id!r}")
    missing = [field for field in _QUESTION_FIELDS if field not in question]
    if missing:
        raise CatalogBuildError(f"question {question_id!r} missing fields: {missing}")
    if question["schema_version"] != "0.1":
        raise CatalogBuildError(f"question {question_id!r} schema_version must be '0.1'")
    if question["id"] != question_id:
        raise CatalogBuildError(f"question {question_id!r} id does not match the manifest entry")
    if question["license"] != LICENSE_BY_FAMILY[family]:
        raise CatalogBuildError(
            f"question {question_id!r} license does not match the {family} family"
        )
    options = question["options"]
    if not isinstance(options, list) or not 5 <= len(options) <= 8:
        raise CatalogBuildError(f"question {question_id!r} must have 5-8 options")
    seen: set[str] = set()
    for index, raw_option in enumerate(options):
        option = _require_object(raw_option, f"question {question_id!r} options[{index}]")
        missing = [field for field in _OPTION_FIELDS if field not in option]
        if missing:
            raise CatalogBuildError(f"question {question_id!r} options[{index}] missing fields: {missing}")
        option_id = _require_id(option["id"], f"question {question_id!r} options[{index}] id")
        if option_id in seen:
            raise CatalogBuildError(f"question {question_id!r} has duplicate option id {option_id!r}")
        seen.add(option_id)
        _require_str(option["text"], f"question {question_id!r} options[{index}] text")
        if option["score"] not in _SCORES:
            raise CatalogBuildError(f"question {question_id!r} options[{index}] score is not 0, 0.5 or 1")
        _require_str(option["feedback"], f"question {question_id!r} options[{index}] feedback")
        misconception = option["misconception"]
        if misconception is not None:
            _require_str(misconception, f"question {question_id!r} options[{index}] misconception")


# --- build -------------------------------------------------------------------


def _load_pool(catalog: ContentCatalog, release: Release) -> list[PoolQuestion]:
    pool: list[PoolQuestion] = []
    seen: dict[str, str] = {}
    for course_id, questions in release.courses:
        for question_id, product_id in questions:
            if question_id in seen:
                raise CatalogBuildError(
                    f"question_id {question_id!r} is not unique (also in course {seen[question_id]!r})"
                )
            seen[question_id] = course_id
            entry = catalog.lookup("question", question_id)
            if entry is None:
                raise CatalogBuildError(f"question {question_id!r} is not in the manifest")
            if product_id is not None and entry.product_id != product_id:
                raise CatalogBuildError(f"question {question_id!r} product binding contradicts the manifest")
            try:
                data = json.loads(catalog.read(entry).decode("utf-8"))
            except ContentError as exc:
                raise CatalogBuildError(f"question {question_id!r} failed the hash-checked read") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CatalogBuildError(f"question {question_id!r} is not valid JSON") from exc
            _validate_question(question_id, data, release.family)
            pool.append(PoolQuestion(course_id=course_id, question_id=question_id,
                                     product_id=product_id, content_sha256=entry.sha256))
    return pool


def _validate_exposures(exposures: tuple[Exposure, ...], pool: list[PoolQuestion],
                        catalog: ContentCatalog) -> None:
    pool_ids = {question.question_id for question in pool}
    for row in exposures:
        entry = catalog.lookup(row.kind, row.id)
        if entry is None:
            raise CatalogBuildError(f"exposure row {row.kind}/{row.id} is not in the manifest")
        if entry.sha256 != row.content_sha256:
            raise CatalogBuildError(f"exposure row {row.kind}/{row.id} hash contradicts the manifest")
        for affected in row.affected_question_ids:
            if affected not in pool_ids:
                raise CatalogBuildError(
                    f"exposure row {row.kind}/{row.id} references {affected!r}, which is not in the pool"
                )
    rows = {(row.kind, row.id): row for row in exposures}
    for question in pool:
        row = rows.get(("question", question.question_id))
        if row is None:
            raise CatalogBuildError(
                f"pool question {question.question_id!r} has no exposure row (own self-reference required)"
            )
        if question.question_id not in row.affected_question_ids:
            raise CatalogBuildError(
                f"exposure row for {question.question_id!r} must include its own id"
            )


def build_artifact(content_root, manifest_path, release_path, exposures_path) -> bytes:
    """Return the canonical artifact bytes, or raise ``CatalogBuildError``."""
    release = parse_release(_read_json(release_path, "release"))
    exposures = parse_exposures(_read_json(exposures_path, "exposures"))
    try:
        catalog = ContentCatalog.load(content_root, manifest_path)
    except ContentError as exc:
        raise CatalogBuildError("content catalog could not be loaded") from exc
    pool = _load_pool(catalog, release)
    _validate_exposures(exposures, pool, catalog)

    by_course: dict[str, list[PoolQuestion]] = {}
    for question in pool:
        by_course.setdefault(question.course_id, []).append(question)

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "catalog_version": release.catalog_version,
        "rules_version": release.rules_version,
        "family": release.family,
        "courses": [
            {
                "course_id": course_id,
                "N_c": len(by_course[course_id]),
                "questions": [
                    {
                        "question_id": question.question_id,
                        "product_id": question.product_id,
                        "content_sha256": question.content_sha256,
                    }
                    for question in sorted(by_course[course_id], key=lambda item: item.question_id)
                ],
            }
            for course_id in sorted(by_course)
        ],
        "exposures": [
            {
                "kind": row.kind,
                "id": row.id,
                "content_sha256": row.content_sha256,
                "affected_question_ids": list(row.affected_question_ids),
            }
            for row in exposures
        ],
    }
    return (json.dumps(artifact, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# --- output ------------------------------------------------------------------


def _existing_catalog_version(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogBuildError("existing artifact is not valid JSON; refusing to overwrite") from exc
    version = data.get("catalog_version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def _atomic_write(path: Path, payload: bytes) -> None:
    directory = path.parent if str(path.parent) else Path(".")
    handle = tempfile.NamedTemporaryFile(dir=directory, prefix=".scoring-catalog-", delete=False)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except OSError:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def write_artifact(out_path, payload: bytes) -> str:
    """Write atomically; refuse to reuse a release version with a changed pool."""
    path = Path(out_path)
    if path.exists():
        current = path.read_bytes()
        if current == payload:
            return "unchanged"
        new_version = json.loads(payload.decode("utf-8"))["catalog_version"]
        if _existing_catalog_version(path) == new_version:
            raise CatalogBuildError(
                "catalog_version already exists with a different pool; a new release version is required"
            )
    _atomic_write(path, payload)
    return "written"


def check_artifact(out_path, payload: bytes) -> bool:
    path = Path(out_path)
    if not path.exists():
        return False
    return path.read_bytes() == payload


def unexposed_manifest_entries(content_root, manifest_path, exposures_path) -> tuple[tuple[str, str], ...]:
    """Manifest ``(kind, id)`` entries with no exposure row.

    The runtime default-denies every such item (for every product) while scoring
    is on, so a release owner wants to see what the server will refuse. This is a
    report, never part of the artifact, and it makes no claim that the listed
    items are safe or that the exposure map is a complete leak inventory.
    """
    exposures = parse_exposures(_read_json(exposures_path, "exposures"))
    try:
        catalog = ContentCatalog.load(content_root, manifest_path)
    except ContentError as exc:
        raise CatalogBuildError("content catalog could not be loaded") from exc
    covered = {(row.kind, row.id) for row in exposures}
    return tuple(sorted(
        (entry.kind, entry.id) for entry in catalog.entries()
        if (entry.kind, entry.id) not in covered
    ))


# --- CLI ---------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.scoring_catalog_build",
        description="Build or check a frozen scorer catalog artifact (synthetic inputs in tests).",
    )
    parser.add_argument("--content-root", required=True, help="private content root (never written)")
    parser.add_argument("--manifest", required=True, help="content manifest with kind/id/product/hash")
    parser.add_argument("--release", required=True, help="versioned release selection (explicit pool)")
    parser.add_argument("--exposures", required=True, help="reviewed exposure map")
    parser.add_argument("--out", required=True, help="artifact path")
    parser.add_argument("--check", action="store_true",
                        help="compare --out with a fresh build without writing; non-zero on drift")
    parser.add_argument("--list-unexposed", action="store_true",
                        help="also list manifest entries with no exposure row (runtime default-deny)")
    return parser


def _report_unexposed(content_root, manifest_path, exposures_path, list_entries: bool) -> None:
    unexposed = unexposed_manifest_entries(content_root, manifest_path, exposures_path)
    if not unexposed:
        return
    print(f"note: {len(unexposed)} manifest entries have no exposure row and stay default-denied at runtime",
          file=sys.stderr)
    if list_entries:
        for kind, ident in unexposed:
            print(f"  {kind}/{ident}", file=sys.stderr)


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = build_artifact(args.content_root, args.manifest, args.release, args.exposures)
        _report_unexposed(args.content_root, args.manifest, args.exposures, args.list_unexposed)
        if args.check:
            if check_artifact(args.out, payload):
                print("scoring catalog unchanged")
                return 0
            print("scoring catalog drift: artifact differs from a fresh build", file=sys.stderr)
            return 1
        result = write_artifact(args.out, payload)
        print(f"scoring catalog {result}")
        return 0
    except CatalogBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
