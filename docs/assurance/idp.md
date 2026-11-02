# Assurance checklist: the identity provider

For the IdP administrator and the CISO of the client (PingFederate for the first client, ADR
0013). OpenPlanner has no accounts, passwords or second factors of its own for staff (ADR 0013,
ADR 0042 §8); the identity provider (IdP) does all of it. These 43 ASVS 5.0.0 requirements
therefore cannot be proven from this repository. They are proven by a configuration export or a
screenshot from the IdP, signed by the owner named in each row. How to use this file:
`docs/assurance/README.md`. The requirement texts are the OWASP ASVS 5.0.0 (CC BY-SA 4.0); this
file gives the ID and a short description in our own words.

## What OpenPlanner does itself, and where the proof is

The application side of the login is built and tested in the repository. Hand these to the
auditor next to the IdP evidence.

| Item | Evidence | Source |
|---|---|---|
| The login is OIDC authorisation code with PKCE, `state` and `nonce`; asymmetric algorithms only | `docker compose exec web python -m app.cli oidc-check` (discovery, signing algorithms, signing keys, redirect URL, back-channel https; no secrets printed) | ADR 0013; `app/adapters/oidc_check.py`; runbook *Nobody can log in* |
| The issuer is pinned: the discovery document and every ID token must name exactly the expected issuer | `oidc-check` compares the pinned issuer and names `OIDC_ISSUER` when it differs from the discovery address; a mismatch refuses the login and logs `oidc.issuer_mismatch` | ADR 0097; PR #329 and #336 |
| A second factor is requested (`acr_values`) and checked (`amr`/`acr` in the ID token); a login without one is refused (`mfa_required`) | `ADMIN_MFA_REQUIRED=true` and the accepted values in `.env`; the security event `admin.login` with reason `mfa_required`; the test login of the Login sub-tab reports whether a second factor showed | ADR 0100; PR #357 |
| Admin sessions live on the server, can be ended, and end after an idle and an absolute time | `python -m app.cli sessions end-all --reason cli`; the session views in the portal | ADR 0098; PR #350, #381, #399 |
| Keys and secrets of the login (client secret, `SESSION_SECRET`) | the inventory entries *OIDC client secrets and keys* and *`SESSION_SECRET`* | `docs/cryptography.md`; ADR 0094 |

Open on our side, not claimed here: the `acr` value PingFederate emits is not known (tracker
S-WP7). Until the IdP team names it, `ADMIN_MFA_REQUIRED` stays `false` for a PingFederate
installation, and the rows for the second factor below stay open for it (ADR 0100 point 6).

## How to read the Keycloak column

The bundled Keycloak (ADR 0090) is a reference, not a claim about PingFederate. The column says
what `deploy/keycloak/realm.template.json` sets, so an IdP administrator can compare. Where it
says *not set by the template*, the value is Keycloak's own default for the version pinned in
`docker-compose.yml`; confirm it in the admin console before quoting it. A production
installation that uses the bundled Keycloak (`--idp bundled`, ADR 0090 point 1) owns these
rows itself and answers them from that console.

## Password policy and password handling

| ID | Asks for | How to show it | Owner | Keycloak reference |
|---|---|---|---|---|
| V6.1.2 | A written list of context words (organisation, product, system names) that may not be used in passwords | The documented list and where the IdP enforces it | Client IdP administrator | Not set by the template; *Authentication → Policies → Password policy*, a "not contains" or blacklist rule |
| V6.2.1 | Minimum password length of at least 8, with 15 advised | Screenshot or export of the password policy | Client IdP administrator | `passwordPolicy` is not set by the template: set a length rule in *Authentication → Policies* |
| V6.2.2 | A user can change their own password | Screenshot of the self-service page, or a recorded test | Client IdP administrator | Account console, *Signing in* (ADR 0100 point 7) |
| V6.2.3 | Changing a password asks for the current and the new one | Test record of the change flow | Client IdP administrator | Account console password change |
| V6.2.4 | New passwords are checked against a list of at least the 3000 most common | Policy export naming the list | Client IdP administrator | Not set by the template; a blacklist policy with a file of common passwords |
| V6.2.5 | No composition rules (no demand for upper case, digits or symbols) | Policy export showing no such rule | Client IdP administrator | Not set by the template; leave out the character-class rules |
| V6.2.6 | Password fields are masked | Screenshot of the login page | Client IdP administrator | Default login theme |
| V6.2.7 | Paste, browser helpers and password managers are allowed | Test record: paste into the password field works | Client IdP administrator | Default login theme |
| V6.2.8 | The password is checked exactly as typed, no truncation or case change | IdP product statement or test with a mixed-case, long password | Client IdP administrator | Keycloak product behaviour; test it |
| V6.2.9 | Passwords of at least 64 characters are accepted | Test record with a 64-character password | Client IdP administrator | Test it; no limit is set by the template |
| V6.2.10 | No forced periodic rotation | Policy export showing no expiry rule | Client IdP administrator | Not set by the template; do not add a password-age rule |
| V6.2.11 | The context-word list of V6.1.2 is actually enforced | Test record: a password built from a listed word is refused | Client IdP administrator | Same policy as V6.1.2 |
| V6.2.12 | New passwords are checked against a set of breached passwords | Policy export or product statement naming the source | Client IdP administrator | Not set by the template; blacklist policy or an external check |

## Multi-factor authentication

| ID | Asks for | How to show it | Owner | Keycloak reference |
|---|---|---|---|---|
| V6.3.3 | Access to the portal needs a second factor (a hardware factor at the highest level) | IdP policy export showing MFA for every OpenPlanner role; the security event `admin.login` with `mfa_required` after a test login without one; `oidc-check` and the Login sub-tab test login | Client CISO, IdP administrator, OpenPlanner supplier | Realm flow `openplanner browser` asks for OTP or WebAuthn at level 2 (`mfa`); client `openplanner-admin` asks for `mfa` by default; WebAuthn user verification `required`; ADR 0100 |
| V6.4.1 | Initial passwords and activation codes are random, follow the policy and expire or are spent at first use | Description of the account-creation procedure and a test | Client IdP administrator | `super` gets a generated first password with `UPDATE_PASSWORD` and `CONFIGURE_TOTP` required at the first login (ADR 0090 point 2) |
| V6.4.2 | No password hints or secret questions | Screenshot of the login and recovery pages | Client IdP administrator | Keycloak offers none by default |
| V6.4.3 | Password reset does not bypass an enabled second factor | Test record: reset, then log in; the second factor is still asked | Client IdP administrator | `resetPasswordAllowed` is true; the second-factor subflow still applies after a reset; test it |
| V6.4.4 | Losing a second factor needs identity proofing as strong as at enrolment | The helpdesk procedure for a lost factor | Client CISO, IdP administrator | Admin-console reset of the OTP credential (runbook *Second factor required*); the procedure around it is the client's |
| V6.5.1 | One-time codes and TOTP work once only | IdP product statement or test: reuse a code | Client IdP administrator | Keycloak OTP policy; test it |
| V6.5.2 | Stored lookup secrets with low entropy are hashed with an approved algorithm | IdP product statement | Client IdP administrator | Applies only if recovery codes are used; the template configures none |
| V6.5.3 | Codes and seeds come from a secure random source | IdP product statement | Client IdP administrator | Keycloak product statement |
| V6.5.4 | One-time codes carry at least 20 bits of entropy | IdP product statement or OTP policy export | Client IdP administrator | `otpPolicyType` is `totp`; digits not set by the template |
| V6.5.5 | Out-of-band codes live at most 10 minutes, TOTP at most 30 seconds | OTP policy export | Client IdP administrator | Period and digits are not set by the template; read them in *Authentication → Policies → OTP policy* |

## Out-of-band factors and the use of authentication strength

| ID | Asks for | How to show it | Owner | Keycloak reference |
|---|---|---|---|---|
| V6.6.1 | Codes by phone or SMS only to a verified number, and only when stronger options exist alongside | The list of factors on offer and how SMS is restricted, or "SMS not used" | Client IdP administrator | The template offers OTP app and WebAuthn only, no SMS |
| V6.6.2 | An out-of-band code is bound to the login attempt that asked for it | IdP product statement or test | Client IdP administrator | Not used by the template |
| V6.6.3 | Code-based factors are protected against guessing by rate limiting | Policy export (lockout or throttling) | Client IdP administrator | `bruteForceProtected` is true in the template |
| V6.8.4 | The application verifies the authentication strength the IdP reports, for functions that need it | The `acr`/`amr` values the IdP emits, a sample ID token with the claims (values only, no personal data), and the OpenPlanner settings that accept them | Client CISO, IdP administrator, OpenPlanner supplier | `acr.loa.map` is `{"mfa":2}`; OpenPlanner accepts `amr` `mfa otp hwk swk` by default (ADR 0100 point 3). PingFederate's `acr` is not yet known |

## Sessions and account changes at the IdP

| ID | Asks for | How to show it | Owner | Keycloak reference |
|---|---|---|---|---|
| V7.4.3 | After a change of a login factor the user can end their other sessions | Screenshot of the IdP's session or device page; the offboarding time (see below) | Client IdP administrator, OpenPlanner supplier | Account console, *Device activity*. OpenPlanner side: sessions are server-side and endable (ADR 0098) |
| V7.5.1 | Changing the e-mail address, phone or MFA settings needs a fresh login | IdP product statement or test | Client IdP administrator, OpenPlanner supplier | Account console; test it. OpenPlanner holds no such attributes itself |
| V9.2.4 | A key that signs tokens for several audiences puts the audience in the token | Sample ID token (header and claims, no personal data) showing `aud` | Client IdP administrator | Client `openplanner-admin` gets `aud` equal to its client id; check it in a sample token |

Offboarding is part of V7.4.3 and V7.5.1 and is not a separate requirement: agree and write down
how long after a leaver is disabled at the IdP the person loses access to the portal. The ceiling
on the OpenPlanner side is the idle and absolute session time (ADR 0098); the application does not poll the IdP in between. Chain test: disable a test user at the IdP and measure the
time until the portal refuses the session.

## OAuth authorisation server and OpenID provider

| ID | Asks for | How to show it | Owner | Keycloak reference |
|---|---|---|---|---|
| V10.4.1 | Redirect URIs are matched exactly against a pre-registered list | Client registration export showing no wildcard | Client IdP administrator | `redirectUris` is the single exact URI `<base>/admin/callback` |
| V10.4.2 | An authorisation code works once; a second use fails | IdP product statement or test | Client IdP administrator | Keycloak product behaviour; test it |
| V10.4.3 | The authorisation code is short-lived (at most 10 minutes for level 2) | Export of the code lifespan | Client IdP administrator | Not set by the template (`accessCodeLifespan`); read it under *Realm settings → Tokens* |
| V10.4.5 | Refresh tokens of public clients are protected against replay | Refresh-token policy export, or "no refresh tokens issued" | Client IdP administrator | The client is confidential and the OIDC adapter (`app/adapters/oidc.py`) requests and stores no refresh token; confirm in the client's settings |
| V10.4.6 | PKCE is required for the code flow | Client registration export | Client IdP administrator | `pkce.code.challenge.method` is `S256`; `oidc-check` reads the advertised methods; `python -m app.cli evidence` lists `code_challenge_methods_supported` and `token_endpoint_auth_methods_supported` for the dossier |
| V10.4.8 | Refresh tokens have an absolute expiry | Refresh-token policy export | Client IdP administrator | Not set by the template; see V10.4.5 |
| V10.6.1 | The provider offers only the safe response types | Discovery document (`response_types_supported`) | Client IdP administrator | `implicitFlowEnabled` is false; `oidc-check` prints the discovery result; `python -m app.cli evidence` lists `response_types_supported` and `grant_types_supported` as announced |
| V10.6.2 | Forced logout cannot be abused for denial of service: confirmation, or validated logout parameters | Logout behaviour test or product statement | Client IdP administrator | `post.logout.redirect.uris` is a single exact URI |
| V10.7.1 | The provider ensures consent per authorisation request, or the client is trusted | Client consent setting and the reason | Client IdP administrator | `consentRequired` is not set by the template; first-party client, so consent is off by design; state that decision |
| V10.7.2 | If consent is asked, it names what is being granted | Screenshot of the consent screen, or "no consent screen" | Client IdP administrator | Follows V10.7.1 |
| V10.7.3 | The user can review and withdraw consents | Screenshot, or "not applicable: no consent" | Client IdP administrator | Follows V10.7.1 |

## Password storage inside the IdP

| ID | Asks for | How to show it | Owner | Keycloak reference |
|---|---|---|---|---|
| V11.4.2 | Passwords are stored with an approved, slow hashing function with current parameters | IdP product statement and the hashing settings | Client IdP administrator | Keycloak hashing policy; read it under *Authentication → Policies → Password policy* (not set by the template) |
| V11.4.4 | Keys derived from passwords use an approved function with stretching | Same statement | Client IdP administrator | Same as V11.4.2 |

## Sign-off

| Owner | Name | Date | Evidence reference |
|---|---|---|---|
| Client IdP administrator | | | |
| Client CISO | | | |
| OpenPlanner supplier (rows with a supplier part) | | | |
