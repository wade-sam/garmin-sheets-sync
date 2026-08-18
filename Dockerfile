FROM python:3.12.11-slim

ARG APP_VERSION=unknown

COPY --from=ghcr.io/astral-sh/uv:0.8.3 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    TZ=Europe/London \
    APP_VERSION=${APP_VERSION} \
    GARMIN_TOKEN_DIR=/data/garmin \
    SYNC_LOCK_FILE=/data/sync.lock \
    SYNC_SOURCE=garmin \
    SYNC_DESTINATION=onedrive \
    ALERT_MODE=smtp

WORKDIR /app

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra live --no-install-project

COPY src ./src

RUN uv sync --frozen --no-dev --extra live \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /data /run/secrets \
    && chown -R app:app /data

USER app

VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os; os.kill(1, 0)"]

ENTRYPOINT ["garmin-sheets-sync"]
CMD ["worker"]
