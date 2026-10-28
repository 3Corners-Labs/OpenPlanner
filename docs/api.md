# API

Generated from the OpenAPI spec (`GET /openapi.json`). Status: work package 1 — only the health
endpoints exist; `/api/v1` arrives in work package 3. Breaking changes to `/api/v1` need an ADR.

The machine API for contact-center bots (`/api/agent/v1`, ADR 0023) has its own spec at
`/api/agent/v1/openapi.json` (only while the superuser publishes it); see `docs/agent-api.md`.

| Method | Path | Response |
|---|---|---|
| GET | `/healthz` | `200 {"status": "ok"}` |
| GET | `/readyz` | `200 {"status": "ok"}`, `200 {"status": "degraded"}` (data volume short of space, ADR 0040) or `503 {"status": "unavailable"}` |
