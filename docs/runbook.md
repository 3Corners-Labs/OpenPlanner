# Runbook

Status: skeleton (work package 1). Hosting and backup ownership are open (HANDOVER O7);
sections marked *TBD* are completed in work package 9.

## Deploy

```sh
docker compose pull   # or: docker compose build
docker compose up -d
docker compose ps     # web and worker must report "healthy"
```

`web` applies pending Alembic migrations before it starts serving. The worker starts after
`web` is healthy.

## Verifying the image signature

Only deploy images signed by the `main` workflow of this repository:

```sh
cosign verify ghcr.io/<owner>/<repo>@sha256:<digest> \
  --certificate-identity-regexp '^https://github.com/<owner>/<repo>/.github/workflows/main.yml@refs/(heads/main|tags/v.*)$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Deploy by digest, not by tag.

## Health

| Check | Healthy | Meaning when unhealthy |
|---|---|---|
| `GET /healthz` | `200 {"status":"ok"}` | web process down or hung |
| `GET /readyz` | `200 {"status":"ok"}` | `503`: database unreachable; see `readiness.database_unavailable` in the log |
| worker container health | `healthy` | heartbeat older than 120 s: worker stuck or database unreachable (`worker.database_unavailable`) |

Logs are JSON on stdout: `docker compose logs -f web worker`.

`database_busy` errors mean a booking waited longer than 5 s for SQLite's write lock. At the
expected volume (peak 20 bookings/min) this indicates a stuck transaction or a slow disk, not
load: check for long-running admin exports or a full volume.

## Production settings

The image refuses to start in production (`ENVIRONMENT` defaults to `prod`) without
`MANAGEMENT_TOKEN_KEY` and an `https` `PUBLIC_BASE_URL`. Behind a reverse proxy also set:

- `FORWARDED_ALLOW_IPS=<proxy address>` (read by uvicorn), so rate limits see the visitor's IP
  instead of one shared proxy address. Without it, a single bot can exhaust the booking limit
  for everyone.
- A request body limit on the proxy (for example 64 KB for `/g/*` and `/b/*`).

## Mail

The worker sends mail when `SMTP_HOST` is set (`SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, `MAIL_FROM`). Failed deliveries are retried after 1, 5 and 15 minutes and then
marked `failed` in the `jobs` table (`last_error` holds the exception type). Without
`SMTP_HOST`, message jobs stay pending until mail is configured.

## Management token key

`MANAGEMENT_TOKEN_KEY` (ADR 0011) is required in production. Rotating it invalidates every
link in mails already sent; bookings are unaffected. Rotate only after an incident, and tell the
contact center that citizens may call about broken links.

## Rollback

Deploy the previous image tag. If the release contained a migration, first run
`docker compose run --rm web alembic downgrade <previous-revision>` with the *new* image, then
deploy the old image. Every migration is reversible (enforced in CI).

## Backup and restore

*TBD (O7).* Planned: Litestream or a daily `sqlite3 .backup`, encrypted, retention = PII term
+ 7 days (HANDOVER §7).

## Rotating secrets

*TBD.* All secrets come from environment variables / the secret store; none are baked into the
image.

## Webex Connect is down

*TBD (work package 6).*
