"""Platform registry.

Adding a platform is a new module in this package plus one entry in REGISTRY.
Nothing above this line (the API, the cache, the front end) needs to change,
because none of it names a platform.

Order matters only where two platforms could claim the same URL. Today nothing
overlaps, so the order is just the order they were added.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import ModuleType
from typing import Optional

import httpx

from . import x
from .base import Resolution

log = logging.getLogger(__name__)

REGISTRY: tuple[ModuleType, ...] = (x,)

__all__ = ["REGISTRY", "PostRef", "Resolution", "identify", "supported"]


@dataclass(frozen=True)
class PostRef:
    """A post pinned to the platform that recognised it."""

    platform: str
    post_id: str
    handler: ModuleType

    @property
    def cache_key(self) -> str:
        """Namespaced, because IDs are only unique within a platform."""
        return f"{self.platform}:{self.post_id}"

    async def fetch(self, client: httpx.AsyncClient) -> Resolution:
        return await self.handler.fetch(self.post_id, client)


async def identify(url: str, client: httpx.AsyncClient) -> Optional[PostRef]:
    """First platform that claims this URL, or None if nothing does.

    Split from fetching so the cache can be consulted on the post's identity
    without paying for the upstream lookup that produces its media.
    """
    for handler in REGISTRY:
        try:
            post_id = await handler.identify(url, client)
        except Exception:
            # One platform's parser blowing up must not make the whole site
            # reject a link another platform could have handled.
            log.exception("%s failed to identify a URL", handler.NAME)
            continue

        if post_id:
            return PostRef(platform=handler.NAME, post_id=post_id, handler=handler)

    return None


def supported() -> list[dict[str, object]]:
    """What the front end tells visitors it accepts."""
    return [
        {"name": h.NAME, "label": h.LABEL, "hosts": list(h.HOSTS)} for h in REGISTRY
    ]


def labels() -> str:
    """Human list for error copy: 'X (Twitter)', or 'A, B and C' later on."""
    names = [h.LABEL for h in REGISTRY]
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"
