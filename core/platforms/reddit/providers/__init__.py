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

from ...base import UpstreamRefused
from . import jsonapi, oldhtml

log = logging.getLogger(__name__)

CHAIN = (oldhtml, jsonapi)


async def resolve(post_id: str, client: httpx.AsyncClient) -> dict[str, Any]:
    """Try each provider until one returns media.

    A provider that was refused does not end the chain: the other one may be
    reachable, and while Reddit rate-limits the JSON in bursts that is the
    normal case rather than the exception.

    Only every source refusing is reported as a refusal. One of them being
    turned away proves nothing about the address: measured from an IP Reddit
    was perfectly happy with, the JSON endpoint 403'd and then served the same
    post a minute later. If any provider got a real answer, we were not walled,
    and an empty result is the post's own emptiness. Claiming a block there
    would trade one wrong reply for its mirror image: telling someone the site
    is blocking us when their post really is deleted.
    """
    last: dict[str, Any] = {}
    refusal: UpstreamRefused | None = None
    refusals = 0

    for provider in CHAIN:
        try:
            result = await provider.fetch(post_id, client)
        except UpstreamRefused as exc:
            refusal = exc
            refusals += 1
            continue

        if result.get("items"):
            log.info(
                "resolved %s via %s (%d item(s))",
                post_id, provider.__name__, len(result["items"]),
            )
            return {**result, "source": provider.__name__.rsplit(".", 1)[-1]}
        last = result or last
        log.info("%s had no media for %s, trying next", provider.__name__, post_id)

    if refusal is not None and refusals == len(CHAIN):
        raise refusal

    return last
