FROM python:3.12.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GARMIN_TOKEN_DIR=/data/garmin \
    SYNC_LOCK_FILE=/data/sync.lock \
    SYNC_SOURCE=garmin \
    SYNC_DESTINATION=google \
    ALERT_MODE=smtp

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir '.[live]' \
    && useradd --create-home --uid 10001 sync \
    && mkdir -p /data /run/secrets \
    && chown -R sync:sync /data /app

USER sync

VOLUME ["/data"]

ENTRYPOINT ["garmin-sheets-sync"]
CMD ["sync"]
