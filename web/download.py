"""Server-side delivery, for media the browser is not allowed to fetch itself.

Two platforms' worth of reasons to exist:

  PROXY      i.redd.it sends no Access-Control-Allow-Origin, so a cross-origin
             fetch from the page is blocked outright. We stream it through.
  PROXY_MUX  Reddit keeps audio in a separate DASH file. The two get joined
             here, because a browser cannot mux.

**This endpoint takes indices, never URLs.** It re-derives every URL from our
own resolution of the post. Accepting a caller-supplied URL would turn the box
into an open proxy that fetches arbitrary hosts on request, which is worth more
to an attacker than anything else here. As a second line of defence the host is
checked against the platform's declared MEDIA_HOSTS before any fetch happens.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

from core import mux
from core.platforms import REGISTRY
from core.platforms.base import DIRECT, PROXY_MUX

log = logging.getLogger(__name__)

MB = 1024 * 1024


def platform_named(name: str):
    for handler in REGISTRY:
        if handler.NAME == name:
            return handler
    return None


def _host_allowed(url: str, handler) -> bool:
    """URL must sit on a host the platform itself declares as its media CDN."""
    allowed = getattr(handler, "MEDIA_HOSTS", ())
    try:
        host = (httpx.URL(url).host or "").lower()
    except (httpx.InvalidURL, ValueError, TypeError, UnicodeError):
        return False
    return any(host == a or host.endswith("." + a) for a in allowed)


def _disposition(filename: str) -> str:
    """Force a save, with a filename the browser will honour.

    The whole reason a user is here rather than right-clicking is to get a real
    name, so the header is the point of the endpoint rather than a detail.
    """
    safe = filename.replace('"', "").replace("\\", "").replace("\n", "")
    return f'attachment; filename="{safe}"'


async def _guard_size(url: str, client: httpx.AsyncClient, cap: int) -> Optional[int]:
    """Refuse before downloading when the CDN admits the file is too big."""
    try:
        response = await client.head(url, timeout=10.0, follow_redirects=True)
        raw = response.headers.get("content-length")
        size = int(raw) if raw else None
    except (httpx.HTTPError, ValueError):
        return None

    if size is not None and size > cap:
        raise ValueError(f"{size} bytes exceeds the {cap} byte cap")
    return size


async def stream_through(
    url: str, filename: str, client: httpx.AsyncClient, cap: int
) -> Any:
    """Pass a single file through, without buffering it in memory."""
    try:
        size = await _guard_size(url, client, cap)
    except ValueError as exc:
        log.info("refusing oversized proxy for %s: %s", url, exc)
        return JSONResponse(
            {"error": "That file is too large to fetch through the server."},
            status_code=413,
        )

    request = client.build_request("GET", url, timeout=60.0)
    response = await client.send(request, stream=True, follow_redirects=True)

    if response.status_code >= 400:
        await response.aclose()
        return JSONResponse(
            {"error": f"The source returned {response.status_code}."}, status_code=502
        )

    headers = {"Content-Disposition": _disposition(filename)}
    if size:
        headers["Content-Length"] = str(size)

    async def body():
        try:
            async for chunk in response.aiter_bytes(chunk_size=256 * 1024):
                yield chunk
        finally:
            await response.aclose()

    return StreamingResponse(
        body(),
        media_type=response.headers.get("content-type", "application/octet-stream"),
        headers=headers,
    )


async def mux_and_send(
    video_url: str,
    audio_url: str,
    filename: str,
    client: httpx.AsyncClient,
    *,
    cap: int,
    tmp_root: Path,
    gate: asyncio.Semaphore,
) -> Any:
    """Download both streams, join them, and hand back one file.

    Written to disk rather than piped: ffmpeg needs to seek both inputs to write
    a faststart index, and a fragmented-mp4 alternative plays badly in the
    desktop players people actually open these files in.
    """
    try:
        await _guard_size(video_url, client, cap)
    except ValueError as exc:
        log.info("refusing oversized mux: %s", exc)
        return JSONResponse(
            {"error": "That video is too large to process on the server."},
            status_code=413,
        )

    tmp_root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="mux-", dir=tmp_root))

    async def fetch(url: str, dest: Path) -> None:
        written = 0
        async with client.stream("GET", url, timeout=60.0, follow_redirects=True) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                async for chunk in r.aiter_bytes(chunk_size=256 * 1024):
                    written += len(chunk)
                    if written > cap:
                        raise ValueError("stream exceeded the size cap mid-download")
                    fh.write(chunk)

    try:
        video = workdir / "video.mp4"
        audio = workdir / "audio.mp4"
        # Sequential, not concurrent: two large streams at once on a 1 vCPU box
        # with limited bandwidth finish no sooner and compete for the same pipe.
        await fetch(video_url, video)
        await fetch(audio_url, audio)

        out = workdir / "out.mp4"
        # ffmpeg is the one genuinely contended resource here.
        async with gate:
            await mux.mux(video, audio, out)

    except (httpx.HTTPError, ValueError) as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        log.warning("mux download failed: %s", exc)
        return JSONResponse(
            {"error": "Couldn't fetch that video from Reddit just now."},
            status_code=502,
        )
    except mux.MuxError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        log.warning("mux failed: %s", exc)
        return JSONResponse(
            {"error": "Couldn't join the audio and video for that post."},
            status_code=500,
        )

    # The response streams from disk, so cleanup has to wait until it is sent.
    return FileResponse(
        out,
        media_type="video/mp4",
        headers={"Content-Disposition": _disposition(filename)},
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )


async def deliver(
    payload: dict[str, Any],
    handler,
    item_index: int,
    variant_index: int,
    client: httpx.AsyncClient,
    *,
    cap: int,
    tmp_root: Path,
    gate: asyncio.Semaphore,
) -> Any:
    """Route one item to the delivery mode its platform says it needs."""
    media = payload.get("media") or []
    if not 0 <= item_index < len(media):
        return JSONResponse({"error": "No such item in that post."}, status_code=404)

    item = media[item_index]
    variants = item.get("variants") or []
    if not 0 <= variant_index < len(variants):
        return JSONResponse({"error": "No such quality for that item."}, status_code=404)

    url = variants[variant_index]["url"]
    audio_url = item.get("audio_url")
    filename = item.get("filename") or "download"

    if not _host_allowed(url, handler):
        log.error("blocked proxy to unexpected host: %s", url)
        return JSONResponse({"error": "Refusing that URL."}, status_code=400)

    delivery = item.get("delivery", DIRECT)

    if delivery == PROXY_MUX and audio_url:
        if not _host_allowed(audio_url, handler):
            log.error("blocked mux audio from unexpected host: %s", audio_url)
            return JSONResponse({"error": "Refusing that URL."}, status_code=400)
        return await mux_and_send(
            url, audio_url, filename, client, cap=cap, tmp_root=tmp_root, gate=gate
        )

    if delivery == DIRECT:
        # Nothing to do server-side. The browser could have fetched this itself,
        # so send it there rather than paying for the bytes.
        return RedirectResponse(url, status_code=302)

    return await stream_through(url, filename, client, cap)
