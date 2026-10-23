# Threat model

STRIDE per flow, plus abuse cases per public endpoint. Every abuse case has a test.
Status: work package 1.

## Flow: health checks

Public, unauthenticated: `GET /healthz`, `GET /readyz`.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Information disclosure | Readiness error reveals database path, driver message or connection string | Fixed body `{"status":"unavailable"}`; only the exception type is logged | `tests/api/test_health.py::test_readiness_reports_unavailable_without_leaking_details` |
| Information disclosure | Interactive docs expose internals and load third-party scripts | Swagger UI / ReDoc disabled | `test_interactive_docs_are_disabled` |
| Denial of service | `/readyz` flooded to exhaust database connections | Cheap `SELECT 1` on a pooled connection; rate limiting at the reverse proxy *(TBD, O7)* | — |
| Tampering | Clickjacking / MIME sniffing on any response | Security headers middleware | `test_security_headers_on_every_response` |

## Cross-cutting

| Threat | Mitigation | Test |
|---|---|---|
| PII in logs | structlog processor masks PII fields, also for stdlib loggers; semgrep rule forbids PII fields in log calls | `tests/test_logging.py`, `.semgrep/rules.py` |
| Secrets in access logs (management tokens in paths) | uvicorn access log disabled | `test_uvicorn_logs_are_json_and_access_log_is_off` |
| Container escape / tampering | non-root uid 10001, read-only rootfs, `cap_drop: ALL`, `no-new-privileges` | CI `container` job |

## Abuse cases (planned, from HANDOVER §12)

Slot exhaustion by bots, management-token guessing, rescheduling into another group's slot,
PII through error messages. Added with the endpoints in work packages 2–3.
