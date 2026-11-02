# 0013. Admin login with OIDC: PingFederate in production, Keycloak in development

Status: accepted (2026-10-23). Resolves open point O3 in part; refines "OIDC: authlib" in
CLAUDE.md. §1 refined by ADR 0097 (the issuer is pinned, not taken from discovery). §3 (claim
mapping) amended by ADR 0042 §8 (class C) and ADR 0049 (entitlements). Extended by ADR 0046,
0083 and 0090. ADR 0098 (proposed) would supersede §5; §5 stands until 0098 is accepted and
built. Note (2026-11-01 review): the session also holds the login's roles, organisations,
groups and claim values and UI preferences (`app/admin/session.py`), more than §5 says.

## Context

Only the admin portal has a login; citizens never log in (HANDOVER B8). The organisation's
identity provider will be **PingFederate**. Roles, organisation and groups come from the
identity provider; there is no user table (B8). Development and CI need a provider that behaves
the same way without access to PingFederate.

authlib 1.9 marks its httpx integration as deprecated ("use httpx2"). Under the test setting
`filterwarnings = error` it cannot be imported, and it is not a base to build on. Libraries must
be actively maintained and the de-facto standard in their field (CLAUDE.md, `docs/dependencies.md`).

## Decision

1. **Protocol.** OpenID Connect authorization code flow with PKCE (S256), `state` and `nonce`
   (32 random bytes each), confidential client. Endpoints come from the provider's discovery
   document (`OIDC_DISCOVERY_URL`); the issuer in it is the only accepted `iss`.
2. **Libraries.** `httpx` for the three calls (discovery, token, JWKS) and **PyJWT**
   (`pyjwt[crypto]`, MIT, the de-facto JWT library for Python) for ID-token verification. Only
   RS256/ES256/PS256 are accepted; `alg: none` and HMAC are refused. `iss`, `aud` (and `azp` with
   several audiences), `exp`, `iat` and `nonce` are checked, with 60 s leeway. No crypto is
   implemented by us. This replaces authlib in CLAUDE.md.
3. **Claims are configuration.** Which claim holds the roles, the organisation slugs and the
   group slugs, and which values mean superuser, org_admin and group_admin, is set per
   deployment (`OIDC_ROLE_CLAIM`, `OIDC_ORG_CLAIM`, `OIDC_GROUPS_CLAIM`, `OIDC_ROLE_*`). For
   PingFederate this is the attribute contract of the OIDC policy. The claim values are
   organisation and group **slugs**.
4. **Scope.** The identity becomes a domain `Scope` (superuser, organisation slugs, group
   slugs). `require_scope` is the only way into admin routes; every admin query filters by the
   scope. Superuser is a scope that matches everything, not a bypass.
5. **Session.** Signed cookie (`op_admin`, path `/admin`, `HttpOnly`, `SameSite=Lax`, `Secure` in
   production, 8 h absolute lifetime) holding the subject, display name and scope only. The
   session is replaced at login (no fixation). `SESSION_SECRET` is a bootstrap secret (ADR 0008);
   development generates a shared one in the data volume, like the token key (ADR 0011).
6. **Development and CI.** Keycloak in the `dev` compose profile with an imported realm:
   one test user per role plus an outsider from another organisation, protocol mappers that
   emit the same claims. Credentials in that realm file are development-only test values. They
   let developers, CI (Playwright) and Claude Code walk the admin UI as every role. There is no
   login bypass or role switcher in the application.

## Consequences

- PingFederate needs: an OIDC client (confidential, PKCE allowed, redirect URI
  `https://<host>/admin/callback`), an access token manager, and claims for roles,
  organisations and groups in the ID token. The runbook lists them.
- Who maintains role and group membership in PingFederate (O3, identity management) is still
  an organisational question.
- Logout ends the local session **and** the session at the provider (OIDC RP-initiated
  logout via `end_session_endpoint`, with `client_id` and `post_logout_redirect_uri`). Without
  it, single sign-on logged the admin straight back in, which a walk-through as several roles
  showed. PingFederate must allow `https://<host>/admin/logged-out` as post-logout redirect.
