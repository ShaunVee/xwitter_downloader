"""Environment-driven configuration.

Everything that differs between "private bot on my Mac" and "public bot on a
server" lives here, so switching modes is an .env edit rather than a code change.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

MB = 1024 * 1024


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


@dataclass(frozen=True)
class Config:
    bot_token: str
    access_mode: str = "private"
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)
    blocked_user_ids: frozenset[int] = field(default_factory=frozenset)

    max_upload_mb: int = 49
    max_concurrent_downloads: int = 3
    max_concurrent_transcodes: int = 1
    max_queue_per_user: int = 5

    cache_db: str = "/data/cache.sqlite"
    tmp_dir: str = "/tmp/xdl"
    log_level: str = "INFO"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * MB

    @classmethod
    def from_env(cls) -> Config:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit(
                "TELEGRAM_BOT_TOKEN is not set. Put it in a .env file next to "
                "docker-compose.yml: see the README's Quick start."
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
            access_mode=mode,
            allowed_user_ids=allowed,
            blocked_user_ids=_ids(os.environ.get("BLOCKED_USER_IDS", "")),
            max_upload_mb=_int("MAX_UPLOAD_MB", 49),
            max_concurrent_downloads=_int("MAX_CONCURRENT_DOWNLOADS", 3),
            max_concurrent_transcodes=_int("MAX_CONCURRENT_TRANSCODES", 1),
            max_queue_per_user=_int("MAX_QUEUE_PER_USER", 5),
            cache_db=os.environ.get("CACHE_DB", "/data/cache.sqlite"),
            tmp_dir=os.environ.get("TMP_DIR", "/tmp/xdl"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )
