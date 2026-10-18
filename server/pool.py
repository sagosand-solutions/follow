# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator tool for the scoring question pool: ``python -m server.pool freeze|retire|free|check-static``.

One way only: a ranked question can be retired, a retired one can be released as free, nothing moves back. Every change is
one transaction with an append-only audit row; a version is frozen before any of its questions is retired. Operator and
reason are short codes, never names or e-mail addresses. Rolling the scoring service back is an operator runbook, not a
command here (server/README.md).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from pathlib import Path

from .db import Database

_CODE_RE = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_OPERATOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}\Z")
_RELEASE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_TEXT_SUFFIXES = {".json", ".js", ".mjs", ".html", ".css", ".txt", ".md", ".csv", ".xml", ".svg", ".webmanifest", ".map"}
MAX_SCAN_BYTES = 8 * 1024 * 1024


class PoolError(Exception):
    """A refused or invalid pool operation. The message names ids and reasons, never content."""


def _check(pattern, value, what):
    if not isinstance(value, str) or not pattern.match(value):
        raise PoolError(f"invalid {what}")
    return value


def freeze(db: Database, version_id: str, *, reason_code: str, now: float) -> int:
    """Open -> frozen for the whole version; every open issue of its (family, catalog) is withdrawn. Returns issues withdrawn."""
    _check(_CODE_RE, reason_code, "reason code")
    with db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT family, catalog_version, status FROM ranking_versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            raise PoolError("unknown version")
        if row["status"] != "open":
            raise PoolError("version is already frozen")
        conn.execute("UPDATE ranking_versions SET status = 'frozen', frozen_at = ?, freeze_reason = ? WHERE id = ?",
                     (now, reason_code, version_id))
        withdrawn = conn.execute("UPDATE question_issues SET consumed_at = ? WHERE consumed_at IS NULL AND family = ? AND catalog_version = ?",
                                 (now, row["family"], row["catalog_version"])).rowcount
        conn.execute("UPDATE board_revisions SET revision = revision + 1 WHERE version_id = ?", (version_id,))
    return withdrawn


def _open_versions_with(conn, question_id: str) -> list:
    return [r[0] for r in conn.execute(
        "SELECT v.id FROM ranking_versions v JOIN ranking_version_members m ON m.version_id = v.id "
        "WHERE m.question_id = ? AND v.status = 'open' ORDER BY v.id", (question_id,))]


def retire(db: Database, question_id: str, *, release_id: str, reason_code: str, operator_id: str, now: float,
           clone_audit_ref: str | None = None) -> int:
    """Ranked -> retired. Refused while an open version still contains the question. Returns open issues withdrawn."""
    _check(_RELEASE_RE, release_id, "release id")
    _check(_CODE_RE, reason_code, "reason code")
    _check(_OPERATOR_RE, operator_id, "operator id")
    if clone_audit_ref is not None:
        _check(_REF_RE, clone_audit_ref, "clone audit reference")
    with db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT state FROM pool_questions WHERE question_id = ?", (question_id,)).fetchone()
        if row is None:
            raise PoolError("unknown question")
        if row["state"] != "ranked":
            raise PoolError(f"question is {row['state']}, not ranked")
        blocking = _open_versions_with(conn, question_id)
        if blocking:
            raise PoolError("freeze every open version that contains the question first: " + ", ".join(blocking))
        conn.execute("UPDATE pool_questions SET state = 'retired', release_id = ?, since = ?, reason_code = ?, operator_id = ?, "
                     "clone_audit_ref = ? WHERE question_id = ?", (release_id, now, reason_code, operator_id, clone_audit_ref, question_id))
        conn.execute("INSERT INTO pool_lifecycle_events (question_id, from_state, to_state, release_id, effective_at, operator_id, "
                     "reason_code, clone_audit_ref) VALUES (?, 'ranked', 'retired', ?, ?, ?, ?, ?)",
                     (question_id, release_id, now, operator_id, reason_code, clone_audit_ref))
        return conn.execute("UPDATE question_issues SET consumed_at = ? WHERE question_id = ? AND consumed_at IS NULL",
                            (now, question_id)).rowcount


def free(db: Database, question_id: str, *, release_id: str, reason_code: str, operator_id: str, now: float,
         clone_audit_ref: str, license_ok_ref: str, exposure_audit_ref: str) -> None:
    """Retired -> free. Needs the clone audit, the separate licence decision and the exposure inventory, and no open version."""
    _check(_RELEASE_RE, release_id, "release id")
    _check(_CODE_RE, reason_code, "reason code")
    _check(_OPERATOR_RE, operator_id, "operator id")
    for value, what in ((clone_audit_ref, "clone audit reference"), (license_ok_ref, "licence reference"),
                        (exposure_audit_ref, "exposure audit reference")):
        _check(_REF_RE, value, what)
    with db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT state FROM pool_questions WHERE question_id = ?", (question_id,)).fetchone()
        if row is None:
            raise PoolError("unknown question")
        if row["state"] != "retired":
            raise PoolError(f"question is {row['state']}, not retired")
        blocking = _open_versions_with(conn, question_id)
        if blocking:
            raise PoolError("an open version still contains the question: " + ", ".join(blocking))
        conn.execute("UPDATE pool_questions SET state = 'free', release_id = ?, since = ?, reason_code = ?, operator_id = ?, "
                     "clone_audit_ref = ?, license_ok_ref = ?, exposure_audit_ref = ? WHERE question_id = ?",
                     (release_id, now, reason_code, operator_id, clone_audit_ref, license_ok_ref, exposure_audit_ref, question_id))
        conn.execute("INSERT INTO pool_lifecycle_events (question_id, from_state, to_state, release_id, effective_at, operator_id, "
                     "reason_code, clone_audit_ref) VALUES (?, 'retired', 'free', ?, ?, ?, ?, ?)",
                     (question_id, release_id, now, operator_id, reason_code, clone_audit_ref))


def check_static(db: Database, public_root: str) -> list:
    """Findings (path + id + how) for every ranked or retired question whose id or content hash appears under ``public_root``.
    Even a question an operator has played is a finding: static material is world-readable. An id followed by a dot (a file
    extension) counts as a mention, and a symlink or a file over MAX_SCAN_BYTES is itself a finding (never followed or read), so
    the check errs toward a false alarm rather than a missed leak."""
    root = Path(public_root)
    if not root.is_dir():
        raise PoolError("static root is not a directory")
    conn = db.connect()
    protected = [(r["question_id"], r["content_sha256"]) for r in conn.execute(
        "SELECT question_id, content_sha256 FROM pool_questions WHERE state IN ('ranked', 'retired') ORDER BY question_id")]
    patterns = {qid: re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(qid) + r"(?![A-Za-z0-9_-])") for qid, _ in protected}
    findings = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink()):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            findings.append((rel, "-", "symlink is not followed or scanned"))
            continue
        size = path.stat().st_size
        if size > MAX_SCAN_BYTES:
            findings.append((rel, "-", "file too large to scan"))
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        text = data.decode("utf-8", "replace") if path.suffix.lower() in _TEXT_SUFFIXES or size < 65536 else ""
        for qid, sha in protected:
            if digest == sha or sha in text:
                findings.append((rel, qid, "content hash"))
            elif patterns[qid].search(text) or patterns[qid].search(rel):
                findings.append((rel, qid, "id"))
    return findings


def _database(path: str) -> Database:
    if not path or not os.path.exists(path):
        raise PoolError("database file not found")
    return Database(path, clock=time.time)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m server.pool", description="Scoring question pool operator tool.")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", ""), help="SQLite path (default: $DB_PATH)")
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("freeze", help="freeze a ranking version")
    f.add_argument("--version", required=True)
    f.add_argument("--reason", required=True)
    for name in ("retire", "free"):
        p = sub.add_parser(name, help=f"{name} a pool question")
        p.add_argument("--question", required=True)
        p.add_argument("--release", required=True)
        p.add_argument("--reason", required=True)
        p.add_argument("--operator", required=True)
        p.add_argument("--clone-audit", required=(name == "free"))
        if name == "free":
            p.add_argument("--license-ok", required=True)
            p.add_argument("--exposure-audit", required=True)
    c = sub.add_parser("check-static", help="fail if a ranked or retired question appears in static files")
    c.add_argument("--public-root", required=True)
    return parser


def main(argv=None, *, clock=time.time) -> int:
    args = _parser().parse_args(argv)
    try:
        db = _database(args.db)
        try:
            now = clock()
            if args.command == "freeze":
                withdrawn = freeze(db, args.version, reason_code=args.reason, now=now)
                print(f"frozen {args.version}; {withdrawn} open issue(s) withdrawn")
            elif args.command == "retire":
                withdrawn = retire(db, args.question, release_id=args.release, reason_code=args.reason, operator_id=args.operator,
                                   now=now, clone_audit_ref=args.clone_audit)
                print(f"retired {args.question}; {withdrawn} open issue(s) withdrawn")
            elif args.command == "free":
                free(db, args.question, release_id=args.release, reason_code=args.reason, operator_id=args.operator, now=now,
                     clone_audit_ref=args.clone_audit, license_ok_ref=args.license_ok, exposure_audit_ref=args.exposure_audit)
                print(f"released {args.question}")
            else:
                findings = check_static(db, args.public_root)
                for rel, qid, how in findings:
                    print(f"{rel}: {qid} ({how})", file=sys.stderr)
                if findings:
                    print(f"{len(findings)} finding(s): protected questions must not appear in static files", file=sys.stderr)
                    return 1
                print("no protected question appears in static files")
        finally:
            db.close()
    except PoolError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
