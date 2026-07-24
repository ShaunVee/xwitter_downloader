"""Platform-neutral extraction.

Everything that knows how to turn a link into a list of downloadable files, and
nothing that knows what happens to them afterwards. Both front ends depend on
this; it depends on neither.

    core/          link -> media, per platform
    web/           serves the aggregator site
    bot/           Telegram delivery for one platform

The dependency points one way on purpose. When the second platform ships its own
Telegram bot, that bot reuses `bot/`'s machinery and `core/`'s extraction without
either having to know the other exists.
"""
