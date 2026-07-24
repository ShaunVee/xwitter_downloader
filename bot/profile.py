"""The bot's public identity: the blurbs Telegram shows and the command menu.

All user-facing copy lives here rather than in whatever was last typed into
BotFather, and gets pushed on every startup, so the profile is reviewable,
diffable and survives a rebuild on a fresh host.

One bot serves one platform, so the copy is per-platform too: a Reddit user
should be told about `/s/` share links, not t.co. Each profile is a `Profile`
instance in PROFILES, keyed by the platform NAME in `core.platforms`, and the
bot picks its own out at startup. Adding a platform's bot is an entry here plus
`TELEGRAM_BOT` on the platform module.

Two things the Bot API deliberately can't set, so BotFather keeps them:
the display name (rate-limited, changed rarely) and the profile photo (no
API method at all). Both are set by hand in BotFather, and both are recorded
on the profile anyway so the intended values live in the repo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram import BotCommand
from telegram.error import TelegramError

log = logging.getLogger(__name__)

# Telegram's own limits. Enforced by tests so a copy edit fails locally
# instead of silently at startup.
SHORT_DESCRIPTION_LIMIT = 120
DESCRIPTION_LIMIT = 512
COMMAND_DESCRIPTION_LIMIT = 256

# The menu behind the "/" button. Shared: both bots answer the same two
# commands. Only list commands that actually exist, or a menu entry becomes a
# dead end for the user. There's a test for that.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "What this bot does"),
    ("help", "How to use it"),
)


@dataclass(frozen=True)
class Profile:
    """Everything a user sees before the bot does any work."""

    # BotFather /setname. Kept here so the intended value is recorded somewhere.
    name: str
    # BotFather /setuserpic, which has no API method at all. Repo-relative.
    photo: str
    # Profile page, under the photo. Also what Telegram shows when the bot is
    # forwarded or shared as a link.
    short_description: str
    # The empty-chat screen, shown above the Start button before anyone taps it.
    description: str
    # In-chat reply to /start and /help.
    help: str
    # Reply when someone sends text that isn't a link this bot handles. Naming
    # the shape of a good link is the whole job of this string.
    unknown_link: str
    commands: tuple[tuple[str, str], ...] = COMMANDS


X = Profile(
    name="X Media Downloader",
    photo="assets/logo.png",
    short_description=(
        "Send an X (Twitter) post link, get the video back as an mp4. "
        "Handles multi-video posts, GIFs, images and t.co links."
    ),
    description=(
        "Send me a link to an X (Twitter) post and I'll send the video back as "
        "an mp4, playable inline and saveable.\n\n"
        "• posts with several videos, in order\n"
        "• GIFs and images\n"
        "• t.co short links\n\n"
        "Videos over Telegram's 50 MB limit are compressed to fit automatically. "
        "Anything too long to compress without ruining it comes back as a direct "
        "download link instead. Links you've sent before return instantly."
    ),
    help=(
        "Send me a link to an X (Twitter) post and I'll send the video back as "
        "an mp4, playable inline and saveable.\n\n"
        "I also handle posts with several videos, GIFs, images and t.co short "
        "links.\n\n"
        "Videos over Telegram's 50 MB limit are compressed automatically; "
        "anything too long to compress comes back as a direct download link."
    ),
    unknown_link=(
        "That doesn't look like an X post link. Send something like\n"
        "https://x.com/user/status/1234567890"
    ),
)

# Sound is the headline, not a footnote: Reddit keeps audio in a separate DASH
# file, so the mute clip is what people are used to getting and the reason
# they're looking for another downloader.
REDDIT = Profile(
    name="Reddit Media Downloader",
    photo="assets/bot-reddit.png",
    short_description=(
        "Send a Reddit post link, get the video back as an mp4 — with sound. "
        "Galleries and share links work too."
    ),
    description=(
        "Send me a link to a Reddit post and I'll send the video back as an "
        "mp4, playable inline and saveable.\n\n"
        "• video with its audio track joined back on\n"
        "• image posts and galleries at full resolution\n"
        "• redd.it and /s/ share links from the app\n\n"
        "Reddit splits audio into a separate file, which is why so many "
        "downloaders hand back silent clips. Videos over Telegram's 50 MB limit "
        "are compressed to fit automatically. Links you've sent before return "
        "instantly."
    ),
    help=(
        "Send me a link to a Reddit post and I'll send the video back as an "
        "mp4, with the audio joined back on.\n\n"
        "Share links from the app (/s/…), redd.it short links, image posts and "
        "galleries all work.\n\n"
        "Videos over Telegram's 50 MB limit are compressed automatically; "
        "anything too long to compress comes back as a direct download link."
    ),
    unknown_link=(
        "That doesn't look like a Reddit post link. Send something like\n"
        "https://reddit.com/r/videos/comments/abc123/title\n"
        "A share link from the app (/s/…) or a redd.it link works too."
    ),
)

PROFILES: dict[str, Profile] = {"x": X, "reddit": REDDIT}


def for_platform(name: str) -> Profile:
    """The profile for a platform. Unknown platforms are a config error."""
    try:
        return PROFILES[name]
    except KeyError:
        raise SystemExit(
            f"No bot profile for platform {name!r}. Add one to bot/profile.py."
        ) from None


async def apply(bot, profile: Profile) -> None:
    """Push the name-independent profile fields to Telegram.

    Never fatal: a transient API failure here shouldn't stop a bot that is
    otherwise ready to serve.
    """
    try:
        await bot.set_my_commands(
            [BotCommand(name, desc) for name, desc in profile.commands]
        )
        await bot.set_my_short_description(profile.short_description)
        await bot.set_my_description(profile.description)
        log.info("bot profile synced (%d commands)", len(profile.commands))
    except TelegramError as exc:
        log.warning("could not sync bot profile: %s", exc)
