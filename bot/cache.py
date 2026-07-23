"""Telegram file_id cache.

Once Telegram accepts an upload it hands back a `file_id`, and re-sending that id
costs zero egress and zero CPU — no download, no transcode, no upload. For one
user that's a nice speedup on a repeat link; the moment two people request the
same viral post it's the difference between one fetch and N.

SQLite because it needs to survive restarts and doesn't warrant a service.
Writes are small and infrequent, so the default locking is fine.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_ids (
    tweet_id    TEXT NOT NULL,
    media_index INTEGER NOT NULL,
    variant_url TEXT NOT NULL,
    kind        TEXT NOT NULL,
    file_id     TEXT NOT NULL,
    width       INTEGER,
    height      INTEGER,
    duration    REAL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (tweet_id, media_index, variant_url)
);
"""


class FileIdCache:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def get(
        self, tweet_id: str, media_index: int, variant_url: str
    ) -> Optional[dict]:
        if self._conn is None:
            return None
        async with self._lock:
            row = self._conn.execute(
                "SELECT file_id, kind, width, height, duration FROM file_ids "
                "WHERE tweet_id=? AND media_index=? AND variant_url=?",
                (tweet_id, media_index, variant_url),
            ).fetchone()
        if not row:
            return None
        log.info("cache hit for %s[%d]", tweet_id, media_index)
        return {
            "file_id": row[0],
            "kind": row[1],
            "width": row[2],
            "height": row[3],
            "duration": row[4],
        }

    async def put(
        self,
        tweet_id: str,
        media_index: int,
        variant_url: str,
        kind: str,
        file_id: str,
        *,
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        if self._conn is None:
            return
        async with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO file_ids "
                "(tweet_id, media_index, variant_url, kind, file_id, width, height, duration, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    tweet_id,
                    media_index,
                    variant_url,
                    kind,
                    file_id,
                    width,
                    height,
                    duration,
                    int(time.time()),
                ),
            )
            self._conn.commit()

    async def forget(self, tweet_id: str) -> None:
        """Drop a tweet's entries — used when Telegram rejects a stale file_id."""
        if self._conn is None:
            return
        async with self._lock:
            self._conn.execute("DELETE FROM file_ids WHERE tweet_id=?", (tweet_id,))
            self._conn.commit()
