# Cryptographic inventory

Every key, secret and cryptographic algorithm of OpenPlanner in one place (ADR 0094, ASVS V11.1.1
and V11.1.2): what it protects, the algorithm and parameters, where it lives, who can read it,
how it is rotated and how it is destroyed. The facts come from the code named in each entry; when
the code changes, this document changes in the same pull request. `docs/threat-model.md` and
`docs/architecture.md` link here; the procedures are in `docs/runbook.md` (*Rotating secrets*).

**Rules that hold for every entry**

- Fixed crypto parameters (algorithms, key lengths, nonce size, key-id length, scrypt cost, HKDF
  labels) are constants, not settings (ADR 0042 and ADR 0094). A change needs a new versioned label
  and an entry here.
- No key, token or plaintext is logged, audited, put in a job payload or in a URL (`CLAUDE.md`).
- A key that protects data at rest is never stored next to that data. The database backup does not
  carry the bootstrap keys, and the bootstrap keys do not travel in a configuration package.
- **Destroying a key** means removing it from the secret store or `secrets/secrets.env`, from every
  shell history and every copy you made for the rotation, after nothing needs it any more. A
  database backup made before a rotation holds values sealed under the old key; keep that key
  exactly as long as you may still restore such a backup, and no longer.
- Bootstrap keys are class A (ADR 0042): environment variables or `X_FILE` only, never the
  database or the portal.

## Overview

| Name | Purpose | Algorithm | Lives in | Rotation |
|---|---|---|---|---|
| `SETTINGS_ENCRYPTION_KEY` | Integration secrets at rest | HKDF-SHA256, AES-256-GCM | Environment, `X_FILE` | `…_PREVIOUS` and `rekey` |
| `MANAGEMENT_TOKEN_KEY` | The participant's change/cancel link; derived pseudonym keys | HMAC-SHA256, HKDF-SHA256 | Environment, `X_FILE` | `…_PREVIOUS`, no re-sealing |
| `SESSION_SECRET` | Signature of the admin session cookie | HMAC (`itsdangerous`, SHA-1) | Environment, `X_FILE` | `sessions end-all`, then replace |
| OIDC client secrets and keys | Login of staff at the identity provider | Client secret, or ES256 `private_key_jwt` | `OIDC_CLIENT_SECRET`; per organisation sealed in the database | Replace; key rotation in the portal |
| API client secrets and tokens | Machine API of contact-center bots | SHA-256 of 256-bit random values | Hash in the database | New secret in the portal |
| Webhook secrets | Inbound events of Webex and WxCC | HMAC-SHA1 (Webex), HMAC-SHA256 (WxCC) | Environment, `X_FILE` | Replace, then `register-webhooks` |
| TLS private key | HTTPS of the bundled proxy | Certificate key (ACME or own) | Host volume, never the database | Portal, new signing request |
| Image signature | Which image may be deployed | cosign keyless (Sigstore) | No key held; GitHub OIDC identity | Not applicable |
| Configuration package passphrase | Secrets inside an exported package | scrypt, AES-256-GCM | In the head of the administrator | New export |

## `SETTINGS_ENCRYPTION_KEY`

- **Purpose.** Encrypts every secret the portal stores: channel secrets (SMTP password, Connect
  service key), the Webex service-app tokens, identity-provider client secrets and
  `private_key_jwt` private keys, and log-destination secrets (ADR 0008). The sealed columns are
  listed once, in `SEALED` in `app/adapters/db/sealed.py`.
- **Algorithm.** The value is stretched with HKDF-SHA256 (label `openplanner integration secrets v1`,
  no salt) into one AES-256 key (`app/adapters/crypto.py`). A value is sealed with AES-256-GCM, a
  fresh 96-bit random nonce and the record name as associated data, so a ciphertext cannot be moved
  to another record. The stored nonce is the 12 nonce bytes followed by the 8-byte key id (the first
  8 bytes of SHA-256 over the derived key); a 12-byte nonce is a value from before key ids and is
  tried under every configured key.
- **Lives in.** `SETTINGS_ENCRYPTION_KEY` (at least 32 bytes), environment or
  `SETTINGS_ENCRYPTION_KEY_FILE`. Production never generates it; development makes one in
  `dev_settings_key_path` (`app/keys.py`).
- **Who can read it.** The host operator, and the `web` and `worker` processes. No portal role, no
  API. Secrets sealed under it are write-only in the portal.
- **Rotation.** Set the old value as `SETTINGS_ENCRYPTION_KEY_PREVIOUS`, the new one as
  `SETTINGS_ENCRYPTION_KEY`, restart, run `python -m app.cli rekey` (one transaction, idempotent, one
  audit event with counts only), remove `…_PREVIOUS` once System status shows 0 records under the
  previous key. Nothing is lost on the way. Procedure: runbook *Rotating `SETTINGS_ENCRYPTION_KEY`*.
- **Destruction.** After `rekey` has succeeded and System status shows 0, remove the previous key.
  Keep it as long as a backup made before the rotation may be restored.
- **Never protect with it.** Passwords or tokens of participants, anything PII (the PII fields are
  plain columns of `bookings` and are removed by `purge_pii`), the TLS private key.

## `MANAGEMENT_TOKEN_KEY`

- **Purpose.** Lets every message carry the booking's change/cancel link again without storing the
  link (ADR 0011). Two further keys are derived from it.
- **Algorithm.** The token is `HMAC-SHA256(key, "manage:" + salt)`, URL-safe base64 without padding
  (`app/domain/tokens.py`). The salt is 32 random bytes per booking; the database keeps the salt and
  `sha256(token)`, compared with `hmac.compare_digest`. Derived keys (`app/keys.py`), each with its
  own HKDF-SHA256 label: the client pseudonym key (ADR 0053, security events) and the agent
  pseudonym key (ADR 0043, who claimed a booking); the pseudonyms themselves are HMAC-SHA256.
- **Lives in.** `MANAGEMENT_TOKEN_KEY` (at least 32 bytes), environment or `X_FILE`. The token is
  never stored, never in a job payload or a log.
- **Who can read it.** The host operator and the `web` and `worker` processes. A copy of the
  database alone cannot produce a link.
- **Rotation.** Set the old value as `MANAGEMENT_TOKEN_KEY_PREVIOUS` and a new random value as
  `MANAGEMENT_TOKEN_KEY`, restart. A message to a booking made earlier derives its token under the
  current key and, if that does not match the stored hash, under the previous key (ADR 0094 point 4),
  so a booking keeps one link for its whole life. Agent pseudonyms are compared under both keys.
  Client pseudonyms start anew. There is nothing to re-seal. Remove `…_PREVIOUS` when System status
  shows 0 open bookings with a link under it.
- **Destruction.** Remove the previous key from the secret store at that point.
- **Never protect with it.** Data at rest (that is `SETTINGS_ENCRYPTION_KEY`); sessions.

## `SESSION_SECRET`

- **Purpose.** Signs the admin cookie (`__Host-op_admin` on https, `op_admin` over http), which
  carries a random session reference and the signed login state (ADR 0013, ADR 0098). The session
  itself is a record on the server (`admin_sessions`, the reference stored only as its SHA-256).
- **Algorithm.** Starlette's `SessionMiddleware` signs with `itsdangerous.TimestampSigner` at its
  default: an HMAC with SHA-1 and a timestamp. This is the signing of a cookie value whose content
  is not secret and whose validity is also checked against the server-side register; it is not used
  for confidentiality. It is recorded here as a known weak digest (ASVS V11.4); the session register
  makes a forged cookie useless without a live session reference.
- **Lives in.** `SESSION_SECRET` (at least 32 random bytes), environment or `X_FILE`; development
  makes one in `dev_session_key_path`.
- **Who can read it.** The host operator and the `web` process.
- **Rotation.** Logs everyone out. In this order: `python -m app.cli sessions end-all --reason
  secret_rotated`, replace the value, restart `web` and `worker` (ADR 0098). A cookie signed with the
  old secret is invalid from the restart on.
- **Destruction.** Remove the old value from the secret store; nothing else holds it.
- **Never protect with it.** Anything that must survive a rotation.

## OIDC client secrets and keys

- **Purpose.** OpenPlanner proves itself to the identity provider when it exchanges the login code
  (ADR 0013, ADR 0083).
- **Algorithm.** PKCE `S256`; `state` and `nonce` are checked; ID tokens are accepted only with
  asymmetric signature algorithms (`ASYMMETRIC_ALGORITHMS`, `app/adapters/oidc.py`). Client
  authentication is `client_secret_basic` (a secret) or `private_key_jwt` (RFC 7523, an ES256 key
  pair that OpenPlanner generates per provider, P-256).
- **Lives in.** The installation's own provider: `OIDC_CLIENT_SECRET` in the environment or
  `X_FILE`. Providers of organisations (ADR 0083): the client secret or the private key sealed under
  `SETTINGS_ENCRYPTION_KEY` (`identity_providers`, `identity_provider_keys`); only the public key is
  published, as a JWKS.
- **Who can read it.** Environment secret: the host operator and the `web` process. Sealed ones:
  nobody, they are write-only in the portal.
- **Rotation.** Environment secret: register a new secret at the identity provider, replace the
  value, restart `web`. Per organisation: replace the secret in the portal, or rotate the
  `private_key_jwt` key (the old public key stays published for a while so the provider can switch).
- **Destruction.** Revoke the old secret at the identity provider; remove the old value from the
  secret store.
- **Never protect with it.** Participants never log in; nothing of theirs depends on it.

## API client secrets and access tokens

- **Purpose.** Authenticate the contact-center bots on the machine API (ADR 0023).
- **Algorithm.** A client secret (`ops_…`) and an access token (`opa_…`) are 256-bit random values.
  The database keeps only their SHA-256; comparison is `hmac.compare_digest`
  (`app/domain/api_clients.py`). A token lives 10 minutes, at most 5 per client.
- **Lives in.** The secret only in the contact center's vault, shown once; its hash in the database.
- **Who can read it.** Nobody at OpenPlanner can read a secret back; a database copy cannot call the
  API.
- **Rotation.** Superuser: *Nieuw secret maken* on the client (the old secret and every token stop
  at once); a warning shows 30 days before the date. Runbook *API clients of contact-center bots*.
- **Destruction.** Switch the client off (its tokens are revoked at once); the hash goes with the
  client.
- **Never protect with it.** Not a general-purpose hash: it suits only high-entropy random values.

## Webhook secrets (Webex and WxCC)

- **Purpose.** Authenticate events that Webex and Webex Contact Center send to
  `/webhooks/webex` and `/webhooks/wxcc` (`app/adapters/webhooks.py`).
- **Algorithm.** Webex: hex HMAC-SHA1 of the raw body in `X-Spark-Signature`. WxCC: hex HMAC-SHA256
  in `X-WebexCC-Signature`, with a signed timestamp that must lie within 300 s. Comparison is
  constant-time.
- **Recorded provider exception (ASVS V11.4.1, V11.4.3).** Webex signs with HMAC-SHA1 and offers
  nothing else. HMAC-SHA1 is not broken as a MAC, but ASVS lists SHA-1 as not approved. The mitigation
  is a random secret of at least 32 characters, constant-time comparison, and replay protection by
  `event_key` (Webex sends no signed event time). When Webex offers SHA-256 the adapter switches
  (ADR 0094 point 5).
- **Lives in.** `WEBEX_WEBHOOK_SECRET` and `WXCC_WEBHOOK_SECRET`, environment or `X_FILE`; the
  provider holds the same value in its subscription. Neither is typed into the portal.
- **Who can read it.** The host operator and the `web` process.
- **Rotation.** Replace the value in the secret store, restart `web`, run `python -m app.cli
  register-webhooks` so the subscriptions carry the new secret. Until then events are refused
  (`webhook.rejected`).
- **Destruction.** Remove the old value from the secret store.

## TLS private key

- **Purpose.** HTTPS of the bundled proxy (ADR 0064, ADR 0065, ADR 0070).
- **Algorithm.** The certificate's key: issued by ACME in the bundled proxy, or an own key. For an own
  certificate the key is generated on the host with a signing request (CSR flow) or imported; the
  certificate chain, which is public, is kept in the database.
- **Lives in.** The `portal-certs` volume (`privkey.pem`, mode 600, and `pending.key` while a request
  waits), or the proxy's own `proxy-data` volume for ACME. **Never in the database** and so never in
  a database backup or a configuration package (`app/adapters/certificate_volume.py`).
- **Who can read it.** The host operator and the proxy. Not the portal: it can order a new key and
  upload a chain, never read a key back.
- **Rotation.** The superuser orders a new signing request in the portal (a new key waits apart from
  the one in use), uploads the issued certificate, which then replaces the pair; ACME renews itself.
  Runbook *HTTPS certificate*.
- **Destruction.** The superseded key is overwritten by the new pair on the volume; a key that left
  service is deleted from the volume. Keep the `proxy-data` volume in the host backup, apart from the
  database backup.

## Image signing (cosign, keyless)

- **Purpose.** Only images built by the `main` workflow of this repository are deployed (ADR 0006).
- **Algorithm.** cosign keyless signing through Sigstore: a short-lived certificate bound to the
  GitHub Actions OIDC identity of `main.yml`, recorded in the transparency log; the SBOM is attested.
  The application image and the proxy image are signed by the same run.
- **Lives in.** No long-lived signing key exists to hold or leak. The verifier is the identity pattern
  in `cosign verify` (runbook *Verifying the image signature*), run by the installer and the host
  updater with the host's `cosign`.
- **Who can read it.** Not applicable.
- **Rotation and destruction.** Not applicable. The trust anchor is the workflow identity: change it
  by changing the identity regexp in the installer and the runbook.

## Configuration package passphrase

- **Purpose.** Moves the configuration, with its secrets, from one installation to another (ADR 0089).
- **Algorithm.** The passphrase (at least 16 characters) is stretched with scrypt (N 2^16, r 8, p 1,
  16-byte random salt stored in the package) into an AES-256-GCM key
  (`app/adapters/db/configuration_package.py`). Every sealed value is opened with the installation's
  key and sealed again under the package key, with the same record names as associated data. A
  verifier (a fixed text sealed under the package key) tells a wrong passphrase from a damaged file.
  This uses the same re-sealing as `rekey` (`app/adapters/db/sealed.py`).
- **Lives in.** Nowhere in OpenPlanner: it is never stored or logged. The administrator keeps it, for
  example in a secrets file with mode 600 for the duration of the export.
- **Who can read it.** The administrator who chose it. A package without it holds no secret in clear
  text; a package with it holds every secret of the installation, and must be handled so.
- **Rotation.** Export again with a new passphrase.
- **Destruction.** Delete the package file and the passphrase file after the import. The bootstrap
  keys (`SETTINGS_ENCRYPTION_KEY`, `MANAGEMENT_TOKEN_KEY`, `SESSION_SECRET`) never travel in a package.

## Other secrets in the environment

`SMTP_PASSWORD`, `WEBEX_CLIENT_SECRET`, `WEBEX_REFRESH_TOKEN` and `UPDATE_CHECK_TOKEN` are the
environment's fallback or bootstrap values for integrations that the portal normally holds (ADR 0008,
ADR 0042). They are secrets in the environment or in `X_FILE`; they are never shown, logged or
exported. Replace the value and restart. The tokens that Webex rotates later are stored in the
database sealed under `SETTINGS_ENCRYPTION_KEY`.

## Algorithms that are not keys

| Use | Algorithm | Where |
|---|---|---|
| Admin session reference, API secrets, tokens, token hashes | SHA-256 of high-entropy random values | `app/domain/admin_sessions.py`, `api_clients.py`, `tokens.py` |
| ID-token signature | Asymmetric only (RS/ES/PS families), checked by PyJWT | `app/adapters/oidc.py` |
| Login code exchange | PKCE `S256`, `state`, `nonce` | `app/adapters/oidc.py` |
| Static asset and widget integrity | SHA-256, SHA-384 (SRI) of public files | `app/web/static_assets.py`, `widget_bundle.py` |
| Key id of a sealed record | First 8 bytes of SHA-256 over the derived key | `app/adapters/crypto.py` |

Every derivation carries a versioned label, and a new algorithm or parameter gets a new label and key
id; old data stays readable until `rekey` has run (ADR 0094 point 6). That is also how a later move to
other primitives would go.
