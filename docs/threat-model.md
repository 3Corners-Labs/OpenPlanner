# Threat model

STRIDE per flow, plus abuse cases per public endpoint. Every abuse case has a test.
Status: work package 7a.

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

## Dynamic scan (OWASP ZAP baseline, on merge to `main`)

ZAP scans the seeded booking page `/g/demo` and everything it links to. Accepted alerts are
listed with their reason in `.zap/rules.tsv`: non-storable content (deliberate `no-store`),
cookie poisoning and user-controllable attributes (only validated values are reflected), and
absent anti-CSRF tokens (Fetch Metadata is the CSRF defence, see above). Any other warning
fails the build.

## Cross-cutting

| Threat | Mitigation | Test |
|---|---|---|
| PII in logs | structlog processor masks PII fields, also for stdlib loggers; semgrep rule forbids PII fields in log calls | `tests/test_logging.py`, `.semgrep/rules.py` |
| Secrets in access logs (management tokens in paths) | uvicorn access log disabled | `test_uvicorn_logs_are_json_and_access_log_is_off` |
| Container escape / tampering | non-root uid 10001, read-only rootfs, `cap_drop: ALL`, `no-new-privileges` | CI `container` job |

## Flow: booking, cancelling, rescheduling (domain and persistence, WP2)

HTTP endpoints follow in WP3; the rules below are enforced in `app/domain/services.py` and
`app/adapters/db/uow.py`, so every entry point inherits them.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Tampering | Two citizens book the last place at the same time | `BEGIN IMMEDIATE` write lock around check and insert; unique `(group_id, start)` on slots | `tests/adapters/db/test_uow.py::test_concurrent_bookings_on_separate_connections` (fails without the lock), `test_slot_uniqueness_is_enforced_by_the_database` |
| Denial of service | Writers wait forever on the lock | `busy_timeout`, then `database_busy` (HTTP 503 in WP3); readers never wait in WAL mode | `test_lock_timeout_becomes_database_busy`, `test_reads_are_not_blocked_by_a_writer` |
| Elevation | Rescheduling into another group's slot | Reschedule takes only a start; the slot is looked up in the booking's own group | `tests/domain/test_services.py::test_reschedule_into_another_groups_slot_is_refused`, adapter equivalent |
| Spoofing | Guessing a management token | 32 random bytes; only SHA-256 stored; constant-time comparison; unknown and wrong tokens give the same `booking_not_found` | `tests/domain/test_tokens.py`, `test_unknown_tokens_are_not_found` |
| Information disclosure | PII in error messages or repr | `DomainError` carries a code and a field name only; contact fields and tokens are excluded from `repr` | `test_invalid_contact_names_the_field_only`, `test_booking_repr_hides_personal_data`, `test_receipt_repr_hides_the_token` |
| Tampering | Booking a time that is not offered (unaligned, closed day, past, beyond horizon) | Start must match a slot of the current schedule; notice and horizon checked; closing a day blocks new bookings in existing slots | `test_unbookable_starts`, `test_closing_a_day_stops_new_bookings_in_existing_slots` |
| Tampering | Cancelling or rescheduling after the task is in the WxCC queue, leaving an agent with a dead appointment | Booking is read-only from `change_deadline` (the group's `dispatch_lead_minutes` before the start, default 5, the dispatch moment): `booking_locked`; a group's minimum notice is never shorter than its dispatch lead | `test_booking_is_read_only_from_the_dispatch_moment`, `test_booking_can_change_until_just_before_the_deadline` |
| Repudiation / integrity | Partial writes when a reschedule fails | One transaction; any error rolls back the cancellation of the old booking | `test_failed_reschedule_rolls_back` |

## Flow: public booking pages (WP3)

Public, unauthenticated: `GET /g/{slug}`, `GET/POST /g/{slug}/book`, `GET /b/{token}`,
`GET/POST /b/{token}/cancel`, `GET/POST /b/{token}/reschedule`.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Denial of service | Slot exhaustion by bots | slowapi: 10 writes/IP/hour, 60 reads/IP/minute; honeypot field; minimal form | `tests/api/test_booking_pages.py::test_bookings_are_rate_limited`, `test_honeypot_is_refused` |
| Tampering (CSRF) | A third-party site posts a booking or cancellation | Fetch Metadata: `Sec-Fetch-Site` must be same-origin/none; without it `Origin` must match. `Referrer-Policy: same-origin` (with `no-referrer` browsers send `Origin: null` on form posts) | `test_cross_site_posts_are_refused`, `test_same_origin_posts_are_accepted` |
| Information disclosure | PII through error responses | Error summary and error pages use catalogue texts only; submitted values are only re-filled into the visitor's own form | `test_invalid_input_is_explained_and_kept`, `test_honeypot_is_refused` |
| Information disclosure | Leaked management link shows personal data | The management page shows date, team and status only, never name, email or phone; old links of moved bookings show only "cancelled" | `test_full_booking_flow`, `test_reschedule_flow` |
| Spoofing | Token guessing at `/b/{token}` | 256-bit tokens (HMAC, ADR 0011); lookup by SHA-256, constant-time compare; same 404 for every unknown token; rate-limited | `test_unknown_token_is_a_404_page`, `tests/domain/test_tokens.py` |
| Tampering | Changing a booking after the WxCC task is dispatched | Read-only from the change deadline; cancel/reschedule pages redirect, POSTs return 409 | `test_booking_is_read_only_from_the_dispatch_moment` |
| Tampering / information disclosure | Cross-origin windows or resources attach to our pages; pages use browser features they do not need | `Permissions-Policy` (camera, microphone, location … off), `Cross-Origin-Opener-Policy`, `-Embedder-Policy`, `-Resource-Policy` | `tests/api/test_health.py::test_security_headers_on_every_response`, ZAP baseline on `main` |
| Tampering (XSS) | Arbitrary query parameters reflected into links | The language switcher keeps only `week` and `start`; output is autoescaped | `tests/api/test_pages.py::test_switcher_keeps_only_known_query_parameters` |
| Information disclosure | Management token leaks through Referer or logs | `Referrer-Policy: same-origin`; uvicorn access log off; rejections log origins only | `test_security_headers_on_every_response`, `test_uvicorn_logs_are_json_and_access_log_is_off` |

## Flow: outgoing email (WP3, worker)

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Information disclosure | PII in job payloads or logs | Jobs hold IDs only; the worker re-reads the booking; failures log the exception type | `tests/jobs/test_runner.py` |
| Tampering | Message sent for a moved or cancelled booking | Pinned jobs are skipped when the booking changed or was purged | `test_stale_jobs_send_nothing`, `tests/domain/test_job_plan.py::test_stale_jobs` |
| Injection | Group name or text with HTML in emails | Jinja2 autoescape for the HTML part; plain-text part separate | `tests/jobs/test_messages.py::test_html_is_escaped_and_semantic` |
| Information disclosure | Personal data in calendar files | `.ics` contains team, times and the management link only | `test_calendar_attachment` |

## Flow: admin portal login and authorisation (WP7a)

Admins only; citizens never log in. `GET /admin/login` → identity provider (PingFederate in
production, Keycloak in development, ADR 0013) → `GET /admin/callback` → session cookie
`op_admin`; `POST /admin/logout` → identity-provider logout → `GET /admin/logged-out`. Every
other `/admin` route declares one permission of the matrix (ADR 0014). Inputs from outside:
`code`, `state`, `next`, the ID token and its claims, the session cookie, path slugs.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Spoofing | Forged or replayed authorisation response | `state` compared in constant time and used once; PKCE S256; `nonce` in the ID token checked | `tests/admin/test_admin_flow.py::test_bad_state_or_missing_code_fails`, `test_a_login_can_be_completed_only_once`, `tests/adapters/test_oidc.py::test_nonce_mismatch_is_refused`, `test_full_flow_with_pkce` |
| Spoofing | Forged ID token: wrong key, `alg: none`, HS256 with the public key as secret | Only asymmetric algorithms from an allow-list; signature verified against the provider's JWKS; `iss`, `aud`, `azp`, `exp`, `iat`, `sub` required | `test_forged_signature_is_refused`, `test_unsafe_algorithms_are_refused`, `test_invalid_claims_are_refused` |
| Spoofing | Key rotation used to slip in an unknown key | Unknown `kid` triggers one JWKS refetch from the discovered URL, then refusal | `test_key_rotation_refetches_the_key_set` |
| Elevation | A claim grants rights it should not (organisation claim without the org-admin role, unknown group) | Rights come only from `decide()` on role × permission × scope; claims without the matching role or for unknown groups grant nothing | `tests/domain/test_authorization.py::test_decide`, `tests/domain/test_admin_service.py::test_claims_without_the_matching_role_give_no_context` |
| Elevation | An admin route without an authorisation check | Every admin route depends on exactly one `RequirePermission`; a test walks the router and the role × route × own/other-group table | `tests/admin/test_authorization_matrix.py::test_every_admin_route_declares_one_permission_and_is_in_the_table`, `test_matrix` |
| Elevation / information disclosure | A listing shows data of another organisation | Listing queries take a mandatory `Scope`, filtered in SQL; the scope and the per-object decision come from the same matrix | `tests/adapters/db/test_admin_queries.py::test_every_listing_query_requires_a_scope`, `tests/domain/test_authorization.py::test_database_scope_and_decision_never_disagree` |
| Spoofing | Session fixation or a stolen, long-lived session | Session cleared before login; signed cookie `HttpOnly; SameSite=Lax; Path=/admin` (`Secure` in production); absolute lifetime of 8 h enforced server-side; tampered cookie means logged out | `test_successful_login_starts_a_fresh_session_and_is_audited`, `test_session_has_an_absolute_lifetime`, `test_tampered_cookie_means_logged_out` |
| Spoofing | Silent re-login after logout through the identity provider's SSO session | RP-initiated logout at the provider's `end_session_endpoint`; an interstitial page keeps CSP `form-action 'self'` | `test_logout`, `tests/adapters/test_oidc.py::test_logout_url_uses_the_end_session_endpoint`, `e2e/tests/admin-roles.spec.ts` (outsider logs out, super must log in afresh) |
| Tampering (CSRF) | A third-party site logs an admin out | Logout is a POST with the Fetch Metadata check | `test_logout` |
| Spoofing (open redirect) | `next` sends the admin to another site after login | `next` must be a path under `/admin/` | `test_next_cannot_leave_the_admin_portal` |
| Repudiation | An admin denies a login, a refused attempt or a change | Logins, logouts, failed logins and denials go to `audit_events`, per organisation/group in the admin's scope; the audit log shows names from `admin_profiles` | `test_denials_are_audited`, `test_identity_errors_are_audited`, `test_audit_log_shows_names_and_logins_within_the_organisation` |
| Information disclosure | Personal data in logs or audit events | Logs and audit events carry the subject identifier only; the display name is stored once in `admin_profiles` and hidden from `repr` | `tests/domain/test_authorization.py::test_principal_repr_hides_the_display_name`, `tests/test_logging.py` |
| Elevation | A development shortcut reaches production (login bypass, role switcher, test users) | No bypass or switcher exists in code; test users exist only in the Keycloak dev realm; production requires an `https` discovery URL and a client secret | `tests/test_config.py::test_production_is_the_default_and_refuses_development_defaults`, `e2e/tests/admin-roles.spec.ts` logs in through the real provider |
