# SPDX-License-Identifier: AGPL-3.0-or-later
"""The git tree id and artifact digest of a directory, recomputed from the scanned bytes (synthetic files only)."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from server import pool_artifact

LAYOUT = {
    "index.html": b"<!doctype html><title>synthetic</title>\n",
    "data/index.json": b'{"questions": []}\n',
    "data/a.json": b"{}\n",
    "data/a-b.json": b"[]\n",
    "data/a/b.json": b"{}\n",               # a directory named like the file a.json: git's name order puts a/ after a-b.json
    "js/app.js": b"console.log('synthetic');\n",
    "café/menu.txt": "naïve\n".encode(),
}
#: git rev-parse HEAD:public for exactly LAYOUT (plain modes), taken from git 2.56.0 (AgainstRealGitTests re-derives it live)
KNOWN_TREE = "6f86a4cc9c51720eae93a013ac7d7423d1d7923d"


def write(root: Path, files: dict, executable=()):
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if rel in executable:
            path.chmod(0o755)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "public"
        self.root.mkdir()


@unittest.skipUnless(shutil.which("git"), "git is needed to compare against its own object ids")
class AgainstRealGitTests(Tmp):
    def git_tree(self, files, executable=()):
        repo = Path(self.tmp.name) / "repo"
        shutil.rmtree(repo, ignore_errors=True)
        (repo / "public").mkdir(parents=True)
        write(repo / "public", files, executable)
        env = {"PATH": os.environ["PATH"], "HOME": self.tmp.name, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True, env=env).stdout.strip()
        run("init", "-q")
        run("add", "-A")
        run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "synthetic")
        return run("rev-parse", "HEAD:public"), repo / "public"

    def test_the_tree_id_equals_gits_for_nested_names_unicode_and_the_executable_bit(self):
        cases = [(LAYOUT, ()), ({"one.txt": b"1\n"}, ()), ({"run.sh": b"#!/bin/sh\n", "doc.txt": b"x\n"}, ("run.sh",)),
                 ({"a": b"1", "a.b": b"2", "a-b": b"3", "a0": b"4", "dir/a": b"5", "dir.x": b"6", "dir0": b"7"}, ()),
                 ({"z/y/x/w.txt": b"deep\n", "z/y.txt": b"y\n", "empty.txt": b""}, ())]
        for files, executable in cases:
            with self.subTest(files=sorted(files)):
                expected, root = self.git_tree(files, executable)
                result = pool_artifact.read_tree(str(root))
                self.assertEqual(result["tree_id"], expected)
                self.assertEqual(set(result["files"]), set(files))

    def test_a_changed_byte_a_renamed_file_or_a_flipped_mode_changes_the_tree_id(self):
        base, root = self.git_tree(LAYOUT)
        (root / "data" / "a.json").write_bytes(b"{ }\n")
        self.assertNotEqual(pool_artifact.read_tree(str(root))["tree_id"], base)
        (root / "data" / "a.json").write_bytes(LAYOUT["data/a.json"])
        self.assertEqual(pool_artifact.read_tree(str(root))["tree_id"], base)
        (root / "data" / "a.json").rename(root / "data" / "b.json")
        self.assertNotEqual(pool_artifact.read_tree(str(root))["tree_id"], base)
        (root / "data" / "b.json").rename(root / "data" / "a.json")
        (root / "js" / "app.js").chmod(0o755)
        self.assertNotEqual(pool_artifact.read_tree(str(root))["tree_id"], base)


class ArtifactTests(Tmp):
    def test_the_pinned_tree_id_of_the_fixed_layout_holds_without_git(self):
        write(self.root, LAYOUT)
        self.assertEqual(pool_artifact.read_tree(str(self.root))["tree_id"], KNOWN_TREE)

    def test_the_artifact_digest_is_not_the_tree_id_and_follows_path_size_and_bytes(self):
        write(self.root, LAYOUT)
        first = pool_artifact.read_tree(str(self.root))
        self.assertEqual(len(first["artifact_sha256"]), 64)
        self.assertNotEqual(first["artifact_sha256"], first["tree_id"])
        self.assertEqual(pool_artifact.read_tree(str(self.root))["artifact_sha256"], first["artifact_sha256"], "stable")
        (self.root / "data" / "a.json").write_bytes(b"{ }\n")
        self.assertNotEqual(pool_artifact.read_tree(str(self.root))["artifact_sha256"], first["artifact_sha256"])

    def test_nothing_that_could_not_come_from_a_git_tree_can_be_bound(self):
        with self.assertRaises(pool_artifact.ArtifactError):
            pool_artifact.read_tree(str(self.root))                                   # an empty root: no files
        write(self.root, {"a.txt": b"x"})
        (self.root / "empty-dir").mkdir()
        with self.assertRaises(pool_artifact.ArtifactError):
            pool_artifact.read_tree(str(self.root))                                   # an empty directory
        (self.root / "empty-dir").rmdir()
        (self.root / "link").symlink_to(self.root / "a.txt")
        with self.assertRaises(pool_artifact.ArtifactError):
            pool_artifact.read_tree(str(self.root))                                   # a symlink
        (self.root / "link").unlink()
        os.mkfifo(self.root / "pipe")
        with self.assertRaises(pool_artifact.ArtifactError):
            pool_artifact.read_tree(str(self.root))                                   # a special file
        os.unlink(self.root / "pipe")
        (self.root / "big.bin").write_bytes(b"x" * (pool_artifact.MAX_FILE_BYTES + 1))
        with self.assertRaises(pool_artifact.ArtifactError):
            pool_artifact.read_tree(str(self.root))                                   # too large
        (self.root / "big.bin").unlink()
        for bad in (str(self.root / "a.txt"), str(self.root / "nope")):
            with self.assertRaises(pool_artifact.ArtifactError):
                pool_artifact.read_tree(bad)
        linked = Path(self.tmp.name) / "linked"
        linked.symlink_to(self.root)
        with self.assertRaises(pool_artifact.ArtifactError):
            pool_artifact.read_tree(str(linked))

    def test_scan_finds_ids_hashes_and_copies_from_the_bytes_already_read(self):
        secret = b'{"synthetic": "protected body"}'
        write(self.root, {"data/index.json": b'{"id": "syn-free-a"}', "copy.bin": secret, "hash.json": hashlib.sha256(b"other").hexdigest().encode(),
                          "fine.json": b'{"id": "syn-free-ab"}', "q/syn-free-b.json": b"{}"})
        protected = [("syn-free-a", hashlib.sha256(b"a").hexdigest()), ("syn-secret", hashlib.sha256(secret).hexdigest()),
                     ("syn-other", hashlib.sha256(b"other").hexdigest()), ("syn-free-b", hashlib.sha256(b"b").hexdigest())]
        found = pool_artifact.scan(pool_artifact.read_tree(str(self.root))["files"], protected)
        self.assertEqual(sorted(found), [("copy.bin", "syn-secret", "content hash"), ("data/index.json", "syn-free-a", "id"),
                                         ("hash.json", "syn-other", "content hash"), ("q/syn-free-b.json", "syn-free-b", "id")])


if __name__ == "__main__":
    unittest.main()
