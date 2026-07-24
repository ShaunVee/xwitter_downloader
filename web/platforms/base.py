"""The contract every platform implements.

Lives in its own module so a handler can import `Resolution` without importing
the registry that imports the handler.

A platform is a plain module exposing four names:

    NAME     stable slug, used in cache keys and the API payload
    LABEL    what a human is told is supported
    HOSTS    domains for the "works with…" hint on the page
    identify(url, client)   -> post id, or None if this isn't ours
    fetch(post_id, client)  -> Resolution

`identify` is async because recognising a link can need the network: a t.co
shortlink says nothing about its destination until it is followed.

Returning None from `identify` means "not my platform" and lets the registry
try the next one. A `Resolution` with no items means "mine, but there's nothing
in it": a different answer, and a different HTTP status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from bot.models import MediaItem


@dataclass(frozen=True)
class Resolution:
    """One post's media, normalized and platform-neutral.

    Deliberately not `bot.models.TweetMedia`: that type is named for the one
    platform the Telegram bot serves, and the whole point of this layer is that
    nothing above it knows where a file came from. `MediaItem` and `Variant`
    are reused as-is: they were already neutral.
    """

    platform: str
    post_id: str
    items: tuple[MediaItem, ...] = field(default_factory=tuple)
    author: Optional[str] = None
    text: Optional[str] = None
    source: Optional[str] = None  # which upstream endpoint answered

    def __bool__(self) -> bool:
        return bool(self.items)
