# 0112. An internal certificate authority for labs, and the bundled identity provider behind the proxy

Status: proposed (2026-11-03), requested by the product owner. Amends ADR 0064 (two certificate
sources become three), ADR 0090 step 2 (the bundled Keycloak is reached through the proxy) and
ADR 0106 step 5 (the installer's https default covers the identity provider). Partly built: steps 0 to 5 merged or live (#476, docker01 since 2026-11-03); step 6 open.

## Context

On 2026-11-03 the product owner asked to look at https for the test and default environment.
ADR 0064 already makes https the standard shape for `production` and `test`, with the proxy in
front of `web`. Two things were still plain http:

- **The bundled Keycloak** (ADR 0090) is not behind the proxy. The Caddyfile has one route,
  `reverse_proxy web:8000`. Even in a default `test` installation the browser is sent to
  `http://<host>:8780`, so the password and the one-time code of every admin cross the network
  unencrypted, Keycloak warns that its cookies are not secured, and `oidc-check` flags the
  endpoints.
- **A lab host has no public name.** docker01 answers to `docker01.3cornerslabs.nl` on the lab
  network only (`NXDOMAIN` from outside), so ACME with a public CA cannot issue for it, and the
  product owner has no certificate of an internal CA at hand.

## Decision

1. **A third certificate source, `internal`.** `TLS_INTERNAL=true` in `proxy.env` makes the
   proxy issue its certificates from Caddy's own certificate authority (`tls internal`). It
   needs no outside connection and no mailbox. It is meant for a lab or a network without a
   public name; `production` keeps `acme` or an own certificate. The order of precedence in
   `deploy/proxy/entrypoint.sh` becomes: operator files, internal, portal pair, ACME. Like the
   operator's files, `internal` ignores the portal's choice, and the installer locks the portal
   setting (`TLS_CERTIFICATE_SOURCE=own`, shown as "managed on the host").
2. **The root certificate is public and is published; the authority's key never leaves the
   proxy.** The entrypoint copies Caddy's `root.crt` to the volume `internal-ca`, which the
   proxy mounts read-write and `web` and `worker` read-only. The CA's private key stays in the
   proxy's `/data`. Browsers do not trust the root until it is installed once per machine, so
   System status offers it for download (`GET /admin/settings/ca.crt`, `settings.platform.view`)
   whenever the file exists, and the runbook says how to install it.
3. **The host's own checks trust that root, and `expiring` follows the lifetime.** The certificate probe (ADR 0064) and the round
   trip to the public address (ADR 0086) verify against `TLS_INTERNAL_CA_FILE` when the file
   exists (class B, default `/internal-ca/root.crt`). The probe then reports `ok` for the
   internal certificate instead of a lasting `untrusted`, while a name mismatch or an expiry
   still shows. An address with a certificate that does not chain to this root stays
   `untrusted`. Caddy's internal certificates last 12 hours and are renewed at a third of
   that, so `expiring` (ADR 0064 point 3) is now `tls_expiry_warning_days` *capped at a quarter
   of the certificate's own lifetime*: unchanged for a 90-day certificate (a quarter is above
   20 days), and 3 hours for a 12-hour one. Without the cap an internal certificate would be
   `expiring` for its whole life.
4. **The bundled Keycloak is served through the proxy under `/idp`.** With `IDP_UPSTREAM=idp:8080`
   in `proxy.env` the Caddyfile gets a second route, `handle /idp/*`, before the one to `web`
   (the entrypoint renders it like the `tls` line, since the Caddyfile cannot branch). Keycloak
   runs with `KC_HTTP_RELATIVE_PATH=/idp`, `KC_PROXY_HEADERS=xforwarded` and
   `KC_HOSTNAME=https://<host>/idp`. One host name and one certificate serve both, so no
   wildcard or second name is needed. The browser reaches the login at
   `https://<host>/idp/realms/openplanner`; `web` still talks to Keycloak on the internal
   network as `http://idp:8080/idp/...`.
5. **The render script derives all of it from the public address.** When
   `KC_OPENPLANNER_BASE_URL` starts with `https://`, `scripts/render-keycloak-realm.sh` writes the
   https `KC_HOSTNAME`, the relative path and the proxy header mode into `secrets/keycloak.env`,
   and the application's half (`secrets/keycloak-app.env`) gains `OIDC_DISCOVERY_URL`,
   `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` and the admin URL with the path. Nothing
   is copied by hand any more. With an `http://` address nothing changes (the loopback and
   development shape of ADR 0090).
6. **The installer.** `--internal-ca` writes `TLS_INTERNAL=true` to `proxy.env`, for `lab` with
   `--tls-domain` and for `test`, asks for no mailbox, and locks the portal's source setting like
   `--own-certificate` does. It refuses `--internal-ca` together with `--own-certificate` or
   `--no-tls`, and warns in `production`. `IDP_UPSTREAM` is written together with the bundled
   provider itself (`--idp bundled`, ADR 0106 step 5); until then it is one line in `proxy.env`.
7. **Keycloak's mail server** stays with ADR 0106 step 5 (the installer sets it in the realm);
   the runbook documents the manual setting meanwhile.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `TLS_INTERNAL` | proxy.env (host) | unset | `true` | operator | the proxy's own file, read only by the proxy; not an application setting |
| `IDP_UPSTREAM` | proxy.env (host) | unset | `host:port` | operator | adds the `/idp` route; written by the installer |
| `TLS_INTERNAL_CA_FILE` | B deployment | `/internal-ca/root.crt` | a readable file | operator | trust anchor of the host's own checks; tied to the volume layout |

**Fixed on purpose:** the path `/idp` (a stable, documented address that realm, proxy and
Keycloak agree on); the CA key never leaving the proxy volume; `internal` never being the
default of `production`.

## Consequences

- A `test` or `lab` installation with https no longer sends a password or a one-time code over
  plain http, and its checks are clean.
- A browser shows a warning until the root is installed once. System status offers it, so the
  step is a download and a double click, not a shell session.
- Production is unchanged: ACME or an own certificate, and the identity provider of the
  organisation. A production installation that bundles Keycloak reaches it through the same
  `/idp` route with its ACME certificate.
- Anyone who obtains the proxy's `/data` volume can issue certificates for the lab's name. The
  volume is the proxy's alone, as it always held the ACME account key.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #476 | merged |
| 1 | Proxy: `internal` source, `/idp` route, root published to `internal-ca`; compose; the host's checks trust it (`app/adapters/internal_ca.py`, `TLS_INTERNAL_CA_FILE`) | #476 | merged |
| 2 | Keycloak: render script derives `KC_HOSTNAME`, relative path, proxy headers and the login half in `keycloak-app.env` from an https address | #476 | merged |
| 3 | System status offers the root (`GET /admin/settings/tls/ca.crt`); runbook (install the root, the identity provider behind the proxy); `scripts/check-proxy.sh` pass 3 | #476 | merged |
| 4 | Installer `--internal-ca` | #476 | merged |
| 5 | docker01 on https (2026-11-03, by hand with the files the installer writes; runbook) | #476 | live |
| 6 | `IDP_UPSTREAM` and the realm's mail server written by the installer (with ADR 0106 step 5a) | – | planned |
