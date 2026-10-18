# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded process-local rolling-window limits for the scoring routes (023).

Keys are HMAC'd, so no raw IP or principal id is kept. Capacity is bounded and a
full table refuses NEW keys rather than evicting live counters (a flood cannot
reset anyone's limit). Expired windows are purged on every call.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import threading
from collections import deque


class RollingLimiter:
    def __init__(self, secret: bytes, *, clock, max_keys: int = 20_000):
        self._secret = secret
        self._clock = clock
        self._max_keys = max_keys
        self._events: dict[str, tuple[float, deque]] = {}  # key -> (window, events)
        self._lock = threading.Lock()

    def _key(self, bucket: str, value: str) -> str:
        return hmac.new(self._secret, b"scoring-limit:v1\0" + bucket.encode() + b"\0" + value.encode(),
                        hashlib.sha256).hexdigest()

    def consume(self, bucket: str, value: str, *, limit: int, window: float) -> tuple[bool, int]:
        """Record one event; return (allowed, retry_after_seconds)."""
        now = self._clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            return False, int(window)
        key = self._key(bucket, value)
        with self._lock:
            for k, (w, events) in tuple(self._events.items()):
                while events and events[0] <= now - w:
                    events.popleft()
                if not events:
                    del self._events[k]
            entry = self._events.get(key)
            if entry is None:
                if len(self._events) >= self._max_keys:
                    return False, int(window)  # bounded: refuse new keys, never evict live ones
                entry = (window, deque())
                self._events[key] = entry
            events = entry[1]
            if len(events) >= limit:
                return False, max(1, math.ceil(events[0] + window - now))
            events.append(float(now))
        return True, 0
