"""Telegram bot entrypoint.

Long-polling, not webhooks: polling is outbound-only, so there's no public
endpoint, no TLS certificate and no inbound firewall rule to maintain, which
removes most of the friction of running this on a cloud VM.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx
from telegram import InputMediaPhoto, Message, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core import select
from core.platforms.x import providers, urls

from . import profile, transcode
from .access import is_allowed
from .cache import FileIdCache
from .config import Config
from .jobs import Job, JobQueue, Limits, QueueFull
from core.models import GIF, PHOTO, VIDEO, MediaItem
from .profile import HELP

log = logging.getLogger(__name__)

MB = 1024 * 1024
STATUS_MIN_INTERVAL = 3.0  # seconds between status edits, to stay clear of flood limits


class Status:
    """A single status message, edited in place and throttled."""

    def __init__(self, message: Optional[Message]) -> None:
        self._message = message
        self._last_text = ""
        self._last_edit = 0.0

    async def set(self, text: str, *, force: bool = False) -> None:
        if self._message is None or text == self._last_text:
            return
        now = time.monotonic()
        if not force and (now - self._last_edit) < STATUS_MIN_INTERVAL:
            return
        self._last_text = text
        self._last_edit = now
        try:
            await self._message.edit_text(text)
        except BadRequest:
            pass  # message deleted, or edited to identical text
        except TelegramError as exc:
            log.debug("status edit failed: %s", exc)

    async def done(self) -> None:
        if self._message is None:
            return
        try:
            await self._message.delete()
        except TelegramError:
            pass


def _human(size: Optional[int]) -> str:
    if not size:
        return "?"
    return f"{size / MB:.1f} MB"


class Runtime:
    """Holds the long-lived objects and does the actual work of a job."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.cache = FileIdCache(cfg.cache_db)
        self.client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; xwitter-downloader/1.0)"},
        )
        self.tmp_root = Path(cfg.tmp_dir)

    async def aclose(self) -> None:
        await self.client.aclose()
        self.cache.close()

    async def handle_job(self, job: Job, limits: Limits) -> None:
        bot = job.payload["bot"]
        status = Status(job.payload.get("status"))
        workdir = Path(tempfile.mkdtemp(prefix=f"{job.tweet_id}-", dir=self._tmp_base()))

        try:
            await status.set("Fetching post…", force=True)
            tweet = await providers.resolve(job.tweet_id, self.client)

            if not tweet.items:
                await status.done()
                await bot.send_message(
                    job.chat_id,
                    "I couldn't find any video or images in that post. It may have "
                    "been deleted, be from a private account, or be age-restricted.",
                )
                return

            # Keep the original index: it's part of the cache key.
            playable = [
                (i, item) for i, item in enumerate(tweet.items) if item.kind != PHOTO
            ]
            for position, (index, item) in enumerate(playable, start=1):
                await self._deliver_video(
                    bot, job, item, index, status, limits, workdir,
                    total=len(playable), position=position,
                )

            if tweet.photos:
                await self._deliver_photos(bot, job, tweet.photos, status)

            await status.done()

        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _tmp_base(self) -> Path:
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        return self.tmp_root

    async def _deliver_video(
        self,
        bot,
        job: Job,
        item: MediaItem,
        index: int,
        status: Status,
        limits: Limits,
        workdir: Path,
        *,
        total: int,
        position: int,
    ) -> None:
        label = f" ({position}/{total})" if total > 1 else ""
        cap = self.cfg.max_upload_bytes

        selection = await select.pick_variant(item, self.client, cap)
        if selection is None:
            await bot.send_message(
                job.chat_id, f"No downloadable mp4 in that post{label}."
            )
            return

        # A cache hit skips download, transcode and upload entirely.
        cached = await self.cache.get(job.tweet_id, index, selection.variant.url)
        if cached:
            await status.set("Sending from cache…", force=True)
            try:
                await self._send_by_file_id(bot, job, item, cached)
                return
            except BadRequest as exc:
                log.info("stale file_id for %s: %s", job.tweet_id, exc)
                await self.cache.forget(job.tweet_id)

        await status.set(
            f"Downloading{label}… {_human(selection.size_bytes)}", force=True
        )
        await bot.send_chat_action(job.chat_id, ChatAction.UPLOAD_VIDEO)

        src = workdir / f"{index}-source.mp4"
        async with limits.downloads:
            async def progress(done: int, total_bytes: Optional[int]) -> None:
                if total_bytes:
                    pct = done * 100 // total_bytes
                    await status.set(f"Downloading{label}… {pct}%")

            # Allow a generous margin over the cap so a transcodable file still lands.
            ceiling = cap if not selection.needs_transcode else cap * 20
            await select.download(
                selection.variant.url, src, self.client,
                max_bytes=ceiling, progress=progress,
            )

        meta = await transcode.probe(src)
        upload = src

        if src.stat().st_size > cap or selection.needs_transcode:
            await status.set(f"Compressing{label}…", force=True)
            out = workdir / f"{index}-fit.mp4"
            try:
                async with limits.transcodes:
                    meta = await transcode.fit_to_size(
                        src, out, int(cap * 0.98), meta=meta
                    )
                upload = out
            except transcode.TranscodeNotWorthIt:
                await status.done()
                await bot.send_message(
                    job.chat_id,
                    f"That video is too long to compress under Telegram's 50 MB "
                    f"limit without ruining it. Direct download:\n"
                    f"{selection.variant.url}",
                    disable_web_page_preview=True,
                )
                return
            except transcode.TranscodeError as exc:
                log.warning("transcode failed for %s: %s", job.tweet_id, exc)
                await bot.send_message(
                    job.chat_id,
                    f"I couldn't compress that one. Direct download:\n"
                    f"{selection.variant.url}",
                    disable_web_page_preview=True,
                )
                return

        await status.set(f"Uploading{label}…", force=True)
        sent = await self._send_file(bot, job, item, upload, meta)

        file_id = self._file_id_of(sent)
        if file_id:
            await self.cache.put(
                job.tweet_id, index, selection.variant.url, item.kind, file_id,
                width=meta.width, height=meta.height, duration=meta.duration_s,
            )

    async def _send_file(self, bot, job: Job, item: MediaItem, path: Path, meta):
        with path.open("rb") as fh:
            if item.kind == GIF:
                return await bot.send_animation(
                    job.chat_id, animation=fh,
                    width=meta.width, height=meta.height, duration=_int(meta.duration_s),
                )
            return await bot.send_video(
                job.chat_id, video=fh,
                width=meta.width, height=meta.height, duration=_int(meta.duration_s),
                supports_streaming=True,
            )

    async def _send_by_file_id(self, bot, job: Job, item: MediaItem, cached: dict):
        if item.kind == GIF:
            return await bot.send_animation(job.chat_id, animation=cached["file_id"])
        return await bot.send_video(
            job.chat_id, video=cached["file_id"], supports_streaming=True
        )

    async def _deliver_photos(self, bot, job: Job, photos, status: Status) -> None:
        await status.set(f"Sending {len(photos)} image(s)…", force=True)
        urls_ = [p.variants[0].url for p in photos if p.variants]
        if not urls_:
            return
        if len(urls_) == 1:
            await bot.send_photo(job.chat_id, photo=urls_[0])
            return
        # Albums cap at 10 items.
        for start in range(0, len(urls_), 10):
            group = [InputMediaPhoto(u) for u in urls_[start : start + 10]]
            await bot.send_media_group(job.chat_id, media=group)

    @staticmethod
    def _file_id_of(message) -> Optional[str]:
        if message is None:
            return None
        if getattr(message, "video", None):
            return message.video.file_id
        if getattr(message, "animation", None):
            return message.animation.file_id
        return None


def _int(value: Optional[float]) -> Optional[int]:
    return int(value) if value else None


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not is_allowed(cfg, update.effective_user.id if update.effective_user else None):
        return
    await update.message.reply_text(HELP)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = context.application
    cfg: Config = app.bot_data["cfg"]
    runtime: Runtime = app.bot_data["runtime"]
    queue: JobQueue = app.bot_data["queue"]

    user = update.effective_user
    if not is_allowed(cfg, user.id if user else None):
        log.info("ignoring message from %s", user.id if user else "unknown")
        return

    message = update.message
    if message is None or not message.text:
        return

    tweet_id = await urls.extract_tweet_id(message.text, runtime.client)
    if not tweet_id:
        await message.reply_text(
            "That doesn't look like an X post link. Send something like "
            "https://x.com/user/status/1234567890"
        )
        return

    status = await message.reply_text("Queued…")
    job = Job(
        user_id=user.id,
        chat_id=message.chat_id,
        tweet_id=tweet_id,
        payload={"bot": app.bot, "status": status},
    )

    try:
        await queue.submit(job)
    except QueueFull as exc:
        await status.edit_text(str(exc))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("unhandled error", exc_info=context.error)


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #

def build_application(cfg: Config) -> Application:
    runtime = Runtime(cfg)
    runtime.cache.connect()

    limits = Limits.create(cfg.max_concurrent_downloads, cfg.max_concurrent_transcodes)
    queue = JobQueue(
        runtime.handle_job,
        limits,
        workers=max(cfg.max_concurrent_downloads, 1),
        max_per_user=cfg.max_queue_per_user,
    )

    async def post_init(app: Application) -> None:
        queue.start()
        await profile.apply(app.bot)
        me = await app.bot.get_me()
        log.info("running as @%s in %s mode", me.username, cfg.access_mode)

    async def post_shutdown(app: Application) -> None:
        await queue.stop()
        await runtime.aclose()

    app = (
        Application.builder()
        .token(cfg.bot_token)
        .rate_limiter(AIORateLimiter())
        # Uploads near 50 MB need far more than the default write timeout.
        .write_timeout(300)
        .read_timeout(120)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.bot_data.update({"cfg": cfg, "runtime": runtime, "queue": queue})
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    cfg = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not transcode.available():
        log.warning("ffmpeg/ffprobe not found: oversized videos cannot be compressed")

    build_application(cfg).run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
