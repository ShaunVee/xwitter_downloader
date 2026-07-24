"""URL -> tweet ID.

Handles the hosts people actually paste: x.com, twitter.com, the mobile subdomain,
and the fxtwitter/vxtwitter/fixupx embed mirrors. Also resolves t.co shortlinks,
which is what you get when copying a link out of most other apps.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx

# Hosts we accept a /status/<id> path from. Leading "www." is stripped first.
_TWEET_HOSTS = {
    "x.com",
    "twitter.com",
    "mobile.x.com",
    "mobile.twitter.com",
    "fxtwitter.com",
    "vxtwitter.com",
    "fixupx.com",
    "fixvx.com",
    "twittpr.com",
    "nitter.net",
}

# /<user>/status/<id>, also matches /i/web/status/<id> and /statuses/<id>.
_STATUS_RE = re.compile(
    r"^/(?:[^/]+/)*status(?:es)?/(?P<id>\d{5,25})", re.IGNORECASE
)

# Bare tweet ID, for when someone pastes just the number.
_BARE_ID_RE = re.compile(r"^\d{5,25}$")

# First URL in a block of text.
_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)

_TCO_RE = re.compile(r"^https?://t\.co/[A-Za-z0-9]+", re.IGNORECASE)


def find_url(text: str) -> Optional[str]:
    """Pull the first URL out of a message, so captions and extra words are fine."""
    m = _URL_RE.search(text or "")
    return m.group(0) if m else None


def is_tco(url: str) -> bool:
    return bool(_TCO_RE.match(url or ""))


def tweet_id_from_url(url: str) -> Optional[str]:
    """Extract a tweet ID, or None if this isn't a recognizable X post URL."""
    if not url:
        return None

    raw = url.strip()
    if _BARE_ID_RE.match(raw):
        return raw

    if "//" not in raw:
        raw = "https://" + raw

    try:
        parsed = httpx.URL(raw)
    except (httpx.InvalidURL, ValueError, TypeError, UnicodeError):
        return None

    host = (parsed.host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _TWEET_HOSTS:
        return None

    m = _STATUS_RE.match(parsed.path or "")
    return m.group("id") if m else None


async def resolve_tco(url: str, client: httpx.AsyncClient) -> str:
    """Follow a t.co shortlink to its destination. Returns the input unchanged on failure."""
    try:
        resp = await client.get(url, follow_redirects=True, timeout=10.0)
    except httpx.HTTPError:
        return url
    return str(resp.url)


async def extract_tweet_id(text: str, client: httpx.AsyncClient) -> Optional[str]:
    """Full path from a raw message to a tweet ID, resolving t.co if needed."""
    if not text:
        return None

    candidate = find_url(text) or text.strip()

    tweet_id = tweet_id_from_url(candidate)
    if tweet_id:
        return tweet_id

    if is_tco(candidate):
        resolved = await resolve_tco(candidate, client)
        return tweet_id_from_url(resolved)

    return None
