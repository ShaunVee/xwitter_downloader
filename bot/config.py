"""Environment-driven configuration.

Everything that differs between "private bot on my Mac" and "public bot on a
server" lives here, so switching modes is an .env edit rather than a code change.

The same applies to *which platform* a bot serves. One process serves one
platform, so the X bot and the Reddit bot are the same image started with a
different PLATFORM: no per-platform entrypoint, no per-platform handler code.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

MB = 1024 * 1024

DEFAULT_PLATFORM = "x"


def _ids(raw: str) -> frozenset[int]:
    """Parse a comma-separated list of Telegram user IDs, ignoring junk."""
    out = set()
    for chunk in raw.replace(" ", "").split(","):
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            logging.warning("ignoring non-numeric user id in config: %r", chunk)
    return frozenset(out)


def _token(platform: str) -> str:
    """This platform's bot token.

    TELEGRAM_BOT_TOKEN_REDDIT and TELEGRAM_BOT_TOKEN_X, so both bots can read
    the same .env and still hold different tokens.

    The unsuffixed TELEGRAM_BOT_TOKEN is honoured only for the default platform.
    It is the spelling from when there was one bot, and that bot was X, so
    that's what it means. Letting *any* platform fall back to it would take a
    .env upgraded halfway and start the Reddit bot on X's token: two processes
    long-polling one bot, each seeing a random half of the messages, with
    nothing in the logs to say so. Refusing to start is the better failure.
    """
    suffixed = os.environ.get(f"TELEGRAM_BOT_TOKEN_{platform.upper()}", "").strip()
    if suffixed:
        return suffixed
    if platform == DEFAULT_PLATFORM:
        return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    return ""


@dataclass(frozen=True)
class Config:
    bot_token: str
    platform: str = DEFAULT_PLATFORM
    access_mode: str = "private"
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)
    blocked_user_ids: frozenset[int] = field(default_factory=frozenset)

    max_upload_mb: int = 49
    max_concurrent_downloads: int = 3
    max_concurrent_transcodes: int = 1
    max_queue_per_user: int = 5

    cache_db: str = "/data/cache.sqlite"
    tmp_dir: str = "/tmp/justthefile-bot"
    log_level: str = "INFO"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * MB

    @classmethod
    def from_env(cls) -> Config:
        # Imported here rather than at module scope: config is imported by the
        # tests that never touch a platform, and the registry pulls in httpx
        # and every provider behind it.
        from core.platforms import REGISTRY

        platform = os.environ.get("PLATFORM", DEFAULT_PLATFORM).strip().lower()
        known = [h.NAME for h in REGISTRY]
        if platform not in known:
            raise SystemExit(
                f"PLATFORM={platform!r} is not a registered platform. "
                f"Known: {', '.join(known)}."
            )

        token = _token(platform)
        if not token:
            raise SystemExit(
                f"No bot token for PLATFORM={platform}. Set "
                f"TELEGRAM_BOT_TOKEN_{platform.upper()} (or TELEGRAM_BOT_TOKEN) "
                "in the .env file next to docker-compose.yml: see the README's "
                "Quick start."
            )

        mode = os.environ.get("ACCESS_MODE", "private").strip().lower()
        if mode not in {"private", "allowlist", "public"}:
            raise SystemExit(
                f"ACCESS_MODE must be private, allowlist, or public (got {mode!r})"
            )

        allowed = _ids(os.environ.get("ALLOWED_USER_IDS", ""))
        if mode in {"private", "allowlist"} and not allowed:
            raise SystemExit(
                f"ACCESS_MODE={mode} but ALLOWED_USER_IDS is empty: the bot would "
                "ignore everyone, including you. Get your ID from @userinfobot."
            )

        def _int(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, default))
            except ValueError:
                logging.warning("%s is not an integer, using default %d", name, default)
                return default

        return cls(
            bot_token=token,
            platform=platform,
            access_mode=mode,
            allowed_user_ids=allowed,
            blocked_user_ids=_ids(os.environ.get("BLOCKED_USER_IDS", "")),
            max_upload_mb=_int("MAX_UPLOAD_MB", 49),
            max_concurrent_downloads=_int("MAX_CONCURRENT_DOWNLOADS", 3),
            max_concurrent_transcodes=_int("MAX_CONCURRENT_TRANSCODES", 1),
            max_queue_per_user=_int("MAX_QUEUE_PER_USER", 5),
            cache_db=os.environ.get("CACHE_DB", "/data/cache.sqlite"),
            tmp_dir=os.environ.get("TMP_DIR", "/tmp/justthefile-bot"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )
