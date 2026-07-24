"""Environment-driven configuration for the web service.

Deliberately separate from `bot.config`: the two processes share extraction code
but nothing operational. The bot needs a token, an access mode and ffmpeg
budgets; this needs a port and abuse limits, and would refuse to start if it had
to satisfy the bot's required TELEGRAM_BOT_TOKEN.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        log.warning("%s is not an integer, using default %d", name, default)
        return default


@dataclass(frozen=True)
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080

    # Token bucket per client IP. The burst allows a few pastes in quick
    # succession; the refill rate is what holds over a sustained flood.
    rate_burst: int = 10
    rate_per_minute: int = 30

    # Resolved tweets are cached in-process. A link doing the rounds otherwise
    # means every visitor's lookup hits X from this one server IP.
    resolve_ttl_s: int = 900
    resolve_cache_size: int = 2048

    # Sizing every rung of every ladder is one HEAD per variant. Capped so a
    # post with many videos can't fan out into dozens of upstream requests.
    max_head_requests: int = 12

    # Enable only when something in front of this actually sets the header.
    # Untrusted X-Forwarded-For is a free bypass of the per-IP rate limit.
    trust_proxy: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> WebConfig:
        return cls(
            host=os.environ.get("WEB_HOST", "0.0.0.0"),
            port=_int("WEB_PORT", 8080),
            trust_proxy=os.environ.get("WEB_TRUST_PROXY", "").strip().lower()
            in {"1", "true", "yes"},
            rate_burst=_int("WEB_RATE_BURST", 10),
            rate_per_minute=_int("WEB_RATE_PER_MINUTE", 30),
            resolve_ttl_s=_int("WEB_RESOLVE_TTL_S", 900),
            resolve_cache_size=_int("WEB_RESOLVE_CACHE_SIZE", 2048),
            max_head_requests=_int("WEB_MAX_HEAD_REQUESTS", 12),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )
