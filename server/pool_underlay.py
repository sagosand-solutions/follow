# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pool underlay: ids, content hashes and states of the protected questions, and nothing else (no bytes, no text).

It lets ``server.pool check-static`` run without a database, for a release pipeline or a self-test. The digest proves only that
the file is intact; it does not prove where the file came from or that it is current and complete. A real release therefore
binds the check to a digest taken from the release decision (``--expect-digest``) and the file must be exported from the
authoritative pool, never derived from the input being checked. Anything missing, malformed, empty or mismatching is an error.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

SCHEMA = 1
KIND = "scoring-pool-underlay"
MAX_BYTES = 4 * 1024 * 1024
MAX_QUESTIONS = 100_000
STATES = ("ranked", "retired")
_ID_RE = re.compile(r"[a-z][a-z0-9.-]{0,79}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOP = frozenset({"schema", "kind", "count", "questions", "digest"})
_ROW = frozenset({"question_id", "content_sha256", "state"})


class UnderlayError(Exception):
    """The underlay cannot be trusted. The message names the problem, never a value from the file."""


def digest_of(questions: list) -> str:
    return hashlib.sha256(json.dumps(questions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build(rows) -> dict:
    """Underlay document from (question_id, content_sha256, state) rows."""
    questions = sorted(({"question_id": q, "content_sha256": h, "state": s} for q, h, s in rows), key=lambda r: r["question_id"])
    _validate_questions(questions)
    return {"schema": SCHEMA, "kind": KIND, "count": len(questions), "questions": questions, "digest": digest_of(questions)}


def dumps(document: dict) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=1) + "\n").encode()


def from_db(db) -> dict:
    return build((r["question_id"], r["content_sha256"], r["state"]) for r in db.connect().execute(
        "SELECT question_id, content_sha256, state FROM pool_questions WHERE state IN ('ranked', 'retired')"))


def from_catalogs(paths) -> dict:
    """From frozen scoring-catalog artifacts (a release input). A catalog that does not parse, including one whose pool question
    lacks its own exposure row, stops here, before anything is exported."""
    from .models import ContentError
    from .scoring_catalog import load_catalog
    rows = []
    for path in paths:
        try:
            catalog = load_catalog(path)
        except ContentError:
            raise UnderlayError("a scoring catalog could not be loaded or has an exposure gap") from None
        rows.extend((pq.question_id, pq.content_sha256, "ranked") for pq in catalog.questions.values())
    return build(rows)


def write(document: dict, out: str) -> None:
    """Create-only, atomic: an existing file is never overwritten."""
    target = Path(out)
    if target.exists() or target.is_symlink():
        raise UnderlayError("refusing to overwrite an existing file")
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".underlay-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(dumps(document))
        os.link(tmp, target)                                    # fails if the target appeared meanwhile
    except OSError:
        raise UnderlayError("could not write the underlay") from None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _validate_questions(questions: list) -> None:
    if not 1 <= len(questions) <= MAX_QUESTIONS:
        raise UnderlayError("the pool is empty or too large: an empty underlay is never a pass")
    ids, hashes = set(), set()
    for row in questions:
        if (not isinstance(row, dict) or set(row) != _ROW or not isinstance(row["question_id"], str) or not _ID_RE.fullmatch(row["question_id"])
                or not isinstance(row["content_sha256"], str) or not _HASH_RE.fullmatch(row["content_sha256"]) or row["state"] not in STATES):
            raise UnderlayError("a question row is malformed")
        if row["question_id"] in ids or row["content_sha256"] in hashes:
            raise UnderlayError("a question id or content hash is listed twice")
        ids.add(row["question_id"])
        hashes.add(row["content_sha256"])


def load(path: str) -> tuple[list, str]:
    """(protected [(question_id, content_sha256)], digest). Raises UnderlayError on anything but a complete, intact file."""
    target = Path(path)
    try:
        if target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_BYTES:
            raise UnderlayError("the underlay is missing, not a plain file, or too large")
        document = json.loads(target.read_bytes().decode("utf-8"), object_pairs_hook=_no_duplicates,
                              parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except (OSError, ValueError):
        raise UnderlayError("the underlay is unreadable or not strict JSON") from None
    if not isinstance(document, dict) or set(document) != _TOP or document["schema"] != SCHEMA or document["kind"] != KIND:
        raise UnderlayError("unknown underlay shape or version")
    questions = document["questions"]
    if not isinstance(questions, list):
        raise UnderlayError("the question list is malformed")
    _validate_questions(questions)
    if document["count"] != len(questions) or questions != sorted(questions, key=lambda r: r["question_id"]):
        raise UnderlayError("the count or the order does not match")
    declared = document["digest"]
    if not isinstance(declared, str) or not _HASH_RE.fullmatch(declared) or declared != digest_of(questions):
        raise UnderlayError("the digest does not match the content")
    return [(r["question_id"], r["content_sha256"]) for r in questions], declared
