# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded process-local rolling-window limits for email-start attempts."""
from __future__ import annotations

import hashlib
import hmac
import math
import threading
import unicodedata
import ipaddress
from collections import OrderedDict, deque


class EmailStartLimiter:
    def __init__(self, secret: bytes, *, clock, per_email: int = 5, per_ip: int = 20,
                 window_seconds: int = 3600, max_keys: int = 10_000):
        self._secret = secret
        self._clock = clock
        self._per_email = per_email
        self._per_ip = per_ip
        self._window = window_seconds
        self._max_keys = max_keys
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _key(self, purpose: bytes, value: str) -> str:
        msg = purpose + b"\0" + value.encode("utf-8")
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def consume(self, email: str, ip: str) -> tuple[bool, int]:
        now = self._clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            return False, self._window
        normalized_email = unicodedata.normalize("NFC", email).casefold()
        try:
            address = ipaddress.ip_address(ip)
            if address.version == 6 and address.ipv4_mapped is not None:
                address = address.ipv4_mapped
            if address.version == 6:
                network = ipaddress.ip_network(f"{address}/64", strict=False)
                normalized_ip = f"{network.network_address.compressed}/64"
            else:
                normalized_ip = address.compressed
        except ValueError:
            normalized_ip = ip
        keys = (self._key(b"email-start:email", normalized_email), self._key(b"email-start:ip", normalized_ip))
        limits = (self._per_email, self._per_ip)
        cutoff = now - self._window
        with self._lock:
            for key, events in tuple(self._events.items()):
                while events and events[0] <= cutoff:
                    events.popleft()
                if not events:
                    del self._events[key]
            current = [self._events.get(key, deque()) for key in keys]
            blocked = [(events, limit) for events, limit in zip(current, limits) if len(events) >= limit]
            if blocked:
                retry_after = max(1, math.ceil(max(events[0] + self._window for events, _ in blocked) - now))
                return False, retry_after
            for key, events in zip(keys, current):
                events.append(float(now))
                self._events[key] = events
                self._events.move_to_end(key)
            while len(self._events) > self._max_keys:
                self._events.popitem(last=False)
        return True, 0
