# API

Generated from the OpenAPI spec (`GET /openapi.json`). Status: the health endpoints and the widget
API (`/api/v1/widget`, ADR 0035). Breaking changes to `/api/v1` need an ADR.

The machine API for contact-center bots (`/api/agent/v1`, ADR 0023) has its own spec at
`/api/agent/v1/openapi.json` (only while the superuser publishes it); see `docs/agent-api.md`.

| Method | Path | Response |
|---|---|---|
| GET | `/healthz` | `200 {"status": "ok"}` |
| GET | `/readyz` | `200 {"status": "ok"}`, `200 {"status": "degraded"}` (data volume short of space, ADR 0040) or `503 {"status": "unavailable"}` |

## Widget API (`/api/v1/widget`, ADR 0035)

Called by the `<gov-booking>` Web Component (`docs/widget.md`). The group must belong to
`{org}` and be switched on for embedding, else `404 {"error": "group_not_found"}`. A request
with an `Origin` must come from the organisation's allowed origins, else
`403 {"error": "origin_not_allowed"}`; a booking must carry one. Allowed origins get
`Access-Control-Allow-Origin` (no credentials). Every answer is `Cache-Control: no-store`,
`Vary: Origin`. Texts and labels follow `?lang=` (`nl`, `en`).

| Method | Path | Response |
|---|---|---|
| GET | `/api/v1/widget/texts` | `200` the texts for loading and "not available" (any origin, `Access-Control-Allow-Origin: *`) |
| GET | `/api/v1/widget/{org}/{group}/` | `200` name, intro, standalone page URL, closure notice, contact fields, honeypot name, texts |
| GET | `/api/v1/widget/{org}/{group}/availability?month=YYYY-MM&day=YYYY-MM-DD` | `200` month grid, free times of the day, first free moment |
| POST | `/api/v1/widget/{org}/{group}/bookings` | `201 {"status": "booked", "start", "end", "label", "message"}`; `400` honeypot, `409` slot taken or group closed, `413`, `422 invalid_request` or `invalid_contact` with `field`, `429 rate_limited` |
| OPTIONS | `/api/v1/widget/{org}/{group}/bookings` | `204` CORS preflight for allowed origins (not in the spec) |
