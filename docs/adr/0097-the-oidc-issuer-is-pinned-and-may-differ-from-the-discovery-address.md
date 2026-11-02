# 0097. The OIDC issuer is pinned, and may differ from the discovery address

Status: accepted (2026-11-01), requested by the product owner after the ASVS 5.0.0 L2 audit of
1 November 2026 (V10.5.3). Refines ADR 0013 (OIDC) and ADR 0083 (providers per organisation).
Built (merged in #329); see *Build status*.

## Context

The OIDC adapter took the issuer for the ID-token check from the discovery document itself
(`metadata["issuer"]`). Whoever controls that document therefore also controls the issuer the
tokens are checked against; OIDC Discovery §4.3 and Core §3.1.3.7 require the issuer to be
the one the relying party expects. For an organisation provider registration already
compares the document with its address, but tolerated a trailing slash, and the adapter did
not use the stored value afterwards.

The obvious expected issuer for the platform provider is the discovery URL without its
`/.well-known/openid-configuration` suffix. That is wrong in our own shape of installation:
Keycloak with `KC_HOSTNAME_BACKCHANNEL_DYNAMIC` (the dev compose and the bundled `idp` of
ADR 0090) is reached by the web container as `keycloak:8080` or `idp:8080`, but announces
`KC_HOSTNAME` as its issuer.

## Decision

1. **The expected issuer is pinned.** `OidcConfig.expected_issuer` is what the discovery
   document must announce and every ID token must carry in `iss`; both are compared exactly,
   with no trailing-slash tolerance. A mismatch refuses the login (`discovery_failed` or
   `invalid_token`) and logs `oidc.issuer_mismatch` without any value.
2. **Platform provider:** `OIDC_ISSUER` (class B). Empty: derived from `OIDC_DISCOVERY_URL`
   (`issuer_of`). Set it when the provider announces another address than the one it is
   fetched on.
3. **Organisation providers:** the issuer stored at registration (`registration.issuer`),
   which registration has already compared exactly with the discovery URL. A later change of
   the provider's document cannot move it.
4. `check_discovery` compares exactly, so a registration whose document announces the issuer
   with another trailing slash is refused.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `OIDC_ISSUER` | B deployment | empty (derived) | an https or, in development, http URL | environment | Tied to how the host reaches the IdP; not C or D because it decides who is trusted to sign in, so it is not editable in the portal. |

**Fixed on purpose:** the exact comparison and the refusal of a mismatch are not a setting;
switching a protection off is never C or D.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | `expected_issuer`, `OIDC_ISSUER`, exact checks, abuse case in the threat model | #329 | merged |

## Consequences

An installation whose IdP announces another issuer than its discovery URL names must set
`OIDC_ISSUER` before updating, or its admin login stops (the log says `oidc.issuer_mismatch`).
`.env.example` sets it for the dev Keycloak; the runbook names it for the bundled `idp`.
`docs/settings.md`, `docs/threat-model.md` (abuse case) and `docs/runbook.md` are updated.
