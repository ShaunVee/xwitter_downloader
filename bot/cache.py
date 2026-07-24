"""Telegram file_id cache.

Once Telegram accepts an upload it hands back a `file_id`, and re-sending that id
costs zero egress and zero CPU: no download, no transcode, no upload. For one
user that's a nice speedup on a repeat link; the moment two people request the
same viral post it's the difference between one fetch and N.

SQLite because it needs to survive restarts and doesn't warrant a service.
Writes are small and infrequent, so the default locking is fine.

Keys are `platform:post_id`, never a bare id: post IDs are only unique within a
platform, and `abc123` is a plausible id on more than one of them. Each bot
still gets its own database file, so the namespace is belt and braces.
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
    post_key    TEXT NOT NULL,
    media_index INTEGER NOT NULL,
    variant_url TEXT NOT NULL,
    kind        TEXT NOT NULL,
    file_id     TEXT NOT NULL,
    width       INTEGER,
    height      INTEGER,
    duration    REAL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (post_key, media_index, variant_url)
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
        self._drop_pre_platform_table()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _drop_pre_platform_table(self) -> None:
        """Discard the X-only table this predates, if the file has one.

        The keys changed shape when the bot stopped being about tweets, and
        migrating them would mean guessing which platform each row came from.
        This is a cache: throwing it away costs one re-download per post and
        nothing else, so it's the cheaper answer than a real migration.
        """
        assert self._conn is not None
        columns = self._conn.execute("PRAGMA table_info(file_ids)").fetchall()
        if any(column[1] == "tweet_id" for column in columns):
            log.info("dropping pre-platform file_id cache; it will refill on use")
            self._conn.execute("DROP TABLE file_ids")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def get(
        self, post_key: str, media_index: int, variant_url: str
    ) -> Optional[dict]:
        if self._conn is None:
            return None
        async with self._lock:
            row = self._conn.execute(
                "SELECT file_id, kind, width, height, duration FROM file_ids "
                "WHERE post_key=? AND media_index=? AND variant_url=?",
                (post_key, media_index, variant_url),
            ).fetchone()
        if not row:
            return None
        log.info("cache hit for %s[%d]", post_key, media_index)
        return {
            "file_id": row[0],
            "kind": row[1],
            "width": row[2],
            "height": row[3],
            "duration": row[4],
        }

    async def put(
        self,
        post_key: str,
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
                "(post_key, media_index, variant_url, kind, file_id, width, height, duration, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    post_key,
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

    async def forget(self, post_key: str) -> None:
        """Drop a post's entries, used when Telegram rejects a stale file_id."""
        if self._conn is None:
            return
        async with self._lock:
            self._conn.execute("DELETE FROM file_ids WHERE post_key=?", (post_key,))
            self._conn.commit()
