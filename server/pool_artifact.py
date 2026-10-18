# SPDX-License-Identifier: AGPL-3.0-or-later
"""The exact bytes of a public tree: its git tree id, a separate artifact digest, and a scan, from ONE read of every file.

The release decision pins ``<commit>:public`` (the git tree object id). A command-line commit id alone proves nothing about the
directory that was scanned, so the tree id is recomputed here from the scanned bytes with git's own object rules (blob and tree
sha-1, git's name order, mode 100644 or 100755). The artifact digest (sha-256 over path, size and sha-256 of each file) is a
different, stronger digest and is never the same thing as the git id. Symlinks, special files, empty directories, an empty tree and
files over the size cap are errors: none of them can come from a git checkout of a tree, and none can be bound to one.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 20_000
_TEXT_SUFFIXES = {".json", ".js", ".mjs", ".html", ".css", ".txt", ".md", ".csv", ".xml", ".svg", ".webmanifest", ".map"}


class ArtifactError(Exception):
    """The tree cannot be bound to a git tree. The message names the problem, never file content."""


def _blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 (git object id, not a security digest)


def _tree_id(entries: list) -> str:
    """entries: (name, mode, hex object id); git orders names bytewise, a directory as if its name ended in '/'."""
    def key(entry):
        name, mode, _ = entry
        return name.encode("utf-8") + (b"/" if mode == "40000" else b"")
    body = b"".join(mode.encode() + b" " + name.encode("utf-8") + b"\0" + bytes.fromhex(oid) for name, mode, oid in sorted(entries, key=key))
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()  # noqa: S324


def read_tree(root: str) -> dict:
    """Walk ``root`` once. Returns {'files': {relpath: bytes}, 'tree_id', 'artifact_sha256'}; raises ArtifactError."""
    base = Path(root)
    if base.is_symlink() or not base.is_dir():
        raise ArtifactError("the public root is not a plain directory")
    files: dict = {}

    def build(directory: Path, prefix: str) -> str:
        entries = []
        names = sorted(os.listdir(directory))
        if not names:
            raise ArtifactError("an empty directory cannot come from a git tree")
        for name in names:
            path = directory / name
            rel = prefix + name
            try:
                name.encode("utf-8")
            except UnicodeEncodeError:
                raise ArtifactError("a file name is not valid UTF-8") from None
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ArtifactError("a symlink is not allowed in a public tree")
            if stat.S_ISDIR(info.st_mode):
                entries.append((name, "40000", build(path, rel + "/")))
            elif stat.S_ISREG(info.st_mode):
                if info.st_size > MAX_FILE_BYTES:
                    raise ArtifactError("a file is too large to bind")
                if len(files) >= MAX_FILES:
                    raise ArtifactError("too many files")
                data = path.read_bytes()
                files[rel] = data
                mode = "100755" if info.st_mode & 0o100 else "100644"
                entries.append((name, mode, _blob_id(data)))
            else:
                raise ArtifactError("a special file is not allowed in a public tree")
        return _tree_id(entries)

    tree = build(base, "")
    if not files:
        raise ArtifactError("the tree has no files")
    listing = "".join(f"{rel}\0{len(data)}\0{hashlib.sha256(data).hexdigest()}\n" for rel, data in sorted(files.items()))
    return {"files": files, "tree_id": tree, "artifact_sha256": hashlib.sha256(listing.encode("utf-8")).hexdigest()}


def scan(files: dict, protected: list) -> list:
    """Findings (path, id, how) over bytes already read: ids as whole tokens (an id followed by '.' counts), content hashes,
    and a byte-identical copy of a protected question. Same rules as ``server.pool.check_static``."""
    patterns = {qid: re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(qid) + r"(?![A-Za-z0-9_-])") for qid, _ in protected}
    findings = []
    for rel, data in sorted(files.items()):
        digest = hashlib.sha256(data).hexdigest()
        suffix = Path(rel).suffix.lower()
        text = data.decode("utf-8", "replace") if suffix in _TEXT_SUFFIXES or len(data) < 65536 else ""
        for qid, sha in protected:
            if digest == sha or sha in text:
                findings.append((rel, qid, "content hash"))
            elif patterns[qid].search(text) or patterns[qid].search(rel):
                findings.append((rel, qid, "id"))
    return findings
