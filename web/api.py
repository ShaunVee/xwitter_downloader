"""JSON API + static front end.

One endpoint does the work: paste a link, get back every downloadable rendition
with a direct CDN URL. The browser fetches the bytes itself, so this process
never touches media, which is what keeps it cheap enough to run beside the bot
on the same small VPS.

Nothing here names a platform. Which links are accepted, and how they resolve,
is entirely `web.platforms`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import platforms
from .cache import TTLCache
from .config import WebConfig
from .ratelimit import RateLimiter
from .serialize import describe

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def _client_ip(request: Request, trust_proxy: bool) -> str:
    """Caller identity for rate limiting.

    X-Forwarded-For is trusted only when configured, because anyone can send the
    header: honouring it unconditionally would let a single client mint a fresh
    bucket per request and walk straight through the limiter.
    """
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_app(cfg: WebConfig | None = None) -> FastAPI:
    cfg = cfg or WebConfig.from_env()
    limiter = RateLimiter(cfg.rate_burst, cfg.rate_per_minute)
    cache = TTLCache(cfg.resolve_ttl_s, cfg.resolve_cache_size)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; justthefile/1.0)"},
        )
        log.info("web front end ready on %s:%d", cfg.host, cfg.port)
        yield
        await app.state.client.aclose()

    app = FastAPI(
        title="justthefile",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/resolve")
    async def resolve(request: Request, url: str = Query(..., max_length=2048)):
        ip = _client_ip(request, cfg.trust_proxy)
        if not limiter.allow(ip):
            return JSONResponse(
                {"error": "Too many requests, give it a moment."},
                status_code=429,
                headers={"Retry-After": str(limiter.retry_after(ip))},
            )

        client: httpx.AsyncClient = request.app.state.client

        ref = await platforms.identify(url, client)
        if ref is None:
            return JSONResponse(
                {
                    "error": f"That link isn't from a site we handle yet. "
                    f"Right now: {platforms.labels()}."
                },
                status_code=400,
            )

        cached = cache.get(ref.cache_key)
        if cached is not None:
            return JSONResponse(cached, headers={"X-Cache": "hit"})

        try:
            resolution = await ref.fetch(client)
        except Exception:
            log.exception("resolve failed for %s", ref.cache_key)
            return JSONResponse(
                {"error": "Couldn't reach that site just now. Try again in a moment."},
                status_code=502,
            )

        if not resolution.items:
            return JSONResponse(
                {
                    "error": "No video or images in that post. It may be deleted, "
                    "private, or age-restricted."
                },
                status_code=404,
            )

        payload = await describe(resolution, client, max_heads=cfg.max_head_requests)
        if not payload["media"]:
            return JSONResponse(
                {"error": "Nothing downloadable in that post."}, status_code=404
            )

        cache.put(ref.cache_key, payload)
        return JSONResponse(payload, headers={"X-Cache": "miss"})

    @app.get("/api/platforms")
    async def supported() -> dict[str, object]:
        """Drives the "works with" line, so the page updates itself when a
        platform is added to the registry."""
        return {"platforms": platforms.supported()}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def main() -> None:
    import uvicorn

    cfg = WebConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, access_log=False)


if __name__ == "__main__":
    main()
