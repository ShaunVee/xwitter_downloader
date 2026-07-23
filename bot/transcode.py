"""ffprobe metadata and ffmpeg fit-to-size.

Two jobs:

1. `probe()` reads the *actual* dimensions and duration off the downloaded file.
   This is not optional politeness — the APIs lie. For tweet 1578401165338976258
   fxtwitter reports 1080x1080 while the file served is 720x720, and feeding
   Telegram the wrong dimensions makes its inline player render the video wrong.

2. `fit_to_size()` re-encodes to land under Telegram's cap, used only when no
   rendition on the variant ladder already fits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

AUDIO_BITRATE_KBPS = 128

# Below this, the re-encode is no longer worth watching, so we hand back a direct
# link instead of a smeared mess. Roughly: anything over ~45 min at a 48 MB cap.
MIN_VIDEO_BITRATE_KBPS = 150

# Encoding overhead fudge — container and muxing cost a little over the raw streams.
SIZE_SAFETY = 0.95


class TranscodeError(RuntimeError):
    pass


class TranscodeNotWorthIt(TranscodeError):
    """Target bitrate would be so low the result is unwatchable."""


@dataclass(frozen=True)
class Probe:
    width: Optional[int] = None
    height: Optional[int] = None
    duration_s: Optional[float] = None
    has_audio: bool = False


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


async def _run(*args: str) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout, stderr


async def probe(path: Path) -> Probe:
    """Read real dimensions/duration from the file. Never raises — returns blanks."""
    code, stdout, stderr = await _run(
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    )
    if code != 0:
        log.warning("ffprobe failed on %s: %s", path, stderr.decode(errors="replace")[:300])
        return Probe()

    try:
        payload = json.loads(stdout)
    except ValueError:
        return Probe()

    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    duration = None
    raw_duration = (payload.get("format") or {}).get("duration")
    if raw_duration is not None:
        try:
            duration = float(raw_duration)
        except ValueError:
            duration = None

    if not video:
        return Probe(duration_s=duration, has_audio=has_audio)

    return Probe(
        width=video.get("width"),
        height=video.get("height"),
        duration_s=duration,
        has_audio=has_audio,
    )


def target_video_bitrate_kbps(
    target_bytes: int, duration_s: float, has_audio: bool
) -> int:
    """Bitrate budget left for video after the audio track takes its share."""
    total_kbps = (target_bytes * 8 * SIZE_SAFETY) / duration_s / 1000
    video_kbps = total_kbps - (AUDIO_BITRATE_KBPS if has_audio else 0)
    return int(video_kbps)


def width_for_bitrate(video_kbps: int, ceiling: int = 1280) -> int:
    """Cap resolution to something the bitrate can actually support.

    Holding 720p at a few hundred kbps just spends the budget on macroblocks;
    downscaling first keeps the picture clean at the same file size.
    """
    for min_kbps, width in ((1500, 1280), (800, 960), (400, 640), (0, 480)):
        if video_kbps >= min_kbps:
            return min(width, ceiling)
    return min(480, ceiling)


async def fit_to_size(
    src: Path,
    dest: Path,
    target_bytes: int,
    *,
    meta: Optional[Probe] = None,
    max_width: int = 1280,
) -> Probe:
    """Re-encode `src` to land under `target_bytes`. Returns the output's metadata.

    Raises TranscodeNotWorthIt when the required bitrate is too low to bother.
    """
    if not available():
        raise TranscodeError("ffmpeg/ffprobe not found on PATH")

    info = meta or await probe(src)
    if not info.duration_s:
        raise TranscodeError("cannot size-target a file with unknown duration")

    video_kbps = target_video_bitrate_kbps(target_bytes, info.duration_s, info.has_audio)
    if video_kbps < MIN_VIDEO_BITRATE_KBPS:
        raise TranscodeNotWorthIt(
            f"would need {video_kbps} kbps for {info.duration_s:.0f}s of video"
        )

    width = width_for_bitrate(video_kbps, ceiling=max_width)

    args = [
        "ffmpeg", "-y", "-nostdin",
        "-i", str(src),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-b:v", f"{video_kbps}k",
        "-maxrate", f"{int(video_kbps * 1.2)}k",
        "-bufsize", f"{int(video_kbps * 2)}k",
        "-pix_fmt", "yuv420p",
        # Never upscale; -2 keeps height even, which libx264 requires.
        "-vf", f"scale='min({width},iw)':-2",
        # faststart puts the index up front so Telegram can stream without a full fetch.
        "-movflags", "+faststart",
    ]
    if info.has_audio:
        args += ["-c:a", "aac", "-b:a", f"{AUDIO_BITRATE_KBPS}k"]
    else:
        args += ["-an"]
    args.append(str(dest))

    log.info(
        "transcoding %s -> %d kbps video, max width %d (duration %.1fs)",
        src.name, video_kbps, width, info.duration_s,
    )

    code, _, stderr = await _run(*args)
    if code != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        raise TranscodeError(
            f"ffmpeg exited {code}: {stderr.decode(errors='replace')[-400:]}"
        )

    return await probe(dest)
