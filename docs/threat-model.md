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

## Flow: TLS termination (ADR 0030)

Public: ports 80 and 443 of the `proxy` service (`--profile tls`).

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Information disclosure | Traffic, session cookies or management tokens read on the network | HTTPS only: port 80 answers ACME challenges and otherwise redirects (308); HSTS from the app | `scripts/check-proxy.sh` (CI step "proxy check") |
| Denial of service | Certificate expires unnoticed: bookings, admin login and webhooks stop together | Caddy renews at a third of the lifetime with retries; certificates kept in `proxy-data`; expiry check in the runbook | — (operational) |
| Spoofing | Client forges `X-Forwarded-For` to dodge or exhaust per-IP rate limits | Caddy sets the header itself (no `trusted_proxies`); `web` trusts it only from the proxy's fixed address on the `edge` network | — (configuration in `docker-compose.yml`) |
| Denial of service | Oversized bodies on public forms | 64 KB on `/g/*` and `/b/*`, 1 MB elsewhere (HTTP 413) | `scripts/check-proxy.sh` |
| Denial of service | Slow-loris clients hold connections open | Header 10 s, body 30 s, write 60 s, idle 2 min in the Caddyfile | — |
| Information disclosure | Booking paths with tokens in proxy logs | No access log in the Caddyfile | — |
| Elevation | Compromised proxy gains host rights | uid 10001, read-only filesystem, `cap_drop: ALL`, `no-new-privileges`, unprivileged ports | `trivy` misconfiguration scan |

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
| Tampering (XSS) | Arbitrary query parameters reflected into links | The language switcher keeps only `start`, `month`, `day`, `days`, `from` and `until`; picker and filter parameters are parsed as dates, weekday digits and whole hours, and garbage falls back to defaults; output is autoescaped | `tests/api/test_pages.py::test_switcher_keeps_only_known_query_parameters`, `tests/api/test_booking_pages.py::test_month_navigation_is_clamped_and_ignores_garbage`, `test_filter_ignores_garbage_and_the_switcher_keeps_it`, `tests/api/test_slot_picker.py::test_parse_filter` |
| Tampering (XSS) | The calendar script (ADR 0020) is abused to run injected code | CSP allows only same-origin script files, no inline scripts or styles; the script inserts only markup fetched from the same page, which is autoescaped server-side | `tests/api/test_booking_pages.py::test_group_page_shows_the_month_and_the_first_free_day` (CSP), `e2e/tests/booking-flow.spec.ts` |
| Tampering | One click moves a booking to a wrong time | A chosen new time is confirmed first (from → to); only the POST moves it | `test_reschedule_flow`, `test_reschedule_confirmation_needs_a_free_other_time` |
| Information disclosure | Management token leaks through Referer or logs | `Referrer-Policy: same-origin`; uvicorn access log off; rejections log origins only | `test_security_headers_on_every_response`, `test_uvicorn_logs_are_json_and_access_log_is_off` |

## Flow: organisation branding (WP3b)

Admin: `GET /admin/branding`, `GET/POST /admin/organisations/{org}/branding`,
`POST …/branding/logo`, `POST …/branding/logo/remove`. Public: `GET /o/{slug}/theme.css`,
`GET /o/{slug}/logo`.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Elevation of privilege | An organisation admin changes branding (or another organisation's) | `branding.manage` is superuser only (HANDOVER §6); `RequirePermission(..., organisation_param=)` and the domain both decide; denials audited | `tests/admin/test_authorization_matrix.py`, `tests/domain/test_branding_service.py::test_org_admin_may_not_change_branding_handover_section_6` |
| Tampering (XSS / CSS injection) | A colour value breaks out of the theme stylesheet | Only `#RRGGBB` is accepted (domain); the stylesheet is generated from the validated value | `tests/domain/test_branding.py::test_only_six_digit_hex_colours_are_accepted`, `tests/api/test_brand_assets.py::test_theme_contains_only_the_validated_colour` |
| Tampering (stored XSS) | An uploaded SVG or HTML "logo" runs script | Type recognised from magic bytes (PNG, JPEG, WebP only); served with its own media type, `nosniff` and `default-src 'none'` | `tests/domain/test_branding.py::test_svg_html_gif_and_empty_files_are_refused`, `tests/admin/test_branding_routes.py::test_svg_and_oversized_logos_are_refused` |
| Denial of service | Huge uploads fill the disk or memory | `Content-Length` above 200 kB + 16 kB refused unread (413); at most one file is read, up to the limit | `test_svg_and_oversized_logos_are_refused` |
| Information disclosure | Branding unreadable for citizens (contrast) | Accent must reach 4.5:1 against white | `test_accents_without_4_5_to_1_against_white_are_refused` |
| Repudiation | Who changed the branding | Audit event `branding.manage` with field names (never values) | `test_superuser_changes_name_and_colour_and_it_is_audited`, `tests/admin/test_branding_routes.py::test_name_and_colour_are_saved_shown_and_audited` |
| Tampering (CSRF) | A third-party site changes branding in a superuser's browser | Fetch Metadata check on every POST | `test_cross_site_branding_posts_are_refused` |

## Flow: organisation management

Admin: `GET /admin/organisations`, `GET /admin/organisations/new`, `POST /admin/organisations`,
`GET/POST /admin/organisations/{org}`. Inputs from outside: name, slug and default language
(form fields), `org` (path).

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Elevation of privilege | An organisation admin creates organisations or renames their own | `organisation.manage` is a global permission of the superuser only (HANDOVER §6); `RequirePermission` and `OrganisationService` both decide; denials audited | `tests/admin/test_authorization_matrix.py`, `tests/domain/test_organisations.py::test_org_admin_may_not_create_or_edit_organisations` |
| Elevation of privilege | A changed slug moves an organisation under another identity-provider claim | The slug is fixed after creation; the form does not offer it and a submitted value is ignored; taken and reserved slugs (`new`) are refused | `tests/admin/test_organisation_routes.py::test_edit_saves_name_and_language_but_never_the_slug`, `tests/domain/test_organisations.py::test_a_taken_or_reserved_slug_is_refused` |
| Tampering | Control characters or unknown languages in stored settings | Name and slug validated in the domain (shared with groups); the default language must have a catalogue | `tests/domain/test_organisations.py::test_invalid_input_is_refused_with_its_field` |
| Repudiation | Who created or renamed an organisation | Audit event `organisation.manage` with `create` or the field names (never values) | `test_superuser_creates_an_organisation_and_it_is_audited`, `test_update_changes_name_and_locale_and_names_the_fields` |
| Tampering (CSRF) | A third-party site creates an organisation in a superuser's browser | Fetch Metadata check on every POST | `tests/admin/test_organisation_routes.py::test_cross_site_posts_are_refused` |

## Flow: outgoing email (WP3, worker)

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Information disclosure | PII in job payloads or logs | Jobs hold IDs only; the worker re-reads the booking; failures log the exception type | `tests/jobs/test_runner.py` |
| Tampering | Message sent for a moved or cancelled booking | Pinned jobs are skipped when the booking changed or was purged | `test_stale_jobs_send_nothing`, `tests/domain/test_job_plan.py::test_stale_jobs` |
| Injection | Group name or text with HTML in emails | Jinja2 autoescape for the HTML part; plain-text part separate | `tests/jobs/test_messages.py::test_html_is_escaped_and_semantic` |
| Information disclosure | Personal data in calendar files | `.ics` contains team, times and the management link only | `test_calendar_attachment` |

## Flow: message channels, SMS reminders and message texts (ADR 0022, 0028)

Superusers configure SMTP and the SMS gateway (provider, today Webex Connect) per organisation at
`/admin/organisations/{org}/channels`; organisation admins add reminder rules per group at
`/admin/groups/{slug}/reminders`; group admins edit message texts at
`/admin/groups/{slug}/messages/{locale}`. The worker sends through the organisation's sender.
Superusers can send a test email through that same sender to an address they type in.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Information disclosure | SMTP password or SMS API key leaks through the UI, logs or the database | Write-only fields (always empty, `autocomplete=new-password`); sealed with AES-256-GCM under a per-record name; never returned by the `Transaction` port; audit names fields only | `tests/admin/test_channel_routes.py::test_smtp_is_saved_and_the_password_never_shown`, `tests/adapters/db/test_messaging_queries.py::test_channel_settings_round_trip_with_sealed_secret` |
| Information disclosure | SMTP password sent unencrypted | A username requires STARTTLS or TLS, in the admin portal and for the `SMTP_*` fallback (the services refuse to start) | `tests/domain/test_channel_settings.py::test_invalid_smtp_settings_name_the_field`, `tests/adapters/test_senders.py::test_fallback_mail_refuses_a_login_in_clear_text` |
| Tampering | A sealed secret copied to another organisation or channel | The record name is the associated data; a moved ciphertext fails to open | `test_a_secret_cannot_be_moved_to_another_channel` |
| Elevation of privilege | An organisation admin changes where mail or SMS of the organisation goes | `integrations.manage` is superuser only; route and service both check | `tests/admin/test_authorization_matrix.py`, `test_org_admin_may_not_configure_channels_handover_section_6` |
| Spoofing / injection | Header injection through sender name, address or subject texts | No control characters or line breaks in sender fields and subject blocks; addresses without `<>",;` | `test_a_header_injection_in_the_sender_is_refused`, `tests/domain/test_message_texts.py::test_invalid_texts_name_the_block` |
| Abuse | The test email is used to send mail to arbitrary addresses | Superuser only with a fresh login, Fetch Metadata check, rate-limited like a booking (10/IP/hour), fixed subject and text, audited per send | `tests/admin/test_authorization_matrix.py`, `tests/domain/test_channel_service.py::test_a_test_email_goes_through_the_organisations_sender_and_is_audited` |
| Spoofing / injection | Header injection through the test recipient | Same address check as sender addresses (no line breaks or `<>",;`) | `test_a_test_email_needs_a_valid_address`, `test_a_test_email_needs_a_valid_recipient` |
| Information disclosure | The SMTP server's answer (which can name the recipient) reaches a log, job error or page | The adapter raises only a code (`unauthorized`, `unreachable`, …) without the original exception; the portal explains each code in its own words; the audit names the code, never the address | `tests/adapters/test_smtp.py::test_failures_are_reported_as_codes_without_details`, `test_a_failed_test_email_says_why`, `test_a_failed_test_email_returns_and_audits_only_the_code` |
| Tampering | SSRF or downgrade through the SMS API host | https only, host name check, no credentials, query or fragment | `tests/domain/test_channel_settings.py::test_invalid_sms_settings_name_the_field` |
| Information disclosure | A stored SMS API key or SMTP password is sent to a new provider or host after a settings change | The stored secret is kept only for the same provider and host (`secret_binding`); otherwise it must be entered again (ADR 0028) | `tests/domain/test_channel_service.py::test_a_stored_secret_never_follows_a_new_host` |
| Tampering | An unknown SMS provider is posted to route messages elsewhere | The provider must be a value of `SmsProvider`; each value has exactly one adapter | `tests/admin/test_channel_routes.py::test_an_unknown_sms_provider_is_refused`, `tests/adapters/test_senders.py::test_every_sms_provider_has_an_adapter` |
| Information disclosure | A group text pulls in other data (`{x.__class__}`, `{email}`) | Only placeholders from the block's allow-list; the formatter has no attribute access | `test_an_unknown_placeholder_is_refused_and_kept_in_the_form`, `test_used_placeholders` |
| Information disclosure | Phone number or text in logs or errors | Every SMS adapter logs a random correlation ID only; response bodies are neither logged nor raised | `tests/adapters/test_webex_connect_sms.py::test_refusals_map_to_integration_errors` |
| Denial of service | Reminder spam to citizens | At most five rules per group, 5 minutes to 30 days, one per channel and offset | `tests/domain/test_messaging_service.py::test_at_most_five_reminders` |
| Repudiation | Who changed channels, reminders or texts | Audit events with field names or rule keys, never values or texts | `test_saving_smtp_with_password_is_audited_without_values`, `test_add_and_remove_an_sms_reminder_is_audited` |
| Elevation of privilege | Texts or reminders of another organisation's group | Group-level permission on the group slug; the service authorises again | `test_texts_of_another_organisation_are_out_of_reach`, `test_reminders_need_reminders_manage_on_the_group` |

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
| Information disclosure | A group admin reads the organisation's own events, or any admin reads another organisation's or group's events in the audit log (ADR 0031) | `audit.view` is a group-level permission; the scope from the matrix is applied in SQL and is exact per level (a group admin's scope has no organisations, so events with an empty group never match); the page's filters are ANDed with the scope and an out-of-scope group is ignored | `tests/adapters/db/test_admin_queries.py::test_audit_scope_is_exact_per_level`, `test_audit_filter_narrows_within_the_scope`, `tests/admin/test_audit_routes.py::test_each_role_sees_its_own_level_and_nothing_above_or_beside`, `test_a_filter_cannot_reach_outside_the_scope` |
| Information disclosure | The read-only auditor role (ADR 0037) reaches citizens' personal data or changes something | The auditor's permissions are reading only and exclude every permission in `PII_PERMISSIONS` (`bookings.view`); in the audit log a booking id links to the timeline only with `bookings.view`; routes and services decide again on every request | `tests/domain/test_authorization.py::test_the_auditor_never_holds_personal_data_or_a_change`, `tests/admin/test_authorization_matrix.py::test_matrix` (auditor column), `e2e/tests/admin-roles.spec.ts` (auditor refused on agenda and settings) |
| Elevation | A permission that affects several organisations or secrets reaches a role other than the superuser (ADR 0037) | `LOCKED_PERMISSIONS` lists them; a test asserts that only the superuser holds each one; the matrix page and `docs/authorization.md` mark them | `tests/domain/test_authorization.py::test_locked_permissions_belong_to_the_superuser_only` |
| Information disclosure | Personal data of citizens in system events | The worker records only the job type, outcome, error code (never exception text), attempt, channel and booking id | `tests/jobs/test_runner.py::test_a_sent_message_is_a_system_event_of_the_group`, `test_retries_and_the_final_failure_are_recorded_with_the_error_code` |
| Information disclosure | Personal data in logs or audit events | Logs and audit events carry the subject identifier only; the display name is stored once in `admin_profiles` and hidden from `repr` | `tests/domain/test_authorization.py::test_principal_repr_hides_the_display_name`, `tests/test_logging.py` |
| Elevation | A development shortcut reaches production (login bypass, role switcher, test users) | No bypass or switcher exists in code; test users exist only in the Keycloak dev realm; production requires an `https` discovery URL and a client secret | `tests/test_config.py::test_production_is_the_default_and_refuses_development_defaults`, `e2e/tests/admin-roles.spec.ts` logs in through the real provider |

## Flow: groups and planning in the admin portal (WP7b)

Admins only. `GET/POST /admin/groups/new`, `/admin/groups`, `/admin/groups/{slug}/settings`,
`/staffing`, `/staffing/{id}` (change a block), `/staffing/{id}/split` (split a block in two, ADR 0027), `/exceptions`
and their `/delete` actions; the old `/hours` and `/hours/{id}` GET paths answer with a permanent redirect to `/staffing`.
Inputs from outside: form fields (names, slug, time zone, numbers, times, dates, queue id, the optional label of a block,
the split time), path slugs, item ids and the `?weekday=` query parameter that preselects the day of the staffing form.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Elevation | A group admin changes routing or settings of their group, or anyone acts on another group | One `RequirePermission` per route (route level) and `PlanningService` authorises again (domain level); the matrix test covers every role × route × method × own/other group | `tests/admin/test_authorization_matrix.py::test_matrix`, `tests/domain/test_planning.py::test_group_admin_may_not_change_group_settings`, `test_schedule_of_another_group_is_out_of_reach` |
| Elevation | An org admin creates a group in another organisation by editing the form's organisation field | The chosen organisation is authorised in the domain (`group.create` at organisation level); the form only offers organisations in scope | `test_creating_outside_the_scope_is_refused_and_audited`, `tests/admin/test_group_routes.py::test_outsider_cannot_create_a_group_in_another_organisation` |
| Tampering (IDOR) | Changing, splitting or removing another group's staffing block by guessing an item id under one's own group | Reads, updates, splits and deletes are bound to the group (`WHERE id = ? AND group_id = ?`; the split looks the block up among the group's own rules); a miss is `schedule_item_not_found` | `test_schedule_of_another_group_is_out_of_reach`, `test_changing_a_window_of_another_group_is_refused`, `test_splitting_a_block_of_another_group_is_refused`, `tests/adapters/db/test_planning_queries.py::test_schedule_items_carry_ids_and_stay_within_their_group` |
| Tampering (CSRF) | A third-party site submits a change in an admin's browser | Fetch Metadata check on every POST; session cookie `SameSite=Lax` on `/admin` | `test_cross_site_changes_are_refused` |
| Elevation | A role revoked in the IdP keeps granting changes for the rest of the session | Changes and their forms need a login of at most 60 minutes (ADR 0015); a POST returns only to admin paths | `test_changes_need_a_recent_login`, `test_forged_referer_cannot_leave_the_portal` |
| Tampering | A change breaks existing appointments (capacity lowered, time grid moved) | Capacity is snapshotted per slot; time zone and slot length are frozen while confirmed bookings lie ahead; overlapping windows are refused for the whole schedule, also when a window is changed | `test_capacity_change_never_touches_existing_slots`, `test_a_changed_window_may_overlap_only_its_own_old_times`, `test_time_grid_is_frozen_while_future_bookings_exist`, `test_overlapping_opening_hours_are_refused` |
| Tampering (XSS) | A crafted `?weekday=` value reflected into the staffing form | Only the digits 0–6 are kept; anything else falls back to Monday; output is autoescaped | `tests/admin/test_group_routes.py::test_every_day_links_to_the_form_with_that_day_chosen` |
| Tampering | A split leaves the schedule inconsistent: a part of zero length, a split time outside the block, an invalid agent count or label for the second part, or a crash halfway that keeps only one part | The split time must lie strictly inside the block (`split_at`); the second part is a `TimeWindow` validated like a new block; both parts are validated against the whole schedule and written in one locked transaction with the audit event (`rule.split`), so either both parts exist or the old block does | `tests/domain/test_planning.py::test_a_block_is_split_strictly_inside_it`, `test_the_second_part_is_validated_like_a_new_block`, `test_splitting_a_block_replaces_it_with_two_touching_blocks`, `tests/admin/test_group_routes.py::test_a_split_outside_the_block_is_explained` |
| Tampering (open redirect) / elevation | The old `/hours` paths are used to redirect elsewhere or to skip the permission check | The redirects declare the same `schedule.manage` permission as the target, build the location from the authorised group's slug and the integer id only, and keep only a valid `?weekday=` digit; anything else in the query is dropped | `tests/admin/test_group_routes.py::test_old_hours_paths_redirect_permanently_to_staffing`, `tests/admin/test_authorization_matrix.py::test_matrix` |
| Tampering (XSS) | Markup in a group name or queue id | Jinja2 autoescape; names and queue ids without control characters | `test_names_are_escaped`, `tests/domain/test_scheduling.py::test_group_rejects_invalid_configuration` |
| Tampering (XSS) / information disclosure | Markup, line breaks or personal data in the label of a block (ADR 0027), or the label reaching citizens or logs | At most 50 characters, no control characters or line breaks (domain check, `label` error); Jinja2 autoescape; the label is admin content like a group name: shown only in the admin portal, never to citizens, never in logs, and audit events record only the `rule.add`-style key | `tests/domain/test_scheduling.py::test_a_block_rejects_an_invalid_label`, `tests/admin/test_group_routes.py::test_an_invalid_label_is_explained`, `test_a_block_without_agents_gets_a_label`, `test_group_admin_splits_a_block_for_lunch` |
| Tampering | A schedule change moves the slot grid, and new slots are booked on top of bookings on the old grid, so more appointments overlap than there are agents | Active bookings of overlapping slots (start less than one slot length away) take places in the new slot, counted at its busiest moment, in availability and again inside the locked booking transaction | `tests/domain/test_availability.py::test_bookings_on_an_old_grid_take_places_in_overlapping_slots`, `test_a_full_old_grid_slot_blocks_the_slots_it_overlaps`, `test_overlap_counts_the_busiest_moment_not_every_overlapping_slot`, `tests/domain/test_services.py::test_bookings_on_an_old_grid_count_against_overlapping_slots` |
| Tampering | A block with 0 agents (lunch) still offers slots or direct calls | A 0-block ends the run of the slot grid and makes `is_open` false, exactly like a gap; the database checks allow 0 to 1000 and the domain caps at `MAX_CAPACITY` | `tests/domain/test_availability.py::test_a_block_without_agents_offers_no_slots`, `test_a_block_without_agents_ends_the_run`, `tests/domain/test_direct_call.py::test_closed_during_a_block_without_agents` |
| Tampering | A crafted time zone (unknown name, fixed offset without summer time) is posted instead of a listed one | The form offers only `Region/City` zones and UTC; the domain accepts only zones of the tz database; a rejected value is not echoed back as an option | `tests/admin/test_group_routes.py::test_new_group_rejects_an_unknown_time_zone`, `tests/admin/test_timezones.py::test_an_unknown_submitted_value_is_not_echoed` |
| Spoofing | A new group takes an admin path as its slug (`/admin/groups/new`) | Reserved slugs | `test_reserved_slugs_cannot_be_taken` |
| Repudiation | An admin denies a change | Every change is audited in the same transaction, with the changed field names or `rule.add`-style keys (never values) | `test_settings_change_records_the_changed_fields`, `test_group_admin_manages_opening_hours_of_their_group` |

## Flow: manual and context-sensitive help (ADR 0033)

Admins only. `GET /admin/help` (topics of the screens the admin can open) and
`GET /admin/help/{topic}`, both `dashboard.view`; the (i) disclosures and the "Help with this
page" link are part of the documented screens. The text is the Markdown in `docs/manual/`,
reviewed in PRs and shipped in the image. Inputs from outside: the `{topic}` path segment and
the language (`?lang=`, cookie, header; already resolved to a known locale). No personal data.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Information disclosure (path traversal) | `{topic}` reads another file (`../`, an encoded path, a trailing newline) | Only registered topics are served (`TOPICS`); a slug must fully match `[a-z0-9-]`; the locale is a known code | `tests/web/test_help.py::test_manual_falls_back_to_dutch_and_knows_missing_topics`, `tests/admin/test_help_routes.py::test_unknown_topic_is_not_found` |
| Tampering (stored XSS) | Markup in the manual runs in the portal | Raw HTML is off in the renderer; the only markup added is a heading id from the marker pattern; the CSP has no scripts or inline styles | `tests/web/test_help.py::test_raw_html_in_the_manual_is_escaped`, `test_the_manual_needs_no_inline_styles` |
| Elevation | The help page is used to reach a screen or data beyond the admin's rights | Help pages only show static text; the topic list follows the matrix; every screen still checks its own permission | `tests/admin/test_authorization_matrix.py` (`/admin/help` rows), `tests/admin/test_help_routes.py::test_topic_list_shows_only_screens_the_admin_can_open` |
| Information disclosure | Citizens are sent to GitHub or the admin manual | Citizen pages explain themselves through `_()`; a test forbids links to GitHub, `/admin/help` or `docs/manual` in citizen templates | `tests/web/test_help.py::test_the_manual_never_links_citizens_away` |

## Flow: holiday calendars (ADR 0025)

Admins only. `GET/POST /admin/holidays`, `/admin/holidays/{calendar}` with `/days`,
`/national`, `/days/{id}/delete` and `/delete`; `GET/POST /admin/organisations/{org}/holidays`;
`GET/POST /admin/groups/{slug}/holidays` with `/{day}/open` and `/{day}/close`. Inputs from
outside: calendar name and slug, holiday date and name, a year, ticked calendar ids
(`calendar-<id>`), the source choice, path slugs, ids and dates. No personal data.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Elevation | An organisation or group admin changes a calendar that every organisation uses | `holidays.manage` is global and superuser only; route and `HolidayService` both decide | `tests/admin/test_authorization_matrix.py::test_matrix`, `tests/domain/test_holiday_service.py::test_only_the_superuser_maintains_calendars` |
| Elevation | An organisation admin sets the calendars of another organisation, or a group admin those of another group | `holidays.assign` at organisation level and `schedule.manage` at group level, decided on the path's target | `test_org_admin_cannot_choose_for_another_organisation`, `test_group_admin_cannot_touch_another_group` |
| Tampering (IDOR) | Removing another calendar's holiday by guessing its id; choosing a calendar id that does not exist | Deletes are bound to the calendar (`WHERE id = ? AND calendar_id = ?`); chosen ids must exist | `test_remove_a_holiday_only_from_its_own_calendar`, `test_unknown_calendars_are_refused`, `tests/admin/test_holiday_routes.py::test_unknown_source_and_calendar_are_refused` |
| Denial of service (of the service to citizens) | Deleting a calendar in use silently reopens every holiday | Refused in the domain (`holiday_calendar_in_use`) and by `ON DELETE RESTRICT` | `test_a_calendar_in_use_cannot_be_deleted`, `tests/adapters/db/test_holiday_queries.py::test_a_calendar_in_use_is_protected_by_the_database` |
| Tampering | A typo year (20026) or a malformed date creates junk | Years 2000–2100 only; dates parsed strictly; "keep open" only for an upcoming holiday of the group's own calendars | `test_generator_refuses_implausible_years`, `test_only_upcoming_holidays_of_the_group_can_be_kept_open`, `test_a_malformed_day_is_not_found` |
| Tampering (XSS) | Markup in a calendar or holiday name | Jinja2 autoescape; names without control characters | `tests/domain/test_holidays.py::test_calendar_validates_slug_and_name` |
| Tampering (CSRF) | A third-party site changes holidays in an admin's browser | Fetch Metadata check on every POST; changes need a recent login | `tests/admin/test_holiday_routes.py::test_cross_site_holiday_changes_are_refused` |
| Repudiation | Who changed a calendar or a group's choice | Audit events `holidays.manage` (`<calendar>:holiday.add`), `holidays.assign` (`calendars`) and `schedule.manage` (`holidays.calendars`, `holiday.open`), never names or dates | `test_superuser_creates_and_fills_a_calendar`, `test_org_admin_chooses_calendars_for_their_organisation` |

## Flow: emergency closure (ADR 0024)

Admins: `GET/POST /admin/groups/{slug}/closure` and its `update`, `lift`, `reinstate`,
`cancel`, `bookings/{id}/…` actions; `/admin/closures`, `/admin/organisations/{org}/closure`
and `/lift`. Citizens: the notice on `/g/{slug}`, and `/b/{token}` for a booking on hold.
Inputs from outside: notice texts, date-times, booking ids in paths, a chosen slot.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Elevation | An admin closes, or handles the bookings of, a group or organisation outside their scope | One `RequirePermission` per route and `ClosureService` authorises again; `closure.manage` (group) and `organisation.closure` (organisation, not for group admins) in the matrix | `tests/admin/test_authorization_matrix.py::test_matrix`, `tests/domain/test_closure_service.py::test_other_groups_admin_cannot_close_or_touch_bookings`, `test_group_admin_cannot_close_the_organisation` |
| Tampering (IDOR) | Moving or cancelling another group's booking by putting its id under one's own group | The booking must belong to the authorised group, else `booking_not_found` (no hint it exists); only suspended bookings can be handled | `test_other_groups_admin_cannot_close_or_touch_bookings`, `test_reinstate_one_booking` |
| Tampering (XSS) | Markup or scripts in the notice, shown on public pages and in mail | Plain text only, no control characters, at most 1000 characters; Jinja2 autoescape on pages and in the HTML mail; never a template (ADR 0009) | `tests/domain/test_closure.py::test_invalid_notice_texts_are_refused`, `tests/admin/test_closure_routes.py::test_group_admin_closes_the_team_and_citizens_see_why`, `tests/jobs/test_messages.py::test_suspension_carries_the_notice_and_no_join_link_or_calendar` |
| Information disclosure | Personal data kept longer than needed while bookings are on hold | Purge at closure end + 30 days (ADR 0024); the admin list shows references and times only; the audit keeps keys and counts | `test_closing_suspends_covered_bookings_and_keeps_their_data`, `test_open_ended_closure_has_no_purge_until_it_ends`, `test_update_mails_everyone_on_hold_and_suspends_newly_covered` |
| Denial of service | Bots book into a closed team, or start direct calls while it is closed | No slot inside a closure is offered or accepted (`group_closed`); direct calls off while active | `test_closed_group_offers_and_accepts_no_slots`, `test_no_direct_call_while_closed`, `tests/admin/test_closure_routes.py::test_closed_team_cannot_be_booked` |
| Tampering | An update mail reaches citizens whose booking was already moved or cancelled | Closure mails only run while the booking is still suspended | `tests/domain/test_closure.py::test_closure_notices_only_go_to_bookings_still_on_hold`, `tests/jobs/test_runner.py::test_closure_notice_goes_only_to_bookings_on_hold` |
| Tampering (CSRF) | A third-party site closes a team in an admin's browser | Fetch Metadata check on every POST; changes need a recent login (ADR 0015) | `tests/admin/test_group_routes.py::test_cross_site_changes_are_refused` (same dependency) |
| Repudiation | An admin denies closing a team or cancelling appointments | Audited in the same transaction, per group, with counts | `test_closing_suspends_covered_bookings_and_keeps_their_data`, `test_org_admin_closes_and_lifts_every_group_of_the_organisation` |

## Flow: video meetings, work items and direct calls (WP5/WP6)

Public: `GET /b/{token}/join`, `GET /b/{token}/join/go`, `GET/POST /g/{slug}/direct`,
`GET /b/{token}/wait`. Machine: `POST /webhooks/webex`, `POST /webhooks/wxcc`. Outbound:
Webex Meetings API, WxCC Create Task v2. Inputs from outside: tokens in paths, the contact
form, webhook bodies and headers, API responses.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Spoofing / tampering | Forged or altered webhook changes a call's status | HMAC over the raw body before parsing (WxCC SHA-256, Webex SHA-1, constant-time); endpoints exist only with a secret; 64 KB cap; rate limit | `tests/adapters/test_webhooks.py::test_wxcc_signature_timestamp_and_window`, `test_webex_signature`, `tests/api/test_webhooks.py::test_forged_or_stale_webhooks_are_refused`, `test_oversized_bodies_are_refused`, `test_without_a_secret_the_endpoints_do_not_exist` |
| Repudiation / tampering | Replayed WxCC webhook | Header and body timestamp must match and lie within 300 s | `test_wxcc_signature_timestamp_and_window`, `test_forged_or_stale_webhooks_are_refused` |
| Tampering | Replayed Webex webhook (Webex signs no event time) | Signature still required; each event's key (digest of resource, event, `data.id`, join/leave time) is remembered for 30 days in `webhook_events` in the same transaction, so a replay or redelivery is recorded once; counts never go below zero; only aggregates are stored | `tests/domain/test_integration.py::test_a_replayed_webex_event_changes_the_counts_once`, `test_participants_never_go_below_zero_and_failures_are_kept`, `tests/api/test_webhooks.py::test_a_replayed_participant_event_counts_once` |
| Information disclosure | Personal data to Webex or WxCC | Meeting request: group name and times only; work item: reference, first name, start, group, link, kind | `test_meeting_request_carries_no_personal_data`, `test_work_item_carries_only_first_name_and_meeting_link`, `tests/adapters/test_wxcc.py::test_task_body_matches_the_create_task_v2_contract` |
| Information disclosure | Participant names or emails from webhooks stored or logged | Parsers read ids, event types, the host flag and reasons only | `test_webex_events_map_to_call_events_without_names` |
| Information disclosure | Personal data to WxCC when a cancelled task is marked | The cancellation message carries only `bookingReference` and `kind`; the task id is URL-escaped so it cannot change the API path | `tests/adapters/test_wxcc.py::test_cancellation_message_carries_the_reference_only`, `test_a_task_id_cannot_change_the_path` |
| Information disclosure | `wxcc-check` output shows tokens or user data | Prints status and a scope hint only; bodies are never read into the report | `tests/adapters/test_diagnostics.py::test_report_has_status_and_hint_but_no_token_or_body`, `tests/test_cli.py::test_wxcc_check_reports_todays_403s_and_fails` |
| Information disclosure | `wxcc-config-check` leaks secrets or agent data from the configuration | Queues and entry points are listed with selected attributes only; asset webhook secrets are dropped when parsing; flows are reduced to variable and activity names | `tests/adapters/test_wxcc_config.py::test_config_is_fetched_with_selected_fields_and_no_secrets` |
| Information disclosure | `webhooks-list` or **Koppelingen** shows secrets, or tokens that other integrations put in their target URLs | Only selected fields are kept; `secret` becomes "set/not set"; targets lose credentials, query and fragment, and foreign targets keep only scheme and host; the stored report is built from the same reduced fields | `tests/adapters/test_registrations.py::test_the_report_never_shows_secrets_or_foreign_query_strings`, `test_the_stored_report_round_trips_without_secrets` |
| Tampering / denial of service | `webhooks-reset` deletes another application's webhook, or a current one, and stops delivery | Only `STALE` registrations (OpenPlanner's path or name, wrong in a named way) are deleted; a dry run unless `--yes`; foreign and current ones are never touched; refusals are reported per item | `test_delete_removes_only_stale_registrations_and_reports_refusals`, `test_a_dry_run_deletes_nothing_and_says_what_it_would_delete`, `tests/test_cli.py::test_webhooks_reset_is_a_dry_run_without_yes` |
| Tampering / SSRF | A listing's id or `Link: next` header steers the Service App token to another path or host | Ids must match `[A-Za-z0-9_=-]{1,256}` before they go into a DELETE path; next pages are followed over https on the API host only, at most 10 | `test_an_id_that_is_not_an_api_id_is_never_put_in_a_path`, `test_a_next_page_is_followed_on_the_same_https_host_only` |
| Tampering | The delivery self-test changes a booking or leaks a secret | Test events carry `openplanner-selftest-<random>` ids that match no meeting or task (acknowledged as unknown, nothing stored); no bearer token is sent; the secret never leaves the process, only the HMAC | `tests/adapters/test_webhook_selftest.py::test_the_test_events_carry_no_bearer_token_and_unknown_ids`, `test_signed_events_are_accepted_and_unsigned_ones_refused` |
| Elevation | The admin portal offers a way to delete registrations | **Koppelingen** shows the stored reports read-only (`integrations.manage`); deleting stays on the command line in the `web` container | `tests/admin/test_call_routes.py::test_integrations_page_shows_the_last_checks_read_only` |
| Spoofing (open redirect) | `/join/go` sends the citizen to an attacker's site | Redirect only to the stored Webex link and only `https://`; only while the join window is open | `tests/api/test_join_pages.py::test_never_redirects_to_a_non_https_link`, `test_page_waits_until_fifteen_minutes_before` |
| Information disclosure | Meeting link leaks or outlives the booking | Link is a PII field: excluded from `repr`, purged with the contact; the email links to OpenPlanner, not to Webex; `Referrer-Policy: same-origin` | `tests/adapters/db/test_call_queries.py::test_purge_removes_the_join_link_but_keeps_the_ids` |
| Denial of service | Bots start direct calls and flood the WxCC queue | Same rate limit and honeypot as booking; only while open and switched on per group; optional cap on open direct calls per group, checked in the booking's write lock (ADR 0026); WxCC limits active digital contacts | `tests/api/test_direct_pages.py::test_honeypot_and_cross_site_are_refused`, `test_not_offered_when_switched_off`, `test_full_when_every_agent_is_taken`, `tests/domain/test_direct_call.py::test_direct_call_only_when_enabled_and_open`, `test_direct_call_is_refused_when_every_agent_is_taken` |
| Denial of service | Bots take every open direct-call place so citizens see "all agents busy" | Rate limit and honeypot bound one source; a waiting call stops counting after `not_picked_up_minutes`, an ended, failed or cancelled one at once | `tests/domain/test_direct_queue.py::test_phase_of_a_direct_call`, `tests/domain/test_direct_call.py::test_an_ended_call_frees_its_place` |
| Information disclosure | Queue feedback reveals other citizens | Only counts and a median wait; computed from call status without personal data | `tests/domain/test_direct_queue.py::test_position_counts_waiting_calls_that_started_earlier` |
| Tampering (CSRF) | A third-party site starts a direct call in the citizen's browser | Fetch Metadata check on the POST | `test_honeypot_and_cross_site_are_refused` |
| Elevation (IDOR) | An admin opens another group's booking through their own group's URL | Timeline authorised on the booking's group, and the booking must belong to the URL's group | `tests/admin/test_call_routes.py::test_a_booking_cannot_be_opened_through_another_groups_url` |
| Information disclosure | Integration secrets readable from the database or logs | Tokens AES-256-GCM with the record name as associated data (ADR 0008); response bodies never logged; the admin page shows expiry only | `tests/adapters/test_crypto.py`, `tests/adapters/db/test_credentials.py`, `tests/adapters/test_webex_auth.py::test_first_use_refreshes_with_the_bootstrap_token` |
| Information disclosure | Webex echoes submitted citizen data in a refusal that is logged | A refusal logs status and `trackingId` only; Webex's message is logged only for Meetings requests, which carry no citizen data, with email addresses masked | `tests/adapters/test_webex_auth.py::test_without_explain_only_the_tracking_id_is_logged`, `test_a_refusal_logs_webexs_reason_and_tracking_id_without_addresses`, `tests/adapters/test_webex_meetings.py::test_a_refused_meeting_logs_webexs_reason` |
| Denial of service | Webex or WxCC slow or down blocks bookings | Bookings commit first; meetings and work items are idempotent jobs with backoff; direct calls defer to the worker | `tests/jobs/test_call_jobs.py`, `tests/api/test_direct_pages.py::test_platform_failure_defers_to_the_worker` |
| Tampering | A schema migration fails half-way on production data | SQLite rebuilds run with foreign keys off and must pass `foreign_key_check` | `tests/test_migrations.py::test_upgrade_and_downgrade_with_data` |

## Flow: verify button and simulator (admin)

`POST /admin/groups/{slug}/check` ("Koppeling controleren") and
`POST /admin/groups/{slug}/bookings/{id}/simulate` (simulator, not in production).

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Elevation of privilege | A group admin or another organisation's admin checks or simulates | `group.edit` on the route (`RequirePermission`, fresh login) and again in the domain; the booking must belong to the group | `tests/admin/test_authorization_matrix.py`, `tests/domain/test_integration_check.py`, `tests/domain/test_simulation.py::test_a_booking_of_another_group_is_not_found` |
| Information disclosure | A check reveals another organisation's groups (shared asset) | Peers come from the group's own organisation only | `tests/domain/test_integration_check.py::test_run_stores_the_result_and_audits_it` |
| Tampering at the platforms | The check changes Webex or WxCC | Read-only: only GET (and the token refresh) go to the platforms; self-test events go to our own endpoints without a bearer token | `tests/adapters/test_group_check.py::test_nothing_is_created_or_deleted_and_no_token_leaves_for_our_endpoints` |
| Denial of service | Repeated checks hammer the platforms | 6 runs per minute per address, fresh admin login | rate limit setting `checks` |
| Spoofing | The simulator forges events in production | Off unless `SIMULATOR_ENABLED=true`; production refuses to start with it; without it the route answers 403 and the card is hidden | `tests/test_config.py::test_production_is_the_default_and_refuses_development_defaults`, `test_the_simulator_is_off_unless_switched_on`, `tests/admin/test_simulator_routes.py::test_without_the_switch_there_is_no_simulator` |
| Repudiation / human error | An admin changes settings in the wrong environment | A striped environment bar on every admin page outside production; none in production | `tests/web/test_environment_bar.py`, `tests/admin/test_environment_bar.py` |
| Tampering | Simulated ids overwrite a real meeting or task | `prepare` sets ids only where none exist; stand-ins are marked `simulated` on the timeline | `tests/domain/test_simulation.py::test_real_platform_ids_are_never_overwritten` |
| Tampering (CSRF) | A third-party site triggers a check or a simulated event | Fetch Metadata check on both POSTs | `tests/admin/test_check_routes.py::test_cross_site_check_is_refused`, `tests/admin/test_simulator_routes.py::test_unknown_events_and_cross_site_posts_are_refused` |

## Flow: machine API for contact-center bots (ADR 0023)

Machine: `POST /api/agent/v1/oauth/token` (client credentials), `GET /api/agent/v1/me`,
`/groups`, `/groups/{slug}/availability`, `/openapi.json`, `/docs`. Admin (superuser):
`/admin/api` and `/admin/api/clients/...`. Inputs from outside: the `Authorization` header
(Basic or Bearer), the token form, path slugs, query dates and language, the caller's IP
address; in the admin portal the client form.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Elevation | The API becomes a back door into bookings or personal data | No route returns or accepts personal data or a management token; the only scope reads free times; changes (next step) only through the citizen's own confirmation; no superuser equivalent for machines | `tests/api/test_agent_api.py::test_docs_exist_only_while_published` (the spec lists every path), `tests/domain/test_authorization.py::test_decide_client` |
| Elevation | A deployment exposes the API without anyone deciding to | Two switches, both off by default: `AGENT_API_ENABLED` (routes do not exist) and the superuser's switch (tokens refused) | `test_without_the_environment_switch_the_api_does_not_exist`, `tests/domain/test_api_access.py::test_the_switches_stop_tokens_and_calls` |
| Spoofing | Guessing a client secret or token | 256-bit random values; constant-time comparison of SHA-256 hashes, also for unknown clients; token endpoint rate-limited per IP; every refusal audited | `tests/domain/test_api_clients.py::test_credentials_are_random_prefixed_and_only_their_hash_is_compared`, `test_tokens_are_rate_limited_per_ip`, `test_every_credential_problem_looks_the_same_but_is_audited_apart` |
| Information disclosure | Client enumeration through different errors | Every credential problem answers `invalid_client`; the reason is only in the audit log | `test_every_credential_problem_looks_the_same_but_is_audited_apart`, `test_wrong_secret_is_invalid_client_and_audited_without_the_secret` |
| Spoofing | A stolen secret or token used from elsewhere | Optional network allow-list per client, checked at token issue and on every call; tokens live 10 minutes; secrets expire (90–365 days) | `test_a_stolen_token_from_another_network_is_refused_and_audited`, `test_tokens_expire` |
| Elevation | A revoked or disabled client keeps working | Opaque tokens checked against the database on every call; disabling, rotating or changing the grant revokes tokens in the same transaction; emergency stop | `test_disabling_rotating_and_revoking_end_every_token`, `test_changing_the_grant_revokes_tokens_but_renaming_does_not`, `test_the_emergency_stop_refuses_tokens_and_calls` |
| Elevation | Scope escalation or another organisation's group | Requested scopes must be granted; `decide_client` checks organisation and the client's groups; every route declares one `RequireScope`; removing a group cannot widen a narrowed client (`RESTRICT`) | `test_scope_escalation_is_refused`, `test_calls_stay_inside_the_grant`, `test_another_organisations_group_is_insufficient_scope`, `test_every_route_declares_one_scope_except_token_and_docs`, `tests/adapters/db/test_api_client_queries.py::test_a_group_with_clients_cannot_be_removed` |
| Information disclosure | Secrets in the database, logs, URLs or caches | Only SHA-256 stored; the secret is shown once in the POST response (`no-store`), never in a URL; every API response `no-store`; no secret or token in logs or audit | `tests/admin/test_api_routes.py::test_register_shows_the_secret_once_and_stores_only_its_hash`, `test_client_credentials_flow_end_to_end` |
| Information disclosure | Personal data echoed in validation errors | The API's own handler answers `invalid_request` with no input; only parameter names are logged | `test_invalid_input_is_refused_without_echoing_it` |
| Denial of service | A bot floods the API or piles up tokens | Rate limit per client; at most 5 live tokens per client; range of at most 62 days per availability call | `tests/domain/test_api_access.py::test_tokens_do_not_pile_up`, `test_invalid_input_is_refused_without_echoing_it` |
| Elevation / tampering | Someone other than the superuser registers or changes a client, or a third-party site does it in their browser | `api_clients.manage` is superuser-only, with a fresh login; Fetch Metadata check on every POST; the service authorises again | `tests/admin/test_authorization_matrix.py::test_matrix`, `tests/domain/test_api_access.py::test_only_the_superuser_manages_clients`, `tests/admin/test_api_routes.py::test_cross_site_posts_are_refused` |
| Repudiation | Who issued a client, who used it, what was refused | Client changes, token issues, refused tokens and refused calls are audit events (actor = admin subject or client id) | `test_superuser_registers_a_client_and_only_the_hash_is_stored`, `test_valid_credentials_get_a_short_lived_token` |

## Flow: updates from the admin portal (ADR 0029)

Worker: `GET https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest` (hourly).
Admin (superuser): `GET /admin/updates`, `POST /admin/updates/update`, `/restore`,
`/requests/{id}/cancel`, `/snooze`. Host updater: `python -m app.cli update-claim`,
`update-report`, `backup-db`, `restore-db` inside the web container. Inputs from outside: the
release body from GitHub (tag, page), the snooze's return path, a request id in the path, and
the updater's status and reason. No personal data, except in the database backups.

| STRIDE | Threat | Mitigation | Test |
|---|---|---|---|
| Elevation | A compromised web process replaces its own image or runs any image | The containers have no Docker socket; the application only stores requests; the host updater installs only images of this repository's release tags that pass `cosign verify` | `tests/scripts/test_updater.py::test_invalid_signature_leaves_the_running_version`; `tests/domain/test_update_service.py::test_superuser_requests_the_available_update_and_it_is_audited` |
| Elevation | A compromised container feeds shell code to the host updater (claim line, Alembic revision) | The updater checks every value from the container against a strict pattern (id, versions, revision, image references) and reads its state file with `sed`, never sourcing it | `tests/scripts/test_updater.py::test_a_claim_is_never_executed`, `test_an_alembic_revision_is_never_executed`, `test_a_tampered_state_file_is_never_executed`, `test_the_state_file_is_never_sourced`; shellcheck in CI |
| Elevation | An organisation or group admin updates or restores the installation | `system.update` is global and superuser only; route and `UpdateService` both decide; requests need a fresh login | `tests/admin/test_authorization_matrix.py::test_matrix`, `tests/domain/test_update_service.py::test_org_admin_cannot_request_an_update`, `tests/admin/test_update_routes.py::test_requests_need_a_fresh_login` |
| Tampering | A downgrade disguised as an update, or two requests racing | Update targets must be newer than the running release build; at most one open request, checked under the write lock; a restore only to the version the latest update replaced, within 24 hours | `test_no_update_request_without_a_newer_release`, `test_only_one_open_request`, `test_no_restore_after_the_window` |
| Spoofing | A forged release (other repository, foreign release page, odd tag) | `UPDATE_REPOSITORY` must be `owner/name`; tags must be `vX.Y.Z`; the release page must be this repository's GitHub page, else the release is ignored | `tests/adapters/test_releases.py::test_a_release_that_is_no_version_or_has_a_foreign_page_is_none`, `tests/test_config.py::test_update_repository_must_be_owner_and_name` |
| Tampering | The updater (or anyone with a shell in the container) reports success for the wrong version | "succeeded" is only accepted from the target version; otherwise recorded as `failed`/`version_mismatch`; reasons must be short codes | `test_success_reported_by_the_wrong_version_is_recorded_as_failed`, `tests/domain/test_updates.py::test_finish_refuses_reasons_that_are_not_codes` |
| Tampering (CSRF, open redirect) | A third-party site requests an update in an admin's browser; the snooze sends the admin elsewhere | Fetch Metadata check on every POST; the snooze's return path must be an admin path | `test_requests_need_the_same_origin`, `test_snooze_never_redirects_off_the_portal` |
| Denial of service | A broken release takes the booking pages down | Backup before the switch; health wait of `HEALTH_TIMEOUT`; automatic return to the previous digest and the backup | `tests/scripts/test_updater.py::test_unhealthy_update_restores_the_backup_and_the_previous_digest`, `test_restore_downgrades_the_schema_then_runs_the_previous_digest`; runbook "Updates" |
| Information disclosure | Database backups (personal data) outlive the retention rules | Backups only in the data volume, mode 600, deleted after 24 hours on every updater run | `tests/adapters/db/test_backup.py::test_backup_is_a_private_consistent_copy`, `test_prune_deletes_backups_older_than_a_day` |
| Tampering | A backup name used as a path | Backup names are plain words (`[A-Za-z0-9_-]{1,64}`) | `test_backup_name_is_a_plain_word` |
| Repudiation | Who started an update and what the updater did | Requests and cancellations audited (`system.update`, `update.request`, `restore.request`, `request.cancel`); every updater step audited as `host-updater` with its reason | `test_cancel_a_waiting_request`, `test_updater_claims_the_waiting_request_once` |

