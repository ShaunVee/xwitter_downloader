"""Normalized media model.

Both providers get mapped into these types, so nothing downstream of
`providers/` ever has to know which API the data came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

VIDEO = "video"
GIF = "gif"
PHOTO = "photo"


@dataclass(frozen=True)
class Variant:
    """One downloadable rendition of a media item."""

    url: str
    bitrate: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    content_type: str = "video/mp4"

    @property
    def is_mp4(self) -> bool:
        return self.content_type == "video/mp4"

    def estimated_bytes(self, duration_s: Optional[float]) -> Optional[int]:
        """Fallback size estimate for when a HEAD request fails."""
        if not self.bitrate or not duration_s:
            return None
        return int(self.bitrate * duration_s / 8)


@dataclass(frozen=True)
class MediaItem:
    kind: str  # VIDEO | GIF | PHOTO
    variants: tuple[Variant, ...]
    duration_s: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    # Poster frame. Unused by the bot (Telegram generates its own), but the web
    # UI shows it as a preview before the user commits to a download.
    thumbnail: Optional[str] = None

    # Set when the platform serves audio as a separate file, which DASH sources
    # like Reddit do. The variants above are then silent video, and something
    # has to mux the two before a user gets a file worth having. X never sets
    # this, so nothing on that path changes.
    audio_url: Optional[str] = None

    @property
    def needs_mux(self) -> bool:
        return self.audio_url is not None

    @property
    def mp4_variants_best_first(self) -> list[Variant]:
        """mp4 renditions, highest bitrate first. Excludes HLS playlists."""
        return sorted(
            (v for v in self.variants if v.is_mp4),
            key=lambda v: (v.bitrate or 0, (v.width or 0) * (v.height or 0)),
            reverse=True,
        )


@dataclass(frozen=True)
class TweetMedia:
    tweet_id: str
    items: tuple[MediaItem, ...] = field(default_factory=tuple)
    author: Optional[str] = None
    text: Optional[str] = None
    source: Optional[str] = None  # which provider resolved this

    @property
    def playable(self) -> list[MediaItem]:
        return [i for i in self.items if i.kind in (VIDEO, GIF)]

    @property
    def photos(self) -> list[MediaItem]:
        return [i for i in self.items if i.kind == PHOTO]

    def __bool__(self) -> bool:
        return bool(self.items)
