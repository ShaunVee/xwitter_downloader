"""HTTP front end.

Serves `core/`'s extraction to a browser and adds nothing to it. The machinery
the Telegram bot needs below `providers.resolve()` (variant fitting, ffmpeg, the
job queue, the file_id cache) exists only to satisfy Telegram's 50 MB upload cap
and has no analogue here, because the browser fetches media straight from the
source CDN and this service only ever moves JSON.

That holds for as long as every platform is DELIVERY = DIRECT. TikTok's CDN
sends no CORS header and Reddit splits audio from video, so both will need the
bytes routed back through here when they land. See `core.platforms.base`.
"""
