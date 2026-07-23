"""Provider chain.

syndication is primary because it returns the full variant ladder, which is what
makes size-fitting possible. fxtwitter is the fallback that catches posts
syndication silently drops. Neither alone covers every case.
"""

from __future__ import annotations

import logging

import httpx

from ..models import TweetMedia
from . import fxtwitter, syndication

log = logging.getLogger(__name__)

# Order matters: ladder-capable provider first.
CHAIN = (syndication, fxtwitter)


async def resolve(tweet_id: str, client: httpx.AsyncClient) -> TweetMedia:
    """Try each provider until one returns media.

    Returns an empty TweetMedia if every provider comes up dry — the caller
    distinguishes "no media in this post" from "extraction is broken" by
    checking whether the post exists at all.
    """
    last = TweetMedia(tweet_id=tweet_id)

    for provider in CHAIN:
        result = await provider.fetch(tweet_id, client)
        if result:
            log.info(
                "resolved %s via %s (%d item(s))",
                tweet_id,
                result.source,
                len(result.items),
            )
            return result
        last = result
        log.info("%s had no media for %s, trying next", provider.__name__, tweet_id)

    return last
