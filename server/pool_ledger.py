# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pool ledger, the release decision and the checks that bind a candidate to them.

The ledger is an append-only, per-release record of EVERY question the scoring pool has ever registered, in either family: id, family,
content hash, and a state that only moves ranked -> retired -> free. Nothing is ever removed or re-hashed. Each ledger carries the digest
of the one before it and the lifecycle events since then, so a verifier can prove that every change between two ledgers is explained by
a complete, ordered, valid chain of events with the references that were actually used. A decision written by the Architect (read from a
separate, trusted checkout, never from the candidate) pins the ledger digest, the counts, the public git tree and the open catalog
versions. No raw question text, answer or person ever enters any of these documents. Rows are ids and hashes only.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .models import ContentError

SCHEMA = 2
LEDGER_KIND = "scoring-pool-ledger"
INVENTORY_KIND = "scoring-pool-inventory"
DECISION_KIND = "scoring-release-decision"
PREFIX = {LEDGER_KIND: b"follow/pool-ledger/schema-2\n", INVENTORY_KIND: b"follow/pool-inventory/schema-2\n"}
FAMILIES = ("official", "free")
STATES = ("ranked", "retired", "free")
MOVES = {("ranked", "retired"), ("retired", "free")}
MAX_BYTES = 8 * 1024 * 1024
MAX_QUESTIONS = 100_000
_ID_RE = re.compile(r"[a-z][a-z0-9.-]{0,79}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}\Z")
_CODE_RE = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_RELEASE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_ROW = ("question_id", "family", "content_sha256", "state", "clone_audit_ref", "license_ok_ref", "exposure_audit_ref")
_EVENT = ("question_id", "from", "to", "release_id", "reason_code", "clone_audit_ref", "license_ok_ref", "exposure_audit_ref")
_REFS = ("clone_audit_ref", "license_ok_ref", "exposure_audit_ref")


class LedgerError(Exception):
    """A document cannot be trusted or a binding does not hold. The message names the problem, never a value from a file."""


# --- canonical digest ----------------------------------------------------------------------------------------------------------------
def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def document_digest(document: dict) -> str:
    """sha-256 over a domain prefix and the canonical form of EVERY field except ``digest`` itself."""
    body = {k: v for k, v in document.items() if k != "digest"}
    return hashlib.sha256(PREFIX[document["kind"]] + canonical(body)).hexdigest()


def events_digest(events: list) -> str:
    return hashlib.sha256(b"follow/pool-events/schema-2\n" + canonical(events)).hexdigest()


def empty_counts() -> dict:
    return {family: {state: 0 for state in STATES} for family in FAMILIES}


def counts_of(rows: list) -> dict:
    counts = empty_counts()
    for row in rows:
        counts[row["family"]][row["state"]] += 1
    return counts


# --- strict loading ----------------------------------------------------------------------------------------------------------------------
def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def read_json(path) -> dict:
    target = Path(path)
    try:
        if target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_BYTES:
            raise LedgerError("a document is missing, not a plain file, or too large")
        document = json.loads(target.read_bytes().decode("utf-8"), object_pairs_hook=_no_duplicates,
                              parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except (OSError, ValueError):
        raise LedgerError("a document is unreadable or not strict JSON") from None
    if not isinstance(document, dict):
        raise LedgerError("a document is not an object")
    return document


def _opt_ref(value) -> bool:
    return value is None or (isinstance(value, str) and bool(_REF_RE.fullmatch(value)))


def _check_row(row) -> None:
    if not isinstance(row, dict) or set(row) != set(_ROW):
        raise LedgerError("a question row is malformed")
    if (not isinstance(row["question_id"], str) or not _ID_RE.fullmatch(row["question_id"]) or row["family"] not in FAMILIES
            or not isinstance(row["content_sha256"], str) or not _HASH_RE.fullmatch(row["content_sha256"]) or row["state"] not in STATES
            or not all(_opt_ref(row[k]) for k in _REFS)):
        raise LedgerError("a question row is malformed")
    if row["state"] == "ranked" and any(row[k] is not None for k in _REFS):
        raise LedgerError("a ranked question carries no lifecycle references")
    if row["state"] == "retired" and (row["license_ok_ref"] is not None or row["exposure_audit_ref"] is not None):
        raise LedgerError("a retired question carries no licence or exposure reference")
    if row["state"] == "free" and any(row[k] is None for k in _REFS):
        raise LedgerError("a free question needs all three references")


def _check_event(event) -> None:
    if not isinstance(event, dict) or set(event) != set(_EVENT):
        raise LedgerError("an event is malformed")
    if (not isinstance(event["question_id"], str) or not _ID_RE.fullmatch(event["question_id"]) or (event["from"], event["to"]) not in MOVES
            or not isinstance(event["release_id"], str) or not _RELEASE_RE.fullmatch(event["release_id"])
            or not isinstance(event["reason_code"], str) or not _CODE_RE.fullmatch(event["reason_code"])
            or not all(_opt_ref(event[k]) for k in _REFS)):
        raise LedgerError("an event is malformed")
    if event["to"] == "retired" and (event["license_ok_ref"] is not None or event["exposure_audit_ref"] is not None):
        raise LedgerError("a retire event carries no licence or exposure reference")
    if event["to"] == "free" and any(event[k] is None for k in _REFS):
        raise LedgerError("a free event needs all three references")


def _check_rows(rows) -> None:
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_QUESTIONS:
        raise LedgerError("the question list is empty, too large or malformed")
    ids, hashes = set(), set()
    for row in rows:
        _check_row(row)
        if row["question_id"] in ids or row["content_sha256"] in hashes:
            raise LedgerError("a question id or content hash is listed twice")
        ids.add(row["question_id"])
        hashes.add(row["content_sha256"])
    if rows != sorted(rows, key=lambda r: r["question_id"]):
        raise LedgerError("the question rows are not in canonical order")


def _check_events(events) -> None:
    if not isinstance(events, list) or len(events) > MAX_QUESTIONS * 2:
        raise LedgerError("the event list is malformed")
    for event in events:
        _check_event(event)


def _check_counts(counts) -> None:
    if (not isinstance(counts, dict) or set(counts) != set(FAMILIES)
            or not all(isinstance(counts[f], dict) and set(counts[f]) == set(STATES)
                       and all(isinstance(counts[f][s], int) and not isinstance(counts[f][s], bool) and counts[f][s] >= 0 for s in STATES)
                       for f in FAMILIES)):
        raise LedgerError("the counts are malformed")


def validate_ledger(document: dict) -> dict:
    keys = {"schema", "kind", "seq", "release_id", "previous_digest", "questions", "events", "counts", "lifecycle_events_digest", "digest"}
    if set(document) != keys or document["schema"] != SCHEMA or document["kind"] != LEDGER_KIND:
        raise LedgerError("unknown ledger shape or version")
    seq = document["seq"]
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise LedgerError("the sequence number is malformed")
    if not isinstance(document["release_id"], str) or not _RELEASE_RE.fullmatch(document["release_id"]):
        raise LedgerError("the release id is malformed")
    previous = document["previous_digest"]
    if (seq == 1) != (previous is None) or (previous is not None and not (isinstance(previous, str) and _HASH_RE.fullmatch(previous))):
        raise LedgerError("the previous digest does not fit the sequence number")
    _check_rows(document["questions"])
    _check_events(document["events"])
    _check_counts(document["counts"])
    if document["counts"] != counts_of(document["questions"]):
        raise LedgerError("the counts do not match the rows")
    for key in ("lifecycle_events_digest", "digest"):
        if not isinstance(document[key], str) or not _HASH_RE.fullmatch(document[key]):
            raise LedgerError("a digest is malformed")
    if document["lifecycle_events_digest"] != events_digest(document["events"]):
        raise LedgerError("the event digest does not match the events")
    if document["digest"] != document_digest(document):
        raise LedgerError("the ledger digest does not match its content")
    return document


def validate_inventory(document: dict) -> dict:
    keys = {"schema", "kind", "questions", "events", "digest"}
    if set(document) != keys or document["schema"] != SCHEMA or document["kind"] != INVENTORY_KIND:
        raise LedgerError("unknown inventory shape or version")
    _check_rows(document["questions"])
    _check_events(document["events"])
    if not isinstance(document["digest"], str) or document["digest"] != document_digest(document):
        raise LedgerError("the inventory digest does not match its content")
    return document


def load_ledger(path) -> dict:
    return validate_ledger(read_json(path))


def load_inventory(path) -> dict:
    return validate_inventory(read_json(path))


def seal(document: dict) -> dict:
    document = dict(document)
    document["digest"] = document_digest(document)
    return document


def dumps(document: dict) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=1) + "\n").encode()


def write_new(document: dict, out: str) -> None:
    """Create-only and atomic: an existing file (or link) is never overwritten."""
    import os
    import tempfile
    target = Path(out)
    if target.exists() or target.is_symlink():
        raise LedgerError("refusing to overwrite an existing file")
    try:
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".ledger-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(dumps(document))
            os.link(tmp, target)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except OSError:
        raise LedgerError("could not write the document") from None


# --- the chain: every change between two ledgers is an explained, ordered, valid sequence of events ---------------------------------------
def verify_step(previous: dict, current: dict) -> None:
    """Raise LedgerError unless ``current`` is a valid successor of ``previous``."""
    if current["seq"] != previous["seq"] + 1 or current["previous_digest"] != previous["digest"]:
        raise LedgerError("the ledger does not continue the previous one")
    old = {r["question_id"]: r for r in previous["questions"]}
    new = {r["question_id"]: r for r in current["questions"]}
    for qid, row in old.items():
        now = new.get(qid)
        if now is None:
            raise LedgerError("a registered question has disappeared")
        if (now["family"], now["content_sha256"]) != (row["family"], row["content_sha256"]):
            raise LedgerError("a registered question changed its family or content hash")
    for qid, row in new.items():
        if qid not in old and (row["state"] != "ranked" or any(row[k] is not None for k in _REFS)):
            raise LedgerError("a new question must enter as plain ranked")
    by_question: dict = {}
    for event in current["events"]:
        if event["question_id"] not in old:
            raise LedgerError("an event concerns a question the previous ledger did not hold")
        by_question.setdefault(event["question_id"], []).append(event)
    for qid, row in old.items():
        chain = by_question.get(qid, [])
        state = row["state"]
        for event in chain:
            if event["from"] != state or (event["from"], event["to"]) not in MOVES:
                raise LedgerError("an event chain is not a complete ordered sequence of valid moves")
            state = event["to"]
        if state != new[qid]["state"]:
            raise LedgerError("a state change is not explained by its complete event chain")
        if chain and chain[-1]["to"] == "free":
            if any(chain[-1][k] != new[qid][k] for k in _REFS):
                raise LedgerError("the free references of an event and its row differ")
        elif chain and chain[-1]["to"] == "retired" and chain[-1]["clone_audit_ref"] != new[qid]["clone_audit_ref"]:
            raise LedgerError("the clone reference of an event and its row differ")
        if not chain and any(new[qid][k] != row[k] for k in _REFS):
            raise LedgerError("a reference changed without an event")


def load_history(directory) -> list:
    """The trusted chain from ``<directory>/pool-ledger``: contiguous, valid, each step verified. An absent directory is an empty history."""
    base = Path(directory) / "pool-ledger"
    if not base.exists():
        return []
    if base.is_symlink() or not base.is_dir():
        raise LedgerError("the ledger directory is not a plain directory")
    names = sorted(p.name for p in base.iterdir())
    ledgers = []
    for name in names:
        if not re.fullmatch(r"ledger-[0-9]{6}\.json", name):
            raise LedgerError("an unexpected file sits in the ledger directory")
        ledger = load_ledger(base / name)
        if name != f"ledger-{ledger['seq']:06d}.json":
            raise LedgerError("a ledger file name does not match its sequence number")
        ledgers.append(ledger)
    for index, ledger in enumerate(ledgers):
        if ledger["seq"] != index + 1:
            raise LedgerError("the ledger sequence has a gap")
        if index == 0:
            if ledger["events"]:
                raise LedgerError("the first ledger cannot carry events")
            if any(r["state"] != "ranked" for r in ledger["questions"]):
                raise LedgerError("the first ledger registers plain ranked questions only")
        else:
            verify_step(ledgers[index - 1], ledger)
    return ledgers


# --- decision --------------------------------------------------------------------------------------------------------------------------
def validate_decision(document: dict) -> dict:
    keys = {"schema", "kind", "release_id", "candidate_commit", "public_tree", "artifact_sha256", "previous_ledger_digest", "ledger_digest",
            "first_ledger", "no_prior_history_attestation", "human_approval_ref", "counts", "open_versions", "released", "decided_at"}
    if set(document) != keys or document["schema"] != 1 or document["kind"] != DECISION_KIND:
        raise LedgerError("unknown decision shape or version")
    checks = (
        (isinstance(document["release_id"], str) and _RELEASE_RE.fullmatch(document["release_id"]) is not None),
        (isinstance(document["candidate_commit"], str) and _GIT_RE.fullmatch(document["candidate_commit"]) is not None),
        (isinstance(document["public_tree"], str) and _GIT_RE.fullmatch(document["public_tree"]) is not None),
        (isinstance(document["artifact_sha256"], str) and _HASH_RE.fullmatch(document["artifact_sha256"]) is not None),
        (isinstance(document["ledger_digest"], str) and _HASH_RE.fullmatch(document["ledger_digest"]) is not None),
        (isinstance(document["first_ledger"], bool)),
        (isinstance(document["human_approval_ref"], str) and _REF_RE.fullmatch(document["human_approval_ref"]) is not None),
        (isinstance(document["decided_at"], str) and 10 <= len(document["decided_at"]) <= 40),
    )
    if not all(checks):
        raise LedgerError("a decision field is malformed")
    previous, attestation = document["previous_ledger_digest"], document["no_prior_history_attestation"]
    if document["first_ledger"]:
        if previous is not None or not (isinstance(attestation, str) and _REF_RE.fullmatch(attestation)):
            raise LedgerError("a first ledger needs the explicit no-prior-history attestation and no previous digest")
    elif not (isinstance(previous, str) and _HASH_RE.fullmatch(previous)) or attestation is not None:
        raise LedgerError("a later ledger names its previous digest and carries no first-ledger attestation")
    _check_counts(document["counts"])
    versions = document["open_versions"]
    if not isinstance(versions, list) or not versions:
        raise LedgerError("the open versions are missing: nothing authoritative to reconcile against")
    seen = set()
    for item in versions:
        if (not isinstance(item, dict) or set(item) != {"family", "catalog_version", "catalog_digest"} or item["family"] not in FAMILIES
                or not isinstance(item["catalog_version"], str) or not _VERSION_RE.fullmatch(item["catalog_version"])
                or not isinstance(item["catalog_digest"], str) or not _HASH_RE.fullmatch(item["catalog_digest"])):
            raise LedgerError("an open version entry is malformed")
        key = (item["family"], item["catalog_version"])
        if key in seen:
            raise LedgerError("an open version is listed twice")
        seen.add(key)
    released = document["released"]
    if not isinstance(released, list):
        raise LedgerError("the released list is malformed")
    for item in released:
        if (not isinstance(item, dict) or set(item) != {"question_id", "content_sha256", *_REFS} or not isinstance(item["question_id"], str)
                or not _ID_RE.fullmatch(item["question_id"]) or not isinstance(item["content_sha256"], str) or not _HASH_RE.fullmatch(item["content_sha256"])
                or not all(isinstance(item[k], str) and _REF_RE.fullmatch(item[k]) for k in _REFS)):
            raise LedgerError("a released entry is malformed")
    return document


def load_decision(directory, release_id: str) -> dict:
    if not isinstance(release_id, str) or not _RELEASE_RE.fullmatch(release_id):
        raise LedgerError("the release id is malformed")
    folder = Path(directory) / "release-decisions"
    if folder.is_symlink():
        raise LedgerError("the decision directory is not a plain directory")
    document = validate_decision(read_json(folder / f"{release_id}.json"))
    if document["release_id"] != release_id:
        raise LedgerError("the decision names another release")
    return document


# --- composing a ledger (the operator's deterministic helper; check_release re-verifies independently) ---------------------------------
def catalog_rows(catalogs: list) -> list:
    rows = []
    for catalog in catalogs:
        rows.extend({"question_id": pq.question_id, "family": catalog.family, "content_sha256": pq.content_sha256, "state": "ranked",
                     "clone_audit_ref": None, "license_ok_ref": None, "exposure_audit_ref": None} for pq in catalog.questions.values())
    return rows


def compose(release_id: str, previous: dict | None, inventory: dict | None, catalogs: list) -> dict:
    if not isinstance(release_id, str) or not _RELEASE_RE.fullmatch(release_id):
        raise LedgerError("the release id is malformed")
    additions = catalog_rows(catalogs)
    if previous is None:
        if inventory is not None:
            raise LedgerError("a first ledger has no database inventory: there is no history")
        rows, events, seq, previous_digest = additions, [], 1, None
    else:
        if inventory is None:
            raise LedgerError("a later ledger needs the authoritative database inventory")
        rows = [dict(r) for r in inventory["questions"]]
        known = {r["question_id"] for r in rows} | {r["question_id"] for r in previous["questions"]}
        rows += [r for r in additions if r["question_id"] not in known]
        old = {r["question_id"]: r["state"] for r in previous["questions"]}
        events = []
        wanted = {r["question_id"] for r in rows if r["question_id"] in old and r["state"] != old[r["question_id"]]}
        by_question: dict = {}
        for event in inventory["events"]:
            by_question.setdefault(event["question_id"], []).append(event)
        for qid in sorted(wanted):
            chain, state, taking = [], old[qid], False
            for event in by_question.get(qid, []):
                if event["from"] == state and not taking:
                    taking = True
                if taking:
                    chain.append(dict(event))
                    state = event["to"]
            events.extend(chain)
        seq, previous_digest = previous["seq"] + 1, previous["digest"]
    rows = sorted(rows, key=lambda r: r["question_id"])
    document = {"schema": SCHEMA, "kind": LEDGER_KIND, "seq": seq, "release_id": release_id, "previous_digest": previous_digest,
                "questions": rows, "events": events, "counts": counts_of(rows), "lifecycle_events_digest": events_digest(events)}
    sealed = seal(document)
    return validate_ledger(sealed)
