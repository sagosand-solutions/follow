# SPDX-License-Identifier: MIT
"""Resolve a relative URI below a trusted root by file descriptors, never by a path lookup that can be swapped.

A relative URI without ``..`` is a *form* requirement, not filesystem isolation: the isolation is the descriptor walk.
Every component is opened relative to the descriptor of the one before it with ``O_NOFOLLOW``, the leaf is opened
``O_RDONLY|O_NOFOLLOW|O_NONBLOCK`` and checked with ``fstat`` on the very descriptor that is read, only a regular file
with a single link is accepted, and at most ``limit`` + 1 bytes are read. A network URI is refused by form; nothing here
resolves a host name or opens a socket.
"""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from . import canonical


class ResolverError(ValueError):
    """The URI cannot be used: bad form, outside the root, a symlink/hardlink/special file, or too large."""


def _form_error(uri: str) -> str | None:
    if not isinstance(uri, str) or uri == "":
        return "the URI is empty"
    if "\x00" in uri:
        return "the URI contains NUL"
    if "://" in uri or uri.startswith("/"):
        return "the URI is absolute or names a network scheme"
    if "?" in uri or "#" in uri:
        return "the URI carries a query or fragment"
    if any(part in ("", ".", "..") for part in uri.split("/")):
        return "the URI has an empty, '.' or '..' segment"
    return None


def _open_below(name: str, dir_fd: int, flags: int, uri: str) -> int:
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        try:
            found = os.lstat(name, dir_fd=dir_fd)
        except OSError:
            found = None
        if found is not None and stat.S_ISLNK(found.st_mode):
            raise ResolverError(f"{uri}: a symlink is not followed") from None
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            raise ResolverError(f"{uri}: no such file below the trusted root") from None
        raise ResolverError(f"{uri}: cannot be opened ({exc.strerror})") from None


def resolve(root, uri: str, limit: int) -> bytes:
    """The bytes of ``uri`` below ``root``, at most ``limit`` of them; fd-bound, no-follow at every level."""
    problem = _form_error(uri)
    if problem is not None:
        raise ResolverError(problem)
    if "\x00" in str(root):
        raise ResolverError("the trusted root contains NUL")
    parts = Path(uri).parts
    opened: list[int] = []
    try:
        try:
            # O_NOFOLLOW: the trusted root itself must be a real directory, not a symlink that redirects the walk.
            current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            raise ResolverError(f"the trusted root is not a plain directory ({exc.strerror})") from None
        opened.append(current)
        for part in parts[:-1]:
            current = _open_below(part, current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, uri)
            opened.append(current)
        # O_NONBLOCK: opening a FIFO for reading must not wait for a writer; it is refused below as not a regular file.
        fd = _open_below(parts[-1], current, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, uri)
        opened.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ResolverError(f"{uri}: not a regular file")
        if info.st_nlink != 1:
            raise ResolverError(f"{uri}: a hard link is not accepted")
        chunks, total = [], 0
        while total <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise ResolverError(f"{uri}: larger than {limit} bytes")
        return b"".join(chunks)
    finally:
        for fd in reversed(opened):
            os.close(fd)


def resolve_json(root, uri: str, limit: int) -> dict:
    """A JSON document read through :func:`resolve` and parsed with the one strict loader."""
    return canonical.loads_strict(resolve(root, uri, limit))
