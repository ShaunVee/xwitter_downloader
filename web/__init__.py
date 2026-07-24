"""HTTP front end.

Shares `bot/`'s extraction pipeline (urls -> providers -> models) and adds
nothing to it. Everything below `providers.resolve()` in the bot: variant
fitting, ffmpeg, the job queue, the file_id cache: exists to satisfy Telegram's
50 MB upload cap and has no analogue here: the browser fetches media straight
from X's CDN, so this service only ever moves JSON.
"""
