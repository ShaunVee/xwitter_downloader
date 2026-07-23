"""Variant selection and download.

Telegram caps bot uploads at 50 MB, so before downloading anything we walk the
variant ladder from best quality down and pick the highest rendition that fits.
X exposes `content-length` on HEAD requests, so this costs three cheap round
trips instead of a wasted multi-hundred-megabyte download.

Only when nothing on the ladder fits do we fall through to transcoding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

import httpx

from .models import MediaItem, Variant

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, Optional[int]], Awaitable[None]]


@dataclass(frozen=True)
class Selection:
    variant: Variant
    size_bytes: Optional[int]
    needs_transcode: bool


async def head_size(url: str, client: httpx.AsyncClient) -> Optional[int]:
    """Content length via HEAD, or None if the CDN won't say."""
    try:
        resp = await client.head(url, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.debug("HEAD failed for %s: %s", url, exc)
        return None

    raw = resp.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def pick_variant(
    item: MediaItem, client: httpx.AsyncClient, max_bytes: int
) -> Optional[Selection]:
    """Best rendition that fits under `max_bytes`.

    Falls back to a size estimate from bitrate x duration when the CDN gives no
    content-length. If nothing fits, returns the *lowest* bitrate rendition
    flagged for transcoding: when compression has to be that aggressive, a
    higher-quality source buys almost nothing and costs a large download.
    """
    ladder = item.mp4_variants_best_first
    if not ladder:
        return None

    for variant in ladder:
        size = await head_size(variant.url, client)
        if size is None:
            size = variant.estimated_bytes(item.duration_s)
            if size is not None:
                log.debug("estimated %d bytes for %s", size, variant.url)

        # Unknown size: optimistically try it. The download guard enforces the cap.
        if size is None or size <= max_bytes:
            return Selection(variant=variant, size_bytes=size, needs_transcode=False)

    smallest = ladder[-1]
    log.info(
        "no rendition under %d bytes for a %ss item; will transcode",
        max_bytes,
        item.duration_s,
    )
    return Selection(
        variant=smallest,
        size_bytes=await head_size(smallest.url, client),
        needs_transcode=True,
    )


async def download(
    url: str,
    dest: Path,
    client: httpx.AsyncClient,
    *,
    max_bytes: Optional[int] = None,
    progress: Optional[ProgressCb] = None,
) -> int:
    """Stream a URL to disk. Returns bytes written.

    `max_bytes` is a hard ceiling that aborts mid-stream, so a CDN that lied
    about (or omitted) content-length can't fill the disk.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0

    try:
        async with client.stream("GET", url, timeout=60.0, follow_redirects=True) as resp:
            resp.raise_for_status()

            total: Optional[int] = None
            raw_total = resp.headers.get("content-length")
            if raw_total is not None:
                try:
                    total = int(raw_total)
                except ValueError:
                    total = None

            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise DownloadTooLarge(
                            f"exceeded {max_bytes} bytes while downloading {url}"
                        )
                    fh.write(chunk)
                    if progress:
                        await progress(written, total)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise

    return written


class DownloadTooLarge(RuntimeError):
    """Raised when a stream exceeds the byte ceiling."""
