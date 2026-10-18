# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runtime scoring catalog: a frozen, read-only snapshot loaded once at start-up.

The artifact is produced offline (024) and bound to a deployment. This module
only reads and validates it; it never builds a pool, scans content or falls back
to raw bytes. Any inconsistency raises ``ContentError`` so the app answers 503.
Question and exposure hashes are internal and never reach a DTO.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .models import ContentError

FAMILIES = ("official", "free")
RULES_VERSION = "r2"  # +2/+1/0/-1 units, 1000*sum/(2*N_c); r1 (quarter points) is never reinterpreted
SCORE_UNITS = {"correct": 2, "partial": 1, "not_sure": 0, "wrong": -1}
EXPOSURE_KINDS = frozenset({"question", "exam", "study"})
_ID_RE = re.compile(r"[a-z][a-z0-9.-]{0,79}\Z")
_COURSE_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOP_KEYS = frozenset({"schema_version", "catalog_version", "rules_version", "family", "courses", "exposures"})
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class PoolQuestion:
    question_id: str
    course_id: str
    product_id: str | None
    content_sha256: str


@dataclass(frozen=True)
class ScoringCatalog:
    family: str
    catalog_version: str
    rules_version: str
    course_ids: tuple[str, ...]            # sorted; K = len(course_ids)
    pool_sizes: dict                        # course_id -> N_c
    questions: dict                         # question_id -> PoolQuestion
    exposures: dict                         # (kind, id) -> (content_sha256, frozenset(affected_question_ids))

    @property
    def course_count(self) -> int:
        return len(self.course_ids)

    def exposure(self, kind: str, content_id: str):
        return self.exposures.get((kind, content_id))


def _fail(message: str):
    raise ContentError(f"scoring catalog: {message}")


def _exact(obj, keys, what):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        _fail(f"{what}: exact keys {sorted(keys)} required")


def parse_catalog(data: dict, *, expected_family: str | None = None, content_catalog=None) -> ScoringCatalog:
    """Validate a decoded artifact. ``content_catalog`` (official) binds product ids to the manifest."""
    _exact(data, _TOP_KEYS, "artifact")
    if data["schema_version"] != 1 or isinstance(data["schema_version"], bool):
        _fail("unsupported schema_version")
    family = data["family"]
    if family not in FAMILIES or (expected_family is not None and family != expected_family):
        _fail("unknown or unexpected family")
    for name in ("catalog_version", "rules_version"):
        if not isinstance(data[name], str) or not _VERSION_RE.fullmatch(data[name]):
            _fail(f"{name} invalid")
    if data["rules_version"] != RULES_VERSION:
        _fail("unknown rules_version")
    courses = data["courses"]
    if not isinstance(courses, list) or not courses:
        _fail("at least one course required")
    pool_sizes: dict = {}
    questions: dict = {}
    for course in courses:
        _exact(course, {"course_id", "N_c", "questions"}, "course")
        course_id = course["course_id"]
        if not isinstance(course_id, str) or not _COURSE_RE.fullmatch(course_id) or course_id in pool_sizes:
            _fail("course_id invalid or duplicated")
        items = course["questions"]
        if not isinstance(items, list) or not items:
            _fail(f"course {course_id}: empty pool")
        if isinstance(course["N_c"], bool) or course["N_c"] != len(items):
            _fail(f"course {course_id}: N_c does not equal the pool size")
        for item in items:
            _exact(item, {"question_id", "product_id", "content_sha256"}, "question")
            qid, product_id, digest = item["question_id"], item["product_id"], item["content_sha256"]
            if not isinstance(qid, str) or not _ID_RE.fullmatch(qid) or qid in questions:
                _fail("question_id invalid or not globally unique")
            if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
                _fail("content_sha256 invalid")
            if family == "official":
                if not isinstance(product_id, str) or not _COURSE_RE.fullmatch(product_id):
                    _fail("official question needs a product_id")
                if content_catalog is not None:
                    entry = content_catalog.lookup("question", qid)
                    if entry is None or entry.product_id != product_id or entry.sha256 != digest:
                        _fail("official question disagrees with the content manifest")
            elif product_id is not None:
                _fail("free question must not carry a product_id")
            questions[qid] = PoolQuestion(qid, course_id, product_id, digest)
        pool_sizes[course_id] = len(items)
    exposures: dict = {}
    for row in data["exposures"] if isinstance(data["exposures"], list) else _fail("exposures must be a list"):
        _exact(row, {"kind", "id", "content_sha256", "affected_question_ids"}, "exposure")
        kind, content_id, digest, affected = row["kind"], row["id"], row["content_sha256"], row["affected_question_ids"]
        if kind not in EXPOSURE_KINDS or not isinstance(content_id, str) or not _ID_RE.fullmatch(content_id):
            _fail("exposure kind/id invalid")
        if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
            _fail("exposure hash invalid")
        if (not isinstance(affected, list) or len(set(affected)) != len(affected)
                or any(not isinstance(a, str) or a not in questions for a in affected)):
            _fail("affected_question_ids must be unique pool members")
        if (kind, content_id) in exposures:
            _fail("duplicate exposure row")
        if content_catalog is not None and family == "official":
            entry = content_catalog.lookup(kind, content_id)
            if entry is None or entry.sha256 != digest:
                _fail("exposure disagrees with the content manifest")
        exposures[(kind, content_id)] = (digest, frozenset(affected))
    for qid, question in questions.items():
        row = exposures.get(("question", qid))
        if row is None or qid not in row[1]:
            _fail(f"pool question {qid} lacks its own exposure row")
        if row[0] != question.content_sha256:
            _fail(f"pool question {qid} exposure hash differs from the pool hash")
    return ScoringCatalog(family, data["catalog_version"], data["rules_version"], tuple(sorted(pool_sizes)),
                          pool_sizes, questions, exposures)


def load_catalog(path, *, expected_family: str | None = None, content_catalog=None) -> ScoringCatalog:
    """Read an artifact from an explicit local path (no symlink, bounded size)."""
    target = Path(path)
    try:
        if target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_ARTIFACT_BYTES:
            _fail("artifact path rejected")
        data = json.loads(target.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise ContentError("scoring catalog: unreadable artifact") from exc
    return parse_catalog(data, expected_family=expected_family, content_catalog=content_catalog)
