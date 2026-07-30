FROM python:3.12.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes --no-deps -r requirements.lock

FROM python:3.12.11-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TMPDIR=/tmp

RUN groupadd --system --gid 10001 timestamp \
    && useradd --system --uid 10001 --gid timestamp --home-dir /nonexistent --shell /usr/sbin/nologin timestamp

WORKDIR /service
COPY --from=builder /opt/venv /opt/venv
COPY --chown=timestamp:timestamp app ./app
COPY --chown=timestamp:timestamp migrations ./migrations
COPY --chown=timestamp:timestamp scripts ./scripts
COPY --chown=timestamp:timestamp alembic.ini ./alembic.ini

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).read(1024)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "100", "--timeout-keep-alive", "5", "--no-access-log"]
