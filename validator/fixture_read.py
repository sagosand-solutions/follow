# SPDX-License-Identifier: MIT
"""Read one fixture output file below a trusted root, bound to file descriptors from the root down.

There is no separate "check" followed by a "read" of the same path: every component is opened relative to the descriptor of the
one before it with O_NOFOLLOW, so a path that is swapped for a symlink after we started cannot redirect the read, the opened file
is checked with fstat on the descriptor we read from, and at most `limit` + 1 bytes are read, whatever the file grows to.

Trust assumption: the root is a directory the caller already trusts (it is opened once, by the path it was given, which the
exporter resolves first); nothing below it is trusted. POSIX only (it needs dir_fd and O_NOFOLLOW)."""
import errno
import os
from pathlib import Path
import stat

from .core import SetupError


class FixtureProblem(Exception):
    """The fixture is not usable: missing, not a regular file, or too large. The export reports it as a finding."""


def _open_below(name, dir_fd, flags, relative):
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        try:
            found = os.lstat(name, dir_fd=dir_fd)
        except OSError:
            found = None
        if found is not None and stat.S_ISLNK(found.st_mode):
            raise SetupError(f"{relative.as_posix()}: symlink paths are not supported") from None
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            raise FixtureProblem("no such file") from None
        raise SetupError(f"{relative.as_posix()}: cannot be opened ({exc.strerror})") from None


def read_fixture(root, relative, limit):
    """The bytes of `relative` below `root`, at most `limit` of them.

    Raises FixtureProblem (missing, not a regular file, longer than limit) or SetupError (a symlink on the way, or any other
    failure to open). Every descriptor is closed, also on error."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise SetupError(f"{relative}: path must stay below the selected root")
    opened = []
    try:
        current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        opened.append(current)
        for part in relative.parts[:-1]:
            current = _open_below(part, current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, relative)
            opened.append(current)
        # O_NONBLOCK: opening a FIFO for reading must not wait for a writer; it is refused below as not a regular file.
        fd = _open_below(relative.parts[-1], current, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, relative)
        opened.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise FixtureProblem("not a regular file")
        chunks, total = [], 0
        while total <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise FixtureProblem(f"larger than {limit} bytes")
        return b"".join(chunks)
    finally:
        for fd in reversed(opened):
            os.close(fd)
