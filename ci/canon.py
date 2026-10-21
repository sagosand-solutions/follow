# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one canonical JSON byte form used by the CI fixture verifier.

This is ``follow-canon-v1``, not RFC 8785/JCS and not ``json.dumps(sort_keys=True)``: it is an explicit, restrictive
profile chosen for this verifier.

* UTF-8, no BOM, no trailing newline, no whitespace outside strings.
* Object keys are sorted by Unicode code point; duplicate keys are refused, never silently collapsed.
* Strings must be NFC and must not contain lone surrogates. Escapes are only ``\\"``, ``\\\\`` and ``\\u00xx``
  (lowercase hex) for characters below 0x20; ``/`` and everything else are emitted literally.
* Arrays keep the order the caller gives them. The receipt builder sorts ``inputs`` by path, ``artifacts`` by id,
  ``questions`` by question_id, ``licence_set`` and ``limit_hit`` lexicographically; duplicates are refused.
* Numbers are integers in ``[-2**53, 2**53]``. Floats, NaN and Infinity are refused.

Raw fixture and artifact bytes are never normalised.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata

MAX_SAFE_INT = 2**53


class CanonError(ValueError):
    """A document cannot be turned into, or read as, follow-canon-v1."""


def _pairs(pairs):
    seen = set()
    out = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise CanonError("a JSON key is not a string")
        if key in seen:
            raise CanonError(f"duplicate JSON key {key!r}")
        seen.add(key)
        out[key] = value
    return out


def _require_nfc(value, path):
    if unicodedata.normalize("NFC", value) != value:
        raise CanonError(f"string is not NFC at {path}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise CanonError(f"string is not valid Unicode at {path} (lone surrogate)") from None


def _check(value, path="$"):
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        if not (-MAX_SAFE_INT <= value <= MAX_SAFE_INT):
            raise CanonError(f"integer outside the safe range at {path}")
        return
    if isinstance(value, float):
        raise CanonError(f"floats are not part of follow-canon-v1 (at {path})")
    if isinstance(value, str):
        _require_nfc(value, path)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonError(f"non-string JSON key at {path}")
            _require_nfc(key, f"{path}.<key>")
            _check(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check(item, f"{path}[{index}]")
        return
    raise CanonError(f"unsupported JSON value at {path}: {type(value).__name__}")


def _escape_string(value):
    out = ['"']
    for char in value:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif code < 0x20:
            out.append(f"\\u00{code:02x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _emit(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_emit(item) for item in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda pair: pair[0])
        return "{" + ",".join(_escape_string(key) + ":" + _emit(item) for key, item in items) + "}"
    raise CanonError(f"unsupported JSON value: {type(value).__name__}")


def dumps(value):
    """The canonical UTF-8 bytes of ``value`` (no trailing newline)."""
    _check(value)
    return _emit(value).encode("utf-8")


def loads(data):
    """Parse bytes/str and return the value, refusing duplicate keys, floats and non-NFC strings."""
    if isinstance(data, (bytes, bytearray)):
        try:
            data = bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            raise CanonError("input is not UTF-8") from None
    value = json.loads(data, object_pairs_hook=_pairs, parse_float=_reject_float, parse_constant=_reject_constant)
    _check(value)
    return value


def _reject_float(text):
    raise CanonError(f"floats are not part of follow-canon-v1: {text}")


def _reject_constant(text):
    raise CanonError(f"non-finite number is not allowed: {text}")


def sha256_canonical(value):
    return hashlib.sha256(dumps(value)).hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()
