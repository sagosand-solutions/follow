# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoring phase 1, step 4: immutable ranking versions and the exact (float-free) score arithmetic."""
import hashlib
import random
import unittest
from fractions import Fraction
from math import lcm

from server.models import ContentError
from server.scoring_catalog import parse_catalog
from server.scoring_repo import ScoringRepository, new_id
from server.scoring_service import course_points, iso_week, total_points
from server.scoring_versions import (ensure_version, key_width, points, score_key, snapshot_digest, total_numerator,
                                     total_parameters, window_key)
from server.tests.scoring_support import build_scoring_fixture


def catalog_of(sizes: dict, *, version="v1", family="free"):
    courses = [{"course_id": c, "N_c": n, "questions": [{"question_id": f"{c}-q{i}", "product_id": None if family == "free" else "p",
                                                          "content_sha256": hashlib.sha256(f"{c}/{i}".encode()).hexdigest()} for i in range(n)]}
               for c, n in sizes.items()]
    exposures = [{"kind": "question", "id": q["question_id"], "content_sha256": q["content_sha256"], "affected_question_ids": [q["question_id"]]}
                 for c in courses for q in c["questions"]]
    return parse_catalog({"schema_version": 1, "catalog_version": version, "rules_version": "r2", "family": family,
                          "courses": courses, "exposures": exposures}, expected_family=family)


class ArithmeticTests(unittest.TestCase):
    def test_parameters_for_unequal_pools(self):
        catalog = catalog_of({"a": 10, "b": 4, "c": 4})
        self.assertEqual(total_parameters(catalog), (3, 20, 60))
        self.assertEqual(total_numerator({"a": 5, "b": 2}, catalog), 5 * 2 + 2 * 5, "units * L / N_c, an untouched course counts 0")

    def test_units_for_a_course_outside_the_catalog_are_ignored(self):
        catalog = catalog_of({"a": 4})
        self.assertEqual(total_numerator({"a": 2, "gone": 99}, catalog), 2)

    def test_the_total_equals_the_fraction_arithmetic_for_random_attempts(self):
        rng = random.Random(7)
        for _ in range(200):
            sizes = {f"c{i}": rng.choice([1, 2, 3, 4, 7, 9, 10, 12, 16, 25]) for i in range(rng.randint(1, 5))}
            catalog = catalog_of(sizes)
            units = {c: rng.randint(-n, 2 * n) for c, n in sizes.items() if rng.random() < 0.8}
            _, _, d = total_parameters(catalog)
            self.assertEqual(points(total_numerator(units, catalog), d), total_points(units, catalog))
            for c, n in sizes.items():
                self.assertEqual(points(units.get(c, 0), n), course_points(units.get(c, 0), n))

    def test_text_order_of_the_keys_is_the_numeric_order_of_the_scores_exactly(self):
        rng = random.Random(11)
        for denominator in (1, 3, 4, 34, 40, 60, 334, 997, 10 ** 12 + 39):      # 4, 34, 40, 334: 3D has one digit more than 2D
            width = key_width(denominator)
            values = sorted({rng.randint(-denominator, 2 * denominator) for _ in range(80)} | {-denominator, 0, 2 * denominator})
            keys = [score_key(v, denominator) for v in values]
            self.assertEqual(keys, sorted(keys), f"D={denominator}")
            self.assertEqual({len(k) for k in keys}, {width})
            self.assertTrue(all(k.isascii() and k.isdigit() for k in keys))

    def test_equal_rationals_from_different_pool_mixes_get_equal_keys_and_neighbours_do_not(self):
        a, b = catalog_of({"x": 3, "y": 6}), catalog_of({"x": 3, "y": 6}, version="v2")
        _, _, d = total_parameters(a)
        one = total_numerator({"x": 2, "y": 0}, a)           # 2/3 on x
        two = total_numerator({"x": 0, "y": 4}, b)           # 4/6 on y: the same course score
        self.assertEqual((one, d), (two, d))
        self.assertEqual(score_key(one, d), score_key(two, d))
        self.assertNotEqual(score_key(one, d), score_key(one + 1, d))

    def test_a_huge_denominator_stays_exact_and_ordered(self):
        primes = [10007, 10009, 10037, 10039, 10061, 10067]
        catalog = catalog_of({f"c{i}": 2 for i in range(2)})
        catalog = type(catalog)(catalog.family, catalog.catalog_version, catalog.rules_version, tuple(f"p{p}" for p in primes),
                                {f"p{p}": p for p in primes}, {}, {})
        k, pool_lcm, d = total_parameters(catalog)
        self.assertEqual((k, pool_lcm, d), (6, lcm(*primes), 6 * lcm(*primes)))
        self.assertGreater(d, 2 ** 64, "beyond any 64-bit integer")
        top = {f"p{p}": 2 * p for p in primes}
        bottom = {f"p{p}": -p for p in primes}
        t_top, t_bottom = total_numerator(top, catalog), total_numerator(bottom, catalog)
        self.assertEqual((t_top, t_bottom), (2 * d, -d))
        self.assertEqual(points(t_top, d), 1000)
        self.assertEqual(points(t_bottom, d), -500)
        near = total_numerator({**top, "p10007": 2 * 10007 - 1}, catalog)
        keys = [score_key(v, d) for v in (t_bottom, 0, near, t_top)]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), 4)

    def test_out_of_range_numerators_and_bad_denominators_are_refused(self):
        for numerator, denominator in ((-61, 60), (121, 60), (0, 0), (0, -5)):
            with self.assertRaises(ValueError):
                score_key(numerator, denominator)
        self.assertEqual(score_key(-60, 60), "000")
        self.assertEqual(score_key(120, 60), "180")

    def test_windows(self):
        start, _ = iso_week(1_700_000_000.0)
        self.assertEqual(window_key("all"), "all")
        self.assertEqual(window_key("week", start), str(int(start)))
        for bad in (("week", None), ("week", 1.5), ("day", 1.0), ("", None)):
            with self.assertRaises(ValueError):
                window_key(*bad)

    def test_a_catalog_without_courses_or_with_an_empty_pool_has_no_version(self):
        empty = catalog_of({"a": 2})
        none = type(empty)(empty.family, empty.catalog_version, empty.rules_version, (), {}, {}, {})
        zero = type(empty)(empty.family, empty.catalog_version, empty.rules_version, ("a",), {"a": 0}, {}, {})
        for catalog in (none, zero):
            with self.assertRaises(ContentError):
                total_parameters(catalog)


class SnapshotTests(unittest.TestCase):
    def test_the_digest_is_stable_and_changes_with_anything_that_matters(self):
        base = snapshot_digest("free", catalog_of({"a": 2, "b": 3}))
        self.assertEqual(base, snapshot_digest("free", catalog_of({"b": 3, "a": 2})), "order does not matter")
        self.assertNotEqual(base, snapshot_digest("official", catalog_of({"a": 2, "b": 3})))
        self.assertNotEqual(base, snapshot_digest("free", catalog_of({"a": 2, "b": 4})))
        self.assertNotEqual(base, snapshot_digest("free", catalog_of({"a": 2, "b": 3}, version="v2")))
        changed = catalog_of({"a": 2, "b": 3})
        q = next(iter(changed.questions.values()))
        changed.questions[q.question_id] = type(q)(q.question_id, q.course_id, q.product_id, "e" * 64)
        self.assertNotEqual(base, snapshot_digest("free", changed))


class EnsureVersionTests(unittest.TestCase):
    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=("u1",))
        self.addCleanup(tmp.cleanup)
        self.db = self.fx.db()
        self.addCleanup(self.db.close)
        self.conn = self.db.connect()

    def rows(self, sql, args=()):
        return [tuple(r) for r in self.conn.execute(sql, args)]

    def test_start_up_created_one_open_version_per_family_with_the_whole_snapshot(self):
        for family, catalog in self.fx.catalogs.items():
            _, _, d = total_parameters(catalog)
            self.assertEqual(self.rows("SELECT status, courses_k, denominator_total, snapshot_digest FROM ranking_versions WHERE family = ?", (family,)),
                             [("open", catalog.course_count, str(d), snapshot_digest(family, catalog))])
            version = self.rows("SELECT id FROM ranking_versions WHERE family = ?", (family,))[0][0]
            self.assertEqual(self.rows("SELECT course_id, pool_size FROM ranking_version_courses WHERE version_id = ? ORDER BY course_id", (version,)),
                             [(c, catalog.pool_sizes[c]) for c in catalog.course_ids])
            self.assertEqual(self.rows("SELECT COUNT(*) FROM ranking_version_members WHERE version_id = ?", (version,)), [(len(catalog.questions),)])

    def test_ensure_is_idempotent_and_returns_the_same_version(self):
        catalog = self.fx.catalogs["free"]
        first = ensure_version(self.conn, "free", catalog, 5.0, new_id)
        again = ensure_version(self.conn, "free", catalog, 6.0, new_id)
        self.assertEqual(first, again)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM ranking_versions WHERE family = 'free'"), [(1,)])

    def test_a_catalog_that_changed_under_the_same_version_is_refused(self):
        catalog = self.fx.catalogs["free"]
        q = next(iter(catalog.questions.values()))
        altered = parse_catalog({"schema_version": 1, "catalog_version": catalog.catalog_version, "rules_version": "r2", "family": "free",
                                 "courses": [{"course_id": c, "N_c": catalog.pool_sizes[c], "questions": [
                                     {"question_id": pq.question_id, "product_id": None,
                                      "content_sha256": ("f" * 64 if pq.question_id == q.question_id else pq.content_sha256)}
                                     for pq in catalog.questions.values() if pq.course_id == c]} for c in catalog.course_ids],
                                 "exposures": [{"kind": "question", "id": pq.question_id, "affected_question_ids": [pq.question_id],
                                                "content_sha256": ("f" * 64 if pq.question_id == q.question_id else pq.content_sha256)}
                                               for pq in catalog.questions.values()]}, expected_family="free")
        with self.assertRaises(ContentError) as caught:
            ensure_version(self.conn, "free", altered, 5.0, new_id)
        self.assertNotIn("f" * 64, str(caught.exception))

    def test_a_new_catalog_with_a_retired_question_cannot_get_a_version(self):
        successor = catalog_of({"c": 2}, version="next")
        victim = next(iter(successor.questions.values()))
        db = self.db
        with db.transaction(immediate=True):
            ScoringRepository(db).sync_pool([(victim.question_id, "free", victim.content_sha256)], 1.0, "next")
        self.conn.execute("DROP TRIGGER pool_questions_monotone")
        self.conn.execute("UPDATE pool_questions SET state = 'retired' WHERE question_id = ?", (victim.question_id,))
        with self.assertRaises(ContentError):
            ensure_version(self.conn, "free", successor, 5.0, new_id)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM ranking_versions WHERE catalog_version = 'next'"), [(0,)])


if __name__ == "__main__":
    unittest.main()
