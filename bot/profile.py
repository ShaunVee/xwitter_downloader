"""The bot's public identity: the blurbs Telegram shows and the command menu.

All user-facing copy lives here rather than in whatever was last typed into
BotFather, and gets pushed on every startup, so the profile is reviewable,
diffable and survives a rebuild on a fresh host.

Two things the Bot API deliberately can't set, so BotFather keeps them:
the display name (rate-limited, changed rarely) and the profile photo (no
API method at all). Both are set by hand in BotFather.
"""

from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.error import TelegramError

log = logging.getLogger(__name__)

# Telegram's own limits. Enforced by tests so a copy edit fails locally
# instead of silently at startup.
SHORT_DESCRIPTION_LIMIT = 120
DESCRIPTION_LIMIT = 512
COMMAND_DESCRIPTION_LIMIT = 256

# The display name, set with BotFather /setname. Kept here so it's recorded somewhere.
NAME = "X Downloader"

# Profile page, under the photo. Also what Telegram shows when the bot is
# forwarded or shared as a link.
SHORT_DESCRIPTION = (
    "Send an X (Twitter) post link, get the video back as an mp4. "
    "Handles multi-video posts, GIFs, images and t.co links."
)

# The empty-chat screen, shown above the Start button before anyone taps it.
DESCRIPTION = (
    "Send me a link to an X (Twitter) post and I'll send the video back as an "
    "mp4, playable inline and saveable.\n\n"
    "• posts with several videos, in order\n"
    "• GIFs and images\n"
    "• t.co short links\n\n"
    "Videos over Telegram's 50 MB limit are compressed to fit automatically. "
    "Anything too long to compress without ruining it comes back as a direct "
    "download link instead. Links you've sent before return instantly."
)

# In-chat reply to /start and /help.
HELP = (
    "Send me a link to an X (Twitter) post and I'll send the video back as an "
    "mp4, playable inline and saveable.\n\n"
    "I also handle posts with several videos, GIFs, images and t.co short links.\n\n"
    "Videos over Telegram's 50 MB limit are compressed automatically; anything "
    "too long to compress comes back as a direct download link."
)

# The menu behind the "/" button. Only list commands that actually exist:
# a menu entry for a command with no handler is a dead end for the user.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "What this bot does"),
    ("help", "How to use it"),
)


async def apply(bot) -> None:
    """Push name-independent profile fields to Telegram.

    Never fatal: a transient API failure here shouldn't stop a bot that is
    otherwise ready to serve.
    """
    try:
        await bot.set_my_commands([BotCommand(name, desc) for name, desc in COMMANDS])
        await bot.set_my_short_description(SHORT_DESCRIPTION)
        await bot.set_my_description(DESCRIPTION)
        log.info("bot profile synced (%d commands)", len(COMMANDS))
    except TelegramError as exc:
        log.warning("could not sync bot profile: %s", exc)
