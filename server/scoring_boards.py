# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read side of the boards: one bounded query per page, run inside one read snapshot.

The published population for a (version, scope, window) is filtered BEFORE ranking and paging: an active account, an opt-in,
and a moderation state of 'ok'. ``RANK()`` orders by the exact text key alone, so equal scores share a rank (1, 1, 3);
``ROW_NUMBER()`` adds a stable secondary order (fewer answers, earlier last answer, then the internal id, which never leaves
the server) for paging. A page is one statement, so its cost does not grow with the number of principals returned.
"""
from __future__ import annotations

PUBLISHED = """
    pub AS (
      SELECT st.principal_id, st.numerator, st.score_key, st.answered, st.last_accepted_at, pr.alias
      FROM standings st
      JOIN scoring_principals sp ON sp.id = st.principal_id AND sp.user_id IS NOT NULL AND sp.state = 'active'
      JOIN users u ON u.id = sp.user_id AND u.status = 'active'
      JOIN leaderboard_preferences pr ON pr.principal_id = st.principal_id AND pr.family = :family
           AND pr.opted_in_at IS NOT NULL AND pr.moderation_state = 'ok'
      WHERE st.version_id = :version AND st.scope = :scope AND st.window_key = :window),
    ranked AS (
      SELECT principal_id, numerator, score_key, answered, alias,
             RANK() OVER (ORDER BY score_key DESC) AS rnk,
             ROW_NUMBER() OVER (ORDER BY score_key DESC, answered ASC, last_accepted_at ASC, principal_id ASC) AS pos,
             COUNT(*) OVER () AS participants
      FROM pub)
"""

# ONE statement per page, empty page included: the population size comes from the same statement as the rows. A LEFT JOIN from the
# one-row "totals" to the page rows yields exactly one row of NULLs when the page is empty (a quiet board, or a cursor past the end).
PAGE_SQL = (f"WITH {PUBLISHED.replace('pub AS (', 'pub AS MATERIALIZED (', 1)}, "
            "totals AS (SELECT COUNT(*) AS participants FROM pub) "
            "SELECT t.participants AS participants, r.principal_id, r.numerator, r.score_key, r.answered, r.alias, r.rnk, r.pos "
            "FROM totals t LEFT JOIN (SELECT principal_id, numerator, score_key, answered, alias, rnk, pos FROM ranked "
            "WHERE pos > :after ORDER BY pos LIMIT :limit) r ON 1 = 1 ORDER BY r.pos")
OWN_SQL = f"WITH {PUBLISHED} SELECT rnk, pos, participants FROM ranked WHERE principal_id = :me"
HYPOTHETICAL_SQL = f"WITH {PUBLISHED} SELECT COUNT(*), COALESCE(SUM(score_key > :key), 0) FROM pub"


def current_version(conn, family: str, catalog_version: str, rules_version: str):
    return conn.execute("SELECT id, status FROM ranking_versions WHERE family = ? AND catalog_version = ? AND rules_version = ?",
                        (family, catalog_version, rules_version)).fetchone()


def board_revision(conn, version_id: str, window: str) -> int:
    row = conn.execute("SELECT revision FROM board_revisions WHERE version_id = ? AND window_key = ?", (version_id, window)).fetchone()
    return row[0] if row else 0


def page(conn, family: str, version_id: str, scope: str, window: str, after: int, limit: int):
    """(rows, participants) from ONE statement, also for an empty page (a quiet board, or a cursor past the end)."""
    args = {"family": family, "version": version_id, "scope": scope, "window": window, "after": after, "limit": limit}
    rows = conn.execute(PAGE_SQL, args).fetchall()
    participants = rows[0]["participants"]                  # the totals row is always present, even when no page row follows
    return [r for r in rows if r["principal_id"] is not None], participants


def own_position(conn, family: str, version_id: str, scope: str, window: str, principal_id: str):
    return conn.execute(OWN_SQL, {"family": family, "version": version_id, "scope": scope, "window": window, "me": principal_id}).fetchone()


def hypothetical(conn, family: str, version_id: str, scope: str, window: str, key: str) -> tuple[int, int]:
    """(rank this key would take among the published, number of published). Never inserts the caller."""
    count, above = conn.execute(HYPOTHETICAL_SQL, {"family": family, "version": version_id, "scope": scope, "window": window,
                                                    "key": key}).fetchone()
    return 1 + above, count
