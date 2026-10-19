# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release gate: export the live pool read-only, and check a candidate against the trusted history and the Architect's decision.

``check_release`` refuses (LedgerError, exit 2) on any missing, malformed, empty, stale, mismatching or unverifiable input and returns
findings (exit 1) only for a protected question found in the scanned bytes. There is no unbound or fallback mode: a green result means
every binding below held and nothing protected appears in the exact tree the decision pinned.
"""
from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote

from . import pool_artifact, pool_ledger
from .models import ContentError
from .pool_ledger import LedgerError
from .scoring_catalog import load_catalog


def _open_snapshot(db_path: str) -> sqlite3.Connection:
    """Open a quiescent snapshot copy strictly read-only: mode=ro and immutable=1 (no locks, no -shm, no journal pragma, no write).

    ``immutable`` never looks at a write-ahead log, so a snapshot with a non-empty -wal (or a hot rollback journal) could silently
    miss the newest rows; such a file is refused. Produce the snapshot with SQLite's backup API or ``VACUUM INTO`` (both fully
    checkpointed), never by copying a live database file."""
    path = Path(db_path)
    if path.is_symlink() or not path.is_file():
        raise LedgerError("the database snapshot is missing or not a plain file")
    for suffix in ("-wal", "-journal"):
        extra = Path(str(path) + suffix)
        if extra.exists() and extra.stat().st_size > 0:
            raise LedgerError("the database has unapplied journal data: export from a checkpointed snapshot, not a live file")
    try:
        conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn
    except sqlite3.Error:
        raise LedgerError("the database could not be opened read-only") from None


def export_inventory(db_path: str) -> dict:
    """The pool as an inventory document, read from a snapshot through a strictly READ-ONLY connection in one transaction."""
    conn = _open_snapshot(db_path)
    try:
        conn.execute("BEGIN")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise LedgerError("the database failed its integrity check: not a trustworthy snapshot")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"pool_questions", "pool_lifecycle_events", "schema_migrations"} <= tables:
            raise LedgerError("the database has no pool tables: not a scoring database")
        if (conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] or 0) < 7:
            raise LedgerError("the database predates the pool lifecycle")
        rows = [dict(r) for r in conn.execute(
            "SELECT question_id, family, content_sha256, state, clone_audit_ref, license_ok_ref, exposure_audit_ref FROM pool_questions "
            "ORDER BY question_id")]
        free = {r["question_id"]: r for r in rows if r["state"] == "free"}
        events = []
        for r in conn.execute("SELECT question_id, from_state, to_state, release_id, reason_code, clone_audit_ref FROM pool_lifecycle_events "
                              "ORDER BY id"):
            row = free.get(r["question_id"]) if r["to_state"] == "free" else None
            events.append({"question_id": r["question_id"], "from": r["from_state"], "to": r["to_state"], "release_id": r["release_id"],
                           "reason_code": r["reason_code"], "clone_audit_ref": r["clone_audit_ref"],
                           "license_ok_ref": row["license_ok_ref"] if row else None, "exposure_audit_ref": row["exposure_audit_ref"] if row else None})
        conn.execute("COMMIT")
    except LedgerError:
        raise
    except Exception:
        raise LedgerError("the database could not be read") from None
    finally:
        conn.close()
    return pool_ledger.validate_inventory(pool_ledger.seal({"schema": pool_ledger.SCHEMA, "kind": pool_ledger.INVENTORY_KIND,
                                                            "questions": rows, "events": events}))


def _catalogs(paths: list) -> tuple[list, list]:
    """(loaded catalogs, [(family, catalog_version, file sha-256)]). A catalog with an exposure gap or a bad shape is refused."""
    loaded, digests = [], []
    for path in paths:
        try:
            catalog = load_catalog(path)
            data = Path(path).read_bytes()
        except (ContentError, OSError):
            raise LedgerError("a scoring catalog could not be loaded or has an exposure gap") from None
        loaded.append(catalog)
        digests.append((catalog.family, catalog.catalog_version, hashlib.sha256(data).hexdigest()))
    return loaded, digests


def _git_tree(repo: str, commit: str) -> str:
    try:
        out = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"{commit}:public"], capture_output=True, text=True,
                             timeout=30, check=False)  # argument list, never a shell; read-only
    except (OSError, subprocess.SubprocessError):
        raise LedgerError("git could not be run to verify the candidate tree") from None
    value = out.stdout.strip()
    if out.returncode != 0 or not value:
        raise LedgerError("the candidate commit has no public tree in that repository")
    return value


def check_release(*, trusted_dir: str, release_id: str, ledger_path: str, public_root: str, candidate: str, catalog_paths: list,
                  inventory_path: str | None = None, git_repo: str | None = None) -> dict:
    """Verify every binding; returns {'receipt': {...}, 'findings': [...]}. Raises LedgerError for anything unverifiable."""
    history = pool_ledger.load_history(trusted_dir)
    decision = pool_ledger.load_decision(trusted_dir, release_id)
    if not isinstance(candidate, str) or candidate != decision["candidate_commit"]:
        raise LedgerError("the candidate commit is not the one the decision names")
    current = pool_ledger.load_ledger(ledger_path)
    if current["release_id"] != decision["release_id"] or current["digest"] != decision["ledger_digest"]:
        raise LedgerError("the ledger is not the one the decision names")
    if current["counts"] != decision["counts"]:
        raise LedgerError("the ledger counts differ from the decision")

    # the baseline comes from the trusted history, never from the candidate
    if decision["first_ledger"]:
        if history:
            raise LedgerError("a first ledger is refused: the trusted history already holds one (no re-bootstrap)")
        if current["seq"] != 1 or current["events"] or inventory_path is not None:
            raise LedgerError("a first ledger has sequence 1, no events and no database inventory")
        previous = None
    else:
        if not history or history[-1]["digest"] != decision["previous_ledger_digest"]:
            raise LedgerError("the previous ledger is not the trusted history's last one")
        previous = history[-1]
        pool_ledger.verify_step(previous, current)
        if inventory_path is None:
            raise LedgerError("the authoritative database inventory is missing")

    # the free releases in this ledger are exactly those the decision approved, with the references it names
    freed = [e for e in current["events"] if e["to"] == "free"]
    rows = {r["question_id"]: r for r in current["questions"]}
    approved = {(i["question_id"], i["content_sha256"], i["clone_audit_ref"], i["license_ok_ref"], i["exposure_audit_ref"]) for i in decision["released"]}
    made = {(e["question_id"], rows[e["question_id"]]["content_sha256"], e["clone_audit_ref"], e["license_ok_ref"], e["exposure_audit_ref"]) for e in freed}
    if approved != made or len(approved) != len(decision["released"]) or len(made) != len(freed):
        raise LedgerError("the free releases are not exactly the ones the decision approved")

    # reconcile against every open version the decision lists, through the candidate's own validated catalogs
    catalogs, digests = _catalogs(catalog_paths)
    listed = {(v["family"], v["catalog_version"], v["catalog_digest"]) for v in decision["open_versions"]}
    if len(digests) != len(set(digests)) or set(digests) != listed:
        raise LedgerError("the supplied catalogs are not exactly the open versions the decision lists")
    wanted: dict = {}
    for catalog in catalogs:
        for pq in catalog.questions.values():
            if wanted.setdefault(pq.question_id, (catalog.family, pq.content_sha256)) != (catalog.family, pq.content_sha256):
                raise LedgerError("a question appears in two open versions with different content")
    ranked = {qid: (r["family"], r["content_sha256"]) for qid, r in rows.items() if r["state"] == "ranked"}
    if ranked != wanted:
        raise LedgerError("the ranked questions are not exactly the questions of the open versions")
    old = {r["question_id"] for r in previous["questions"]} if previous else set()
    for qid in set(rows) - old:
        if rows[qid]["state"] != "ranked" or qid not in wanted:
            raise LedgerError("a new question is not a ranked addition from a listed catalog")

    # the live database must agree with the ledger, and may lack only the candidate's brand-new questions
    if inventory_path is not None:
        inventory = pool_ledger.load_inventory(inventory_path)
        stored = {r["question_id"]: r for r in inventory["questions"]}
        if not old <= set(stored):
            raise LedgerError("the database lacks a question the trusted history registered")
        for qid, row in stored.items():
            if rows.get(qid) != row:
                raise LedgerError("the database and the ledger disagree about a registered question")
        live = {tuple(sorted(e.items())) for e in inventory["events"]}
        if any(tuple(sorted(e.items())) not in live for e in current["events"]):
            raise LedgerError("a ledger event is not in the database's lifecycle events")

    # the scanned bytes must BE the pinned public tree; the commit alone proves nothing
    try:
        tree = pool_artifact.read_tree(public_root)
    except pool_artifact.ArtifactError as exc:
        raise LedgerError(f"the public tree cannot be bound: {exc}") from None
    if tree["tree_id"] != decision["public_tree"] or tree["artifact_sha256"] != decision["artifact_sha256"]:
        raise LedgerError("the scanned files are not the tree the decision pinned")
    if git_repo is not None and _git_tree(git_repo, candidate) != decision["public_tree"]:
        raise LedgerError("the candidate commit's public tree is not the one the decision pinned")

    protected = [(r["question_id"], r["content_sha256"]) for r in current["questions"] if r["state"] in ("ranked", "retired")]
    if not protected:
        raise LedgerError("the protected pool is empty: nothing to check against, which is never a pass")
    findings = pool_artifact.scan(tree["files"], protected)
    receipt = {"release_id": release_id, "seq": current["seq"], "ledger": current["digest"], "tree": tree["tree_id"],
               "artifact": tree["artifact_sha256"], "counts": current["counts"], "protected": len(protected), "files": len(tree["files"])}
    return {"receipt": receipt, "findings": findings}
