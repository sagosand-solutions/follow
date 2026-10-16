# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private content manifest loading, fail-closed.

The manifest is read only from an explicit private root and maps ``(kind, id)`` to
a product and a relative file inside that root. Paths are never opened from user
input, and ``lookup`` returns an internal reference, never a public URL. Serving a
file to a user is a later task and must re-check entitlement on every request.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .models import ContentError

ALLOWED_KINDS = frozenset({"question", "exam", "study"})
_ID_RE = re.compile(r"[a-z][a-z0-9.-]{0,63}\Z")
_PRODUCT_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_FIELDS = frozenset({"kind", "id", "product_id", "path", "sha256"})


@dataclass(frozen=True)
class ContentEntry:
    kind: str
    id: str
    product_id: str
    path: str
    sha256: str


def _is_symlink_chain(root: Path, relative: str) -> bool:
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            return True
    return False


def _check_path(root: Path, relative: str) -> None:
    if not relative or "\\" in relative or "\x00" in relative:
        raise ContentError(f"content path rejected: {relative!r}")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ContentError(f"content path is not a normalized relative path: {relative!r}")
    if relative.startswith("/"):
        raise ContentError(f"content path must be relative: {relative!r}")
    target = os.path.normpath(os.path.join(str(root), relative))
    if target != str(root) and not target.startswith(str(root) + os.sep):
        raise ContentError(f"content path escapes the root: {relative!r}")
    if _is_symlink_chain(root, relative):
        raise ContentError(f"content path must not traverse a symlink: {relative!r}")


def _parse_entry(root: Path, raw: object) -> ContentEntry:
    if not isinstance(raw, dict):
        raise ContentError("manifest entry must be an object")
    keys = set(raw)
    if keys != _REQUIRED_FIELDS:
        missing = sorted(_REQUIRED_FIELDS - keys)
        extra = sorted(keys - _REQUIRED_FIELDS)
        raise ContentError(f"manifest entry fields invalid (missing {missing}, unexpected {extra})")
    if not all(isinstance(raw[field], str) for field in _REQUIRED_FIELDS):
        raise ContentError("manifest entry values must be strings")
    kind, ident, product_id, path, sha256 = (raw[field] for field in ("kind", "id", "product_id", "path", "sha256"))
    if kind not in ALLOWED_KINDS:
        raise ContentError(f"unknown content kind: {kind!r}")
    if not _ID_RE.fullmatch(ident) or ".." in ident:
        raise ContentError(f"content id rejected: {ident!r}")
    if not _PRODUCT_RE.fullmatch(product_id):
        raise ContentError(f"product id rejected: {product_id!r}")
    if not _HASH_RE.fullmatch(sha256):
        raise ContentError(f"sha256 must be 64 lowercase hex characters: {sha256!r}")
    _check_path(root, path)
    return ContentEntry(kind=kind, id=ident, product_id=product_id, path=path, sha256=sha256)


class ContentCatalog:
    def __init__(self, root: Path, entries: dict[tuple[str, str], ContentEntry]):
        self._root = root
        self._entries = entries

    @classmethod
    def load(cls, content_root, manifest_path) -> "ContentCatalog":
        root = Path(content_root)
        if not root.is_dir():
            raise ContentError("content root must be an existing directory")
        if root.is_symlink():
            raise ContentError("content root must not be a symlink")
        root = root.resolve(strict=True)
        manifest = Path(manifest_path)
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError as exc:
            raise ContentError("content manifest could not be read") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContentError("content manifest is not valid JSON") from exc
        raw_entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(raw_entries, list):
            raise ContentError("content manifest must contain an 'entries' list")
        entries: dict[tuple[str, str], ContentEntry] = {}
        for raw in raw_entries:
            entry = _parse_entry(root, raw)
            key = (entry.kind, entry.id)
            if key in entries:
                raise ContentError(f"duplicate content id: {entry.kind}/{entry.id}")
            entries[key] = entry
        return cls(root, entries)

    @property
    def root(self) -> Path:
        return self._root

    def lookup(self, kind: str, id: str) -> ContentEntry | None:
        return self._entries.get((kind, id))

    def entries(self) -> tuple[ContentEntry, ...]:
        return tuple(self._entries.values())

    def read(self, entry: ContentEntry) -> bytes:
        """Return the file bytes after re-checking the path and the SHA-256."""
        _check_path(self._root, entry.path)
        target = self._root / entry.path
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise ContentError(f"content file unavailable: {entry.kind}/{entry.id}") from exc
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.sha256:
            raise ContentError(f"content hash mismatch: {entry.kind}/{entry.id}")
        return data
