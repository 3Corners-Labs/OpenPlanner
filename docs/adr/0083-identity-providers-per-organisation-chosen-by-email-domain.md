# 0083. Identity providers per organisation, chosen by email domain

Status: accepted (2026-10-31), requested and approved by the product owner; partly built (step 1 merged, step 2 in PR). Amends ADR 0013
(one identity provider, its issuer the only accepted `iss`), ADR 0042 §8 (the OIDC client is
class A; a second identity provider was rejected as a fallback) and ADR 0046 (a staff record is
keyed by the IdP subject). Builds on ADR 0008 (encrypted integration secrets), ADR 0015 (fresh
login), ADR 0049 (entitlements) and ADR 0073 (DNS lookups with `dnspython`).

## Context

The admin portal trusts exactly one OpenID Connect provider, configured in the environment
(`OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_SCOPES`, class A). The
superuser cannot add one in the portal. That fits one client with one IdP team. The product
owner wants OpenPlanner to serve several organisations on one installation, each with its own
staff in its own identity provider (Entra ID, PingFederate, Keycloak, …), and the staff
member's email domain to decide which provider they log in at. It should follow industry
standards wherever they exist.

What is in the way today:

1. **One issuer.** `OidcIdentityProvider` holds one client registration; the callback knows
   one provider; the session does not record where a login came from.
2. **One identity namespace.** `staff.subject`, the audit `actor` and every `created_by` hold
   the bare `sub`. OIDC only promises that `sub` is unique *within* an issuer (OIDC Core §2):
   two providers may hand out the same `sub` for two people.
3. **Every provider could grant everything.** Rights come from the claims (ADR 0049). The
   product owner rejected a fallback IdP on 2026-10-28 (ADR 0042 §8) because "whoever controls
   that IdP becomes superuser". An organisation's IdP that can grant any entitlement has the
   same flaw.
4. **No outbound guard.** The app fetches only fixed or environment-given URLs today. A
   discovery URL typed in the portal makes the server fetch an address chosen by an admin
   (server-side request forgery, OWASP A10:2021).

Industry practice for this is settled. Webex Control Hub, Microsoft Entra ID, Okta and Google
Workspace all do *home realm discovery*: the person types an email address, the domain picks
the identity provider, and the organisation must first prove it owns the domain with a DNS TXT
record. The standards that apply:

| Concern | Standard |
|---|---|
| Provider configuration | OpenID Connect Discovery 1.0 (`.well-known/openid-configuration`) |
| Login, hint to the provider | OpenID Connect Core 1.0, `login_hint` (§3.1.2.1); `email`, `email_verified` (§5.1) |
| Code flow protections | RFC 7636 (PKCE), RFC 9700 (OAuth 2.0 Security Best Current Practice) |
| Mix-up between providers | RFC 9207 (`iss` in the authorisation response), RFC 9700 §4.4 |
| Client authentication | RFC 7523 / OIDC Core §9 `private_key_jwt`; `client_secret_basic` as the fallback |
| Government profile | NL GOV Assurance profile for OAuth 2.0 and OpenID Connect (Logius): PKCE, `private_key_jwt`, asymmetric signatures |
| Logout | OpenID Connect RP-Initiated Logout 1.0 |
| Provisioning (later) | SCIM 2.0, RFC 7643 / 7644 (ADR 0046 step 3) |
| Outbound fetches | OWASP SSRF Prevention Cheat Sheet |

OpenID Federation 1.0 (trust chains between providers and relying parties) solves the same
problem without manual registration. It is not yet final and not used by the IdPs of our
clients; we keep the registration explicit and look at federation again when a client asks for
it (see *Consequences*).

## Decision

### Two kinds of provider

1. **The platform provider** stays as it is: configured in the environment (class A), the only
   one that can grant platform-wide rights (`superuser`, roles with global reach) and the
   only one with the legacy and shadow claim modes of ADR 0049. An installation without
   organisation providers behaves exactly as today: `/admin/login` redirects straight to it.
2. **An organisation provider** belongs to exactly one organisation. It is registered by the
   superuser in the portal and can only grant rights inside that organisation:
   - Its logins are resolved in `entitlements` mode only (ADR 0049 point 13), whatever
     `OIDC_CLAIM_MODE` says.
   - Every entitlement must name the provider's own organisation (`<prefix>:<role>:<org>` or
     `<prefix>:<role>:<org>/<group>`, the organisation segment equal to the organisation's
     `idp_key`). A value naming another organisation, `superuser`, or a role whose reach is
     global (built-in or custom) is refused with `outside_provider_scope` and audited as
     `auth.entitlement_refused`, never granted.
   - This rule sits in the domain (`app/domain/entitlements.py`), in the same `resolve` that
     every request goes through, so a session keeps the restriction after login.

   This answers the objection of 2026-10-28: an organisation provider is not a second way to
   become superuser, and whoever controls it reaches only the organisation that chose it.
3. **At most `idp_max_per_organisation` providers per organisation** (default 2, for a
   migration from one IdP to the next). Each has a key (`IDP_KEY_PATTERN` of ADR 0049), a
   display name and an on/off switch.

### Registration (superuser only)

4. **New permissions** `identity_providers.view` (organisation level: org admin and auditor
   see the status of their own organisation's providers) and `identity_providers.manage`
   (global, in `LOCKED_PERMISSIONS`: superuser only, fresh login per ADR 0015). Every change is
   audited (`idp.created`, `idp.updated`, `idp.enabled`, `idp.disabled`, `idp.deleted`,
   `idp.domain_verified`, `idp.domain_failed`), with the provider key, never a secret.
5. **What the superuser enters:** the discovery URL, the client id, the client authentication
   method, the scopes (default `openid profile email`), the entitlement claim name and prefix
   (defaults from the platform settings), and the email domains.
6. **Client authentication follows the NL GOV profile:** `private_key_jwt` (RFC 7523) is the
   default. OpenPlanner generates an EC P-256 key pair per provider, keeps the private key
   encrypted under `SETTINGS_ENCRYPTION_KEY` (ADR 0008, write-never: it is never shown or
   exported), and publishes the public keys of all providers at `GET /admin/jwks.json` (one
   JWKS, `kid` per provider). The superuser can rotate a key; the old one stays published for
   24 hours. `client_secret_basic` is the alternative for an IdP that cannot do
   `private_key_jwt`; that secret is stored as in ADR 0008 (encrypted, write-only).
7. **Discovery is checked when saved** and again before every login whose cached metadata is
   older than an hour (as today): the document must be valid OIDC Discovery, its `issuer` must
   equal the discovery URL's base (OIDC Discovery §4.3), and it must offer the `code` flow,
   `S256` PKCE, an asymmetric `id_token_signing_alg_values_supported` and, for
   `private_key_jwt`, that method in `token_endpoint_auth_methods_supported`. The issuer must
   differ from the platform provider's and from every other provider's.
8. **Outbound guard (SSRF).** Every server-side fetch of a provider address (discovery, JWKS,
   token endpoint) goes through one guard in the adapter layer: `https` only; the host resolves
   only to public addresses (no loopback, private, link-local, carrier-grade NAT, multicast
   or cloud metadata ranges, IPv4 and IPv6), checked on the address actually connected to;
   no redirects; a timeout and a response size cap. The platform provider keeps its own
   environment-given URL; only `ENVIRONMENT=development` or `test` may relax the guard, for a
   provider on the lab host (refused in production, like the other development defaults).
9. **A guided test login.** Before a provider is switched on, the superuser can start a test
   login through it (as the mapping dry run of ADR 0042 §8): the portal shows which claims
   arrived (names only), whether `email_verified` was true, the domain, and which grants the
   entitlements resolve to, without starting a session.

### Domains and the choice of provider

10. **A domain must be verified before it routes logins**, the way Control Hub, Entra ID and
    Google Workspace do it: the portal shows a TXT record
    `_openplanner-challenge.<domain>` with value `openplanner-domain-verification=<token>`
    (32 random bytes, per domain and provider); the superuser presses *Verify*; the lookup uses
    `dnspython` (ADR 0073). A verified domain also covers its subdomains.
11. **A domain belongs to at most one provider on the installation**, verified or not; a
    second claim is refused. At most 20 domains per provider.
12. **The domain is re-checked** every `idp_domain_recheck_hours`. A record that disappears
    raises a warning system event for the superuser; after `idp_domain_grace_days` without the
    record the domain stops routing (`idp.domain_failed`). Development and test may skip the
    DNS check (`ENVIRONMENT` other than production), so a lab with `.test` domains works.
13. **The login page asks for the email address first** when at least one organisation
    provider is on. `GET /admin/login` shows one field (`autocomplete="username"`, a visible
    label, WCAG rules of CLAUDE.md) and a link *Log in with a platform account*. The address
    is sent by `POST`, never in a URL, and is not stored or logged; only its domain is used.
    The longest verified domain suffix on a label boundary picks the provider; no match picks
    the platform provider. The provider gets the full address as `login_hint`.
14. **The choice is bound to the login.** The pending login in the session records the provider
    key next to `state`, `nonce` and the PKCE verifier. The callback uses only that provider;
    it checks the `iss` parameter of RFC 9207 when the provider sends it (and requires it when
    discovery announces `authorization_response_iss_parameter_supported`), and the ID token's
    `iss` against that provider's metadata. One redirect URI (`/admin/callback`) serves all
    providers.
15. **The ID token must confirm the domain.** For an organisation provider the `email` claim
    must be present, `email_verified` must be `true`, and the address's domain must match one
    of that provider's verified domains. Otherwise the login is refused
    (`email_domain_mismatch` or `email_not_verified`) and audited. This stops a provider from
    signing in people of a domain it does not own.

### Identity and sessions

16. **The identity key becomes provider-qualified.** The platform provider's subjects stay the
    bare `sub`, so existing staff records, audit history and `created_by` values keep their
    meaning without a data migration. An organisation provider's subject is stored as
    `idp:<provider key>:<sub>`. A platform login whose `sub` starts with `idp:` is refused; a
    qualified subject longer than 255 characters is refused. The staff record gets the
    provider key (empty for the platform), shown as a column and filter in the staff
    directory.
17. **The session records its provider.** Every request resolves the claims with that
    provider's restriction (point 2). A provider that is switched off or deleted, or whose
    organisation is deleted, ends its sessions at the next request. A fresh login (ADR 0015)
    and the logout (RP-Initiated Logout) go to the session's own provider.
18. **One person, one provider per login.** Someone who works for two organisations with two
    providers logs in twice as two identities. Rights across organisations need the platform
    provider. This is deliberate: no provider can speak for an organisation other than its own.
19. **Host tools.** `app.cli oidc-check --idp <key>` checks one organisation provider (discovery,
    keys, guard, client authentication method) without a login; `app.cli idp disable <key>`
    switches one off from the host when the portal cannot be reached, audited as
    `system:cli` (ADR 0042 §10). No CLI command adds a provider: adding stays in the portal,
    behind a superuser login.

### Abuse cases

Each becomes a test in the step that builds it and a row in `docs/threat-model.md`.

| # | Abuse | Mitigation |
|---|---|---|
| B1 | An organisation provider issues `superuser` or a global role | refused `outside_provider_scope` (point 2) |
| B2 | An organisation provider issues `org_admin:<other org>` | refused `outside_provider_scope` (point 2) |
| B3 | A provider signs in `someone@<domain of another organisation>` | `email` domain must be one of the provider's verified domains (point 15) |
| B4 | A provider omits `email_verified` or sets it false | refused `email_not_verified` (point 15) |
| B5 | Mix-up: the response of provider X is replayed into a login started at provider Y | provider bound to `state`; RFC 9207 `iss`; ID-token `iss` per provider (point 14) |
| B6 | Two providers hand out the same `sub` | provider-qualified subject (point 16) |
| B7 | A platform `sub` crafted as `idp:<key>:<sub>` to impersonate an organisation admin | platform subjects starting with `idp:` are refused (point 16) |
| B8 | Discovery URL pointing to `169.254.169.254`, `localhost` or an internal host, or a DNS rebinding | outbound guard on the connected address, no redirects (point 8) |
| B9 | Claiming a domain the organisation does not own | DNS TXT verification; one provider per domain (points 10, 11) |
| B10 | A domain lapses or changes owner after verification | periodic re-check, grace period, routing stops (point 12) |
| B11 | Probing which domains have a provider through the login page | accepted: the redirect reveals the provider anyway, as at every SaaS with home realm discovery; the existing rate limit applies; nothing about the person is returned |
| B12 | The email address leaks into a URL or log | `POST` only; only the domain is used; nothing stored (point 13) |
| B13 | An org admin adds or changes a provider for their own organisation | `identity_providers.manage` is locked to the superuser (point 4) |
| B14 | A provider is disabled but a session from it lives on | the session's provider is checked on every request (point 17) |
| B15 | The private key of `private_key_jwt` leaks through the portal or a backup export | write-never, encrypted under `SETTINGS_ENCRYPTION_KEY`; only the public key is published (point 6) |

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `ORGANISATION_IDPS_ENABLED` | B deployment | `false` | – | – | Kill switch: off hides the pages, routes every login to the platform provider and ends organisation-provider sessions. Class B because it opens a new trust boundary that the host team should agree to. |
| `idp_max_per_organisation` | C platform | 2 | 1–5 | `settings.platform.manage` | Two allows moving from one IdP to the next without a gap. |
| `idp_domain_recheck_hours` | C platform | 24 | 1–168 | `settings.platform.manage` | DNS re-check of verified domains (point 12). |
| `idp_domain_grace_days` | C platform | 7 | 0–30 | `settings.platform.manage` | How long a missing TXT record is tolerated before the domain stops routing. |

**Fixed on purpose:** organisation providers resolve in `entitlements` mode only and never
grant global rights (the core of this decision); `email_verified` must be true (OIDC Core
§5.1); `https`, public addresses and no redirects for provider fetches in production (OWASP
SSRF); PKCE S256 and asymmetric ID-token signatures (RFC 9700, NL GOV profile); one provider
per domain; 20 domains per provider; the key type P-256 for `private_key_jwt`; the 24-hour
overlap after a key rotation.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR accepted; row in `docs/adr-status.md` | #248 | merged |
| 1 | Groundwork, no behaviour change: provider-qualified subjects and the `idp:` refusal (`app/domain/identity.py`, the OIDC adapter); the provider key on the pending login, checked at the callback (the platform provider is the only entry until step 2); the provider of a staff record follows from its qualified subject (`provider_of`), so `staff` needs no new column; the outbound guard (`app/adapters/outbound.py`); the dev realm `andere` | #250 | merged |
| 2 | Registration: `identity_providers` and `identity_provider_keys` tables (migration 0079), the permissions `identity_providers.view` and `identity_providers.manage` with their matrix rows, the *Login* sub-tab of the organisation overview (ADR 0047 §7: a seventh top-level tab would need an ADR, so it sits in the second row with the other organisation-wide settings), the discovery check (`app/domain/identity_providers.py`, fetched through the guard by `app/adapters/discovery.py`), `client_secret_basic` (secret sealed, write-only) and `private_key_jwt` (EC P-256 per provider, `app/adapters/signing_keys.py`) with `GET /admin/jwks.json` and key rotation (24 h overlap), the guided test login through `/admin/callback` without a session, the audit events `idp.created`, `idp.updated`, `idp.enabled`, `idp.disabled`, `idp.deleted`, `idp.key_rotated` and `idp.test_login`, the setting `idp_max_per_organisation`, the manual topic `organisation-login` | #272 | in PR |
| 3 | Domains and routing: domain claims, DNS TXT verification and the re-check job, the email-first login page (nl, en), `login_hint`, RFC 9207 check, email-domain check at the callback, scope restriction in `resolve`, the kill switch | | planned |
| 4 | Operations: `oidc-check --idp`, `idp disable`, staff directory filter, system events and the status on *System status*, logout and fresh login per provider | | planned |
| 5 | Docs and tests end to end: threat model rows B1–B15, `docs/dataflow.md`, `docs/architecture.md` diagram, runbook (registering an IdP, the TXT record, key rotation), manual topic for the superuser, e2e login through the second realm | | planned |

## Consequences

- **Easier:** onboarding an organisation with its own IdP needs no redeploy and no host team;
  each organisation keeps its own joiners and leavers process; moving from one IdP to the next
  can run side by side.
- **Harder:** one more trust boundary and one more outbound path, both in the threat model;
  the superuser takes on registration and key rotation; the login page gains a step when
  organisation providers are on.
- **Not changed:** the platform provider and its environment configuration; the break-glass
  decision of 2026-10-28 (no local account, no fallback provider that can reach everything);
  participants still never log in.
- **Migrations:** one for `identity_providers` and its domains and keys, one for the provider
  column on `staff`; both reversible. Existing subjects are not rewritten.
- **Dependencies:** none new. PyJWT already signs the `private_key_jwt` assertion and
  `cryptography` makes the key; `dnspython` is in since ADR 0073.
- **Later:** SCIM per provider (ADR 0046 step 3); OpenID Federation 1.0 when it is final and a
  client's IdP supports it, which would replace manual registration with trust chains;
  group-based routing rules besides domains, as Control Hub offers.
