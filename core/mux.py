"""Join a separate audio track onto a video file.

DASH sources keep audio in its own file, so a Reddit video downloaded on its own
is silent. This puts the two back together.

Nothing is re-encoded. Both streams are copied into a new container, so the cost
is I/O rather than CPU and the output is bit-identical to what the CDN served.
That matters on a one vCPU box, where a real transcode would be the difference
between "a second" and "a minute".
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


class MuxError(RuntimeError):
    """ffmpeg could not join the two streams."""


def available() -> bool:
    return bool(shutil.which("ffmpeg"))


async def mux(video: Path, audio: Path, dest: Path, *, timeout: float = 120.0) -> Path:
    """Copy `video` and `audio` into `dest`. Returns dest."""
    if not available():
        raise MuxError("ffmpeg not found on PATH")

    dest.parent.mkdir(parents=True, exist_ok=True)

    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-nostdin",
        "-i", str(video),
        "-i", str(audio),
        # Take the first video and first audio stream explicitly: without this
        # ffmpeg's default mapping can drop one of them when either input has
        # more than one stream.
        "-map", "0:v:0", "-map", "1:a:0",
        "-c", "copy",
        # Put the index at the front so the file starts playing before it has
        # fully downloaded, which is what a browser preview needs.
        "-movflags", "+faststart",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        dest.unlink(missing_ok=True)
        raise MuxError(f"ffmpeg timed out after {timeout}s")

    if process.returncode != 0:
        dest.unlink(missing_ok=True)
        raise MuxError(
            f"ffmpeg exited {process.returncode}: "
            f"{stderr.decode(errors='replace')[-400:]}"
        )

    return dest
