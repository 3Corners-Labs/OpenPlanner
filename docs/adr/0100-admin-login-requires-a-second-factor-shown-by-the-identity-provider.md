# 0100. Admin login requires a second factor, shown by the identity provider

Status: accepted (2026-11-01), requested by the product owner after the question about MFA,
step-up and four-eyes during ADR 0095. Refines ADR 0013 (OIDC) and ADR 0090 (bundled identity
provider). Built in PR #357; see *Build status*.

## Context

Admins hold broad rights over groups of participants. A password alone is a weak proof, and
OpenPlanner has no accounts of its own (ADR 0013, no break-glass login). The best practice is
that the identity provider owns multi-factor authentication (enrolment, recovery, policy) and
the relying party verifies the proof: the `amr` claim (RFC 8176) and the `acr` claim (OIDC
Core §2), asked for with `acr_values` (OIDC Core §3.1.2.1). The product owner chose the small
version: only MFA, configured at the IdP, requested and checked by OpenPlanner.

## Decision

1. **MFA is the IdP's.** A provider of the organisation (PingFederate) is the IdP team's to
   configure. The bundled Keycloak (ADR 0090) is configured by `deploy/keycloak/realm.template.json`:
   `acr` `mfa` is level 2 (`acr.loa.map`), the client asks for it by default, and the browser
   flow asks for an authenticator app (OTP) or a security key (WebAuthn) at that level.
   `super` must register an OTP at the first login (`CONFIGURE_TOTP`).
2. **Request.** `start_login` adds `acr_values` (`OIDC_ACR_VALUES`, default `mfa`) when not
   empty. An IdP that does not know the value ignores it (it is voluntary, Core §3.1.2.1).
3. **Verify.** `finish_login` (and the trial and test logins, which share the token check)
   refuses an ID token that shows no second factor with `IdentityError("mfa_required")`. The
   proof is `amr` containing one of `mfa`, `otp`, `hwk`, `swk` (a fixed rule in
   `app/domain/mfa.py`), or an `acr` equal to one of the values asked for. Any other `acr`,
   and any `amr` of another type, is no proof. Fails closed: a missing claim is a refusal.
4. **Refusal.** The login callback answers 401 with a clear text (`admin.login_refused.mfa_required`,
   nl and en), records the security event `admin.login` with reason `mfa_required` for
   `anonymous`, and logs `oidc.mfa_missing` with the provider key only. No claim value, name or
   address reaches a log or event.
5. **Scope.** The switch applies to the platform provider. An organisation provider
   (ADR 0083) is the organisation's own; whether to require MFA of it is a later decision.
6. **The switch.** `ADMIN_MFA_REQUIRED` (class B, default `true`). Production refuses to start
   with `false` (a `production_problems` entry); `dev`, `test` and `lab` may set it, and the
   dev Keycloak and the e2e stack do. A protection is never switched off from the portal.
7. **Self-service at the bundled Keycloak.** The realm sets `resetPasswordAllowed`, so the
   login prompt offers *Forgot password*, and a user changes the password and registers the
   authenticator app again (delete the old OTP, add a new one) under *Signing in* in the
   Keycloak account console. Both are the IdP's, not OpenPlanner's. Not built here: limiting
   this to users whose email domain suffix is active on the bundled Keycloak (ADR 0083 domain
   routing), and the realm's outgoing mail (SMTP) that the reset link needs; both are a
   follow-up and are listed in *Build status*.
8. **Locked out** (lost authenticator, a policy that sends no `amr`): fixed at the IdP; no
   break-glass login (ADR 0042 §8). `docs/runbook.md` says how, with the bundled Keycloak and
   with the host CLI.
9. **Not now.** Step-up per action (the half-built `RequirePermission(fresh=True)` with
   `ADMIN_REAUTH_MINUTES`) asking for MFA again, and four-eyes approval of a sensitive change
   by a second admin, are later options, built only on request.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `ADMIN_MFA_REQUIRED` | B deployment | `true` | bool; production refuses `false` | environment | A protection: switching it off is never C or D. Not A: the login works without it. |
| `OIDC_ACR_VALUES` | B deployment | `mfa` | space separated; empty asks for nothing | environment | Tied to what the IdP's policy is called; wrong values lock the login, so not in the portal. |

**Fixed on purpose:** the accepted `amr` values (`mfa`, `otp`, `hwk`, `swk`) are the standard
of RFC 8176, not a tunable.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 2 | Self-service reset limited to the active domain suffix; realm SMTP | | follow-up |
| 1 | `app/domain/mfa.py`, `acr_values` and the check in `app/adapters/oidc.py`, the two settings, login message, Keycloak realm flow, runbook, threat model, manual | #357 | in PR |

## Consequences

An installation whose IdP does not emit `amr`/`acr` stops logging in until the IdP team maps
its MFA policy or `ADMIN_MFA_REQUIRED=false` is set (not in production). The Keycloak flow in the
realm template is checked by a structure test; a live run on the bundled `idp` belongs to
the next host test. `docs/settings.md`, `.env.example`, `docs/runbook.md`,
`docs/threat-model.md` and `docs/manual/{nl,en}/staff.md` are updated.
