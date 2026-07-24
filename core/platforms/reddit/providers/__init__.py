"""Provider chain for Reddit.

Reliability first, richness second: the HTML page answered every request across
testing while the JSON API returned 403 in bursts, so the scrape leads and the
API is the fallback. That is the opposite ordering to X, where the structured
endpoint is the dependable one.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from . import jsonapi, oldhtml

log = logging.getLogger(__name__)

CHAIN = (oldhtml, jsonapi)


async def resolve(post_id: str, client: httpx.AsyncClient) -> dict[str, Any]:
    """Try each provider until one returns media."""
    last: dict[str, Any] = {}

    for provider in CHAIN:
        result = await provider.fetch(post_id, client)
        if result.get("items"):
            log.info(
                "resolved %s via %s (%d item(s))",
                post_id, provider.__name__, len(result["items"]),
            )
            return {**result, "source": provider.__name__.rsplit(".", 1)[-1]}
        last = result or last
        log.info("%s had no media for %s, trying next", provider.__name__, post_id)

    return last
