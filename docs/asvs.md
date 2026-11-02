# ASVS evidence: where each measure is

This document answers one question for the OWASP ASVS 5.0.0 level 2 audit of OpenPlanner: **for each
requirement the audit did not fully accept, where does the measure sit now, and is the gap closed?**
It is the application's own answer. It points at code, tests, ADRs and documents; it proves nothing
by itself.

## What this is, and what it is not

- The baseline audit was done on commit `bf14c54e` on 1 November 2026. It judged 253 level 1 and 2
  requirements plus 16 further controls of our own (organisation separation and the privacy chain).
  Of these, 99 were *partly substantiated* or a *deviation*: 92 ASVS requirements and 7 further
  controls. Those 99 are the rows below. The requirements the audit already accepted from the
  repository are not repeated here.
- **This is not a certificate and not a conformity statement.** A closed row says that the gap the
  audit named now has a source in the repository. It does not say the requirement is met in a given
  installation, and it is not a penetration test. The scope of the penetration test is in
  `docs/assurance/pentest-scope.md`.
- The requirement texts belong to the OWASP ASVS project (CC BY-SA 4.0). This document uses the IDs
  and a short description in our own words; read the standard for the full text.
- Every row names at least one file that exists; a test (`tests/test_asvs_doc.py`) fails when a path
  in this document is gone, when a row lacks a source, or when the counts below do not match the
  rows. A PR number such as `#350` is the change that built the measure.

### Status words

| Word | Meaning |
|---|---|
| Closed | The gap the audit named has a measure with a source: code, a test, a document or an ADR. |
| Limitation | A recorded decision accepts the gap on purpose. The row names the ADR or the section of the threat model that records it and the controls that compensate. |
| Open | Not closed from the repository: the evidence needs the target environment, the identity provider or the host, or the measure is not built. The row says which. |

### Summary

| Status | ASVS requirements | Further controls |
|---|---|---|
| Closed | 74 | 7 |
| Limitation | 5 | 0 |
| Open | 13 | 0 |
| Total | 92 | 7 |

**Limitation** (5): V3.5.4 (one origin for all organisations), V5.4.3 (no virus scan, ADR 0092),
V11.4.1 and V11.4.3 (the provider-imposed HMAC-SHA1 of Webex webhooks), V12.3.3 (plain HTTP from the
bundled proxy to web on one host, ADR 0091).

**Open** (13): V2.4.1, V4.1.3, V6.1.1, V6.3.1, V6.3.2, V7.1.3, V7.6.1, V12.3.1, V13.3.1, V13.3.2,
V14.3.1, V15.2.2 and V15.3.4. The reason of each is in its row.

**Not repeated here, with a checklist:** 66 ASVS requirements and 5 further controls need evidence from
the client's identity provider, the host, Cisco or the data controller. They are listed under
*Evidence that only an outside party can give* and live in `docs/assurance/`.

## V1 Encoding and sanitization

The architecture behind V1.1 and V1.2 is in [Encoding architecture](#encoding-architecture); the
input rules behind V1.3.3 are in [Validation per entry point](#validation-per-entry-point).

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V1.1.1 | Input decoded once, in a known place | The framework decodes; the three places we decode ourselves are named in *Encoding architecture*. `app/api/agent/token.py`, `tests/api/test_parameter_pollution.py` | Closed |
| V1.1.2 | Output encoded for its destination, at the end | Jinja2 autoescape for HTML, text blocks from an allow-list, DOM text nodes in the widget. `app/web/templates.py`, `app/domain/message_texts.py`, `widget/src/dom.js`, `tests/web/test_templates.py`, `tests/domain/test_message_texts.py` | Closed |
| V1.2.2 | Safe URL schemes and encoding when building URLs | Only `https` links from administrators; URLs built from slugs and ids; the widget uses `encodeURIComponent`. `app/domain/contacts.py`, `tests/domain/test_unsafe_link_schemes.py` (#338) | Closed |
| V1.2.5 | No operating-system command injection | `app/` starts no process and builds no shell command; a test fails when that changes. `tests/test_asvs_doc.py` | Closed |
| V1.2.9 | Regular expressions built safely | Every pattern in `app/` is a literal or built from module constants, never from a request; a test fails otherwise. `tests/test_asvs_doc.py` | Closed |
| V1.3.3 | Length and character set checked on input | The domain checks every contact field and answer; see the validation table. `app/domain/booking.py`, `app/domain/form_fields.py`, `tests/domain/test_booking.py`, `tests/domain/test_form_fields.py` | Closed |
| V1.3.6 | SSRF: an allow-list for what the server calls | One table of every outbound connection with who sets it and whether the guard applies; addresses an organisation can enter go through the guard (https only, public addresses, no redirects, size cap). The SMS and contact-center addresses are set by the superuser only and are https only, without the public-address check; the guard grows to them when a second use asks for it. `docs/dataflow.md`, `app/adapters/outbound.py`, `tests/adapters/test_outbound.py` (#386) | Closed |
| V1.4.1 | Memory-safe handling in native components | The native components are named in *Native components* and watched by the SBOM, grype and trivy in CI. No claim of memory safety is made beyond that. `.github/workflows/ci.yml`, `docs/dependencies.md` | Closed |
| V1.4.2 | Integer overflow and wrap-around | Python integers do not overflow; values reach SQLite (64 bit) only after bounds checks on the input and on every setting. `app/domain/form_fields.py`, `app/domain/settings_registry.py`, `tests/domain/test_settings_registry.py` | Closed |
| V1.4.3 | No use of freed memory in native code | As V1.4.1: no own native code; third-party components are tracked and scanned. `docs/dependencies.md`, `.github/workflows/ci.yml` | Closed |

## V2 Validation and business logic

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V2.1.1 | Validation rules documented per input | *Validation per entry point* below, one row per public, machine and admin entry. `docs/asvs.md`, `docs/threat-model.md` | Closed |
| V2.1.2 | Rules that link several fields documented | Same table, column *Rules across fields*: the slot must belong to the group, the e-mail limit counts per group, a change needs a booking the cookie holds. `app/domain/services.py`, `tests/domain/test_services.py` | Closed |
| V2.1.3 | Business limits documented, per user and global | Every limit is a setting with a class and bounds. `docs/settings.md`, `app/domain/settings_registry.py`, `docs/adr/0066-bookings-per-email-address.md` | Closed |
| V2.3.3 | Transactions that do not run twice or half | The worker claims a call before the external request; only a crash between claim and save after `STALE_CLAIM` remains, and the claim then lapses by design. `app/adapters/db/jobs.py`, `tests/domain/test_call_service_claim.py`, `tests/adapters/db/test_call_queries.py` (#303) | Closed |
| V2.4.1 | Anti-automation measured under load | The controls are in place: rate limits per address and per client, honeypot, a cap on waiting direct calls, a limit per e-mail address. What is open is the measurement on the target environment. `docs/settings.md`, `tests/api/test_security_events.py` | Open |

## V3 Web frontend security

The reasoning for the rows of this chapter is the section *Browser model* of `docs/threat-model.md`.

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V3.2.1 | Content only rendered in the right context | A header policy per response type: `nosniff`, a CSP of `default-src 'none'` on everything that is not a page. `app/api/security_headers.py`, `docs/threat-model.md`, `tests/api/test_health.py` | Closed |
| V3.3.1 | Cookie with the `__Host-` prefix and `Secure` | `__Host-op_admin` and `__Host-op_link` on https, `Secure` follows `PUBLIC_BASE_URL`. `app/web/cookies.py`, `app/admin/session.py`, `tests/admin/test_session_cookie.py`, `tests/web/test_cookies.py` (#363) | Closed |
| V3.3.3 | Cookie bound to the host | `Path=/`, no `Domain`, as the prefix demands. `app/web/cookies.py`, `tests/admin/test_session_cookie.py` | Closed |
| V3.4.3 | CSP on every response | `base-uri 'none'` added; the policy per type is in the threat model. `app/api/security_headers.py`, `tests/api/test_health.py` (#344) | Closed |
| V3.5.1 | Protection against cross-site requests, written down | CSRF in three layers: `SameSite=Lax`, Fetch Metadata, `Origin`. `app/web/protection.py`, `tests/admin/test_admin_flow.py` (#344) | Closed |
| V3.5.2 | Simple cross-origin requests refused | Same three layers, with the one deliberate exception pinned by a test. `app/web/protection.py`, `tests/admin/test_admin_flow.py` | Closed |
| V3.5.3 | No state change through `GET` | The list of `GET` routes with an effect, each with its reason. `docs/threat-model.md`, `tests/api/test_booking_pages.py` | Closed |
| V3.5.4 | Separate origins for separate applications | All organisations and the admin portal share one origin by design: one application that separates by scope, compensated by the CSP, escaping and cookie scope. Section *One origin for all organisations* in `docs/threat-model.md` | Limitation |
| V3.7.2 | Redirect targets restricted | One table of every redirect and who decides its target. `docs/threat-model.md`, `tests/admin/test_admin_flow.py` (#344) | Closed |

## V4 API and web service

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V4.1.2 | Only HTTPS for the API; no silent upgrade | Port 80 answers `/api/*` and `/webhooks/*` with 403 and no `Location`; browser paths keep the redirect. `deploy/proxy/Caddyfile`, `scripts/check-proxy.sh`, `tests/scripts/test_check_proxy.py`, `docs/runbook.md` (#311) | Closed |
| V4.1.3 | Headers set by the proxy cannot be overridden | The app trusts `X-Forwarded-*` only from the proxy address (`FORWARDED_ALLOW_IPS`), Caddy replaces what the visitor sent, and the address check finds a wrong trust. What is missing is a test on the real edge that sends a forged `X-Forwarded-For` and shows it is dropped. `docker-compose.yml`, `deploy/proxy/Caddyfile`, `tests/api/test_instance.py`, `scripts/check-proxy.sh` | Open |

## V5 File handling

The uploads, their limits and how each is served are in the table of ADR 0092 and in the threat model
section *Uploads without a virus scan*.

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V5.1.1 | Documented types, sizes and unpacked sizes | The table per upload in ADR 0092; the unpacked size of a font is a setting. `docs/adr/0092-uploads-validated-by-structure-not-by-a-virus-scanner.md`, `docs/settings.md` | Closed |
| V5.2.2 | Content checked against its claimed type | Images by magic bytes and metadata stripped; fonts by their WOFF2 header and table directory. `app/domain/images.py`, `app/domain/branding.py`, `tests/domain/test_branding.py`, `tests/fakes/woff2.py` (#385) | Closed |
| V5.4.1 | Download names chosen by the server | Five modules set a `content-disposition`; the test pins them and the pattern. `tests/admin/test_download_headers.py` (#338, #379) | Closed |
| V5.4.2 | Download names safe in headers | Letters, digits, dots and hyphens only. `tests/admin/test_download_headers.py`, `app/domain/configuration_package.py` | Closed |
| V5.4.3 | Downloaded files scanned for malware | **Deviation recorded in ADR 0092 and in the threat model**: no scanner, with compensating controls (closed type list recognised by structure, no active content, administrators only, exact media type, `nosniff`, strict CSP). `docs/adr/0092-uploads-validated-by-structure-not-by-a-virus-scanner.md`, `docs/threat-model.md` | Limitation |

## V6 Authentication

OpenPlanner has no passwords of its own: staff sign in at the identity provider, bots use client
credentials, participants never log in. The password, second-factor and recovery requirements are with
the identity provider (see the last section).

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V6.1.1 | One approved policy against credential attacks | The paths and who owns each control are in `docs/authentication.md`; the bot token endpoint is rate limited. The policy of the identity provider has to be approved by its owner, which the repository cannot do. `docs/authentication.md`, `docs/assurance/idp.md` | Open |
| V6.1.3 | All authentication paths documented together | Three paths with proof, strength and withdrawal in one table. `docs/authentication.md` (#344) | Closed |
| V6.3.1 | Credential-stuffing controls in place | Machine API: the token endpoint is rate limited. Staff: the identity provider's policy; the application only checks the token. The identity provider's evidence is not in a checklist yet. `app/api/agent/token.py`, `tests/api/test_agent_api.py` | Open |
| V6.3.2 | No default accounts | The application has no accounts of its own and no break-glass login. The accounts and realm of the production identity provider and host have to be shown from there. `docs/authentication.md`, `docs/adr/0090-bundled-identity-provider-for-test-and-lab-installations.md` | Open |
| V6.3.4 | No undocumented authentication path | Every admin route declares a permission and the matrix test sweeps all of them; the other entry points are in the authentication document. `tests/admin/test_authorization_matrix.py`, `tests/support/routes.py`, `docs/authentication.md` | Closed |

## V7 Session management

All of this chapter is ADR 0098 (steps 1 to 4 built) plus the second-factor requirement of ADR 0100.

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V7.1.1 | Idle and absolute lifetime documented | 60 minutes idle, 8 hours absolute, a login younger than 60 minutes for changes; settings with bounds. `docs/authentication.md`, `docs/adr/0098-admin-sessions-held-on-the-server.md`, `docs/settings.md` | Closed |
| V7.1.2 | Parallel sessions decided | At most five per person; the one idle longest lapses; a login is never refused. `app/domain/admin_sessions.py`, `tests/domain/test_admin_sessions.py` (#350) | Closed |
| V7.1.3 | Session lifetimes coordinated with the identity provider, tested | Logout at the provider is followed (RP-initiated logout); a person switched off is ended by an administrator or by suspension. Not tested against the client's real provider, and back-channel logout is not built. `docs/authentication.md`, `docs/runbook.md` | Open |
| V7.2.4 | New session identifier at login | Every login creates a row and a reference and ends the one the browser brought. `app/admin/session.py`, `tests/admin/test_admin_sessions.py` (#350) | Closed |
| V7.3.1 | Inactivity timeout enforced | The register compares `last_seen_at` with `admin_idle_minutes` on every request. `app/domain/admin_sessions.py`, `tests/domain/test_admin_sessions.py` (#350) | Closed |
| V7.4.1 | Logout ends the session | Logout sets `ended_at` first; a copied cookie is dead; a restore ends all sessions. `app/adapters/db/admin_sessions.py`, `tests/admin/test_admin_sessions.py`, `tests/adapters/db/test_admin_session_queries.py`, `tests/test_cli_sessions.py` (#350, #381) | Closed |
| V7.4.2 | Sessions end when a person leaves | Suspending or erasing a person ends their sessions; *End all sessions* under `staff.manage`. `app/domain/staff_lifecycle.py`, `app/admin/session_routes.py`, `tests/admin/test_session_routes.py`, `tests/domain/test_staff_lifecycle.py` (#399, #402) | Closed |
| V7.4.5 | Administrators can end sessions | Same: one person's sessions from the staff record, all of them from the host CLI. `app/domain/admin_session_service.py`, `tests/admin/test_session_routes.py` (#399) | Closed |
| V7.5.2 | A user can see and end their sessions | *My sessions* lists the own sessions and ends the others. `app/admin/session_routes.py`, `tests/admin/test_session_routes.py` (#399) | Closed |
| V7.6.1 | Re-authentication with the provider as documented | Needs a test with the client's identity provider, which sets its own maximum time between sign-ins. `docs/assurance/idp.md`, `docs/authentication.md` | Open |

## V9 Self-contained tokens

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V9.2.2 | A token accepted only for its purpose | An ID token as bearer on the machine API, a bot token as admin cookie and similar mix-ups are refused. `tests/api/test_token_confusion.py` (#338), `tests/adapters/test_oidc.py` | Closed |

## V10 OAuth and OIDC

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V10.2.2 | Defence against mix-up between providers | The provider is stored with the login in progress and checked at the callback; the issuer is pinned per provider. `app/admin/session.py`, `tests/admin/test_login_provider.py`, `docs/adr/0097-the-oidc-issuer-is-pinned-and-may-differ-from-the-discovery-address.md` | Closed |
| V10.3.4 | Required authentication strength checked | A second factor can be required from the ID token (`amr`, `acr`); the profile of bot clients is written down. `app/domain/mfa.py`, `tests/domain/test_mfa.py`, `docs/authentication.md`, `docs/adr/0100-admin-login-requires-a-second-factor-shown-by-the-identity-provider.md` | Closed |
| V10.4.4 | Only the grants a client needs | The token endpoint knows `client_credentials` and nothing else. `app/api/agent/token.py`, `tests/api/test_agent_api.py` | Closed |
| V10.4.9 | Tokens can be revoked in the interface | An administrator disables or deletes a client; rotating a secret revokes its tokens. `docs/authentication.md`, `tests/api/test_agent_api.py` | Closed |
| V10.4.10 | Confidential clients authenticate | The secret is checked on every token request; it is stored as a hash. `app/api/agent/token.py`, `app/domain/api_clients.py`, `tests/domain/test_api_clients.py` | Closed |
| V10.4.11 | Only the scopes needed | A client holds the scopes and groups an administrator granted. `app/domain/api_access.py`, `tests/domain/test_api_access.py` | Closed |
| V10.5.3 | Issuer fixed, not taken from the discovery document | `OIDC_ISSUER` or the issuer stored at registration, compared exactly at discovery and in every token; `oidc-check` compares it too. `app/adapters/oidc.py`, `tests/adapters/test_oidc.py`, `tests/adapters/test_oidc_check.py` (#329, #336) | Closed |

## V11 Cryptography

The inventory of every key, algorithm, owner, rotation and destruction is `docs/cryptography.md`.

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V11.1.1 | A key management policy | `docs/cryptography.md`, with rotation in `docs/runbook.md`. `app/domain/key_rotation.py`, `tests/domain/test_key_rotation.py` (#392) | Closed |
| V11.1.2 | An inventory of keys and algorithms | Overview table and one section per key in `docs/cryptography.md`. `tests/adapters/db/test_rotation.py` (#392) | Closed |
| V11.2.2 | Algorithms replaceable without loss | `…_PREVIOUS` keys, a key id on every sealed value and `rekey`. `app/adapters/crypto.py`, `app/keys.py`, `tests/adapters/test_crypto.py`, `tests/test_cli_rekey.py` (#388) | Closed |
| V11.2.3 | At least 128 bits of security | Own keys are 256 bit (AES-256-GCM, HMAC-SHA256, P-256); the key sizes are in the inventory. Keys held by the identity provider are outside the product. `docs/cryptography.md`, `tests/adapters/test_crypto.py` | Closed |
| V11.4.1 | No unapproved hash | Webex signs its webhooks with HMAC-SHA1 and offers nothing else; mitigated by a long random secret, constant-time comparison and replay protection. Starlette's cookie signer also uses SHA-1; the cookie carries a random reference only. Recorded in `docs/cryptography.md` and ADR 0094 point 5 | Limitation |
| V11.4.3 | Same, for signatures and MACs | As V11.4.1. `docs/cryptography.md`, `tests/api/test_webhooks.py` | Limitation |
| V11.6.1 | Approved algorithms for key generation and signatures | ID tokens: asymmetric algorithms from an allow-list; `private_key_jwt` keys are P-256 generated by `cryptography`. `docs/cryptography.md`, `app/adapters/oidc.py` | Closed |

## V12 Secure communication

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V12.3.1 | Every outbound connection encrypted and validated | Saving an SMTP server without TLS is refused in production and shown as an error on System status; logging and the others are https only. Sending from an already saved plain configuration is refused only from the next minor release (ADR 0091 point 4), so this is not closed yet. `app/domain/channel_settings.py`, `tests/domain/test_channel_settings.py`, `docs/dataflow.md` (#383) | Open |
| V12.3.2 | Certificates verified | httpx and SMTP verify by default; a test pins it. `tests/adapters/test_certificate_verification.py` (#338) | Closed |
| V12.3.3 | Encryption between internal services | **Recorded exception in ADR 0091 point 2**: the hop from the bundled proxy to web is plain HTTP on the bridge of one host. A proxy on another machine uses `WEB_TLS_CERT_FILE` and `WEB_TLS_KEY_FILE`, and System status warns while it does not. `docs/adr/0091-every-hop-that-carries-personal-data-is-encrypted.md`, `app/domain/web_transport.py`, `tests/domain/test_web_transport.py`, `docs/runbook.md` (#386) | Limitation |

## V13 Configuration

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V13.1.1 | One list of outbound connections | The table *Outbound connections and the hop to web* in `docs/dataflow.md`; a PR that adds a connection adds its row. `docs/adr/0091-every-hop-that-carries-personal-data-is-encrypted.md` (#386) | Closed |
| V13.3.1 | A secrets store and its management | The application reads bootstrap secrets from the environment or `X_FILE` and keeps integration secrets sealed. The secret store of the production host, its access and its recovery are the host's. `docs/cryptography.md`, `docs/assurance/hosting.md` | Open |
| V13.3.2 | Least privilege on secrets | Same: the readers are listed per key in the inventory; the file rights on the host are the host's. `docs/cryptography.md`, `tests/test_config_files.py` | Open |
| V13.4.2 | Debug off in production | Debug mode is off and the interactive documentation answers 404 in production (also the machine API's page); third-party loggers stay at WARNING whatever `LOG_LEVEL` says; a test pins the production flags. `app/logging.py`, `tests/test_logging.py`, `tests/test_production_flags.py` (#409) | Closed |
| V13.4.3 | No directory listing | the static files are served without directory listing; the proxy has no file server; the edge check expects 404 on `/static/`. `scripts/check-proxy.sh`, `deploy/proxy/Caddyfile` | Closed |
| V13.4.4 | TRACE and odd methods refused | The edge check expects 405 on `TRACE`. `scripts/check-proxy.sh`, `tests/scripts/test_check_proxy.py` | Closed |

## V14 Data protection

The classification and retention of every kind of data is the *Retention matrix* in `docs/dataflow.md`
(ADR 0102).

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V14.1.1 | Sensitive data classified | The retention matrix lists every table and every derived copy. `docs/dataflow.md`, `docs/adr/0102-retention-open-closures-backups-and-what-remains-after-a-purge.md` (#395) | Closed |
| V14.1.2 | Protection rules per class | Same matrix, with the control and the term per row. `docs/dataflow.md`, `tests/admin/test_privacy_sweep.py` | Closed |
| V14.2.1 | No sensitive data in URLs | The management link is exchanged once for a cookie and the browser lands on a reference that authorises nothing. `app/api/link_session.py`, `tests/api/test_participant_link.py`, `docs/adr/0093-the-management-link-is-exchanged-for-a-participant-cookie.md` (#391, #393) | Closed |
| V14.2.2 | Sensitive data not cached | HTML is `no-store`; the proxy does not cache and the edge check asserts it; a purge removes derived copies. `app/web/templates.py`, `scripts/check-proxy.sh`, `tests/adapters/db/test_purge_remains.py` | Closed |
| V14.2.4 | Retention enforced, gaps repaired | The purge job, a nightly sweep for stragglers, a maximum for bookings on hold under an open closure, daily counts instead of old rows, and the invariant test. `app/domain/retention_sweep.py`, `tests/domain/test_purge_invariant.py`, `tests/domain/test_retention_sweep.py`, `tests/adapters/db/test_booking_rollup.py` (#328, #394, #395, #401) | Closed |
| V14.3.1 | Authenticated data cleared at logout | The session ends on the server at logout and HTML is `no-store`, but no `Clear-Site-Data` header is sent and no decision has been taken on it. `app/web/templates.py`, `app/admin/session.py` | Open |
| V14.3.3 | No sensitive data in browser storage | The admin cookie holds a random reference and preferences, no name, role or claim; the participant cookie holds a random value. `tests/admin/test_admin_sessions.py`, `tests/web/test_cookies.py` (#350) | Closed |

## V15 Secure coding and architecture

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V15.1.1 | Update terms per component | *Update and remediation terms* below. `docs/adr/0051-dependency-currency.md`, `renovate.json`, `.github/workflows/freshness.yml` | Closed |
| V15.1.2 | Components tracked (SBOM) | A CycloneDX SBOM of the image is scanned by grype in every PR and attached to releases; every pin has a Renovate tracker. `.github/workflows/ci.yml`, `.github/workflows/main.yml`, `scripts/check_pins.py`, `docs/dependencies.md` | Closed |
| V15.1.3 | Costly functions identified, with limits | *Heavy functions and their limits* below. `docs/settings.md` | Closed |
| V15.2.2 | Availability under heavy use | The defences exist (limits, caps, timeouts, bounded queues); proving them needs a capacity and failure trial on the target environment. `docs/adr/0036-scaling-envelope-and-path-beyond-one-vm.md`, `tests/jobs/test_fault_injection_jobs.py` | Open |
| V15.3.4 | The visitor's real address passed on safely | As V4.1.3: trusted proxy address, replaced `X-Forwarded-For`, address check; the forged-header test on the real edge is missing. `docker-compose.yml`, `docs/runbook.md`, `tests/api/test_instance.py` | Open |
| V15.3.5 | Duplicate or wrongly typed parameters | Tests with doubled query and form fields and wrong JSON types. `tests/api/test_parameter_pollution.py` (#338) | Closed |
| V15.3.6 | No prototype pollution in JavaScript | *Widget review* below; a test keeps the dangerous constructs out. `widget/src/dom.js`, `tests/test_asvs_doc.py` | Closed |
| V15.3.7 | HTTP parameter pollution handled | Same tests; for a doubled field Starlette takes the last value and the tests pin what the routes do. `tests/api/test_parameter_pollution.py` | Closed |

## V16 Logging and error handling

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| V16.1.1 | Log inventory per layer | *Log inventory per layer* below, with `docs/logging.md` for the application. `docs/logging.md` | Closed |
| V16.2.3 | Logs only go to documented places | The only destinations are stdout, the audit table and the destinations a superuser adds (none by default, a host kill switch stops them). `app/adapters/log_shipping.py`, `tests/adapters/test_log_shipping.py`, `tests/admin/test_logging_routes.py` (#296, #299) | Closed |
| V16.2.5 | No sensitive data in logs | Masking of the PII fields, drivers and libraries held at WARNING, no bound values in database exceptions. `app/logging.py`, `tests/test_logging.py`, `tests/adapters/db/test_engine.py` (#303, #409, #389) | Closed |
| V16.3.3 | Security events for attacks | A matrix ties every abuse case in the threat model to its event, and fault injection shows it fires. `tests/test_threat_model_events.py`, `tests/api/test_security_events.py` (#387) | Closed |
| V16.3.4 | Events for errors in security controls | A TLS error at an outbound call and a database fault leave an event with a code and no personal data. `tests/adapters/test_fault_injection_outbound.py`, `tests/api/test_fault_injection_booking.py` (#387, #389) | Closed |
| V16.5.2 | Graceful failure of a dependency | Provider timeouts roll back, leave an event with a code and no half-made booking. `tests/api/test_fault_injection_booking.py`, `tests/jobs/test_fault_injection_jobs.py` (#387) | Closed |
| V16.5.3 | Fail closed | A failed login check, a database fault or an unreachable provider grants nothing. `tests/admin/test_fault_injection_login.py` (#387) | Closed |

## Further controls

The audit added 16 controls of its own; these seven were *partly substantiated*.

| ID | The audit asked for | Where the measure is | Status |
|---|---|---|---|
| TEN-03 | Organisations separated under concurrent requests | A parallel A/B trial with two organisations through web and worker. `tests/admin/test_tenant_isolation_concurrent.py` (#312) | Closed |
| PI-01 | A complete record of data and derived identifiers | The retention matrix. `docs/dataflow.md`, `CLAUDE.md` (#395) | Closed |
| PI-02 | A maximum for data kept on hold | `CLOSURE_MAX_RETENTION_DAYS` with notice to administrators and the date shown to the participant. `app/domain/closure_retention.py`, `tests/domain/test_closure_retention.py`, `tests/api/test_closure_pages.py` (#394, #398) | Closed |
| PI-03 | Repair of a missed purge | A nightly sweep purges overdue data, `purge-overdue` on the CLI, an invariant test. `app/domain/retention_sweep.py`, `tests/domain/test_retention_sweep.py`, `tests/adapters/db/test_retention_sweep.py` (#328) | Closed |
| PI-04 | Rollback kept apart from disaster recovery | Update back-ups are pruned by the worker and their age is on System status; the restore procedure ends sessions and runs the purge. `app/adapters/db/backup.py`, `tests/adapters/db/test_backup.py`, `docs/runbook.md` (#404) | Closed |
| PI-05 | Exact words for what a purge leaves | Provider links, event messages and the token hash are wiped; the fixed term is deleted, not made anonymous; old rows become daily counts. `app/domain/privacy.py`, `tests/domain/test_privacy.py`, `tests/adapters/db/test_purge_remains.py` (#395, #401) | Closed |
| PI-09 | Data flow updated before log destinations are active | The destinations are in the outbound table and the data flow before any can be switched on. `docs/dataflow.md`, `docs/threat-model.md` (#297, #299) | Closed |

## Encoding architecture

How a value is treated on its way in and out. Nothing is decoded twice by us; the framework decodes
the request once and we validate the result.

| Context | Rule | Source |
|---|---|---|
| Request decoding | Starlette decodes paths, queries, forms and JSON once. We decode three things ourselves: the client id and secret of a Basic header (RFC 6749 §2.3.1, once, after base64), the Markdown of the admin manual (raw HTML off, repository files only), and the action attributes of our own rendered forms | `app/api/agent/token.py`, `app/web/help.py`, `app/admin/frozen_forms.py` |
| HTML | Jinja2 with autoescape for every `.html`, undefined variables raise; the one `Markup` is the manual renderer, whose input is repository text | `app/web/templates.py`, `tests/web/test_help.py` |
| Messages | Fixed layouts, text blocks from the catalogue, organisation override or group text, placeholders from an allow-list only; administrators never edit Jinja or HTML | `app/domain/message_texts.py`, `tests/domain/test_message_texts.py` |
| CSS | The theme stylesheet is generated from validated tokens; no `url()`, selector or `!important` can be uploaded | `app/domain/theme.py`, `tests/domain/test_theme.py` |
| URLs | Built from slugs and ids; links an administrator enters are plain `https` links without credentials; redirect targets are in the threat model | `app/domain/contacts.py`, `docs/threat-model.md` |
| SQL | SQLAlchemy with bound parameters; no raw SQL outside `app/adapters/db/` | `app/adapters/db/uow.py`, `docs/architecture.md` |
| Headers and file names | Download names are built by the server; no request value reaches a response header | `tests/admin/test_download_headers.py` |
| CSV | A cell that starts like a formula gets a leading quote | `app/domain/audit_export.py`, `tests/domain/test_audit_export.py` |
| Logs | The structlog processor masks the PII fields on every line, also from standard-library loggers | `app/logging.py`, `tests/test_logging.py` |
| Operating system | `app/` starts no process and builds no shell command | `tests/test_asvs_doc.py` |
| Regular expressions | Every pattern is a literal or built from module constants | `tests/test_asvs_doc.py` |
| Widget | DOM text nodes only; never `innerHTML`; see *Widget review* | `widget/src/dom.js`, `tests/test_asvs_doc.py` |

## Validation per entry point

One row per entry that takes input from outside. *Rules across fields* are the rules that look at more
than one value or at stored state. Limits that are settings are in `docs/settings.md` with their
bounds.

| Entry | Input and limits | Validated in | Rules across fields | Tests |
|---|---|---|---|---|
| Booking form `POST /g/{slug}/book` and widget booking `POST /api/v1/widget/{org}/{group}/bookings` | First and last name up to 100 characters without control characters; e-mail up to 254 characters; telephone as E.164 by the group's mode; custom answers from a closed type catalogue, at most 15, key `[a-z][a-z0-9_]{1,39}`, text up to 100 or 1000 characters; the widget body refuses unknown keys | `app/domain/booking.py`, `app/domain/form_fields.py`, `app/api/widget_schemas.py` | The slot must exist, belong to the group, lie in the booking window and have room; at most N bookings per e-mail address per group (ADR 0066); the honeypot stays empty; `RATE_LIMIT_BOOKINGS` per address | `tests/domain/test_booking.py`, `tests/domain/test_form_fields.py`, `tests/domain/test_services.py`, `tests/api/test_widget_api.py`, `tests/api/test_booking_pages.py` |
| Availability reads | Month and day as dates, language `nl` or `en` | `app/api/widget_api.py`, `app/api/slot_picker.py` | Group must exist and the origin must be allowed (widget); `RATE_LIMIT_READS` | `tests/api/test_widget_api.py`, `tests/api/test_slot_picker.py` |
| Participant link `/b/{token}` and `/b/m/{ref}/…` | 32-byte token compared in constant time; reference and cookie value random | `app/domain/tokens.py`, `app/domain/participant_links.py`, `app/api/link_session.py` | The cookie's session must hold the booking; a change needs time before the start (ADR 0069); `RATE_LIMIT_UNKNOWN_LINKS` | `tests/api/test_token_confusion.py`, `tests/api/test_participant_link.py`, `tests/api/test_unknown_link_limit.py` |
| Direct call `POST /g/{slug}/direct` | Same contact fields as a booking | `app/domain/booking.py`, `app/domain/call_service.py` | Offered only in a staffing block; cap on waiting plus connected calls (ADR 0026) | `tests/api/test_direct_pages.py` |
| Machine API `/api/agent/v1/…` | Token request body up to 4096 bytes, `client_credentials` only; availability range of at most 62 days | `app/api/agent/token.py`, `app/api/agent/routes.py` | The client reaches only its granted groups and scopes; `RATE_LIMIT_AGENT_TOKENS` and `RATE_LIMIT_AGENT_CALLS` | `tests/api/test_agent_api.py`, `tests/domain/test_api_access.py` |
| Webhooks `/webhooks/webex` and `/webhooks/wxcc` | Body up to 64 KB; signature of the raw body; timestamp within 300 seconds (WxCC) | `app/api/webhooks.py`, `app/adapters/webhooks.py` | Replay protection by event key (Webex); 600 per minute | `tests/api/test_webhooks.py` |
| Public address round trip `GET /api/v1/instance?probe=` | A random nonce held by the worker | `app/api/instance.py`, `app/domain/public_address.py` | A guessed nonce gets no detail and does not burn the pending one | `tests/api/test_instance.py` |
| Admin forms | Per form in `app/admin/forms.py`, then the domain service of the object; every state change needs the permission and a login younger than `ADMIN_REAUTH_MINUTES` | `app/admin/forms.py`, `app/domain/authorization.py` | Rights from the matrix and the scope, filtered in SQL; Fetch Metadata check on every `POST` | `tests/admin/test_authorization_matrix.py`, `tests/admin/test_admin_flow.py` |
| Settings | Each setting has a class, a type and bounds; a value outside the bounds is refused | `app/domain/settings_registry.py` | Environment lock outside the bounds is warned about | `tests/domain/test_settings_registry.py`, `tests/test_settings_doc.py` |
| Uploads (logo, closure image, font, theme file, own certificate, configuration package) | The table in ADR 0092: type by structure, size, unpacked size | `app/domain/images.py`, `app/domain/branding.py`, `app/domain/theme.py`, `app/domain/own_certificate.py`, `app/domain/configuration_package.py` | Permission, recent login, same-origin | `tests/domain/test_images.py`, `tests/domain/test_branding.py`, `tests/admin/test_branding_routes.py` |
| Login callback `GET /admin/callback` | `state`, `nonce`, PKCE verifier and ID token checked; a pinned issuer | `app/adapters/oidc.py` | The provider stored with the login in progress must be the one that answers | `tests/adapters/test_oidc.py`, `tests/admin/test_login_provider.py` |
| Host CLI | Arguments of `python -m app.cli`, run by the host operator only | `app/cli.py` | Same settings bounds as the portal | `tests/test_cli.py` |

## Native components

OpenPlanner has no native code of its own. Native code arrives with its dependencies and with the
base image:

| Component | Why it is there | Where it is tracked |
|---|---|---|
| `cryptography` (Rust and OpenSSL wheels) | AES-GCM, HKDF, PEM handling, signing keys; also used by PyJWT | `docs/dependencies.md`, `pyproject.toml` |
| `pydantic-core` (Rust) | Validation inside Pydantic v2 | `pyproject.toml` |
| `uvicorn[standard]` extras (`uvloop`, `httptools`) | Event loop and HTTP parser of the web server | `pyproject.toml`, `Dockerfile` |
| SQLite and the C library of the Python image | The database | `Dockerfile` |
| Caddy in the proxy image | TLS termination | `deploy/proxy/Dockerfile` |

What watches them: the lock file is checked and audited in every PR (`pip-audit`), the built image is
turned into a CycloneDX SBOM that grype scans (a high finding with a fix fails the build), trivy scans
the image and the proxy image for CVEs and misconfiguration, and Renovate opens the update. The SBOM is
attached to each release. We make no claim about the memory safety of these components beyond what
their maintainers publish; we claim that a known vulnerability in them is found and acted on within the
terms below. `.github/workflows/ci.yml`, `.github/workflows/main.yml`, `docs/runbook.md`.

## Update and remediation terms

| What | Term | Source |
|---|---|---|
| Routine updates of every tracked version (Python, npm, images, actions, tools) | Renovate runs Monday before 06:00 (Europe/Amsterdam); a release must be 3 days old unless it fixes a vulnerability | `renovate.json` |
| A vulnerability alert (advisory databases and OSV) | A PR is opened at any time, without the 3-day wait | `renovate.json` |
| An update PR left open | The weekly `freshness` job fails when one is older than 14 days, or when the Dependency Dashboard was not refreshed within 8 days | `.github/workflows/freshness.yml`, `scripts/check-renovate-alive.sh` |
| A pinned version Renovate cannot see | `make pins` fails the PR | `scripts/check_pins.py`, `docs/adr/0051-dependency-currency.md` |
| Merging an update | Renovate updates are build work and merge when every check is green | `docs/adr/0078-merge-on-green-decisions-by-the-product-owner-audit-before-a-minor-release.md` |
| A security fix | Released the same working day it is merged, as a patch release when only fixes wait | `docs/adr/0052-release-and-versioning-policy.md`, `docs/runbook.md` |
| A deprecation warning | Fails the build | `scripts/check-deprecations.sh` |
| A known vulnerability in a dependency or the image | Fails the PR: `pip-audit`, `npm audit`, grype (high, with a fix), trivy | `.github/workflows/ci.yml` |

There is no separate term per severity (for example critical in 72 hours). The ceilings above apply to
all updates, and a security update skips the waiting period. A term per severity is an optional
decision of the product owner and is not taken.

## Heavy functions and their limits

Functions that can use much time, memory or disk, and what stops them.

| Function | Limit | Source |
|---|---|---|
| CSV export of the audit log | `AUDIT_EXPORT_MAX_ROWS` (100 000) and `AUDIT_EXPORT_MAX_DAYS` (366); more is refused, never streamed without bound | `app/domain/audit_export.py`, `tests/admin/test_audit_routes.py` |
| Configuration package, export and import | `CONFIGURATION_PACKAGE_MAX_MB` (64) checked before the body is read; sealed with scrypt; superuser with a recent login | `app/domain/configuration_package.py`, `tests/adapters/db/test_configuration_package.py` |
| Uploads | Size per file and per request body, unpacked size of a font (`MAX_FONT_UNPACKED_BYTES`), refused before parsing | `docs/adr/0092-uploads-validated-by-structure-not-by-a-virus-scanner.md` |
| Group check (probe work item) | `INTEGRATION_PROBE_TIMEOUT_SECONDS` (60) | `app/adapters/group_check.py`, `tests/domain/test_integration_check.py` |
| Outbound HTTP | Timeout and a response cap of 1 000 000 bytes through the guard | `app/adapters/outbound.py`, `tests/adapters/test_outbound.py` |
| Roll-up of old booking rows | Batches of 200 | `app/domain/booking_rollup.py`, `tests/domain/test_booking_rollup.py` |
| Key rotation | `rekey` runs from the host CLI, one pass, idempotent | `app/cli.py`, `tests/test_cli_rekey.py` |
| Log shipping | A bounded queue per destination (oldest line dropped when full), batches, backoff, a line longer than 65 536 bytes is not queued | `app/adapters/log_shipping.py`, `tests/adapters/test_log_shipping.py` |
| Public bookings and reads | 10 bookings per hour and 60 reads per minute per address, 20 unknown links per hour | `docs/settings.md`, `app/domain/settings_registry.py` |
| Machine API | 10 token requests and 120 calls per minute per client; a range of at most 62 days | `app/api/agent/routes.py`, `docs/settings.md` |
| Webhooks | Body up to 64 KB, 600 per minute | `app/api/webhooks.py`, `tests/api/test_webhooks.py` |
| Public form bodies | 64 KB on public paths, 1 MB elsewhere, slow-client timeouts at the proxy | `deploy/proxy/Caddyfile`, `scripts/check-proxy.sh` |
| Security events | One event per kind and caller per window, at most `SECURITY_EVENT_MAX_SOURCES` windows | `app/domain/security_log.py`, `tests/domain/test_security_log.py` |

## Widget review

Review of `widget/src/` (about 640 lines in six files) for prototype pollution and for DOM injection,
done at the time of this document; the widget has no runtime dependency (`widget/package.json` lists
esbuild as a build tool only).

| Question | Finding |
|---|---|
| Does any code merge or copy server data into an existing object (`Object.assign`, spread of a response, a deep merge)? | No. The only copies are `Object.fromEntries` over keys the code built, which creates own properties and cannot set a prototype |
| Does any code use a key from the server as a property name? | One place: the answers to custom fields use the name of the field minus `q_` as a key (`widget/src/openplanner-booking.js`). The server only accepts keys that start with a lowercase letter and contain letters, digits and underscores (`app/domain/form_fields.py`), so `__proto__` and `constructor` cannot occur, and the booking request refuses unknown keys (`app/api/widget_schemas.py`) |
| Texts: is `texts[key]` read with a key from the server? | No, only with keys written in the code. A response with a key such as `__proto__` is parsed by `JSON.parse` into an own property and is never read |
| Is anything from the server evaluated or written as markup? | No. `eval`, `new Function` and `innerHTML` do not occur; all text goes through `document.createTextNode` and attributes through `setAttribute` with names from the code (`widget/src/dom.js`) |
| Does the widget send credentials or store data? | The calls use `credentials: 'omit'`; nothing is written to storage or cookies |

The test `tests/test_asvs_doc.py` fails when a later change adds `innerHTML`, `insertAdjacentHTML`,
`outerHTML`, `eval`, `new Function`, `document.write`, `__proto__`, `Object.assign` or `setPrototypeOf`
to `widget/src/`, so the review has to be redone on purpose.

## Log inventory per layer

Where a line can be written, by which layer, and what it holds. Nothing else stores or sends logs. The
application's own record, levels and trace points are in `docs/logging.md`; the data in each line is
limited by the masking of the PII fields.

| Layer | What is logged | Where it goes | Kept | Controlled by | Source |
|---|---|---|---|---|---|
| Application, `web` and `worker` | JSON lines with level, trace and codes; the PII fields are masked; uvicorn's access log is off because paths carry tokens | Standard output | Docker `json-file`, 5 files of 20 MB per container, compressed | `LOG_LEVEL` (C); only our own loggers follow it, every other logger stays at WARNING | `app/logging.py`, `docker-compose.yml`, `tests/test_logging.py` |
| Application database | Audit, system and security events: ids, slugs and codes, never PII fields | Table `audit_events` | `AUDIT_RETENTION_DAYS` | Retention in the portal | `docs/audit-events.md`, `docs/dataflow.md`, `tests/domain/test_event_schema.py` |
| Log destinations | Copies of the masked lines and the chosen event categories | Syslog over TLS, Splunk, Elastic or an HTTP collector, each a destination a superuser added; none by default | The platform's | Portal (superuser); the host switch `LOG_SHIPPING_ENABLED` stops all | `app/adapters/log_shipping.py`, `docs/logging.md`, `tests/admin/test_logging_routes.py` |
| Container driver | Everything the containers write to standard output and error | Docker `json-file` on the host; the `splunk` driver or an agent reading the files is optional | See above | The host | `docker-compose.yml`, `docs/runbook.md` |
| Host | Docker daemon, operating system and the updater (`journalctl -u openplanner-updater`) | The host's journal and files | The host's | The hosting party | `scripts/updater/openplanner-update.sh`, `docs/assurance/hosting.md` |
| Proxy | **No access log** (paths carry management tokens); Caddy's own start-up, certificate and error messages | Standard output of the proxy container, then as above | As above | `deploy/proxy/Caddyfile` | `deploy/proxy/Caddyfile`, `scripts/check-proxy.sh` |
| Identity provider | Sign-ins and failures at the provider; OpenPlanner logs its own `oidc.*` events without claim values | The provider's own logs | The provider's | The provider's owner | `docs/assurance/idp.md`, `docs/authentication.md` |
| Providers (Webex, Webex Contact Center, SMS gateway, mail relay) | They keep their own logs. OpenPlanner logs host, status and duration of its calls and the provider's reason on the booking's timeline, never the URL, a header or a body | The providers' logs; our `outbound.request` line | The providers' | The processor agreements | `app/adapters/http_trace.py`, `docs/assurance/dpia-input.md`, `docs/assurance/cisco.md` |

## Evidence that only an outside party can give

These requirements depend on the client's identity provider, the host, Cisco or the controller. Each is
in exactly one checklist in `docs/assurance/`, and a test keeps it that way
(`tests/test_assurance_docs.py`). The application's own evidence report is `python -m app.cli
evidence` (runbook *Evidence report for an audit*).

| Checklist | Requirements |
|---|---|
| `docs/assurance/idp.md` | V6.1.2, V6.2.1 to V6.2.12, V6.3.3, V6.4.1 to V6.4.4, V6.5.1 to V6.5.5, V6.6.1 to V6.6.3, V6.8.4, V7.4.3, V7.5.1, V9.2.4, V10.4.1 to V10.4.3, V10.4.5, V10.4.6, V10.4.8, V10.6.1, V10.6.2, V10.7.1 to V10.7.3, V11.4.2, V11.4.4 |
| `docs/assurance/hosting.md` | V4.2.1, V12.1.1, V12.1.2, V12.2.1, V12.2.2, V12.3.4, V13.2.1 to V13.2.5, V15.2.1, V16.2.2, V16.4.2, V16.4.3, TEN-06, PI-08 |
| `docs/assurance/cisco.md` | V17.1.1, V17.2.1 to V17.2.4, V17.3.1, V17.3.2 |
| `docs/assurance/dpia-input.md` | V14.2.3, PI-06, PI-07, PI-10 |
