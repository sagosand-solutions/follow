# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned alias blocklist: whole-word matching after a fixed, documented normalisation. No external service.

An alias is normalised (NFKC, case folding, a small look-alike and digit-for-letter fold), split into words at anything that
is not a letter or digit, and refused if any word equals a listed term. Matching whole words keeps innocent names such as
"Modern" or "Assistant" out of the net (the Scunthorpe problem); the price is that a term split by punctuation inside the word
is not caught. That is the stated limit: this is a courtesy filter, and moderators hide the rest (server/moderate.py).
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from .models import SetupError

BLOCKLIST_PATH = Path(__file__).resolve().parent / "blocklist" / "aliases-v1.json"
_TERM_RE = re.compile(r"[a-z][a-z0-9]{1,39}\Z")
MAX_BYTES = 64 * 1024

# Look-alikes of Latin letters from other scripts, and the digits and symbols commonly typed for letters. Applied after NFKC and
# case folding, so full-width and styled letters are already plain by then.
_FOLD = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d",   # Cyrillic
    "α": "a", "ε": "e", "ο": "o", "ρ": "p", "ι": "i", "ν": "v", "τ": "t", "κ": "k", "υ": "u",                      # Greek
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s",
})
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def normalise(text: str) -> list:
    folded = unicodedata.normalize("NFKC", text).casefold().translate(_FOLD)
    return _WORD.findall(folded)


@lru_cache(maxsize=1)
def load(path: str | None = None) -> frozenset:
    """The listed terms. A missing, oversized, malformed or unversioned file is a SetupError: the list never silently empties."""
    target = Path(path) if path else BLOCKLIST_PATH
    try:
        raw = target.read_bytes()
        if len(raw) > MAX_BYTES:
            raise SetupError("alias blocklist is too large")
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError("alias blocklist is missing or unreadable") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or set(data) - {"version", "comment", "terms"}:
        raise SetupError("alias blocklist has an unknown shape or version")
    terms = data.get("terms")
    if (not isinstance(terms, list) or not terms or len(set(terms)) != len(terms)
            or not all(isinstance(t, str) and _TERM_RE.fullmatch(t) for t in terms)):
        raise SetupError("alias blocklist terms are invalid")
    return frozenset(terms)


def is_blocked(alias: str, terms: frozenset | None = None) -> bool:
    return any(word in (terms if terms is not None else load()) for word in normalise(alias))
