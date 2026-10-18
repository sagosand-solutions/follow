# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ranking versions and the exact score arithmetic behind the boards (no floats anywhere).

A version is an immutable snapshot of one catalog: its courses, their pool sizes and every member question with its content
hash. A total score is the mean of the K course scores; with L = lcm of the pool sizes and D = K*L the total numerator
T = sum(units_c * L / N_c) is an integer in [-D, 2D] and the score is 500*T/D. A course score is 500*u/N_c. The sort key is
the zero-padded decimal of (numerator + denominator), so a plain text comparison orders scores exactly, however large D is.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from math import lcm

from .models import ContentError

WEEK_SECONDS = 7 * 86400


def iso_week(now: float) -> tuple[float, float]:
    """[start, end) of the UTC ISO week containing ``now`` (Monday 00:00 to the next Monday)."""
    moment = datetime.fromtimestamp(now, timezone.utc)
    monday = (moment - timedelta(days=moment.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = monday.timestamp()
    return start, start + WEEK_SECONDS


def total_parameters(catalog) -> tuple[int, int, int]:
    """(K, L, D) for a catalog: course count, lcm of the pool sizes, and the total denominator D = K * L."""
    sizes = [catalog.pool_sizes[c] for c in catalog.course_ids]
    k = len(sizes)
    if k == 0 or any(not isinstance(n, int) or n < 1 for n in sizes):
        raise ContentError("a ranking version needs courses with a positive pool size")
    pool_lcm = lcm(*sizes)
    return k, pool_lcm, k * pool_lcm


def total_numerator(units_by_course: dict, catalog) -> int:
    """T = sum(units_c * L / N_c) over ALL catalog courses (a course with no attempt counts 0)."""
    _, pool_lcm, _ = total_parameters(catalog)
    return sum(units_by_course.get(c, 0) * (pool_lcm // catalog.pool_sizes[c]) for c in catalog.course_ids)


def key_width(denominator: int) -> int:
    return len(str(3 * denominator))


def score_key(numerator: int, denominator: int) -> str:
    """Zero-padded decimal of numerator + denominator, in [0, 3*denominator]; equal rationals give equal keys."""
    shifted = numerator + denominator
    if denominator < 1 or not 0 <= shifted <= 3 * denominator:
        raise ValueError("score numerator outside [-D, 2D]")
    return str(shifted).zfill(key_width(denominator))


def points(numerator: int, denominator: int) -> Fraction:
    """500 * numerator / denominator, exactly. Floats are only ever made from this for display."""
    return Fraction(500 * numerator, denominator)


def window_key(window: str, week_start: float | None = None) -> str:
    """'all', or the UTC Monday 00:00 of the ISO week as a whole number of seconds."""
    if window == "all":
        return "all"
    if window == "week" and week_start is not None and week_start == int(week_start):
        return str(int(week_start))
    raise ValueError("unknown window")


def snapshot_digest(family: str, catalog) -> str:
    """Canonical digest of what a version contains: family, versions, courses with pool sizes, members with hashes."""
    body = {
        "family": family, "catalog_version": catalog.catalog_version, "rules_version": catalog.rules_version,
        "courses": [[c, catalog.pool_sizes[c]] for c in catalog.course_ids],
        "members": sorted([pq.course_id, pq.question_id, pq.content_sha256] for pq in catalog.questions.values()),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ensure_version(conn, family: str, catalog, now: float, new_id) -> str:
    """Create the open version for (family, catalog, rules) once; verify an existing one still matches the catalog exactly.

    Returns its id. Raises ContentError when the catalog changed under the same version, or contains a question that is not
    ranked any more (a retired question needs a new catalog version, never a silent smaller pool)."""
    digest = snapshot_digest(family, catalog)
    row = conn.execute("SELECT id, snapshot_digest FROM ranking_versions WHERE family = ? AND catalog_version = ? AND rules_version = ?",
                       (family, catalog.catalog_version, catalog.rules_version)).fetchone()
    if row is not None:
        if row["snapshot_digest"] != digest:
            raise ContentError(f"scoring catalog {family}/{catalog.catalog_version} changed since its ranking version was created")
        return row["id"]
    not_ranked = [pq.question_id for pq in catalog.questions.values()
                  if (conn.execute("SELECT state, content_sha256 FROM pool_questions WHERE question_id = ?",
                                   (pq.question_id,)).fetchone() or (None, None))[:] != ("ranked", pq.content_sha256)]
    if not_ranked:
        raise ContentError(f"scoring catalog {family}/{catalog.catalog_version} contains questions that are not ranked: "
                           + ", ".join(sorted(not_ranked)[:5]))
    k, _, d = total_parameters(catalog)
    version_id = new_id("rv")
    conn.execute("INSERT INTO ranking_versions (id, family, catalog_version, rules_version, snapshot_digest, courses_k, denominator_total, "
                 "created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')",
                 (version_id, family, catalog.catalog_version, catalog.rules_version, digest, k, str(d), now))
    for course in catalog.course_ids:
        conn.execute("INSERT INTO ranking_version_courses (version_id, course_id, pool_size) VALUES (?, ?, ?)",
                     (version_id, course, catalog.pool_sizes[course]))
    for pq in sorted(catalog.questions.values(), key=lambda q: q.question_id):
        conn.execute("INSERT INTO ranking_version_members (version_id, course_id, question_id, content_sha256) VALUES (?, ?, ?, ?)",
                     (version_id, pq.course_id, pq.question_id, pq.content_sha256))
    return version_id
