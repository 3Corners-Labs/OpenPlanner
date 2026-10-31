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

## Rotating the key, switching on or off, removing

**Rotate key** generates a new key pair; the old public key stays in the JWKS for 24 hours, so
an exchange in progress does not break. **Switch off** and **Remove** end the logins through
this provider (removing also erases the secret and the keys). Every change is in the audit
log with the provider's key, never with a secret.
