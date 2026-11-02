# Login: an organisation's identity providers

This screen shows which identity providers (login services) the staff of an organisation log
in with, and lets the superuser register one. Open it through **Organisations → organisation
→ Overview → Login**. Organisation admins and auditors see the status of their own
organisation; registering, changing, switching on or off and removing is the superuser's and
needs a recent login.

Without a provider of its own, staff log in with a platform account. An organisation's own
provider grants rights inside its own organisation only: a value naming another organisation
or the superuser is refused (ADR 0083). An organisation may have at most two, so a move from
one to the next leaves no gap; the superuser can change that maximum under **Advanced
settings**.

## Registering a provider

First register OpenPlanner at the provider, with the **redirect URI** shown on the provider's
page and, for a key pair, the **JWKS address**. Then fill in the form and choose **Register**.
The discovery document is fetched and checked at once: the address must be https and publicly
reachable, the issuer must belong to the address, and the provider must support the code flow
with PKCE S256, an asymmetric signature and the chosen client authentication. A refused check
is in the audit log with its reason.

A new provider is off. Do a **test login** first, then switch it on.

<!-- field: key -->
### Key

Lowercase letters, digits and hyphens, unique on the installation and fixed after
registration. Every identity that logs in through this provider starts with it
(`idp:<key>:<sub>`), so two providers never produce the same identity.

<!-- field: discovery_url -->
### Discovery address

The address of the OpenID Connect discovery document, ending in
`/.well-known/openid-configuration`. Https only; an address on an internal network or a
redirect is refused.

<!-- field: client_auth -->
### Client authentication

By default **key pair (private_key_jwt)**, as the NL GOV profile prescribes: OpenPlanner
generates an EC P-256 key pair per provider, keeps the private key encrypted and publishes the
public key at the JWKS address. The private key is never shown. Choose **client secret** only
when the provider cannot do private_key_jwt; the secret is stored encrypted and never shown
again.

<!-- field: client_secret -->
### Client secret

Only with a client secret. The field is always empty; leaving it empty keeps the stored secret.

<!-- field: scopes -->
### Scopes

Separated by spaces. `openid` is required; `email` is needed to confirm the address's domain at
a real login.

<!-- field: entitlement_claim -->
### Entitlement claim and prefix

The claim in the ID token that carries the rights, and the prefix every value must carry. The
defaults are the platform's; the values themselves are on the **Identity provider mapping**
screen.

## Test login

**Test login** sends you to the provider. After logging in you see which claims came back
(names only), whether `email_verified` was true, the domain of the email address and which
rights the entitlement values resolve to. No session is started; your own session stays as it
was. The verdict is in the audit log as `idp.test_login`.

## Second factor (MFA policy)

Each provider has an **MFA policy** (ADR 0100). The provider itself asks for the second factor;
OpenPlanner only checks that the ID token says it was used, by an `amr` or `acr` value you accept
(empty: `amr` `mfa otp hwk swk`, no `acr`). **Off** ignores it. **Record** (the default) lets the
login through and writes a security event, `admin.login.mfa_missing`, with the provider's key
and no personal data. **Require** refuses the login with the message that the account shows no
second factor. The test login shows whether a second factor showed, so choose **Require** only
after a test login with MFA says *yes*; otherwise every admin of the organisation is locked out.

## Domains

Under **Domains** the superuser decides which e-mail domains belong to this provider; a domain
also covers its subdomains. Once the host allows organisation providers
(`ORGANISATION_IDPS_ENABLED`) and a switched-on provider has a verified domain, the login screen
first asks for the **work e-mail address or user name**. Someone who enters an address on a
domain of this provider logs in there; any other address, and every user name under the suffix
of the bundled identity provider, goes to the platform provider. **Log in with a platform
account** skips the question.

A login through this provider succeeds only when the ID token carries a confirmed e-mail
address (`email_verified`) on one of its own domains, and grants rights inside this
organisation only. Switching the provider off ends the sessions that started through it at
their next request.

1. Enter the domain (for example `gemeente.nl`) and choose **Add domain**.
2. Have the organisation publish the TXT record shown in its DNS: name
   `_openplanner-challenge.<domain>`, value `openplanner-domain-verification=…`.
3. Choose **Verify**. When the record is found, the domain shows **Verified**.

A domain belongs to one provider on the whole installation. The suffix of the bundled identity
provider (`openplanner.internal` by default, and everything under it) is reserved for the
platform and cannot be claimed. When the record disappears later, the domain shows **Record
gone, still routing** (the worker re-checks every verified domain every 24 hours, *Re-check of login domains*, and reports a change as a system event); after the grace period (7 days by default, *Grace for a missing domain
record*) it becomes **Lapsed** and logins go to the platform provider again. **Remove**
releases the domain at once.

## Rotating the key, switching on or off, removing

**Rotate key** generates a new key pair; the old public key stays in the JWKS for 24 hours, so
an exchange in progress does not break. **Switch off** and **Remove** end the logins through
this provider (removing also erases the secret and the keys). Every change is in the audit
log with the provider's key, never with a secret.
