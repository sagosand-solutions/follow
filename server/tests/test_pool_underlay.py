# SPDX-License-Identifier: AGPL-3.0-or-later
"""The static exposure check without a database: the pool underlay, its fail-closed rules and the CI self-test.

SYNTHETIC DATA ONLY: every id, hash and file here is made up for the test; no real pool id, hash or byte belongs in a public
repository. This module is the public CI's proof that the check works and fails closed. The real release check runs in the
private release pipeline against an underlay exported from the authoritative pool and a digest from the release decision.
"""
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from server import pool, pool_underlay
from server.scoring_catalog_build import unexposed_manifest_entries
from server.tests.scoring_support import build_scoring_fixture

REPO = Path(__file__).resolve().parents[2]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def rows(*ids, state="ranked"):
    return [(qid, sha("synthetic:" + qid), state) for qid in ids]


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def path(self, name):
        return str(self.dir / name)

    def write_underlay(self, document, name="underlay.json"):
        Path(self.path(name)).write_bytes(pool_underlay.dumps(document) if isinstance(document, dict) else document)
        return self.path(name)

    def public_root(self, files):
        root = self.dir / "public-tree"
        for name, content in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode() if isinstance(content, str) else content)
        root.mkdir(exist_ok=True)
        return str(root)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = pool.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()


class FormatTests(Tmp):
    def test_a_good_underlay_round_trips_and_carries_no_content(self):
        document = pool_underlay.build(rows("syn-free-a", "syn-free-b") + rows("syn-old", state="retired"))
        path = self.write_underlay(document)
        protected, digest = pool_underlay.load(path)
        self.assertEqual(protected, [(q, sha("synthetic:" + q)) for q in ("syn-free-a", "syn-free-b", "syn-old")])
        self.assertEqual(digest, document["digest"])
        self.assertEqual(set(json.loads(Path(path).read_text())), {"schema", "kind", "count", "questions", "digest"})

    def test_every_malformed_underlay_is_an_error_never_an_empty_or_partial_pool(self):
        good = pool_underlay.build(rows("syn-a", "syn-b"))
        def edit(fn):
            doc = json.loads(json.dumps(good))
            fn(doc)
            return doc
        def redigest(doc):
            doc["digest"] = pool_underlay.digest_of(doc["questions"])
            doc["count"] = len(doc["questions"])
            return doc
        bad = {
            "not json": b"{nope", "binary": b"\xff\xfe\x00", "nan": b'{"schema": NaN}', "array": b"[]",
            "duplicate key": pool_underlay.dumps(good).replace(b'"count": 2,', b'"count": 2, "count": 2,', 1),
            "extra top key": edit(lambda d: d.update(extra=1)), "missing digest": edit(lambda d: d.pop("digest")),
            "wrong schema": edit(lambda d: d.update(schema=2)), "wrong kind": edit(lambda d: d.update(kind="other")),
            "count mismatch": edit(lambda d: d.update(count=5)), "count string": edit(lambda d: d.update(count="2")),
            "empty pool": redigest(edit(lambda d: d.update(questions=[]))),
            "questions not a list": edit(lambda d: d.update(questions={})),
            "bad id": redigest(edit(lambda d: d["questions"][0].update(question_id="Bad Id"))),
            "bad hash": redigest(edit(lambda d: d["questions"][0].update(content_sha256="zz"))),
            "state free": redigest(edit(lambda d: d["questions"][0].update(state="free"))),
            "state missing": redigest(edit(lambda d: d["questions"][0].pop("state"))),
            "extra row key": redigest(edit(lambda d: d["questions"][0].update(text="leak"))),
            "duplicate id": redigest(edit(lambda d: d["questions"][1].update(question_id=d["questions"][0]["question_id"]))),
            "duplicate hash": redigest(edit(lambda d: d["questions"][1].update(content_sha256=d["questions"][0]["content_sha256"]))),
            "unsorted": redigest(edit(lambda d: d["questions"].reverse())),
            "wrong digest": edit(lambda d: d.update(digest="0" * 64)),
            "digest not hex": edit(lambda d: d.update(digest="x")),
            "tampered row, old digest": edit(lambda d: d["questions"][0].update(content_sha256="a" * 64)),
            "a removed row, old digest": edit(lambda d: (d["questions"].pop(), d.update(count=1))),
        }
        for label, document in bad.items():
            with self.subTest(label=label):
                with self.assertRaises(pool_underlay.UnderlayError):
                    pool_underlay.load(self.write_underlay(document, name=label.replace(" ", "-") + ".json"))
        with self.assertRaises(pool_underlay.UnderlayError):
            pool_underlay.load(self.path("absent.json"))
        link = self.dir / "link.json"
        link.symlink_to(self.write_underlay(good))
        with self.assertRaises(pool_underlay.UnderlayError):
            pool_underlay.load(str(link))
        big = self.dir / "big.json"
        big.write_bytes(b" " * (pool_underlay.MAX_BYTES + 1))
        with self.assertRaises(pool_underlay.UnderlayError):
            pool_underlay.load(str(big))

    def test_an_error_never_quotes_the_file(self):
        path = self.write_underlay(pool_underlay.build(rows("syn-secret-id")).copy() | {"digest": "0" * 64})
        with self.assertRaises(pool_underlay.UnderlayError) as caught:
            pool_underlay.load(path)
        self.assertNotIn("syn-secret-id", str(caught.exception))


class ExportTests(Tmp):
    def test_export_from_the_database_lists_only_protected_states_and_refuses_to_overwrite(self):
        fx, tmp = build_scoring_fixture(users=("u1",), free=(("syn-free", 3),))
        self.addCleanup(tmp.cleanup)
        out = self.path("pool.json")
        code, stdout, stderr = self.cli("--db", fx.path, "export-underlay", "--out", out)
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("refused", stderr)
        document = json.loads(Path(out).read_text())
        self.assertIn(f"digest {document['digest']}", stdout)
        self.assertEqual(document["count"], len(fx.questions["official"]) + len(fx.questions["free"]))
        self.assertEqual({r["state"] for r in document["questions"]}, {"ranked"})
        self.assertNotIn("Prompt", Path(out).read_text(), "no question text")
        self.assertEqual(self.cli("--db", fx.path, "export-underlay", "--out", out)[0], 2, "never overwrites")

    def test_export_from_catalogs_stops_before_writing_when_a_protected_free_question_has_an_exposure_gap(self):
        fx, tmp = build_scoring_fixture(users=("u1",), free=(("syn-free", 2),))
        self.addCleanup(tmp.cleanup)
        catalog = fx.catalogs["free"]
        courses = [{"course_id": c, "N_c": catalog.pool_sizes[c], "questions": [
            {"question_id": pq.question_id, "product_id": None, "content_sha256": pq.content_sha256}
            for pq in catalog.questions.values() if pq.course_id == c]} for c in catalog.course_ids]
        whole = {"schema_version": 1, "catalog_version": "syn-1", "rules_version": "r2", "family": "free", "courses": courses,
                 "exposures": [{"kind": "question", "id": q, "content_sha256": pq.content_sha256, "affected_question_ids": [q]}
                               for q, pq in catalog.questions.items()]}
        gap = json.loads(json.dumps(whole))
        gap["exposures"].pop()                                                    # one protected free question has no exposure row
        good_path, gap_path, out = self.path("good.json"), self.path("gap.json"), self.path("pool.json")
        Path(good_path).write_text(json.dumps(whole))
        Path(gap_path).write_text(json.dumps(gap))
        code, stdout, _ = self.cli("export-underlay", "--out", out, "--catalog", good_path)
        self.assertEqual(code, 0, "a complete catalog exports")
        Path(out).unlink()
        code, _, stderr = self.cli("export-underlay", "--out", out, "--catalog", gap_path)
        self.assertEqual(code, 2)
        self.assertIn("exposure gap", stderr)
        self.assertFalse(Path(out).exists(), "nothing is exported for a catalog with an exposure gap")

    def test_the_manifest_level_gap_is_reported_before_any_export(self):
        content = self.dir / "content"
        content.mkdir()
        entries = []
        for kind, ident in (("question", "syn-free-a"), ("study", "syn-study")):
            body = f'{{"id": "{ident}"}}'.encode()
            (content / f"{ident}.txt").write_bytes(body)
            entries.append({"kind": kind, "id": ident, "product_id": "syn-product", "path": f"{ident}.txt", "sha256": hashlib.sha256(body).hexdigest()})
        manifest, exposures = self.dir / "manifest.json", self.dir / "exposures.json"
        manifest.write_text(json.dumps({"entries": entries}))
        exposures.write_text(json.dumps({"schema_version": 1, "exposures": [
            {"kind": "question", "id": "syn-free-a", "content_sha256": entries[0]["sha256"], "affected_question_ids": ["syn-free-a"]}]}))
        self.assertEqual(unexposed_manifest_entries(content, manifest, exposures), (("study", "syn-study"),),
                         "the study page that could reveal the protected free question has no reviewed exposure row")


class CheckStaticTests(Tmp):
    def underlay(self, *ids, name="underlay.json"):
        document = pool_underlay.build(rows(*ids))
        return self.write_underlay(document, name=name), document["digest"]

    def test_a_check_must_say_whether_it_is_bound_to_a_release_digest(self):
        path, digest = self.underlay("syn-free-a")
        root = self.public_root({"index.json": "{}"})
        self.assertEqual(self.cli("check-static", "--public-root", root, "--underlay", path)[0], 2, "neither --expect-digest nor --unbound")
        self.assertEqual(self.cli("check-static", "--public-root", root, "--expect-digest", digest)[0], 2, "a digest needs an underlay")
        self.assertEqual(self.cli("check-static", "--public-root", root, "--unbound")[0], 2)
        self.assertEqual(self.cli("check-static", "--public-root", root, "--underlay", path, "--expect-digest", digest, "--unbound")[0], 2)

    def test_the_release_check_passes_only_with_the_expected_digest_and_a_clean_tree(self):
        path, digest = self.underlay("syn-free-a", "syn-free-b")
        root = self.public_root({"data/index.json": '{"questions": []}'})
        code, out, err = self.cli("check-static", "--public-root", root, "--underlay", path, "--expect-digest", digest)
        self.assertEqual(code, 0, err)
        self.assertNotIn("refused", err)
        self.assertIn("no protected question", out)
        code, _, err = self.cli("check-static", "--public-root", root, "--underlay", path, "--expect-digest", "0" * 64)
        self.assertEqual(code, 2)
        self.assertIn("not the expected release digest", err)

    def test_a_stale_or_truncated_underlay_cannot_pass_a_release_check(self):
        old_path, old_digest = self.underlay("syn-free-a", name="old.json")
        new_path, new_digest = self.underlay("syn-free-a", "syn-free-b", name="new.json")
        root = self.public_root({"leak.json": '{"id": "syn-free-b"}'})
        self.assertEqual(self.cli("check-static", "--public-root", root, "--underlay", old_path, "--expect-digest", new_digest)[0], 2,
                         "the old pool does not match the digest of the current release")
        self.assertEqual(self.cli("check-static", "--public-root", root, "--underlay", new_path, "--expect-digest", new_digest)[0], 1)
        self.assertNotEqual(old_digest, new_digest)

    def test_a_protected_free_id_or_hash_in_the_public_tree_fails_with_paths_and_ids_only(self):
        path, digest = self.underlay("syn-free-a", "syn-free-b", "syn-free-c")
        leak_hash = sha("synthetic:syn-free-c")
        root = self.public_root({"data/index.json": '{"questions": [{"id": "syn-free-a"}]}', "data/hash.json": f'{{"x": "{leak_hash}"}}',
                                 "questions/syn-free-b.json": "{}", "data/fine.json": '{"id": "syn-free-ab"}'})
        code, out, err = self.cli("check-static", "--public-root", root, "--underlay", path, "--expect-digest", digest)
        self.assertEqual((code, out), (1, ""))
        for line in ("data/index.json: syn-free-a (id)", "data/hash.json: syn-free-c (content hash)", "questions/syn-free-b.json: syn-free-b (id)"):
            self.assertIn(line, err)
        self.assertNotIn("fine.json", err)
        self.assertNotIn(leak_hash, err)

    def test_an_unreadable_or_empty_pool_is_never_a_pass(self):
        root = self.public_root({"index.json": "{}"})
        for label, content in (("empty", pool_underlay.dumps({"schema": 1, "kind": pool_underlay.KIND, "count": 0, "questions": [],
                                                                "digest": pool_underlay.digest_of([])})), ("garbage", b"not json")):
            path = self.write_underlay(content, name=f"{label}.json")
            self.assertEqual(self.cli("check-static", "--public-root", root, "--underlay", path, "--unbound")[0], 2, label)
        self.assertEqual(self.cli("check-static", "--public-root", root, "--underlay", self.path("absent.json"), "--unbound")[0], 2)

    def test_the_database_mode_refuses_an_empty_pool_too(self):
        fx, tmp = build_scoring_fixture(users=("u1",))
        self.addCleanup(tmp.cleanup)
        db = fx.db()
        self.addCleanup(db.close)
        db.connect().execute("DROP TRIGGER pool_questions_no_delete")
        db.connect().execute("DELETE FROM pool_questions")
        self.assertEqual(self.cli("--db", fx.path, "check-static", "--public-root", self.public_root({"a.json": "{}"}))[0], 2)

    def test_the_unbound_self_test_says_so_and_runs_on_the_real_public_tree(self):
        path, _ = self.underlay("syn-never-published")
        code, out, err = self.cli("check-static", "--public-root", str(REPO / "public"), "--underlay", path, "--unbound")
        self.assertEqual(code, 0, err)
        self.assertIn("UNBOUND: integrity only", err)


if __name__ == "__main__":
    unittest.main()
