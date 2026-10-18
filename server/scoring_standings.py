# SPDX-License-Identifier: AGPL-3.0-or-later
"""Materialised, exact standings, kept in step with accepted attempts inside the answer transaction.

Per ranking version and window ('all' or an ISO week) an account's rankable attempts are summed per course
(``course_units``); ``standings`` holds the exact numerator and sort key for ``total`` and ``course:<id>``. Anonymous
attempts only feed a private aggregate (``anonymous_units``) used for a hypothetical placement, never a board. The same
function writes standings for the incremental path and for a rebuild, so the two cannot drift. A board revision counts
every change a reader could see, so older cursors go stale.
"""
from __future__ import annotations

from dataclasses import dataclass

from .scoring_versions import iso_week, score_key, total_numerator, total_parameters, window_key


@dataclass(frozen=True)
class VersionShape:
    """What the arithmetic needs from a version: its courses and pool sizes (same attribute names as a catalog)."""
    course_ids: tuple
    pool_sizes: dict


def load_shape(conn, version_id: str) -> VersionShape:
    rows = conn.execute("SELECT course_id, pool_size FROM ranking_version_courses WHERE version_id = ? ORDER BY course_id", (version_id,)).fetchall()
    return VersionShape(tuple(r[0] for r in rows), {r[0]: r[1] for r in rows})


def week_windows(accepted_at: float) -> list:
    start, _ = iso_week(accepted_at)
    return [window_key("all"), window_key("week", start)]


def bump_board(conn, version_id: str, window: str) -> None:
    conn.execute("INSERT OR IGNORE INTO board_revisions (version_id, window_key, revision) VALUES (?, ?, 0)", (version_id, window))
    conn.execute("UPDATE board_revisions SET revision = revision + 1 WHERE version_id = ? AND window_key = ?", (version_id, window))


def bump_principal_boards(conn, principal_id: str) -> None:
    """Everywhere this principal has a standing: a publication, alias or removal change moves those boards."""
    for version_id, window in conn.execute("SELECT DISTINCT version_id, window_key FROM standings WHERE principal_id = ?", (principal_id,)).fetchall():
        bump_board(conn, version_id, window)


def _write_standings(conn, shape: VersionShape, version_id: str, window: str, principal_id: str, only_course: str | None = None) -> None:
    rows = conn.execute("SELECT course_id, units, answered, last_accepted_at FROM course_units "
                        "WHERE version_id = ? AND window_key = ? AND principal_id = ?", (version_id, window, principal_id)).fetchall()
    if not rows:
        return
    _, _, d = total_parameters(shape)
    units = {r["course_id"]: r["units"] for r in rows}
    entries = [("total", total_numerator(units, shape), d, sum(r["answered"] for r in rows), max(r["last_accepted_at"] for r in rows))]
    for r in rows:
        if only_course is None or r["course_id"] == only_course:
            entries.append((f"course:{r['course_id']}", r["units"], shape.pool_sizes[r["course_id"]], r["answered"], r["last_accepted_at"]))
    for scope, numerator, denominator, answered, last in entries:
        conn.execute(
            "INSERT INTO standings (version_id, scope, window_key, principal_id, numerator, score_key, answered, last_accepted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (version_id, scope, window_key, principal_id) DO UPDATE SET "
            "numerator = excluded.numerator, score_key = excluded.score_key, answered = excluded.answered, "
            "last_accepted_at = excluded.last_accepted_at",
            (version_id, scope, window, principal_id, str(numerator), score_key(numerator, denominator), answered, last))


class StandingsWriter:
    def __init__(self):
        self._shapes: dict = {}

    def _shape(self, conn, version_id: str) -> VersionShape:
        shape = self._shapes.get(version_id)
        if shape is None:                       # a version's snapshot is immutable, so caching it is safe
            shape = self._shapes[version_id] = load_shape(conn, version_id)
        return shape

    def apply(self, conn, attempt, principal, published: bool) -> None:
        """Called once per accepted first attempt, inside the answer transaction (a replay never calls it)."""
        version = conn.execute(
            "SELECT v.id FROM ranking_versions v JOIN ranking_version_members m ON m.version_id = v.id "
            "WHERE v.family = ? AND v.catalog_version = ? AND v.rules_version = ? AND v.status = 'open' AND m.question_id = ?",
            (attempt.family, attempt.catalog_version, attempt.rules_version, attempt.question_id)).fetchone()
        if version is None:
            return
        version_id = version[0]
        account = principal.user_id is not None
        if account and attempt.origin == "account":
            table = "course_units"
        elif not account and attempt.origin == "anonymous":
            table = "anonymous_units"
        else:
            return                                           # exposed / legacy / claimed attempts never score
        windows = week_windows(attempt.accepted_at)
        for window in windows:
            conn.execute(
                f"INSERT INTO {table} (version_id, window_key, principal_id, course_id, units, answered, last_accepted_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT (version_id, window_key, principal_id, course_id) DO UPDATE SET "
                "units = units + excluded.units, answered = answered + 1, last_accepted_at = MAX(last_accepted_at, excluded.last_accepted_at)",
                (version_id, window, principal.id, attempt.course_id, attempt.score_units, attempt.accepted_at))
            if account:
                _write_standings(conn, self._shape(conn, version_id), version_id, window, principal.id, only_course=attempt.course_id)
                if published:
                    bump_board(conn, version_id, window)


def rebuild(conn, version_id: str) -> dict:
    """Recompute every aggregate of an OPEN version from first attempts alone; bit-identical to the incremental result."""
    version = conn.execute("SELECT family, catalog_version, rules_version, status FROM ranking_versions WHERE id = ?", (version_id,)).fetchone()
    if version is None:
        raise ValueError("unknown version")
    if version["status"] != "open":
        raise ValueError("a frozen version is history and is never recomputed")
    shape = load_shape(conn, version_id)
    for table in ("standings", "course_units", "anonymous_units"):
        conn.execute(f"DELETE FROM {table} WHERE version_id = ?", (version_id,))
    attempts = conn.execute(
        "SELECT a.principal_id, a.course_id, a.score_units, a.accepted_at, a.origin, sp.user_id IS NOT NULL AS account "
        "FROM first_attempts a JOIN scoring_principals sp ON sp.id = a.principal_id "
        "JOIN ranking_version_members m ON m.version_id = ? AND m.question_id = a.question_id "
        "WHERE a.family = ? AND a.catalog_version = ? AND a.rules_version = ? "
        "AND ((sp.user_id IS NOT NULL AND a.origin = 'account') OR (sp.user_id IS NULL AND a.origin = 'anonymous')) "
        "ORDER BY a.accepted_at, a.principal_id, a.question_id",
        (version_id, version["family"], version["catalog_version"], version["rules_version"])).fetchall()
    sums: dict = {}
    for a in attempts:
        for window in week_windows(a["accepted_at"]):
            cell = sums.setdefault((bool(a["account"]), window, a["principal_id"], a["course_id"]), [0, 0, 0.0])
            cell[0] += a["score_units"]
            cell[1] += 1
            cell[2] = max(cell[2], a["accepted_at"])
    people = set()
    for (account, window, principal, course), (units, answered, last) in sorted(sums.items()):
        table = "course_units" if account else "anonymous_units"
        conn.execute(f"INSERT INTO {table} (version_id, window_key, principal_id, course_id, units, answered, last_accepted_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (version_id, window, principal, course, units, answered, last))
        if account:
            people.add((window, principal))
    for window, principal in sorted(people):
        _write_standings(conn, shape, version_id, window, principal)
    windows = {w for _, w, _, _ in sums} | {w for w, _ in people}
    for window in sorted(windows | {r[0] for r in conn.execute("SELECT window_key FROM board_revisions WHERE version_id = ?", (version_id,))}):
        bump_board(conn, version_id, window)
    return {"attempts": len(attempts), "principals": len(people), "windows": len(windows)}
