# Threat model

STRIDE per flow, plus abuse cases per public endpoint. Every abuse case has a test.
Status: work packages 5 and 6 (video, WxCC, direct call).

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

## Flow: groups and planning in the admin portal (WP7b)

Admins only. `GET/POST /admin/groups/new`, `/admin/groups`, `/admin/groups/{slug}/settings`,
`/hours`, `/exceptions` and their `/delete` actions. Inputs from outside: form fields (names,
slug, time zone, numbers, times, dates, queue id), path slugs and item ids.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Elevation | A group admin changes routing or settings of their group, or anyone acts on another group | One `RequirePermission` per route (route level) and `PlanningService` authorises again (domain level); the matrix test covers every role × route × method × own/other group | `tests/admin/test_authorization_matrix.py::test_matrix`, `tests/domain/test_planning.py::test_group_admin_may_not_change_group_settings`, `test_schedule_of_another_group_is_out_of_reach` |
| Elevation | An org admin creates a group in another organisation by editing the form's organisation field | The chosen organisation is authorised in the domain (`group.create` at organisation level); the form only offers organisations in scope | `test_creating_outside_the_scope_is_refused_and_audited`, `tests/admin/test_group_routes.py::test_outsider_cannot_create_a_group_in_another_organisation` |
| Tampering (IDOR) | Removing another group's opening hours by guessing an item id under one's own group | Deletes are bound to the group (`WHERE id = ? AND group_id = ?`); a miss is `schedule_item_not_found` | `test_schedule_of_another_group_is_out_of_reach`, `tests/adapters/db/test_planning_queries.py::test_schedule_items_carry_ids_and_stay_within_their_group` |
| Tampering (CSRF) | A third-party site submits a change in an admin's browser | Fetch Metadata check on every POST; session cookie `SameSite=Lax` on `/admin` | `test_cross_site_changes_are_refused` |
| Elevation | A role revoked in the IdP keeps granting changes for the rest of the session | Changes and their forms need a login of at most 60 minutes (ADR 0015); a POST returns only to admin paths | `test_changes_need_a_recent_login`, `test_forged_referer_cannot_leave_the_portal` |
| Tampering | A change breaks existing appointments (capacity lowered, time grid moved) | Capacity is snapshotted per slot; time zone and slot length are frozen while confirmed bookings lie ahead; overlapping windows are refused for the whole schedule | `test_capacity_change_never_touches_existing_slots`, `test_time_grid_is_frozen_while_future_bookings_exist`, `test_overlapping_opening_hours_are_refused` |
| Tampering (XSS) | Markup in a group name or queue id | Jinja2 autoescape; names and queue ids without control characters | `test_names_are_escaped`, `tests/domain/test_scheduling.py::test_group_rejects_invalid_configuration` |
| Spoofing | A new group takes an admin path as its slug (`/admin/groups/new`) | Reserved slugs | `test_reserved_slugs_cannot_be_taken` |
| Repudiation | An admin denies a change | Every change is audited in the same transaction, with the changed field names or `rule.add`-style keys (never values) | `test_settings_change_records_the_changed_fields`, `test_group_admin_manages_opening_hours_of_their_group` |

## Flow: video meetings, work items and direct calls (WP5/WP6)

Public: `GET /b/{token}/join`, `GET /b/{token}/join/go`, `GET/POST /g/{slug}/direct`,
`GET /b/{token}/wait`. Machine: `POST /webhooks/webex`, `POST /webhooks/wxcc`. Outbound:
Webex Meetings API, WxCC Create Task v2. Inputs from outside: tokens in paths, the contact
form, webhook bodies and headers, API responses.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Spoofing / tampering | Forged or altered webhook changes a call's status | HMAC over the raw body before parsing (WxCC SHA-256, Webex SHA-1, constant-time); endpoints exist only with a secret; 64 KB cap; rate limit | `tests/adapters/test_webhooks.py::test_wxcc_signature_timestamp_and_window`, `test_webex_signature`, `tests/api/test_webhooks.py::test_forged_or_stale_webhooks_are_refused`, `test_oversized_bodies_are_refused`, `test_without_a_secret_the_endpoints_do_not_exist` |
| Repudiation / tampering | Replayed WxCC webhook | Header and body timestamp must match and lie within 300 s | `test_wxcc_signature_timestamp_and_window`, `test_forged_or_stale_webhooks_are_refused` |
| Tampering | Replayed Webex webhook (Webex signs no event time) | Signature still required; status changes are idempotent; participant counts never go below zero; only aggregates are stored | `tests/domain/test_integration.py::test_participants_never_go_below_zero_and_failures_are_kept` |
| Information disclosure | Personal data to Webex or WxCC | Meeting request: group name and times only; work item: reference, first name, start, group, link, kind | `test_meeting_request_carries_no_personal_data`, `test_work_item_carries_only_first_name_and_meeting_link`, `tests/adapters/test_wxcc.py::test_task_body_matches_the_create_task_v2_contract` |
| Information disclosure | Participant names or emails from webhooks stored or logged | Parsers read ids, event types, the host flag and reasons only | `test_webex_events_map_to_call_events_without_names` |
| Spoofing (open redirect) | `/join/go` sends the citizen to an attacker's site | Redirect only to the stored Webex link and only `https://`; only while the join window is open | `tests/api/test_join_pages.py::test_never_redirects_to_a_non_https_link`, `test_page_waits_until_fifteen_minutes_before` |
| Information disclosure | Meeting link leaks or outlives the booking | Link is a PII field: excluded from `repr`, purged with the contact; the email links to OpenPlanner, not to Webex; `Referrer-Policy: same-origin` | `tests/adapters/db/test_call_queries.py::test_purge_removes_the_join_link_but_keeps_the_ids` |
| Denial of service | Bots start direct calls and flood the WxCC queue | Same rate limit and honeypot as booking; only while open and switched on per group; WxCC limits active digital contacts | `tests/api/test_direct_pages.py::test_honeypot_and_cross_site_are_refused`, `test_not_offered_when_switched_off`, `tests/domain/test_direct_call.py::test_direct_call_only_when_enabled_and_open` |
| Tampering (CSRF) | A third-party site starts a direct call in the citizen's browser | Fetch Metadata check on the POST | `test_honeypot_and_cross_site_are_refused` |
| Elevation (IDOR) | An admin opens another group's booking through their own group's URL | Timeline authorised on the booking's group, and the booking must belong to the URL's group | `tests/admin/test_call_routes.py::test_a_booking_cannot_be_opened_through_another_groups_url` |
| Information disclosure | Integration secrets readable from the database or logs | Tokens AES-256-GCM with the record name as associated data (ADR 0008); response bodies never logged; the admin page shows expiry only | `tests/adapters/test_crypto.py`, `tests/adapters/db/test_credentials.py`, `tests/adapters/test_webex_auth.py::test_first_use_refreshes_with_the_bootstrap_token` |
| Denial of service | Webex or WxCC slow or down blocks bookings | Bookings commit first; meetings and work items are idempotent jobs with backoff; direct calls defer to the worker | `tests/jobs/test_call_jobs.py`, `tests/api/test_direct_pages.py::test_platform_failure_defers_to_the_worker` |
| Tampering | A schema migration fails half-way on production data | SQLite rebuilds run with foreign keys off and must pass `foreign_key_check` | `tests/test_migrations.py::test_upgrade_and_downgrade_with_data` |
