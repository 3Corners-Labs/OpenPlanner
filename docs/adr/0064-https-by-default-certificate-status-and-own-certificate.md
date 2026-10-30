# 0064. HTTPS by default: the certificate watched, shown, replaceable and tested end to end

Status: proposed (2026-10-30), requested by the product owner; step 1 (decisions 1–4) and
step 2 (decision 5) built 2026-10-30. Extends ADR 0030 (HTTPS with ACME certificates) and
ADR 0050 (host installer); builds on ADR 0038 (system events from the worker), ADR 0042 §11
(System status), ADR 0006 (signed images) and ADR 0029 (host updater).

## Context

ADR 0030 gave every installation a TLS proxy (Caddy) that obtains a certificate from Let's
Encrypt and renews it on its own. Three things stayed outside the product:

- **Only production ran it.** The `test` mode of the installer leaves TLS to "your own address
  or proxy", and a lab runs on plain HTTP. The product owner's acceptance installation
  (docker01) therefore shows *Not secure* in the browser, and the whole flow from ACME to a
  renewed certificate has never been exercised outside CI.
- **Nobody could see the certificate.** Whether it was obtained, from which CA, and when it
  expires was visible only with `openssl s_client` on the host. The runbook asked for a monthly
  check by hand; the threat model marks the expiry threat as "operational". Let's Encrypt sends
  no expiry mail any more.
- **No way to an own certificate.** An organisation whose policy requires a PKIoverheid or
  other organisation-validated certificate had no supported path: ADR 0030 only mentioned that
  Caddy *can* load one from files.

The product owner asked (2026-10-30) to make HTTPS the standard part of every installation,
to make the certificate visible and guarded, to remove the dependency on someone else doing
TLS, to make the whole flow testable end to end (in CI and on docker01), and to allow a later
manual override with an own certificate.

## Decision

1. **HTTPS is the standard shape.** The installer runs the TLS proxy in `production` and `test`
   mode by default and asks for the host name and the CA mailbox in both. `--no-tls` opts out
   for an installation behind a load balancer of its own; the installer then asks for the
   public address and the proxy's address (`FORWARDED_ALLOW_IPS`), so rate limits still see the
   visitor. A `lab` opts in with `--tls-domain <host>`, so docker01 runs the full flow. The
   choice is remembered in `.env.update` (`COMPOSE_PROFILES`) like the mode. `PUBLIC_BASE_URL`
   follows the host name whenever the proxy runs.

2. **Two certificate sources, one proxy.** `proxy.env` selects the source:
   - *ACME* (default): `TLS_DOMAIN`, `ACME_EMAIL`, optionally `ACME_CA` for another ACME CA
     (Let's Encrypt staging, ZeroSSL, a government CA that speaks ACME). As ADR 0030.
   - *Own certificate*: `TLS_CERT_FILE` and `TLS_KEY_FILE`, read from `./certs` mounted
     read-only at `/certs`. Caddy then serves these files and manages nothing; renewal is the
     operator's. Replacing the files takes `docker compose --env-file .env.update restart proxy`.
     The installer writes this `proxy.env` with `--own-certificate` after checking that both
     files are in `certs/`.
   The Caddyfile cannot branch, so the image's entrypoint renders it at start: the `tls <cert>
   <key>` line is present only in file mode. The container refuses to start without a host name,
   or with one file of the pair, or in ACME mode without a mailbox. Switching back to ACME is
   removing the two variables and restarting the proxy.

3. **The certificate is watched and shown.** The worker probes the host of `PUBLIC_BASE_URL` (on
   443, or the port that address names) every `tls_check_interval_hours`: a TLS handshake verified against the system's CA
   bundle, then the leaf certificate's subject, issuer, names and validity. The outcome is one
   finding: `ok`, `expiring` (fewer than `tls_expiry_warning_days` left: Caddy renews at 30
   days, so below 20 its renewal is failing), `expired`, `not_yet_valid`, `untrusted` (a
   staging, self-signed or private CA the host does not trust), `name_mismatch`,
   `unreachable`, or `not_https` (no certificate to check). The latest outcome is stored in
   `certificate_checks` (one row) and shown on *System status* (Platform → Advanced settings):
   host, finding, issuer, valid until, days left, last check. A finding other than `ok` is a
   system event `tls.certificate` when it first appears and every `tls_alert_repeat_hours`
   while it lasts; the return to `ok` is recorded once. `python -m app.cli tls-check [--host
   H] [--port P]` runs the same probe on demand and exits 1 on a finding.

4. **Testable end to end.** `scripts/check-proxy.sh` (CI, `make proxy-check`) now runs both
   sources: a certificate obtained from Pebble over ACME, then a self-signed own certificate
   served from files, with redirect, body limit and non-root checks for each; it runs `tls-check`
   from the `web` container against the proxy and expects the `untrusted` finding with a
   days-left value. The runbook gets *Test the certificate flow*: on docker01 install with
   `--tls-domain` and `--acme-staging`, see `untrusted` on System status, switch to the
   production CA, see Let's Encrypt and `ok`; renewal is Caddy's, and the status page proves it
   month after month.

5. **The proxy image is a release artefact (step 2).** Until step 2 every host built Caddy
   itself (`xcaddy` with Go module downloads: slow, and a dependency on proxy.golang.org at
   install time). Now `main.yml` builds, pushes and signs `ghcr.io/<repo>-proxy` from
   `deploy/proxy/` next to the application image, for every release and every release
   candidate, with the same keyless signature and its own SBOM attestation (`sbom-proxy.cdx.json`
   on the release page). Where the proxy runs (`OPENPLANNER_TLS=1`), the installer pulls the
   proxy image at the application's version, verifies it with the same identity and pins it by
   digest as `OPENPLANNER_PROXY_IMAGE` in `.env.update`; a host installed before step 2 gets
   the pin on its next installer run, at the version baked into its pinned application image.
   The host updater resolves, verifies and pins both images on an update, starts the proxy with
   `web` and `worker`, counts the proxy's health towards the health check, and returns both on
   a rollback or a restore (a previous version that ran a locally built proxy gets that image
   back). An installation without the proxy sees none of this. `docker-compose.yml` keeps
   `build: deploy/proxy` behind `image: ${OPENPLANNER_PROXY_IMAGE:-openplanner-proxy:local}`
   for development and `scripts/check-proxy.sh`, which still build the image locally in CI.

Build steps: (1) decisions 1–4 (PR #139); (2) decision 5 (PR #144, stacked on step 1); (3) the
certificate source managed by the superuser in the portal (own ADR). The product owner decided
on 2026-10-30 that docker01 runs the ACME flow by default, and that who manages the certificate
depends on where TLS ends: with the bundled proxy (small installations) the superuser must be
able to choose ACME or upload an own certificate from the portal; behind an enterprise reverse
proxy (`--no-tls`) the certificate is theirs and OpenPlanner only shows what citizens see
(decision 3 covers that already). Until step 3 the operator manages the source on the host, as
decision 2 says.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `TLS_CERT_FILE` | B deployment | empty | path in `/certs` | — | `proxy.env`; with `TLS_KEY_FILE` selects the own-certificate source; a file on the host, so B |
| `TLS_KEY_FILE` | B deployment | empty | path in `/certs` | — | `proxy.env`; see above |
| `tls_check_interval_hours` | C platform | 6 | 1–168 | `settings.platform.manage` | how often the worker probes the certificate |
| `tls_expiry_warning_days` | C platform | 20 | 1–60 | `settings.platform.manage` | below this many days left the finding is `expiring` |
| `tls_alert_repeat_hours` | C platform | 24 | 1–168 | `settings.platform.manage` | a lasting finding is reported again at this interval |

| `OPENPLANNER_PROXY_IMAGE` | B deployment | empty | an image reference by digest | — | `.env.update`, written by the installer and the updater, never by hand; empty means a locally built proxy |
| `UPDATE_PROXY_IMAGE` | B deployment | `<UPDATE_IMAGE>-proxy` | registry name | — | `/etc/openplanner/updater.env`, written by the installer; the host side, like `UPDATE_IMAGE` |

Unchanged: `TLS_DOMAIN`, `ACME_EMAIL`, `ACME_CA` (B, `proxy.env`, ADR 0030); `FORWARDED_ALLOW_IPS`
(B, `.env`). Pins by digest and the registry name of the proxy image are host-side bootstrap
values (needed before the application runs), so B, like `OPENPLANNER_IMAGE` and
`UPDATE_IMAGE` (ADR 0029).

**Fixed on purpose:** the probe's timeout (10 s) and that it probes the address citizens use,
`PUBLIC_BASE_URL` (the CLI takes `--host` and `--port` for another server). The mount point `/certs` and the
probe's trust store (the image's CA bundle) are properties of the image, not of a deployment.

## Consequences

- Every installation except a plain lab needs a public DNS name and ports 80 and 443 open before
  the first start; `docs/install.md` says so per mode. An installation behind its own load
  balancer states that with `--no-tls`, instead of getting it by default.
- A new table `certificate_checks` (migration, reversible; no personal data). The worker
  connects to its own public name every few hours; from a container that is usually allowed,
  and when the host's firewall blocks it the finding says `unreachable` (runbook).
- The threat model's expiry row gets a test (`tests/domain/test_tls_check.py`, the proxy
  check); `docs/dataflow.md` notes the probe (public certificate data only, nothing about
  citizens). `docs/architecture.md`, `docs/runbook.md`, `docs/install.md`, the manual topic
  *Advanced settings* (nl, en) and `proxy.env.example` are updated.
- No new Python dependency: the probe uses the standard library's `ssl` and the already present
  `cryptography` package (PyJWT's) to read the certificate. The proxy image gains a small
  `sh` entrypoint; CI's hadolint and trivy cover it. `scripts/check-proxy.sh` needs `openssl`
  on the runner (present on ubuntu-24.04).
- An own certificate is the operator's to renew: the status page and the system event are the
  only safety net, so the runbook asks for a calendar entry too.
- Step 2: a release and a release candidate produce two signed images; the candidate job
  takes a few minutes longer. A portal update now recreates the proxy as well (every release
  has a new proxy digest), so the public address is unreachable for the seconds the proxy
  restarts, inside the maintenance window the update already has; before, the proxy kept
  running through an update. In return, an unhealthy proxy image rolls an update back, and a
  production host never runs `xcaddy` or reaches proxy.golang.org. The proxy image is scanned
  like the application image (hadolint and trivy per PR, the SBOM attested per release).
