"""Bounded job queue.

Handlers never do the work themselves: they enqueue and return immediately,
and a fixed pool of workers drains the queue. That indirection is the whole
point: with inline work, a single user is fine but a handful of simultaneous
requests would run several ffmpeg processes at once and wedge a small VPS.

Downloads and transcodes get separate limits because they contend for different
resources: downloads are network-bound and can overlap freely, transcodes are
CPU-bound and should be near-serial on small hardware.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)


class QueueFull(RuntimeError):
    """Raised when a user already has the maximum number of queued jobs."""


@dataclass
class Job:
    user_id: int
    chat_id: int
    post_id: str
    payload: dict[str, Any] = field(default_factory=dict)


JobHandler = Callable[["Job", "Limits"], Awaitable[None]]


@dataclass
class Limits:
    """Shared concurrency gates, held for the lifetime of the app."""

    downloads: asyncio.Semaphore
    transcodes: asyncio.Semaphore

    @classmethod
    def create(cls, max_downloads: int, max_transcodes: int) -> Limits:
        return cls(
            downloads=asyncio.Semaphore(max(1, max_downloads)),
            transcodes=asyncio.Semaphore(max(1, max_transcodes)),
        )


class JobQueue:
    def __init__(
        self,
        handler: JobHandler,
        limits: Limits,
        *,
        workers: int = 2,
        max_per_user: int = 5,
    ) -> None:
        self._handler = handler
        self._limits = limits
        self._workers = max(1, workers)
        self._max_per_user = max(1, max_per_user)
        self._queue: asyncio.Queue[Optional[Job]] = asyncio.Queue()
        self._per_user: dict[int, int] = defaultdict(int)
        self._tasks: list[asyncio.Task] = []

    def depth_for(self, user_id: int) -> int:
        return self._per_user[user_id]

    async def submit(self, job: Job) -> int:
        """Enqueue a job. Returns queue position. Raises QueueFull past the per-user cap."""
        if self._per_user[job.user_id] >= self._max_per_user:
            raise QueueFull(
                f"you already have {self._max_per_user} requests queued: "
                "let those finish first"
            )
        self._per_user[job.user_id] += 1
        await self._queue.put(job)
        return self._queue.qsize()

    async def _worker(self, name: str) -> None:
        while True:
            job = await self._queue.get()
            if job is None:  # shutdown sentinel
                self._queue.task_done()
                return
            try:
                await self._handler(job, self._limits)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A crashing job must never take the worker down with it.
                log.exception("job failed (%s, post %s)", name, job.post_id)
            finally:
                self._per_user[job.user_id] = max(0, self._per_user[job.user_id] - 1)
                self._queue.task_done()

    def start(self) -> None:
        for i in range(self._workers):
            self._tasks.append(asyncio.create_task(self._worker(f"worker-{i}")))
        log.info("job queue started with %d worker(s)", self._workers)

    async def stop(self) -> None:
        for _ in self._tasks:
            await self._queue.put(None)
        for task in self._tasks:
            try:
                await asyncio.wait_for(task, timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        self._tasks.clear()
