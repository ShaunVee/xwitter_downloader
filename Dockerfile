# Architecture-neutral: the base image and Debian's ffmpeg are both multi-arch,
# so this builds natively on x86_64 and arm64 alike. Add
# `--platform linux/arm64` only if you need to cross-build for an ARM host.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg + ffprobe are required to compress oversized videos and to read the
# true dimensions off a file before handing it to Telegram.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# One image, two entrypoints: the bot and the web front end share the whole
# extraction pipeline, so building them separately would only duplicate it.
COPY core/ ./core/
COPY bot/ ./bot/
COPY web/ ./web/

# Run unprivileged. /data holds the file_id cache and must outlive the container.
RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /data /tmp/xdl \
    && chown -R botuser:botuser /data /tmp/xdl /app
USER botuser

VOLUME ["/data"]

# The web service overrides this and publishes a port; the bot needs neither,
# since long-polling is outbound-only.
CMD ["python", "-m", "bot.main"]
