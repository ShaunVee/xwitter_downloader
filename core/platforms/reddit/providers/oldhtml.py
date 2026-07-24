"""Primary provider: the old.reddit.com post page.

Reddit's own JSON is the obvious source and it is not dependable. Across
testing it returned 403 in bursts after a handful of requests and then
recovered, which for a site serving many visitors from one VPS IP means
failures that look random. old.reddit.com answered every request in the same
runs, so the *reliable* source is the HTML and the *rich* source is the JSON.
This is the same split as X's syndication and fxtwitter pair.

The page is server-rendered and every field worth having sits in `data-`
attributes on the post's own div, so this is attribute lookup rather than
real HTML parsing. Those attributes have been stable for the decade old.reddit
has existed, but it is still scraping: `providers.resolve` falls through to the
JSON provider when anything here comes up empty.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx

from core.models import PHOTO, VIDEO, MediaItem, Variant

from .. import dash

log = logging.getLogger(__name__)

ENDPOINT = "https://old.reddit.com/comments/{post_id}/"

# Only User-Agent. Setting anything else, even Accept: */* which is the value
# httpx sends by default, reliably earns a 403: injecting a header changes the
# order they go out in, and Reddit fingerprints that order. Verified by sending
# the identical value both ways, with only the ordering differing.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

_THING_RE = re.compile(r'<div[^>]*\bid="thing_t3_[a-z0-9]+"[^>]*>', re.IGNORECASE)
_ATTR_RE = re.compile(r'data-([a-z-]+)="([^"]*)"', re.IGNORECASE)
_VREDDIT_RE = re.compile(r"^https?://v\.redd\.it/([a-z0-9]+)", re.IGNORECASE)
_IREDDIT_RE = re.compile(r"^https?://i\.redd\.it/([a-z0-9]+\.[a-z0-9]+)", re.IGNORECASE)
# Gallery images are rendered from preview.redd.it, but the same basename on
# i.redd.it is the untouched original rather than a resized, signed preview.
_PREVIEW_RE = re.compile(
    r"https://preview\.redd\.it/([a-z0-9]+\.(?:jpg|jpeg|png|webp))", re.IGNORECASE
)


def _attributes(html: str) -> dict[str, str]:
    match = _THING_RE.search(html)
    if not match:
        return {}
    return {k.lower(): v for k, v in _ATTR_RE.findall(match.group(0))}


def _photo(url: str) -> MediaItem:
    return MediaItem(
        kind=PHOTO,
        variants=(Variant(url=url, content_type="image/jpeg"),),
        thumbnail=url,
    )


def _gallery_items(html: str) -> list[MediaItem]:
    """Gallery images, deduplicated, in the order the page renders them."""
    seen: list[str] = []
    for name in _PREVIEW_RE.findall(html):
        if name not in seen:
            seen.append(name)
    return [_photo(f"https://i.redd.it/{name}") for name in seen]


async def _video_item(
    video_id: str, client: httpx.AsyncClient, thumbnail: Optional[str]
) -> Optional[MediaItem]:
    variants, audio_url, duration = await dash.fetch(video_id, client)
    if not variants:
        return None

    return MediaItem(
        kind=VIDEO,
        variants=tuple(variants),
        duration_s=duration,
        width=variants[0].width,
        height=variants[0].height,
        thumbnail=thumbnail,
        # None when the manifest carries no audio track, which is common on
        # Reddit and means the video file alone is already complete.
        audio_url=audio_url,
    )


async def parse(
    html: str, post_id: str, client: httpx.AsyncClient
) -> dict[str, Any]:
    """Map a post page into the pieces the platform module needs."""
    attrs = _attributes(html)
    if not attrs:
        log.info("no post div found for %s", post_id)
        return {}

    url = attrs.get("url") or ""
    author = attrs.get("author") or None
    items: list[MediaItem] = []

    video = _VREDDIT_RE.match(url)
    if video:
        item = await _video_item(video.group(1), client, _thumbnail(html))
        if item:
            items.append(item)
    elif attrs.get("is-gallery") == "true":
        items.extend(_gallery_items(html))
    else:
        photo = _IREDDIT_RE.match(url)
        if photo:
            items.append(_photo(f"https://i.redd.it/{photo.group(1)}"))

    return {"items": items, "author": author, "text": attrs.get("title") or None}


def _thumbnail(html: str) -> Optional[str]:
    match = _PREVIEW_RE.search(html)
    return f"https://preview.redd.it/{match.group(1)}" if match else None


async def fetch(post_id: str, client: httpx.AsyncClient) -> dict[str, Any]:
    """Returns {} on any miss, so the caller can fall through."""
    try:
        response = await client.get(
            ENDPOINT.format(post_id=post_id),
            headers=_HEADERS,
            timeout=15.0,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("old.reddit fetch failed for %s: %s", post_id, exc)
        return {}

    return await parse(response.text, post_id, client)
