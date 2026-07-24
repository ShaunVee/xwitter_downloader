"""Fallback provider: the community-run FixTweet API.

    GET https://api.fxtwitter.com/x/status/<id>

Used only when syndication comes back empty. It earns its place: tweet
1349794411333394432 (a 324-second amplify_video) returns an empty mediaDetails
list from syndication but resolves correctly here.

Two limitations to keep in mind:
  - It returns a single mp4 URL per video, not a ladder, so it can't help us
    size-fit. Anything resolved this way may need transcoding.
  - Its reported width/height describe the *source*, not the file served. For
    tweet 1578401165338976258 it reports 1080x1080 while serving 720x720. We
    therefore treat these dimensions as advisory and ffprobe the real file
    before handing anything to Telegram.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.models import GIF, PHOTO, VIDEO, MediaItem, TweetMedia, Variant

log = logging.getLogger(__name__)

ENDPOINT = "https://api.fxtwitter.com/x/status/{tweet_id}"

_HEADERS = {"User-Agent": "justthefile/1.0", "Accept": "application/json"}


def parse(payload: dict[str, Any], tweet_id: str) -> TweetMedia:
    """Map a FixTweet payload into our model. Pure, so it's easy to test."""
    if payload.get("code") != 200:
        log.info("fxtwitter returned code %s for %s", payload.get("code"), tweet_id)
        return TweetMedia(tweet_id=tweet_id, source="fxtwitter")

    tweet = payload.get("tweet") or {}
    media = tweet.get("media") or {}
    items: list[MediaItem] = []

    for video in media.get("videos") or []:
        url = video.get("url")
        if not url:
            continue
        items.append(
            MediaItem(
                # FixTweet marks looping silent clips as "gif".
                kind=GIF if video.get("type") == "gif" else VIDEO,
                variants=(
                    Variant(
                        url=url,
                        width=video.get("width"),
                        height=video.get("height"),
                        content_type=video.get("format") or "video/mp4",
                    ),
                ),
                duration_s=video.get("duration"),
                width=video.get("width"),
                height=video.get("height"),
                thumbnail=video.get("thumbnail_url"),
            )
        )

    for photo in media.get("photos") or []:
        url = photo.get("url")
        if not url:
            continue
        items.append(
            MediaItem(
                kind=PHOTO,
                variants=(
                    Variant(url=f"{url}?name=orig", content_type="image/jpeg"),
                ),
                width=photo.get("width"),
                height=photo.get("height"),
            )
        )

    author = (tweet.get("author") or {}).get("screen_name")
    return TweetMedia(
        tweet_id=tweet_id,
        items=tuple(items),
        author=author,
        text=tweet.get("text"),
        source="fxtwitter",
    )


async def fetch(tweet_id: str, client: httpx.AsyncClient) -> TweetMedia:
    """Returns an empty TweetMedia on any miss, so the caller can report cleanly."""
    try:
        resp = await client.get(
            ENDPOINT.format(tweet_id=tweet_id), headers=_HEADERS, timeout=15.0
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError as exc:
        log.warning("fxtwitter fetch failed for %s: %s", tweet_id, exc)
        return TweetMedia(tweet_id=tweet_id, source="fxtwitter")
    except ValueError as exc:
        log.warning("fxtwitter returned non-JSON for %s: %s", tweet_id, exc)
        return TweetMedia(tweet_id=tweet_id, source="fxtwitter")

    return parse(payload, tweet_id)
