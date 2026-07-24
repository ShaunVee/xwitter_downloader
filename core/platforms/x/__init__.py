"""X (Twitter).

The extraction itself lives in `providers/` and `urls.py` beside this file.
This module is the platform contract over the top of them, and is the only
thing the registry or a bot needs to import.
"""

from __future__ import annotations

import httpx

from ..base import DIRECT, Resolution
from . import providers, urls

NAME = "x"
LABEL = "X (Twitter)"
HOSTS = ("x.com", "twitter.com", "t.co")

# The Telegram bot that serves this platform, if one is running. One bot per
# platform rather than one bot for all of them: each gets its own name, and a
# platform without a bot yet simply omits this.
TELEGRAM_BOT = "xwitter_downloader_bot"

# Hosts the download endpoint may fetch from. A safety net: every URL it
# handles already came from our own extraction, never from the caller.
MEDIA_HOSTS = ("video.twimg.com", "pbs.twimg.com")

# Measured, not assumed: video.twimg.com reflects arbitrary origins in
# Access-Control-Allow-Origin, so the browser fetches the file itself and this
# stays the one platform that costs us no bandwidth. It does hotlink-protect on
# Referer, which the front end handles with referrerPolicy: "no-referrer".
DELIVERY = DIRECT


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
