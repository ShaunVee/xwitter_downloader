"""Access policy.

One gate function, driven by ACCESS_MODE. Handlers call `is_allowed()` and never
branch on the mode themselves, so opening the bot to everyone later is an .env
edit rather than a code change.
"""

from __future__ import annotations

from .config import Config


def is_allowed(cfg: Config, user_id: int | None) -> bool:
    if user_id is None:  # channel posts and similar have no user
        return False
    if user_id in cfg.blocked_user_ids:
        return False
    if cfg.access_mode == "public":
        return True
    return user_id in cfg.allowed_user_ids
