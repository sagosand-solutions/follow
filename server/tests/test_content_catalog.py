import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from server.content_catalog import ContentCatalog, ContentEntry, PublishedChapter, build_release_summary
from server.models import ContentError, Product


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CatalogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "private"
        self.root.mkdir()
        self.manifest = Path(self.tmp.name) / "manifest.json"

    def write_file(self, relative, data=b"restricted content"):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return data

    def write_manifest(self, entries):
        self.manifest.write_text(json.dumps({"entries": entries}), encoding="utf-8")

    def entry(self, kind="question", id="q-1", product_id="python-lifetime", path="q-1.txt", digest=None):
        return {"kind": kind, "id": id, "product_id": product_id, "path": path,
                "sha256": digest or sha256(b"restricted content")}


class CatalogLoadTests(CatalogTestCase):
    def test_valid_manifest_loads_and_reads(self):
        data = self.write_file("q-1.txt")
        self.write_manifest([self.entry(digest=sha256(data))])
        catalog = ContentCatalog.load(self.root, self.manifest)
        entry = catalog.lookup("question", "q-1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.product_id, "python-lifetime")
        self.assertEqual(catalog.read(entry), data)
        self.assertIsNone(catalog.lookup("question", "missing"))
        self.assertIsNone(catalog.lookup("exam", "q-1"))

    def test_duplicate_id_is_rejected(self):
        self.write_file("q-1.txt")
        digest = sha256(b"restricted content")
        self.write_manifest([self.entry(digest=digest), self.entry(digest=digest)])
        with self.assertRaises(ContentError):
            ContentCatalog.load(self.root, self.manifest)

    def test_unknown_kind_is_rejected(self):
        self.write_file("q-1.txt")
        self.write_manifest([self.entry(kind="video")])
        with self.assertRaises(ContentError):
            ContentCatalog.load(self.root, self.manifest)

    def test_bad_id_is_rejected(self):
        for bad in ("Q-1", "../secret", "q_1", ""):
            with self.subTest(id=bad):
                self.write_manifest([self.entry(id=bad)])
                with self.assertRaises(ContentError):
                    ContentCatalog.load(self.root, self.manifest)

    def test_bad_hash_format_is_rejected(self):
        self.write_file("q-1.txt")
        self.write_manifest([self.entry(digest="XYZ")])
        with self.assertRaises(ContentError):
            ContentCatalog.load(self.root, self.manifest)

    def test_absolute_and_dotdot_paths_are_rejected(self):
        for path in ("/etc/passwd", "../outside.txt", "a/../../b.txt", "sub/./f.txt", "a\\b.txt"):
            with self.subTest(path=path):
                self.write_manifest([self.entry(path=path)])
                with self.assertRaises(ContentError):
                    ContentCatalog.load(self.root, self.manifest)

    def test_symlinked_file_is_rejected(self):
        data = self.write_file("real.txt")
        os.symlink(self.root / "real.txt", self.root / "link.txt")
        self.write_manifest([self.entry(path="link.txt", digest=sha256(data))])
        with self.assertRaises(ContentError):
            ContentCatalog.load(self.root, self.manifest)

    def test_symlinked_directory_escaping_root_is_rejected(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"x")
        os.symlink(outside, self.root / "escape")
        self.write_manifest([self.entry(path="escape/secret.txt")])
        with self.assertRaises(ContentError):
            ContentCatalog.load(self.root, self.manifest)

    def test_entry_fields_are_exact(self):
        extra = self.entry()
        extra["note"] = "hi"
        self.write_manifest([extra])
        with self.assertRaises(ContentError):
            ContentCatalog.load(self.root, self.manifest)
        missing = self.entry()
        del missing["product_id"]
        self.write_manifest([missing])
        with self.assertRaises(ContentError):
            ContentCatalog.load(self.root, self.manifest)

    def test_missing_root_and_manifest_fail_closed(self):
        with self.assertRaises(ContentError):
            ContentCatalog.load(Path(self.tmp.name) / "nope", self.manifest)
        self.write_manifest([])
        with self.assertRaises(ContentError):
            ContentCatalog.load(self.root, Path(self.tmp.name) / "missing.json")


class CatalogReadTests(CatalogTestCase):
    def test_hash_mismatch_fails_closed(self):
        self.write_file("q-1.txt", b"original")
        self.write_manifest([self.entry(digest=sha256(b"original"))])
        catalog = ContentCatalog.load(self.root, self.manifest)
        entry = catalog.lookup("question", "q-1")
        (self.root / "q-1.txt").write_bytes(b"tampered")
        with self.assertRaises(ContentError):
            catalog.read(entry)

    def test_missing_file_fails_closed(self):
        self.write_file("q-1.txt")
        self.write_manifest([self.entry()])
        catalog = ContentCatalog.load(self.root, self.manifest)
        (self.root / "q-1.txt").unlink()
        with self.assertRaises(ContentError):
            catalog.read(catalog.lookup("question", "q-1"))

    def test_read_requires_the_exact_manifest_entry(self):
        data = self.write_file("q-1.txt")
        self.write_manifest([self.entry(digest=sha256(data))])
        catalog = ContentCatalog.load(self.root, self.manifest)
        # Forged in-root entry with a valid hash but absent from the manifest.
        forged = ContentEntry("question", "q-2", "python-lifetime", "q-1.txt", sha256(data))
        with self.assertRaises(ContentError):
            catalog.read(forged)
        # Known id but altered product, path or hash.
        entry = catalog.lookup("question", "q-1")
        altered = [
            ContentEntry(entry.kind, entry.id, "other-product", entry.path, entry.sha256),
            ContentEntry(entry.kind, entry.id, entry.product_id, "other.txt", entry.sha256),
            ContentEntry(entry.kind, entry.id, entry.product_id, entry.path, "0" * 64),
        ]
        for candidate in altered:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ContentError):
                    catalog.read(candidate)
        # The legitimate manifest entry still reads.
        self.assertEqual(catalog.read(entry), data)
        # A forged reference to a missing file is rejected before any file open.
        missing = ContentEntry("question", "q-3", "python-lifetime", "nope.txt", "0" * 64)
        with self.assertRaises(ContentError):
            catalog.read(missing)

    def test_read_does_not_modify_the_file(self):
        data = self.write_file("q-1.txt")
        self.write_manifest([self.entry(digest=sha256(data))])
        catalog = ContentCatalog.load(self.root, self.manifest)
        catalog.read(catalog.lookup("question", "q-1"))
        self.assertEqual(sha256((self.root / "q-1.txt").read_bytes()), sha256(data))


def product(product_id, active=True):
    return Product(product_id, product_id.replace("-", " ").title(), "USD", 500, "python", active)


class ReleaseSummaryTests(unittest.TestCase):
    def test_empty_catalog(self):
        summary = build_release_summary([], [])
        self.assertEqual(summary.products, ())
        self.assertEqual(summary.published_product_count, 0)

    def test_counts_are_distinct_chapters_per_active_product(self):
        summary = build_release_summary(
            [product("python-lifetime"), product("python-team")],
            [PublishedChapter("python-lifetime", "C01"), PublishedChapter("python-lifetime", "C01"),
             PublishedChapter("python-lifetime", "C02"), PublishedChapter("python-team", "C03")],
        )
        self.assertEqual(summary.published_product_count, 2)
        counts = {p.id: p.published_chapter_count for p in summary.products}
        self.assertEqual(counts, {"python-lifetime": 2, "python-team": 1})

    def test_inactive_products_are_excluded(self):
        summary = build_release_summary([product("python-lifetime", active=False), product("python-team")],
                                        [PublishedChapter("python-team", "C01")])
        self.assertEqual([p.id for p in summary.products], ["python-team"])

    def test_chapter_for_inactive_or_unknown_product_fails_closed(self):
        for chapters in ([PublishedChapter("python-lifetime", "C01")], [PublishedChapter("nope", "C01")]):
            with self.subTest(chapters=chapters):
                with self.assertRaises(ContentError):
                    build_release_summary([product("python-lifetime", active=False), product("python-team")], chapters)

    def test_chapter_id_range_is_c01_to_c99(self):
        for good in ("C01", "C99"):
            with self.subTest(chapter=good):
                summary = build_release_summary([product("python-lifetime")],
                                                [PublishedChapter("python-lifetime", good)])
                self.assertEqual(summary.products[0].published_chapter_count, 1)
        for bad in ("C00", "C100", "c01", "C1", 1, None):
            with self.subTest(chapter=bad):
                with self.assertRaises(ContentError):
                    build_release_summary([product("python-lifetime")],
                                          [PublishedChapter("python-lifetime", bad)])

    def test_bad_chapter_and_product_ids_fail_closed(self):
        with self.assertRaises(ContentError):
            build_release_summary([product("python-lifetime")], [PublishedChapter("python-lifetime", "chapter-1")])
        with self.assertRaises(ContentError):
            build_release_summary([product("python-lifetime")], [PublishedChapter("Bad", "C01")])


if __name__ == "__main__":
    unittest.main()
