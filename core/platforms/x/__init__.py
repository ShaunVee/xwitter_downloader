"""X (Twitter).

A thin adapter, not an implementation: the extraction lives in `bot/` and is
shared verbatim with the Telegram bot. All this does is present it through the
platform contract, so X stops being the thing the web layer is built around and
becomes the first entry in a list.
"""

from __future__ import annotations

import httpx

from bot import providers, urls

from .base import Resolution

NAME = "x"
LABEL = "X (Twitter)"
HOSTS = ("x.com", "twitter.com", "t.co")


async def identify(url: str, client: httpx.AsyncClient) -> str | None:
    """Tweet ID, or None if this link isn't X's.

    Note this follows t.co shortlinks, which is a network call on the way to
    deciding whether we even handle the URL. Should another platform ever adopt
    its own shortener, resolution order in the registry starts to matter.
    """
    return await urls.extract_tweet_id(url, client)


async def fetch(post_id: str, client: httpx.AsyncClient) -> Resolution:
    tweet = await providers.resolve(post_id, client)
    return Resolution(
        platform=NAME,
        post_id=post_id,
        items=tweet.items,
        author=tweet.author,
        text=tweet.text,
        source=tweet.source,
    )
