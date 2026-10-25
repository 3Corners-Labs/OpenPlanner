# syntax=docker/dockerfile:1.7
# Base images are pinned by tag; Renovate pins and updates their digests.

FROM ghcr.io/astral-sh/uv:0.13.0 AS uv

FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/srv/.venv
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /srv
COPY --from=build /srv/.venv /srv/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
# The data volume (SQLite database, worker heartbeat) is the only writable path.
RUN mkdir /srv/data && chown app:app /srv/data
# Summer and winter time come from the pinned `tzdata` package (uv.lock, Renovate), not from
# whatever the base image happens to ship: an empty PYTHONTZPATH makes zoneinfo use only it.
ENV PATH=/srv/.venv/bin:$PATH \
    PYTHONPATH=/srv \
    PYTHONTZPATH="" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
VOLUME ["/srv/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
# Access logs are off: request paths will carry management tokens (/b/{token}).
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
