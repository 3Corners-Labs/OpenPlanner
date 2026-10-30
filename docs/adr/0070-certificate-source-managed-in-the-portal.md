# 0070. The certificate source of the bundled proxy, managed by the superuser in the portal

Status: proposed (2026-10-30), requested by the product owner. Build step 3 of ADR 0064 (HTTPS
by default); extends ADR 0030 (TLS proxy) and ADR 0042 (settings classes, environment lock).
The private key is deliberately not an integration secret in the sense of ADR 0008: it never
enters the database.

## Context

ADR 0064 gave every installation the TLS proxy by default, a watched certificate on *System
status*, and an own certificate from two files in `./certs` named in `proxy.env`. The product
owner decided on 2026-10-30 who manages the certificate, depending on where TLS ends:

- **Bundled proxy** (small installations, docker01): the superuser must choose between a
  Let's Encrypt (ACME) certificate and an own certificate from the portal, and upload the own
  certificate there, without touching the host.
- **Enterprise reverse proxy** (`--no-tls`): the certificate is theirs; OpenPlanner only shows
  the certificate citizens see (ADR 0064 decision 3 already does that).

Three things constrain the answer. The product owner decided (2026-10-30) that the private key
of an own certificate never enters the database, so a database backup never carries it; the
key is the server's identity, not a setting. The proxy and `web` are separate containers on a
read-only filesystem, and `web` has no Docker socket, so it cannot restart the proxy. And an
operator who prefers files (`TLS_CERT_FILE`/`TLS_KEY_FILE`, ADR 0064) or wants the choice out
of the portal altogether (ADR 0042 environment lock) must keep that.

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

2. **The key is made on the host; the portal handles the certificate, never the key.** The
   default path is a signing request: the superuser presses *Make a new key and request*, `web`
   makes an ECDSA P-256 key and a CSR for the host of `PUBLIC_BASE_URL` (CN and SAN) and
   writes both into the volume shared with the proxy (`pending.key`, mode 600, `pending.csr`;
   never under `data/`, which the update backup copies). The portal shows the request as text
   and as a download; the superuser hands it to the CA (PKIoverheid or any other) and uploads
   only the certificate chain (PEM, server certificate first, at most 64 KB). The chain is
   validated with `cryptography` before anything is stored: it parses, it covers the host, it
   is neither expired nor not yet valid, and its public key is that of the pending key (which
   then becomes the served key, `privkey.pem`) or of the key already served (a renewal with the
   same key). A chain for no key on this host is refused (`tls_key_mismatch`). The chain is
   public: it is stored in `own_certificates` (one row: chain, the leaf's public key,
   fingerprint, subject, issuer, names, validity, upload time) and shown on the page. A pending
   request never disturbs a certificate in use; a new request replaces an earlier one.

   *Import* of an existing certificate with its key is allowed for migrations: the pair is
   uploaded together, the key must belong to the leaf, and the key goes straight into the
   volume as `privkey.pem` (write-only, never echoed, never logged, never kept in the database
   or the request log). P-256 is the only key type `web` makes; RSA keys arrive only by import
   (a CA that refuses ECDSA is served by importing an RSA pair made elsewhere; no setting).

   Every request, upload, import and removal is an audit event (`tls.key.generate`,
   `tls.certificate.upload`, `tls.key.import`, `tls.certificate.remove`) with the chain's
   fingerprint and expiry date as its only detail. The chain may be replaced at any time and
   removed while the source is `acme` (`tls_certificate_in_use` otherwise); removal never
   touches the key.

   **Consequence of the key staying on the host:** a database restored on another host brings
   the chain but not the key. The page then shows *key missing on this server* next to the
   chain, `own` cannot be chosen (`tls_key_missing`), and `web` withdraws the pair so ACME
   applies until a new request is made and its chain placed, or the pair imported again. The
   volume is excluded from the database backup on purpose; the runbook's backup section says so.

3. **Delivery to the proxy: the same volume and a reload, not an HTTP certificate manager.**
   The named volume `portal-certs` (`/srv/portal-certs` in `web`, read-only at `/portal-certs`
   in the proxy) is where the key already lives; `web` adds `fullchain.pem` and, written last,
   `serial` with the fingerprint when the pair is to be served; every file lands through a
   temporary name and a rename, and withdrawing deletes `serial` first and leaves the key. The proxy's entrypoint starts Caddy as before and
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
   exactly one address, a new surface in the threat model; and (d) with the key on the volume
   only, `web` would have to read it from there at request time anyway. The volume uses only
   documented behaviour (`tls <cert> <key>` and `caddy reload`, which the entrypoint of ADR
   0064 already renders), keeps working while `web` is down, and exposes the key exactly as the
   ACME key in `proxy-data` is exposed today: inside a Docker-managed volume readable by root
   on the host, uid 10001 mode 600 inside the containers.

4. **One place shows what citizens see.** The worker check of ADR 0064 and the certificate
   block on *System status* stay the only report of the served certificate. The source form
   shows, for the uploaded certificate, its fingerprint, subject, issuer, names and expiry
   date; it never claims the proxy serves it. A *Check now* button (`settings.platform.manage`)
   runs the worker's probe at once and stores the result, so a switch is visible within a
   minute instead of at the next scheduled check.

Done looks like: on a small installation the superuser makes a request, has it signed, places
the chain, chooses *own*, presses *Check now* and sees the new issuer on System status; chooses
*ACME* again and sees Let's Encrypt; the host was not touched and the database never held a
key. An enterprise with `--no-tls` sees the status and a
note that the source is managed at its own proxy. `scripts/check-proxy.sh` (CI) proves the
proxy side: a pair written into the volume is served within the poll interval and withdrawn
again, with ACME before and after.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `tls_certificate_source` | C platform | `acme` | `acme`, `own` | `settings.platform.manage` | lock: `TLS_CERTIFICATE_SOURCE` in `.env`; `own` needs an uploaded certificate |
| `TLS_PROXY_BUNDLED` | B deployment | `false` | bool | — | `docker-compose.yml` sets it from `OPENPLANNER_TLS`; tells the portal whether the source is its to manage; tied to the host's shape |
| `TLS_PORTAL_CERTIFICATE_DIR` | B deployment | `portal-certs` | path | — | the volume with the key, the request and the served pair; `/srv/portal-certs` in the container (a mount, so B); never under `data/` |
| `TLS_PORTAL_POLL_SECONDS` | B deployment | 10 | 2–300 | — | `proxy.env`; how often the proxy looks for a changed `serial`; the proxy reads no portal value, so B |

Unchanged: `TLS_CERT_FILE`, `TLS_KEY_FILE` (ADR 0064) keep selecting the operator's files and
win over the portal; `tls_check_interval_hours`, `tls_expiry_warning_days`,
`tls_alert_repeat_hours` (ADR 0064) govern the check that reports the result.

**Fixed on purpose:** the upload limit of 64 KB per file (a chain of three certificates is
about 6 KB, a 4096-bit RSA key 3 KB; a larger file is not a certificate), the validation rules
(a chain for another key or another name would take the site down), the key type `web` makes
(ECDSA P-256: every browser and every public CA accept it; another type arrives by import),
the file names and the mount point `/portal-certs` in the proxy (properties of the image, like
`/certs`), and the fingerprint algorithm (SHA-256).

## Consequences

- The database holds certificates but no private keys: `docs/dataflow.md` says so for the DPIA
  and gets the flow (request → CA → chain → volume → proxy); `docs/threat-model.md` gets the
  threats (key at rest on the host volume, key never in a backup or a log, upload over the admin
  session only, a chain that takes the site down) with their tests; the runbook's backup
  section says the volume is left out on purpose and what to do after a restore elsewhere.
- New table `own_certificates` (migration 0061, reversible: the downgrade drops it, the chain
  must be uploaded again) and a new volume `portal-certs` in `docker-compose.yml` (empty with
  ACME; not in the database backup). The web image gets `/srv/portal-certs` owned by uid 10001.
  The proxy image's entrypoint gains the watcher; `scripts/check-proxy.sh` gains the volume
  pass.
- Five admin routes under `/admin/settings/tls/…` (request, request download, upload, remove,
  check), all `settings.platform.manage`, the changes with a recent login, in the
  authorisation matrix. No new screen: the block lives on the existing *Advanced settings* page
  (ADR 0047: platform → advanced settings → system status).
- `docs/runbook.md` (*Own certificate from the portal*), the manual topic *Advanced settings*
  (nl, en), `docs/settings.md`, `docs/install.md`, `proxy.env.example` and
  `docs/architecture.md` are updated.
- No new dependency: `cryptography` already parses certificates for the probe; the multipart
  reader of ADR 0060 reads the upload.
- An own certificate is still the organisation's to renew: the worker's *expiring* finding and
  the system event are the safety net, and the runbook asks for a calendar entry, as ADR 0064
  does for files. Renewal with the same key is one upload; with a new key it is a new request.
