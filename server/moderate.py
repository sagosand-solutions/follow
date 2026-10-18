# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator tool for hiding a published name: ``python -m server.moderate hide|unhide|find``.

Operator access only (database file access); there is no public admin API. The target is the stable internal principal and the
family, never the alias text, so renaming cannot lift a hide and opting in again cannot either. ``hide`` and ``unhide`` are a
DRY RUN unless ``--write`` is given; a real change appends one audit row (time, operator, principal, family, action, reason
code: no e-mail, address, session or payload) and moves that family's boards to a new revision in the same transaction. The
private history is never touched.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
import time

from .db import Database
from .scoring_standings import bump_principal_boards

FAMILIES = ("official", "free")
REASONS = frozenset({"offensive-alias", "impersonation", "privacy", "spam", "abuse", "error-correction"})
_PRINCIPAL_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_OPERATOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")
FIND_LIMIT = 10


class ModerationError(Exception):
    """A refused or invalid request. The message names the problem, never personal data."""


def _validate(principal_id, family, reason=None, operator=None):
    if not isinstance(principal_id, str) or not _PRINCIPAL_RE.fullmatch(principal_id):
        raise ModerationError("invalid principal id")
    if family not in FAMILIES:
        raise ModerationError("invalid family")
    if reason is not None and reason not in REASONS:
        raise ModerationError("reason must be one of: " + ", ".join(sorted(REASONS)))
    if operator is not None and not (isinstance(operator, str) and _OPERATOR_RE.fullmatch(operator)):
        raise ModerationError("invalid operator id")


def set_hidden(db: Database, principal_id: str, family: str, *, hidden: bool, reason: str, operator: str, now: float, write: bool) -> str:
    """Hide or unhide one (principal, family). Returns a one-line result; changes nothing unless ``write``."""
    _validate(principal_id, family, reason, operator)
    action, state = ("hide", "hidden") if hidden else ("unhide", "ok")
    with db.transaction(immediate=True) as conn:
        principal = conn.execute("SELECT user_id FROM scoring_principals WHERE id = ?", (principal_id,)).fetchone()
        if principal is None:
            raise ModerationError("unknown principal")
        if principal["user_id"] is None:
            raise ModerationError("only account principals appear on lists")
        pref = conn.execute("SELECT moderation_state FROM leaderboard_preferences WHERE principal_id = ? AND family = ?",
                            (principal_id, family)).fetchone()
        current = pref["moderation_state"] if pref else "ok"
        if current == state:
            return f"{action}: already {state}; nothing to do"
        if not write:
            return f"{action}: dry run, would change {family} from {current} to {state}; add --write to apply"
        if pref is None:                                       # hide in advance: a later opt-in stays hidden
            conn.execute("INSERT INTO leaderboard_preferences (principal_id, family, opted_in_at, alias, nickname, moderation_state) "
                         "VALUES (?, ?, NULL, ?, NULL, 'hidden')", (principal_id, family, "Reader-" + secrets.token_hex(3)))
        else:
            conn.execute("UPDATE leaderboard_preferences SET moderation_state = ? WHERE principal_id = ? AND family = ?",
                         (state, principal_id, family))
        conn.execute("INSERT INTO scoring_moderation_audit (at, operator, principal_id, family, action, reason_code, dry_run) "
                     "VALUES (?, ?, ?, ?, ?, ?, 0)", (now, operator, principal_id, family, action, reason))
        bump_principal_boards(conn, principal_id, family)
    return f"{action}: {family} is now {state}"


def find(db: Database, family: str, alias: str) -> list:
    """Read-only: (principal id, published, moderation state) for an exact alias, case-insensitive. Operator use only."""
    if family not in FAMILIES or not isinstance(alias, str) or not 1 <= len(alias) <= 40:
        raise ModerationError("invalid family or alias")
    rows = db.connect().execute(
        "SELECT principal_id, opted_in_at IS NOT NULL AS published, moderation_state FROM leaderboard_preferences "
        "WHERE family = ? AND alias = ? COLLATE NOCASE ORDER BY principal_id LIMIT ?", (family, alias, FIND_LIMIT)).fetchall()
    return [(r["principal_id"], bool(r["published"]), r["moderation_state"]) for r in rows]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m server.moderate", description="Scoring list moderation (operator only).")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", ""), help="SQLite path (default: $DB_PATH)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("hide", "unhide"):
        p = sub.add_parser(name, help=f"{name} a name on one family's lists (dry run unless --write)")
        p.add_argument("--principal", required=True)
        p.add_argument("--family", required=True)
        p.add_argument("--reason", required=True)
        p.add_argument("--operator", required=True)
        mode = p.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="the default: report, change nothing")
        mode.add_argument("--write", action="store_true", help="apply the change and append the audit row")
    f = sub.add_parser("find", help="find principal ids by exact alias (read-only)")
    f.add_argument("--family", required=True)
    f.add_argument("--alias", required=True)
    return parser


def main(argv=None, *, clock=time.time) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.db or not os.path.exists(args.db):
            raise ModerationError("database file not found")
        db = Database(args.db, clock=time.time)
        try:
            if args.command == "find":
                found = find(db, args.family, args.alias)
                for principal_id, published, state in found:
                    print(f"{principal_id}\tpublished={'yes' if published else 'no'}\tmoderation={state}")
                if not found:
                    print("no match")
            else:
                print(set_hidden(db, args.principal, args.family, hidden=args.command == "hide", reason=args.reason,
                                 operator=args.operator, now=clock(), write=args.write))
        finally:
            db.close()
    except ModerationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
