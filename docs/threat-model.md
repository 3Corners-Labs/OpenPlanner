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

## Flow: public pages and language choice

Public, unauthenticated: `GET /` (more pages from WP3), `GET /static/*`. Inputs from outside:
`?lang=`, the `lang` cookie, `Accept-Language`.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Tampering / XSS | Script injected through `?lang=`, the cookie or `Accept-Language` | Values are normalised to a strict locale pattern and must match an enabled locale; anything else is ignored | `tests/i18n/test_negotiation.py`, `tests/api/test_pages.py::test_unsupported_choice_is_ignored` |
| Tampering / XSS | Markup in a translation or admin override | Jinja2 autoescape, `StrictUndefined`; translations are plain text | `tests/i18n/test_formatting.py` |
| Elevation / information disclosure | Format-string injection through an override (`{x.__class__}`) | Own placeholder formatter without attribute, index or conversion access; overrides must keep the source's placeholders | `test_format_has_no_attribute_index_or_conversion_access`, `test_override_must_keep_the_same_placeholders` |
| Spoofing (open redirect) | Language-switcher link pointing to another host through a crafted path | Switcher links are query-only relative URLs (`?lang=en`) | `test_switcher_links_never_contain_the_request_path` |
| Denial of service | Very long `Accept-Language` header | Parsed up to 4096 characters and 32 entries | `test_parse_ignores_absurdly_long_headers` |
| Information disclosure | Cached page served in the wrong language or to another visitor | `Vary: Accept-Language, Cookie`, `Cache-Control: no-store` | `test_html_pages_get_a_csp_allowing_own_stylesheets` |
| Tampering | Injected script or inline style on HTML pages | CSP without `script-src`; only `style-src 'self'`; no inline styles | same |

## Cross-cutting

| Threat | Mitigation | Test |
|---|---|---|
| PII in logs | structlog processor masks PII fields, also for stdlib loggers; semgrep rule forbids PII fields in log calls | `tests/test_logging.py`, `.semgrep/rules.py` |
| Secrets in access logs (management tokens in paths) | uvicorn access log disabled | `test_uvicorn_logs_are_json_and_access_log_is_off` |
| Container escape / tampering | non-root uid 10001, read-only rootfs, `cap_drop: ALL`, `no-new-privileges` | CI `container` job |

## Abuse cases (planned, from HANDOVER §12)

Slot exhaustion by bots, management-token guessing, rescheduling into another group's slot,
PII through error messages. Added with the endpoints in work packages 2–3.
