# SPDX-License-Identifier: MIT
"""Static and file-system tests for the descriptor-bound URI resolver (no fixture execution, no network)."""
import os
import pathlib
import tempfile
import unittest

from validator.harness import canonical, resolver


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="harness-resolver-"))
        self.addCleanup(self._cleanup)
        (self.root / "a" / "b").mkdir(parents=True)
        (self.root / "a" / "b" / "manifest.json").write_bytes(b'{"x":1}')
        self.limit = 1024

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def read(self, uri, limit=None):
        return resolver.resolve(self.root, uri, self.limit if limit is None else limit)

    def test_a_plain_file_below_the_root(self):
        self.assertEqual(self.read("a/b/manifest.json"), b'{"x":1}')
        self.assertEqual(resolver.resolve_json(self.root, "a/b/manifest.json", self.limit), {"x": 1})

    def test_bad_forms_are_refused_before_anything_is_opened(self):
        for uri in ("", "/etc/passwd", "http://evil.net/x", "a?b", "a#b", "../x", "a/../x", "a//b", ".", "./a", "a/.", "a\x00b"):
            with self.subTest(uri=uri):
                with self.assertRaises(resolver.ResolverError):
                    self.read(uri)

    def test_the_trusted_root_must_be_a_plain_directory_not_a_symlink_or_file(self):
        link = self.root.parent / (self.root.name + "-link")
        link.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(link.unlink, missing_ok=True)
        for root in (link, self.root / "a" / "b" / "manifest.json", self.root / "missing-root"):
            with self.subTest(root=root):
                with self.assertRaisesRegex(resolver.ResolverError, "trusted root"):
                    resolver.resolve(root, "a/b/manifest.json", self.limit)

    def test_a_symlink_is_not_followed_at_any_level(self):
        outside = pathlib.Path(tempfile.mkdtemp(prefix="harness-outside-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        (outside / "secret").write_bytes(b"secret")
        (self.root / "a" / "leaf-link").symlink_to(outside / "secret")
        (self.root / "a" / "dir-link").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(resolver.ResolverError, "symlink"):
            self.read("a/leaf-link")
        with self.assertRaisesRegex(resolver.ResolverError, "symlink"):
            self.read("a/dir-link/secret")

    def test_a_parent_swapped_after_it_was_opened_changes_nothing(self):
        original = self.root / "a" / "b"
        outside = pathlib.Path(tempfile.mkdtemp(prefix="harness-swap-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        (outside / "manifest.json").write_bytes(b'{"x":2}')
        real_open = os.open
        swapped = []

        def open_hook(path, flags, *args, **kwargs):
            if not swapped and path == "manifest.json":
                swapped.append(True)
                original.rename(self.root / "a" / "original")
                original.symlink_to(outside, target_is_directory=True)
            return real_open(path, flags, *args, **kwargs)

        os.open = open_hook
        try:
            self.assertEqual(self.read("a/b/manifest.json"), b'{"x":1}', "the directory already opened, not the swapped one")
        finally:
            os.open = real_open

    def test_a_fifo_is_refused_without_hanging(self):
        os.mkfifo(self.root / "pipe")
        with self.assertRaisesRegex(resolver.ResolverError, "regular file"):
            self.read("pipe")

    def test_a_directory_and_a_hard_link_are_refused(self):
        with self.assertRaisesRegex(resolver.ResolverError, "regular file"):
            self.read("a/b")
        os.link(self.root / "a" / "b" / "manifest.json", self.root / "hard.json")
        with self.assertRaisesRegex(resolver.ResolverError, "hard link"):
            self.read("hard.json")

    def test_the_read_is_bounded(self):
        (self.root / "big").write_bytes(b"x" * 200)
        self.assertEqual(len(self.read("big", 200)), 200)
        with self.assertRaisesRegex(resolver.ResolverError, "larger than"):
            self.read("big", 199)

    def test_no_descriptor_leaks_on_any_path(self):
        before = len(os.listdir("/proc/self/fd"))
        for uri in ("a/b/manifest.json", "missing", "../x", "a/b"):
            try:
                self.read(uri)
            except resolver.ResolverError:
                pass
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_bad_json_is_the_strict_loader_s_error(self):
        (self.root / "bad.json").write_bytes(b'{"a":1,"a":2}')
        with self.assertRaises(canonical.CanonicalError):
            resolver.resolve_json(self.root, "bad.json", self.limit)


if __name__ == "__main__":
    unittest.main()
