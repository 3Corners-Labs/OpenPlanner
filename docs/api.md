# API

Generated from the OpenAPI spec (`GET /openapi.json`). Status: work package 1 — only the health
endpoints exist; `/api/v1` arrives in work package 3. Breaking changes to `/api/v1` need an ADR.

| Method | Path | Response |
|---|---|---|
| GET | `/healthz` | `200 {"status": "ok"}` |
| GET | `/readyz` | `200 {"status": "ok"}` or `503 {"status": "unavailable"}` |
