"""DASH manifest -> variant ladder.

v.redd.it publishes a DASHPlaylist.mpd next to every video listing each
rendition it encoded. Unlike Reddit's own API this needs no auth and has not
rate-limited across any of our testing, which makes it the reliable half of
Reddit extraction: the post page tells us *which* video, this tells us what
qualities exist.

The catch, and the reason Reddit cannot be delivered the way X is: audio lives
in its own AdaptationSet and its own file. A video Representation on its own is
silent.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from xml.etree import ElementTree

import httpx

from core.models import Variant

log = logging.getLogger(__name__)

_NS = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}

# PT17.066668S
_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?([\d.]+)S", re.IGNORECASE)


def manifest_url(video_id: str) -> str:
    return f"https://v.redd.it/{video_id}/DASHPlaylist.mpd"


def _duration(root: ElementTree.Element) -> Optional[float]:
    raw = root.get("mediaPresentationDuration") or ""
    match = _DURATION_RE.match(raw)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds)


def _base_url(rep: ElementTree.Element) -> Optional[str]:
    node = rep.find("mpd:BaseURL", _NS)
    return node.text.strip() if node is not None and node.text else None


def parse(xml: str, video_id: str) -> tuple[list[Variant], Optional[str], Optional[float]]:
    """Return (video variants best-first, best audio URL, duration).

    Pure, so it can be tested against a saved manifest without the network.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        log.warning("unparseable DASH manifest for %s: %s", video_id, exc)
        return [], None, None

    base = f"https://v.redd.it/{video_id}/"
    videos: list[Variant] = []
    audios: list[tuple[int, str]] = []

    for adaptation in root.iter(f"{{{_NS['mpd']}}}AdaptationSet"):
        # contentType is the modern attribute; older manifests only set
        # mimeType on the Representation, so fall back to that.
        kind = (adaptation.get("contentType") or "").lower()

        for rep in adaptation.findall("mpd:Representation", _NS):
            name = _base_url(rep)
            if not name:
                continue

            mime = (rep.get("mimeType") or "").lower()
            is_audio = kind == "audio" or mime.startswith("audio")
            try:
                bandwidth = int(rep.get("bandwidth") or 0)
            except ValueError:
                bandwidth = 0

            if is_audio:
                audios.append((bandwidth, base + name))
                continue

            def _int(attr: str) -> Optional[int]:
                raw = rep.get(attr)
                try:
                    return int(raw) if raw else None
                except ValueError:
                    return None

            videos.append(
                Variant(
                    url=base + name,
                    bitrate=bandwidth or None,
                    width=_int("width"),
                    height=_int("height"),
                    content_type="video/mp4",
                )
            )

    videos.sort(key=lambda v: (v.bitrate or 0, (v.width or 0) * (v.height or 0)), reverse=True)
    best_audio = max(audios, key=lambda pair: pair[0])[1] if audios else None
    return videos, best_audio, _duration(root)


async def fetch(video_id: str, client: httpx.AsyncClient):
    """Fetch and parse a manifest. Returns ([], None, None) on any failure."""
    try:
        response = await client.get(manifest_url(video_id), timeout=15.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("DASH manifest fetch failed for %s: %s", video_id, exc)
        return [], None, None

    return parse(response.text, video_id)
