# SPDX-License-Identifier: MIT
"""One canonical JSON byte procedure for the offline fixture harness.

The same bytes must be produced on every machine, so the procedure is explicit: sorted keys, UTF-8, no insignificant
whitespace, no duplicate keys, no lone surrogates and no non-finite numbers. ``binding_sha256`` is over the exact
projection the frozen contract names (contract section 10.2/11); ``manifest_sha256`` is over the whole manifest file's
bytes (the second link, owned by the binding file). Raw fixture and artifact bytes are never normalised.
"""
from __future__ import annotations

import hashlib
import json
import math


class CanonicalError(ValueError):
    """A document cannot be turned into (or read as) the one canonical byte form."""


# Exactly the contract's binding projection. ``receipt`` and ``binding_sha256`` are deliberately absent: the receipt is
# drift data (started_at/duration/usage/limit_hit/status), and the digest cannot contain itself.
PROJECTION_FIELDS = (
    "manifest_schema_version",
    "binding_projection_version",
    "batch_id",
    "question_id",
    "bindings",
    "inputs",
    "config",
    "canonicalisation",
    "cases",
    "artifacts",
    "test_report",
    "licence",
)


def _pairs(pairs):
    seen = set()
    out = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise CanonicalError("a JSON key is not a string")
        if key in seen:
            raise CanonicalError(f"duplicate JSON key {key!r}")
        seen.add(key)
        out[key] = value
    return out


def _finite(value, path="$"):
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalError(f"non-finite number at {path}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, f"{path}[{index}]")
    elif value is None or isinstance(value, (str, int, bool)):
        return value
    else:
        raise CanonicalError(f"unsupported JSON value at {path}: {type(value).__name__}")
    return value


def _strings(value):
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise CanonicalError("a string is not valid Unicode (a lone surrogate cannot be encoded as UTF-8)") from None
    elif isinstance(value, dict):
        for key, item in value.items():
            _strings(key)
            _strings(item)
    elif isinstance(value, list):
        for item in value:
            _strings(item)


def loads_strict(raw) -> dict:
    """Parse one JSON object, refusing duplicate keys, lone surrogates and non-finite numbers."""
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise CanonicalError("the document is not UTF-8") from None
    elif isinstance(raw, str):
        text = raw
    else:
        raise CanonicalError("the document is neither bytes nor text")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(CanonicalError(f"non-finite constant {constant}")),
        )
    except CanonicalError:
        raise
    except ValueError:
        raise CanonicalError("the document is not strict JSON") from None
    if not isinstance(value, dict):
        raise CanonicalError("the document is not a JSON object")
    _finite(value)
    _strings(value)
    return value


def dumps_canonical(value) -> bytes:
    """The one canonical byte form: sorted keys, UTF-8, separators with no whitespace, finite numbers only."""
    _finite(value)
    _strings(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def canonical_bytes(value) -> bytes:
    return dumps_canonical(value)


def projection(manifest: dict) -> dict:
    """The exact fields that ``binding_sha256`` covers (contract 10.2/11)."""
    missing = [field for field in PROJECTION_FIELDS if field not in manifest]
    if missing:
        raise CanonicalError(f"the manifest lacks the projection field(s) {missing}")
    return {field: manifest[field] for field in PROJECTION_FIELDS}


def binding_sha256(manifest: dict) -> str:
    """A deterministic content binding: the same projection gives the same digest, whatever the receipt says."""
    return hashlib.sha256(dumps_canonical(projection(manifest))).hexdigest()


def manifest_sha256(raw) -> str:
    """The digest of the whole manifest file's exact bytes (no normalisation)."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha256(bytes(raw)).hexdigest()
