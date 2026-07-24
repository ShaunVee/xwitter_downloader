"""Primary provider: X's own syndication endpoint.

    GET https://cdn.syndication.twimg.com/tweet-result?id=<id>&token=<token>

This is the endpoint that powers embedded tweets. It needs no auth of any kind,
and crucially it returns the *full variant ladder*: every mp4 bitrate X encoded,
plus duration. That ladder is what lets us pick a rendition that fits under
Telegram's 50 MB cap instead of transcoding.

The `token` param is validated loosely: arbitrary values are accepted (verified
with `token=a`). We still send the value X's own embed script derives, since a
well-formed token is less likely to be affected if that ever tightens up.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Optional

import httpx

from core.models import GIF, PHOTO, VIDEO, MediaItem, TweetMedia, Variant

log = logging.getLogger(__name__)

ENDPOINT = "https://cdn.syndication.twimg.com/tweet-result"

# X's embed bundle uses a plain desktop UA; anything browser-shaped works.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _token(tweet_id: str) -> str:
    """Reimplementation of the token X's embed script derives from the tweet ID.

    ((id / 1e15) * pi) in base 36, with zeros and the decimal point stripped.
    """
    try:
        value = (int(tweet_id) / 1e15) * math.pi
    except (TypeError, ValueError):
        return "a"

    # float -> base 36, mirroring JS Number.prototype.toString(36).
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    whole = int(value)
    frac = value - whole

    out = ""
    if whole == 0:
        out = "0"
    while whole:
        whole, rem = divmod(whole, 36)
        out = digits[rem] + out

    frac_out = ""
    for _ in range(16):
        if frac <= 0:
            break
        frac *= 36
        digit = int(frac)
        frac_out += digits[digit]
        frac -= digit

    return (out + frac_out).replace("0", "") or "a"


def _photo_item(media: dict[str, Any]) -> Optional[MediaItem]:
    url = media.get("media_url_https")
    if not url:
        return None
    info = media.get("original_info") or {}
    # ?name=orig gives the full-resolution original rather than a display crop.
    return MediaItem(
        kind=PHOTO,
        variants=(Variant(url=f"{url}?name=orig", content_type="image/jpeg"),),
        width=info.get("width"),
        height=info.get("height"),
    )


def _video_item(media: dict[str, Any]) -> Optional[MediaItem]:
    info = media.get("video_info") or {}
    variants = []
    for raw in info.get("variants") or []:
        url = raw.get("url")
        if not url:
            continue
        variants.append(
            Variant(
                url=url,
                bitrate=raw.get("bitrate"),
                content_type=raw.get("content_type") or "video/mp4",
            )
        )
    if not variants:
        return None

    duration_ms = info.get("duration_millis")
    original = media.get("original_info") or {}

    return MediaItem(
        kind=GIF if media.get("type") == "animated_gif" else VIDEO,
        variants=tuple(variants),
        duration_s=(duration_ms / 1000.0) if duration_ms else None,
        width=original.get("width"),
        height=original.get("height"),
        thumbnail=media.get("media_url_https"),
    )


def _card_media(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull media entities out of a unified_card.

    Videos attached as cards (amplify_video, promoted clips, swipeable carousels)
    are absent from `mediaDetails` entirely, and instead live in
    `card.binding_values.unified_card`, as a JSON *string* that has to be decoded
    separately. Their entries carry the same shape as mediaDetails, including the
    full variant ladder, so parsing them here keeps size-fitting available for
    posts that would otherwise fall through to the ladder-less fallback provider.
    """
    raw = (
        ((payload.get("card") or {}).get("binding_values") or {})
        .get("unified_card", {})
        .get("string_value")
    )
    if not raw:
        return []

    try:
        card = json.loads(raw)
    except (ValueError, TypeError):
        log.warning("unified_card was not decodable JSON")
        return []

    # Dict order mirrors the card's own media order.
    return list((card.get("media_entities") or {}).values())


def parse(payload: dict[str, Any], tweet_id: str) -> TweetMedia:
    """Map a tweet-result payload into our model. Pure, so it's easy to test."""
    # Deleted/protected/age-gated posts come back as a tombstone rather than an error.
    if payload.get("__typename") not in (None, "Tweet"):
        log.info("syndication returned %s for %s", payload.get("__typename"), tweet_id)
        return TweetMedia(tweet_id=tweet_id, source="syndication")

    media_entries = list(payload.get("mediaDetails") or [])
    if not media_entries:
        media_entries = _card_media(payload)

    items: list[MediaItem] = []
    for media in media_entries:
        item = _photo_item(media) if media.get("type") == "photo" else _video_item(media)
        if item:
            items.append(item)

    user = payload.get("user") or {}
    return TweetMedia(
        tweet_id=tweet_id,
        items=tuple(items),
        author=user.get("screen_name"),
        text=payload.get("text"),
        source="syndication",
    )


async def fetch(tweet_id: str, client: httpx.AsyncClient) -> TweetMedia:
    """Returns an empty TweetMedia on any miss, so the caller can fall through."""
    try:
        resp = await client.get(
            ENDPOINT,
            params={"id": tweet_id, "token": _token(tweet_id), "lang": "en"},
            headers=_HEADERS,
            timeout=15.0,
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError as exc:
        log.warning("syndication fetch failed for %s: %s", tweet_id, exc)
        return TweetMedia(tweet_id=tweet_id, source="syndication")
    except ValueError as exc:
        log.warning("syndication returned non-JSON for %s: %s", tweet_id, exc)
        return TweetMedia(tweet_id=tweet_id, source="syndication")

    return parse(payload, tweet_id)
