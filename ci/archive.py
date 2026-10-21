# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded, non-following readers for the two archives in the CI fixture chain.

The untrusted side is never trusted: a zip wrapper is checked entry by entry, and a tar is parsed **in memory** (the 4 MiB
cap makes filesystem extraction unnecessary). Regular files and directories only; symlinks, hard links, devices, FIFOs,
sockets, PAX/GNU extensions, duplicate paths and path traversal are refused before any bytes are interpreted.

The pack side walks the host tmpfs from a directory descriptor with ``O_NOFOLLOW`` on every component; only regular files
are packed, and the limits are applied during the walk, not after it.
"""
from __future__ import annotations

import io
import os
import stat
import tarfile
import zipfile
import zlib

from . import limits


class ArchiveError(ValueError):
    """An archive is not acceptable under the frozen archive profile."""


def _check_name(name):
    if not isinstance(name, str) or not name:
        raise ArchiveError("empty archive entry name")
    if name.startswith("/") or name.startswith("\\"):
        raise ArchiveError(f"absolute entry name: {name!r}")
    if "\\" in name:
        raise ArchiveError(f"backslash in entry name: {name!r}")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ArchiveError(f"unsafe path component in {name!r}")
    if len(name.encode("utf-8")) > limits.MAX_PATH_BYTES:
        raise ArchiveError(f"entry name too long: {name!r}")
    return name


def _check_caps(names, sizes):
    if len(names) > limits.MAX_ENTRIES:
        raise ArchiveError("too many archive entries")
    if sum(len(name.encode("utf-8")) for name in names) > limits.MAX_TOTAL_PATH_BYTES:
        raise ArchiveError("archive path budget exceeded")
    if sum(sizes) > limits.MAX_UNCOMPRESSED_TOTAL:
        raise ArchiveError("archive uncompressed budget exceeded")


def pack_tar(files):
    """A deterministic, uncompressed ustar archive of regular files (mode 0644, mtime 0)."""
    names = sorted(files)
    _check_caps(names, [len(files[name]) for name in names])
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:", format=tarfile.USTAR_FORMAT) as archive:
        for name in names:
            _check_name(name)
            if len(name.encode("utf-8")) > 100:
                raise ArchiveError(f"ustar name is longer than 100 bytes: {name!r}")
            data = files[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.type = tarfile.REGTYPE
            archive.addfile(info, io.BytesIO(data))
    data = buffer.getvalue()
    if len(data) > limits.MAX_RAW_TAR_BYTES:
        raise ArchiveError("packed tar exceeds the raw.tar cap")
    return data


def _octal(field, label):
    raw = field.split(b"\0", 1)[0].strip()
    if not raw:
        return 0
    if any(char not in b"01234567" for char in raw):
        raise ArchiveError(f"non-octal {label}")
    return int(raw, 8)


def _text(field, label):
    head, _, tail = field.partition(b"\0")
    if tail.strip(b"\0"):
        raise ArchiveError(f"data after the NUL terminator in {label}")
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError:
        raise ArchiveError(f"non-UTF-8 {label}") from None


def _zero(field, label):
    if field.strip(b"\0"):
        raise ArchiveError(f"{label} must be empty in the ustar profile")


def _parse_ustar(data, max_bytes, allowed_names, entry_caps):
    """Validate the raw 512-byte ustar headers before any library parser sees the archive."""
    if len(data) % 512 != 0 or len(data) < 1024:
        raise ArchiveError("tar length is not a multiple of 512 or is too short")
    files = {}
    offset = 0
    total = 0
    count = 0
    while True:
        if offset + 512 > len(data):
            raise ArchiveError("tar ends without a terminator")
        block = data[offset:offset + 512]
        if block == b"\0" * 512:
            if any(data[offset:]):
                raise ArchiveError("non-zero bytes after the tar terminator")
            break
        count += 1
        if count > limits.MAX_ENTRIES:
            raise ArchiveError("too many tar entries")
        name = _check_name(_text(block[0:100], "name"))
        if _octal(block[100:108], "mode") != 0o644:
            raise ArchiveError(f"mode is not 0644: {name!r}")
        if _octal(block[108:116], "uid") != 0 or _octal(block[116:124], "gid") != 0:
            raise ArchiveError(f"uid/gid are not zero: {name!r}")
        size = _octal(block[124:136], "size")
        if _octal(block[136:148], "mtime") != 0:
            raise ArchiveError(f"mtime is not zero: {name!r}")
        checksum = _octal(block[148:156], "checksum")
        computed = sum(block[:148]) + 8 * 32 + sum(block[156:])
        if checksum != computed:
            raise ArchiveError(f"header checksum mismatch: {name!r}")
        if block[156:157] not in (b"0", b"\0"):
            raise ArchiveError(f"unsupported tar type {block[156:157]!r}: {name!r}")
        _zero(block[157:257], "linkname")
        if block[257:263] != b"ustar\0" or block[263:265] != b"00":
            raise ArchiveError(f"not the chosen ustar profile: {name!r}")
        _zero(block[265:297], "uname")
        _zero(block[297:329], "gname")
        _zero(block[329:337], "devmajor")
        _zero(block[337:345], "devminor")
        _zero(block[345:500], "prefix")
        _zero(block[500:512], "padding")
        cap = max_bytes
        if entry_caps and name in entry_caps:
            cap = min(cap, entry_caps[name])
        if size > cap:
            raise ArchiveError(f"entry too large: {name!r}")
        total += size
        if total > max_bytes:
            raise ArchiveError("tar data budget exceeded")
        start = offset + 512
        end = start + size
        padded = start + ((size + 511) // 512) * 512
        if end > len(data) or padded > len(data):
            raise ArchiveError(f"truncated tar data: {name!r}")
        if any(data[end:padded]):
            raise ArchiveError(f"non-zero tar padding: {name!r}")
        if name in files:
            raise ArchiveError(f"duplicate tar entry: {name!r}")
        files[name] = bytes(data[start:end])
        offset = padded
    if allowed_names is not None and set(files) != set(allowed_names):
        raise ArchiveError("tar entry set does not match the expected set")
    return files


def parse_tar(data, *, max_bytes=limits.MAX_ARTIFACT_BYTES, allowed_names=None, require_canonical=False,
              entry_caps=None):
    """Parse an uncompressed tar with a bounded raw ustar pre-validator.

    No tarfile parser sees the bytes: the 512-byte headers are validated first (ustar magic, type 0, zero
    linkname/prefix/uname/gname/device fields, mode 0644, uid/gid/mtime zero, checksum), then the data slices are
    copied. This refuses PAX/GNU/sparse/longname metadata before any library interpretation.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ArchiveError("tar input is not bytes")
    data = bytes(data)
    if len(data) > limits.MAX_RAW_TAR_BYTES:
        raise ArchiveError("tar exceeds the raw.tar cap")
    files = _parse_ustar(data, max_bytes, allowed_names, entry_caps)
    if require_canonical and pack_tar(files) != data:
        raise ArchiveError("tar is not the canonical byte form")
    return files


def parse_zip(data, *, allowed_names, max_bytes=limits.MAX_ZIP_BYTES, max_entries=None, entry_caps=None):
    """Parse the Actions artifact wrapper. Only the exact allowed regular files are accepted."""
    if not isinstance(data, (bytes, bytearray)):
        raise ArchiveError("zip input is not bytes")
    data = bytes(data)
    if len(data) > max_bytes:
        raise ArchiveError("zip exceeds the compressed cap")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, zlib.error) as exc:
        raise ArchiveError(f"not an acceptable zip: {exc}") from None
    names = allowed_names if isinstance(allowed_names, (set, frozenset)) else set(allowed_names)
    expected = max_entries if max_entries is not None else len(names)
    files = {}
    total = 0
    try:
        infos = archive.infolist()
        if len(infos) != expected:
            raise ArchiveError("unexpected number of zip entries")
        for info in infos:
            name = _check_name(info.filename)
            if name not in names:
                raise ArchiveError(f"unexpected zip entry: {name!r}")
            if name in files:
                raise ArchiveError(f"duplicate zip entry: {name!r}")
            if info.is_dir():
                raise ArchiveError(f"zip directory entries are not accepted: {name!r}")
            if info.flag_bits & 0x1:
                raise ArchiveError("encrypted zip entries are not accepted")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ArchiveError(f"unsupported zip compression: {info.compress_type}")
            if info.extra:
                raise ArchiveError(f"unexpected zip extra field: {name!r}")
            if info.file_size > 0xFFFFFFFF or info.compress_size > 0xFFFFFFFF:
                raise ArchiveError("zip64 entries are not accepted")
            cap = max_bytes
            if entry_caps and name in entry_caps:
                cap = min(cap, entry_caps[name])
            if info.file_size > cap:
                raise ArchiveError(f"zip entry too large: {name!r}")
            if info.compress_size == 0 and info.file_size > 0:
                raise ArchiveError(f"zip entry has no compressed bytes: {name!r}")
            if (info.file_size > 1_048_576 and info.compress_size
                    and info.file_size / info.compress_size > limits.MAX_COMPRESSION_RATIO):
                raise ArchiveError(f"compression ratio above the cap: {name!r}")
            total += info.file_size
            if total > max_bytes:
                raise ArchiveError("zip uncompressed budget exceeded")
            try:
                handle = archive.open(info)
            except (zipfile.BadZipFile, OSError, zlib.error) as exc:
                raise ArchiveError(f"cannot open zip entry: {exc}") from None
            with handle:
                chunks, read = [], 0
                while True:
                    try:
                        chunk = handle.read(min(65536, max_bytes + 1 - read))
                    except (zipfile.BadZipFile, OSError, zlib.error) as exc:
                        raise ArchiveError(f"zip entry read failed: {exc}") from None
                    if not chunk:
                        break
                    chunks.append(chunk)
                    read += len(chunk)
                    if read > max_bytes:
                        raise ArchiveError(f"zip entry grew past its limit: {name!r}")
            if read != info.file_size:
                raise ArchiveError(f"zip declared size mismatch: {name!r}")
            files[name] = b"".join(chunks)
    except tarfile.TarError as exc:
        raise ArchiveError(f"tar parsing failed: {exc}") from None
    finally:
        archive.close()
    if set(files) != names:
        raise ArchiveError("zip entry set does not match the expected set")
    return files


def _open_below(name, dir_fd, nonblock=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    if nonblock:
        flags |= os.O_NONBLOCK
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise ArchiveError(f"cannot open {name!r} below the output root: {exc.strerror}") from None


def walk_out(root, *, max_files=limits.MAX_ARTIFACT_FILES, max_bytes=limits.MAX_ARTIFACT_BYTES):
    """Walk ``root`` from its directory descriptor. Only regular files; caps applied during the walk."""
    files = {}
    total = 0

    def walk(fd, prefix, depth):
        nonlocal total
        if depth > 16:
            raise ArchiveError("output directory nesting is too deep")
        for name in os.listdir(fd):
            full = f"{prefix}{name}"
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(st.st_mode):
                raise ArchiveError(f"symlink in output: {full!r}")
            if stat.S_ISDIR(st.st_mode):
                child = _open_below(name, fd)
                try:
                    if not stat.S_ISDIR(os.fstat(child).st_mode):
                        raise ArchiveError(f"not a directory: {full!r}")
                    walk(child, full + "/", depth + 1)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(st.st_mode):
                raise ArchiveError(f"special file in output: {full!r}")
            if st.st_nlink > 1:
                raise ArchiveError(f"hard link in output: {full!r}")
            if len(files) >= max_files:
                raise ArchiveError("too many output files")
            remaining = max_bytes - total
            if st.st_size > remaining:
                raise ArchiveError("output byte cap exceeded")
            entry = _open_below(name, fd, nonblock=True)
            try:
                fst = os.fstat(entry)
                if not stat.S_ISREG(fst.st_mode):
                    raise ArchiveError(f"not a regular file: {full!r}")
                if fst.st_size > remaining:
                    raise ArchiveError("output byte cap exceeded")
                chunks, read = [], 0
                while True:
                    chunk = os.read(entry, min(65536, remaining - read + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    read += len(chunk)
                    if read > remaining:
                        raise ArchiveError("output file grew past the remaining budget")
            finally:
                os.close(entry)
            total += read
            files[full] = b"".join(chunks)

    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        walk(root_fd, "", 0)
    finally:
        os.close(root_fd)
    return files
