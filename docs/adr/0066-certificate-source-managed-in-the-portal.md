# 0066. The certificate source of the bundled proxy, managed by the superuser in the portal

Status: proposed (2026-10-30), requested by the product owner. Build step 3 of ADR 0064 (HTTPS
by default); extends ADR 0030 (TLS proxy), ADR 0008 (secrets managed in the admin UI) and
ADR 0042 (settings classes, environment lock).

## Context

ADR 0064 gave every installation the TLS proxy by default, a watched certificate on *System
status*, and an own certificate from two files in `./certs` named in `proxy.env`. The product
owner decided on 2026-10-30 who manages the certificate, depending on where TLS ends:

- **Bundled proxy** (small installations, docker01): the superuser must choose between a
  Let's Encrypt (ACME) certificate and an own certificate from the portal, and upload the own
  certificate there, without touching the host.
- **Enterprise reverse proxy** (`--no-tls`): the certificate is theirs; OpenPlanner only shows
  the certificate citizens see (ADR 0064 decision 3 already does that).

Three things constrain the answer. The private key of an own certificate is an integration
secret in the sense of ADR 0008: entered in the portal, encrypted at rest with
`SETTINGS_ENCRYPTION_KEY`, write-only, never logged. The proxy and `web` are separate
containers on a read-only filesystem, and `web` has no Docker socket, so it cannot restart the
proxy. And an operator who prefers files (`TLS_CERT_FILE`/`TLS_KEY_FILE`, ADR 0064) or wants
the choice out of the portal altogether (ADR 0042 environment lock) must keep that.

## Decision

1. **One platform setting chooses the source.** `tls_certificate_source` (class C, `acme` or
   `own`, default `acme`, `settings.platform.manage`, recent login, audited with the old and
   the new value) lives on *Platform → Advanced settings*, in the certificate block of *System
   status*, next to what the worker sees. `own` is refused while no certificate is uploaded
   (`tls_certificate_missing`); switching back to `acme` is always allowed. `TLS_CERTIFICATE_
   SOURCE` in `.env` locks it like every class C setting (ADR 0042 §2): the row says *locked by
   the server operator* and the upload form is not shown. The installer writes that lock with
   `--own-certificate`, because the proxy then serves the operator's files whatever the portal
   says. Without the bundled proxy (`--no-tls`, or a development checkout) the block says the
   setting does not apply; `web` learns that from `TLS_PROXY_BUNDLED`, which `docker-compose.yml`
   sets from the installer's `OPENPLANNER_TLS` pin.

2. **The own certificate is uploaded as a write-only pair.** Two files, the PEM chain (server
   certificate first, then the intermediates) and the private key, at most 64 KB each. The
   upload is validated before anything is stored: both parse (`cryptography`, already present),
   the key belongs to the leaf certificate, the certificate covers the host of
   `PUBLIC_BASE_URL`, and it is neither expired nor not yet valid. A refusal names the rule
   (`tls_certificate_unreadable`, `tls_key_mismatch`, `tls_name_mismatch`,
   `tls_certificate_expired`, `tls_certificate_not_yet_valid`) and never echoes a byte of the
   upload. The pair is stored in `own_certificates` (one row): the chain as text (it is public),
   the key sealed with AES-256-GCM under `SETTINGS_ENCRYPTION_KEY` (ADR 0008), plus the SHA-256
   fingerprint, subject, issuer, names and validity for the page. The key is never shown, never
   read by the domain, never logged, and never part of an audit event or a job payload. The
   superuser may replace the pair at any time and remove it while the source is `acme`
   (`tls_certificate_in_use` otherwise). Every upload and removal is an audit event
   (`tls.certificate.upload`, `tls.certificate.remove`) with the fingerprint and the expiry
   date as its only detail.

3. **Delivery to the proxy: a shared volume and a reload, not an HTTP certificate manager.**
   `web` writes the pair into the named volume `portal-certs` (`/srv/portal-certs` in `web`,
   read-only at `/portal-certs` in the proxy): `fullchain.pem`, `privkey.pem` (mode 600) and,
   written last, `serial` with the fingerprint; every file lands through a temporary name and a
   rename, and removal deletes `serial` first. The proxy's entrypoint starts Caddy as before and
   a watcher that reads `serial` every `TLS_PORTAL_POLL_SECONDS` (10) and, when it changed, runs
   `caddy reload` with the `tls` line pointing at the portal files, or without it so Caddy's
   ACME management for the domain applies again. `web` writes the pair whenever the effective
   source is `own` and a certificate is stored (at start-up, after every change in the portal,
   and whenever the portal values are reloaded), and withdraws it otherwise, so a `reset`, a
   CLI change or an environment lock take effect the same way. Precedence in the proxy:
   `TLS_CERT_FILE`/`TLS_KEY_FILE` from `proxy.env` (the operator's files; the watcher does not
   run), then the portal's pair, then ACME.

   The alternative was Caddy's `tls { get_certificate http <url> }`: the proxy fetches the PEM
   bundle from an internal endpoint on `web` at handshake time. It keeps the key out of every
   volume, which is attractive, but it was not chosen because (a) a certificate manager
   *replaces* Caddy's own management for the names of its policy; falling back to ACME when the
   endpoint has nothing is not a documented contract, so a wrong reading of it would leave the
   site without any certificate, and this cannot be tried on the machines the team builds on
   (no Docker); (b) the private key would cross the `edge` network in plain HTTP on every cache
   miss, and when Caddy asks again after a replacement is the manager's cache policy, not ours;
   (c) it adds an unauthenticated internal endpoint to `web` that must be reachable from
   exactly one address, a new surface in the threat model. The volume uses only documented
   behaviour (`tls <cert> <key>` and `caddy reload`, which the entrypoint of ADR 0064 already
   renders), keeps working while `web` is down, and exposes the key exactly as the ACME key in
   `proxy-data` is exposed today: inside a Docker-managed volume readable by root on the host.
   The key stays encrypted in the database; the volume holds the working copy only.

4. **One place shows what citizens see.** The worker check of ADR 0064 and the certificate
   block on *System status* stay the only report of the served certificate. The source form
   shows, for the uploaded certificate, its fingerprint, subject, issuer, names and expiry
   date; it never claims the proxy serves it. A *Check now* button (`settings.platform.manage`)
   runs the worker's probe at once and stores the result, so a switch is visible within a
   minute instead of at the next scheduled check.

Done looks like: on a small installation the superuser uploads a certificate, chooses *own*,
presses *Check now* and sees the new issuer on System status; chooses *ACME* again and sees
Let's Encrypt; the host was not touched. An enterprise with `--no-tls` sees the status and a
note that the source is managed at its own proxy. `scripts/check-proxy.sh` (CI) proves the
proxy side: a pair written into the volume is served within the poll interval and withdrawn
again, with ACME before and after.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `tls_certificate_source` | C platform | `acme` | `acme`, `own` | `settings.platform.manage` | lock: `TLS_CERTIFICATE_SOURCE` in `.env`; `own` needs an uploaded certificate |
| `TLS_PROXY_BUNDLED` | B deployment | `false` | bool | — | `docker-compose.yml` sets it from `OPENPLANNER_TLS`; tells the portal whether the source is its to manage; tied to the host's shape |
| `TLS_PORTAL_CERTIFICATE_DIR` | B deployment | `data/portal-certs` | path | — | where `web` writes the pair; `/srv/portal-certs` in the container (a mount, so B) |
| `TLS_PORTAL_POLL_SECONDS` | B deployment | 10 | 2–300 | — | `proxy.env`; how often the proxy looks for a changed `serial`; the proxy reads no portal value, so B |

Unchanged: `TLS_CERT_FILE`, `TLS_KEY_FILE` (ADR 0064) keep selecting the operator's files and
win over the portal; `tls_check_interval_hours`, `tls_expiry_warning_days`,
`tls_alert_repeat_hours` (ADR 0064) govern the check that reports the result.

**Fixed on purpose:** the upload limit of 64 KB per file (a chain of three certificates is
about 6 KB, a 4096-bit RSA key 3 KB; a larger file is not a certificate), the validation rules
(a key that does not match or a name that does not cover the host would take the site down),
the file names and the mount point `/portal-certs` in the proxy (properties of the image, like
`/certs`), and the fingerprint algorithm (SHA-256).

## Consequences

- A private key now lives in the database, encrypted (ADR 0008): `docs/dataflow.md` gets the
  row, `docs/threat-model.md` the threats (key read from a backup, key in a log or audit
  event, an upload that takes the site down) with their tests, and the runbook's key-rotation
  note covers it (losing `SETTINGS_ENCRYPTION_KEY` means uploading the pair again).
- New table `own_certificates` (migration 0057, reversible: the downgrade drops it, the pair
  must be uploaded again) and a new volume `portal-certs` in `docker-compose.yml` (empty with
  ACME; in the backup with `proxy-data`). The web image gets `/srv/portal-certs` owned by uid
  10001. The proxy image's entrypoint gains the watcher; `scripts/check-proxy.sh` gains the
  volume pass.
- Four admin routes under `/admin/settings/tls/…`, all `settings.platform.manage` with a recent
  login, in the authorisation matrix. No new screen: the block lives on the existing
  *Advanced settings* page (ADR 0047: platform → advanced settings → system status).
- `docs/runbook.md` (*Own certificate from the portal*), the manual topic *Advanced settings*
  (nl, en), `docs/settings.md`, `docs/install.md`, `proxy.env.example` and
  `docs/architecture.md` are updated.
- No new dependency: `cryptography` already parses certificates for the probe; the multipart
  reader of ADR 0060 reads the upload.
- An uploaded certificate is still the organisation's to renew: the worker's *expiring*
  finding and the system event are the safety net, and the runbook asks for a calendar entry,
  as ADR 0064 does for files.
