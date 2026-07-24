"""Per-IP token bucket.

The bot could key limits on a Telegram user ID, which is stable and costly to
forge. A public endpoint has no such handle, so IP is the only thing left,
imperfect (shared NAT throttles a whole office; a rotating pool evades it
entirely) but enough to stop one client hammering X through us, which is the
failure that would actually get our server IP blocked upstream.

Buckets are held in memory: the state is worth less than the round trip to a
store, and a restart handing out fresh allowances costs nothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated: float = field(default_factory=time.monotonic)


class RateLimiter:
    def __init__(self, burst: int, per_minute: int) -> None:
        self._burst = float(max(1, burst))
        self._refill_per_s = max(1, per_minute) / 60.0
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str) -> bool:
        """Spend one token. False when the caller is over their allowance."""
        now = time.monotonic()
        bucket = self._buckets.get(key)

        if bucket is None:
            self._sweep(now)
            # `updated` must be the same `now` measured above, not a fresh
            # reading: a later timestamp makes the first elapsed negative, which
            # docks a fraction of a token and rejects the caller's opening
            # request.
            bucket = _Bucket(tokens=self._burst, updated=now)
            self._buckets[key] = bucket

        elapsed = now - bucket.updated
        bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._refill_per_s)
        bucket.updated = now

        if bucket.tokens < 1.0:
            return False

        bucket.tokens -= 1.0
        return True

    def retry_after(self, key: str) -> int:
        """Whole seconds until the next token, for the Retry-After header."""
        bucket = self._buckets.get(key)
        if bucket is None or bucket.tokens >= 1.0:
            return 1
        return max(1, int((1.0 - bucket.tokens) / self._refill_per_s) + 1)

    def _sweep(self, now: float) -> None:
        """Drop buckets that have refilled to full: they carry no information."""
        full_after = self._burst / self._refill_per_s
        stale = [k for k, b in self._buckets.items() if now - b.updated > full_after]
        for key in stale:
            del self._buckets[key]
