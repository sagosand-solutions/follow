# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator tool for the scoring question pool: ``python -m server.pool freeze|retire|free|rebuild|check-static|export-underlay``.

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


def rebuild_standings(db: Database, version_id: str) -> dict:
    """Recompute an open version's course units, standings and anonymous aggregate from first attempts; boards move to a new revision."""
    from .scoring_standings import rebuild
    with db.transaction(immediate=True) as conn:
        try:
            return rebuild(conn, version_id)
        except ValueError as exc:
            raise PoolError(str(exc)) from None


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


def protected_from_db(db: Database) -> list:
    """(question_id, content_sha256) of every ranked or retired question in the live database."""
    return [(r["question_id"], r["content_sha256"]) for r in db.connect().execute(
        "SELECT question_id, content_sha256 FROM pool_questions WHERE state IN ('ranked', 'retired') ORDER BY question_id")]


def check_static(db: Database | None, public_root: str, protected: list | None = None) -> list:
    """Findings (path + id + how) for every ranked or retired question whose id or content hash appears under ``public_root``.
    Even a question an operator has played is a finding: static material is world-readable. An id followed by a dot (a file
    extension) counts as a mention, and a symlink or a file over MAX_SCAN_BYTES is itself a finding (never followed or read), so
    the check errs toward a false alarm rather than a missed leak. The protected list comes from the database, or from a
    validated underlay passed as ``protected``; an EMPTY list is an error, never a clean pass."""
    root = Path(public_root)
    if not root.is_dir():
        raise PoolError("static root is not a directory")
    if protected is None:
        protected = protected_from_db(db)
    if not protected:
        raise PoolError("the protected pool is empty: nothing to check against, which is never a pass")
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
    r = sub.add_parser("rebuild", help="recompute the standings of an open ranking version from first attempts")
    r.add_argument("--version", required=True)
    c = sub.add_parser("check-static", help="fail if a ranked or retired question appears in static files")
    c.add_argument("--public-root", required=True)
    c.add_argument("--underlay", help="check against an exported underlay instead of the database")
    binding = c.add_mutually_exclusive_group()
    binding.add_argument("--expect-digest", help="with --underlay: the digest from the release decision (required for a release check)")
    binding.add_argument("--unbound", action="store_true", help="with --underlay: integrity only, NOT a release check (self-tests)")
    xi = sub.add_parser("export-inventory", help="write the live pool (ids, hashes, states, lifecycle events) from a READ-ONLY database open")
    xi.add_argument("--out", required=True)
    bl = sub.add_parser("build-ledger", help="compose the next pool ledger (check-release re-verifies it independently)")
    bl.add_argument("--release-id", required=True)
    bl.add_argument("--out", required=True)
    bl.add_argument("--previous", help="the previous ledger (omit for a first ledger)")
    bl.add_argument("--inventory", help="the database inventory (required with --previous)")
    bl.add_argument("--catalog", action="append", default=[], required=True, help="an open version's scoring catalog (repeatable)")
    vl = sub.add_parser("verify-ledger", help="verify the trusted ledger history, and optionally that --ledger is its valid successor")
    vl.add_argument("--trusted-checkout", required=True)
    vl.add_argument("--ledger")
    td = sub.add_parser("tree-digest", help="print the git tree id and the artifact digest of a public tree")
    td.add_argument("--public-root", required=True)
    cr = sub.add_parser("check-release", help="the release gate: every binding must hold; there is no unbound mode")
    cr.add_argument("--trusted-checkout", required=True, help="a separate checkout of the trusted ref: pool-ledger/ and release-decisions/")
    cr.add_argument("--release-id", required=True)
    cr.add_argument("--ledger", required=True)
    cr.add_argument("--public-root", required=True)
    cr.add_argument("--candidate", required=True)
    cr.add_argument("--catalog", action="append", default=[], required=True)
    cr.add_argument("--inventory")
    cr.add_argument("--git-repo", help="a checkout of the candidate: its <commit>:public must be the decision's tree")
    e = sub.add_parser("export-underlay", help="write ids, hashes and states of the protected pool (no content)")
    e.add_argument("--out", required=True)
    e.add_argument("--catalog", action="append", default=[], help="a frozen scoring catalog artifact (repeatable); default: the database")
    return parser


def _static_command(args) -> int:
    from . import pool_underlay
    if args.underlay is None:
        if args.expect_digest or args.unbound:
            raise PoolError("--expect-digest and --unbound need --underlay")
        db = _database(args.db)
        try:
            findings = check_static(db, args.public_root)
        finally:
            db.close()
    else:
        if not (args.expect_digest or args.unbound):
            raise PoolError("an underlay check needs --expect-digest (a release check) or --unbound (integrity only)")
        try:
            protected, digest = pool_underlay.load(args.underlay)
        except pool_underlay.UnderlayError as exc:
            raise PoolError(str(exc)) from None
        if args.expect_digest and args.expect_digest != digest:
            raise PoolError("the underlay digest is not the expected release digest")
        if args.unbound:
            print("UNBOUND: integrity only, this is not a release check", file=sys.stderr)
        findings = check_static(None, args.public_root, protected)
    for rel, qid, how in findings:
        print(f"{rel}: {qid} ({how})", file=sys.stderr)
    if findings:
        print(f"{len(findings)} finding(s): protected questions must not appear in static files", file=sys.stderr)
        return 1
    print("no protected question appears in static files")
    return 0


def _ledger_commands(args) -> int:
    from . import pool_artifact, pool_ledger, pool_release
    from .models import ContentError
    from .scoring_catalog import load_catalog
    try:
        if args.command == "export-inventory":
            document = pool_release.export_inventory(args.db)
            pool_ledger.write_new(document, args.out)
            print(f"inventory written: {len(document['questions'])} question(s), {len(document['events'])} event(s), digest {document['digest']}")
        elif args.command == "build-ledger":
            if (args.previous is None) != (args.inventory is None):
                raise PoolError("--previous and --inventory go together (a first ledger has neither)")
            try:
                catalogs = [load_catalog(path) for path in args.catalog]
            except ContentError:
                raise PoolError("a scoring catalog could not be loaded or has an exposure gap") from None
            previous = pool_ledger.load_ledger(args.previous) if args.previous else None
            inventory = pool_ledger.load_inventory(args.inventory) if args.inventory else None
            document = pool_ledger.compose(args.release_id, previous, inventory, catalogs)
            pool_ledger.write_new(document, args.out)
            print(f"ledger written: seq {document['seq']}, digest {document['digest']}")
        elif args.command == "verify-ledger":
            history = pool_ledger.load_history(args.trusted_checkout)
            if args.ledger:
                candidate = pool_ledger.load_ledger(args.ledger)
                if not history:
                    raise PoolError("there is no trusted history to continue")
                pool_ledger.verify_step(history[-1], candidate)
            print(f"ledger history valid: {len(history)} ledger(s)" + (", candidate continues it" if args.ledger else ""))
        elif args.command == "tree-digest":
            tree = pool_artifact.read_tree(args.public_root)
            print(f"tree {tree['tree_id']}\nartifact {tree['artifact_sha256']}\nfiles {len(tree['files'])}")
        else:
            result = pool_release.check_release(trusted_dir=args.trusted_checkout, release_id=args.release_id, ledger_path=args.ledger,
                                                public_root=args.public_root, candidate=args.candidate, catalog_paths=args.catalog,
                                                inventory_path=args.inventory, git_repo=args.git_repo)
            for rel, qid, how in result["findings"]:
                print(f"{rel}: {qid} ({how})", file=sys.stderr)
            if result["findings"]:
                print(f"{len(result['findings'])} finding(s): protected questions must not appear in the release tree", file=sys.stderr)
                return 1
            r = result["receipt"]
            print(f"release gate OK: release {r['release_id']} seq {r['seq']} ledger {r['ledger']} tree {r['tree']} artifact {r['artifact']} "
                  f"protected {r['protected']} files {r['files']}")
    except (pool_ledger.LedgerError, pool_artifact.ArtifactError) as exc:
        raise PoolError(str(exc)) from None
    return 0


def _export_command(args) -> int:
    from . import pool_underlay
    try:
        if args.catalog:
            document = pool_underlay.from_catalogs(args.catalog)
        else:
            db = _database(args.db)
            try:
                document = pool_underlay.from_db(db)
            finally:
                db.close()
        pool_underlay.write(document, args.out)
    except pool_underlay.UnderlayError as exc:
        raise PoolError(str(exc)) from None
    print(f"underlay written: {document['count']} question(s), digest {document['digest']}")
    return 0


def main(argv=None, *, clock=time.time) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check-static":
            return _static_command(args)
        if args.command == "export-underlay":
            return _export_command(args)
        if args.command in ("export-inventory", "build-ledger", "verify-ledger", "tree-digest", "check-release"):
            return _ledger_commands(args)
        db = _database(args.db)
        try:
            now = clock()
            if args.command == "freeze":
                withdrawn = freeze(db, args.version, reason_code=args.reason, now=now)
                print(f"frozen {args.version}; {withdrawn} open issue(s) withdrawn")
            elif args.command == "rebuild":
                counts = rebuild_standings(db, args.version)
                print(f"rebuilt {args.version}: {counts['attempts']} attempt(s), {counts['principals']} ranked principal(s)")
            elif args.command == "retire":
                withdrawn = retire(db, args.question, release_id=args.release, reason_code=args.reason, operator_id=args.operator,
                                   now=now, clone_audit_ref=args.clone_audit)
                print(f"retired {args.question}; {withdrawn} open issue(s) withdrawn")
            elif args.command == "free":
                free(db, args.question, release_id=args.release, reason_code=args.reason, operator_id=args.operator, now=now,
                     clone_audit_ref=args.clone_audit, license_ok_ref=args.license_ok, exposure_audit_ref=args.exposure_audit)
                print(f"released {args.question}")
        finally:
            db.close()
    except PoolError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
