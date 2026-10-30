# syntax=docker/dockerfile:1.7
# Base images are pinned by tag; Renovate pins and updates their digests.

FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

# The <gov-booking> Web Component (ADR 0035): bundled by esbuild here, never committed. Only
# the bundle reaches the runtime image; node and node_modules stay in this stage.
FROM node:22.20.0-bookworm-slim AS widget
WORKDIR /widget
COPY widget/package.json widget/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY widget/src ./src
RUN npm run build

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
# Release version (ADR 0029), set by main.yml on a v*.*.* tag; anything else is no release.
ARG APP_VERSION=""
# The git commit of the build (ADR 0052), set by main.yml on every image; empty locally.
ARG APP_COMMIT=""
# CVE-2026-84782 (OpenSSL, HIGH): the base tag still ships libssl3 3.0.22-1~deb12u1.
# Remove this layer once the python:3.12-slim-bookworm image carries deb12u2 or later.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libssl3=3.0.22-1~deb12u2 \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /srv
COPY --from=build /srv/.venv /srv/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY --from=widget /widget/dist/gov-booking.js ./widget/dist/gov-booking.js
# The manual is rendered in the portal (ADR 0033).
COPY docs/manual ./docs/manual
# The data volume (SQLite database, worker heartbeat) and the portal-certs volume (the own
# certificate handed to the proxy, ADR 0068) are the only writable paths.
RUN mkdir /srv/data /srv/portal-certs && chown app:app /srv/data /srv/portal-certs
# Summer and winter time come from the pinned `tzdata` package (uv.lock, Renovate), not from
# whatever the base image happens to ship: an empty PYTHONTZPATH makes zoneinfo use only it.
ENV APP_VERSION=${APP_VERSION} \
    APP_COMMIT=${APP_COMMIT} \
    PATH=/srv/.venv/bin:$PATH \
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
