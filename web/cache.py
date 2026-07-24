"""TTL cache for resolved tweets.

The bot cached Telegram file_ids to skip re-uploading. Here the thing worth not
repeating is the upstream lookup: every visitor resolving the same link goes out
from this one server IP, so a link doing the rounds turns into thousands of
requests to cdn.syndication.twimg.com from a single address.

The TTL is short because media URLs are not permanent: X rotates the `tag`
query parameter and eventually expires paths, and a stale entry would hand the
browser a URL that 403s.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Optional


class TTLCache:
    def __init__(self, ttl_s: int, max_entries: int) -> None:
        self._ttl = max(1, ttl_s)
        self._max = max(1, max_entries)
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        entry = self._data.get(key)
        if entry is None:
            return None

        expires, value = entry
        if time.monotonic() >= expires:
            del self._data[key]
            return None

        self._data.move_to_end(key)
        return value

    def put(self, key: str, value: Any) -> None:
        self._data[key] = (time.monotonic() + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)  # evict least recently used
