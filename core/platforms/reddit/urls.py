"""URL -> Reddit post ID.

Reddit hands out more link shapes than X does, and people paste all of them:

    reddit.com/r/sub/comments/<id>/slug/     the canonical permalink
    reddit.com/comments/<id>                 what redd.it shortlinks expand to
    old.reddit.com/..., np.reddit.com/...    mirrors of the same paths
    redd.it/<id>                             shortlink, expands by redirect
    reddit.com/gallery/<id>                  what the share button gives for a
                                             multi-image post
    reddit.com/r/sub/s/<token>               what the mobile app's share sheet
                                             produces, and by far the most
                                             common form in the wild

The `/s/` form carries no post ID at all, only an opaque token, so it has to be
followed. That is the same problem t.co poses on the X side and is solved the
same way.

`/gallery/` is the odd one: a different path to the same post, so the ID in it
is the post's own and needs no redirect. Missing it meant a gallery link pasted
from the share button looked like no Reddit link at all.

The redirect is the only part of this module that can fail for reasons that
have nothing to do with the link. It says so, loudly, rather than returning
None: None means "not a Reddit link", and answering that to a perfectly good
share link sends someone off editing a URL that was never the problem.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from ..base import LinkUnresolved
from .headers import HEADERS

log = logging.getLogger(__name__)

_HOSTS = {
    "reddit.com",
    "old.reddit.com",
    "new.reddit.com",
    "np.reddit.com",
    "i.reddit.com",
    "m.reddit.com",
    "redd.it",
}

# Post IDs are base36. Six to eight characters covers everything Reddit has
# issued; the bound stops a subreddit name being mistaken for an ID.
_ID = r"[a-z0-9]{5,10}"

_COMMENTS_RE = re.compile(rf"^/(?:r/[^/]+/)?comments/(?P<id>{_ID})", re.IGNORECASE)
_GALLERY_RE = re.compile(rf"^/gallery/(?P<id>{_ID})/?$", re.IGNORECASE)
_SHORT_PATH_RE = re.compile(rf"^/(?P<id>{_ID})/?$", re.IGNORECASE)
_SHARE_RE = re.compile(r"^/r/[^/]+/s/[A-Za-z0-9]+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)


def find_url(text: str) -> Optional[str]:
    match = _URL_RE.search(text or "")
    return match.group(0) if match else None


def _host_of(url: str) -> tuple[str, httpx.URL]:
    raw = url.strip()
    if "//" not in raw:
        raw = "https://" + raw
    parsed = httpx.URL(raw)
    host = (parsed.host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host, parsed


def is_share_link(url: str) -> bool:
    """A /s/ link, which needs a redirect followed before it means anything."""
    try:
        host, parsed = _host_of(url or "")
    except (httpx.InvalidURL, ValueError, TypeError, UnicodeError):
        return False
    return host in _HOSTS and bool(_SHARE_RE.match(parsed.path or ""))


def post_id_from_url(url: str) -> Optional[str]:
    """Extract a post ID, or None if this isn't a recognizable Reddit post."""
    if not url:
        return None

    try:
        host, parsed = _host_of(url)
    except (httpx.InvalidURL, ValueError, TypeError, UnicodeError):
        return None

    if host not in _HOSTS:
        return None

    path = parsed.path or ""

    # redd.it/<id> puts the ID in the path root; reddit.com/<id> does not exist,
    # so only accept that shape on the shortener's own host.
    if host == "redd.it":
        match = _SHORT_PATH_RE.match(path)
        return match.group("id").lower() if match else None

    match = _COMMENTS_RE.match(path) or _GALLERY_RE.match(path)
    return match.group("id").lower() if match else None


async def resolve_share_link(url: str, client: httpx.AsyncClient) -> str:
    """Follow a /s/ or redd.it link to whatever it really points at.

    Sent with the same headers as every other Reddit-bound request. The hop
    used to inherit the caller's client headers instead, which is the one
    request in the codebase Reddit had a free hand to refuse.

    Raises LinkUnresolved rather than handing back the URL it was given: that
    return value parsed as no post at all and was indistinguishable from a link
    that was never Reddit's.
    """
    try:
        # Normalized first: a pasted link with no scheme is one httpx refuses
        # to send at all, and that is not Reddit failing to answer.
        _, parsed = _host_of(url)
    except (httpx.InvalidURL, ValueError, TypeError, UnicodeError) as exc:
        raise LinkUnresolved(url) from exc

    target = str(parsed)
    try:
        response = await client.get(
            target, headers=HEADERS, follow_redirects=True, timeout=10.0
        )
    except httpx.HTTPError as exc:
        log.warning("share link %s could not be followed: %s", target, exc)
        raise LinkUnresolved(url) from exc

    final = str(response.url)

    # Whether the destination holds a post is the parser's call, not this
    # function's: a share link can point at something that simply isn't one.
    # What matters here is that the hop happened. A refusal is a 403 on the
    # /s/ URL itself, which raises nothing and leaves the URL unchanged, and
    # re-parsing that looks exactly like a link from some other site.
    if response.is_success and final != target:
        log.info("share link %s -> %s", target, final)
        return final

    log.warning(
        "share link %s was not followed: %s (%d)", target, final, response.status_code
    )
    raise LinkUnresolved(url)


async def extract_post_id(text: str, client: httpx.AsyncClient) -> Optional[str]:
    """Full path from pasted text to a post ID, following shortlinks if needed.

    None means "not a Reddit post link". A share link that Reddit wouldn't
    follow raises LinkUnresolved instead: a different problem, and one the
    person who sent the link can do nothing about.
    """
    if not text:
        return None

    candidate = find_url(text) or text.strip()

    post_id = post_id_from_url(candidate)
    if post_id:
        return post_id

    # Only pay for a redirect on shapes that actually need one.
    if is_share_link(candidate):
        return post_id_from_url(await resolve_share_link(candidate, client))

    return None
