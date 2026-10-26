# 0029. HTTPS with free, automatically renewed certificates from a public CA

Status: accepted (2026-10-26), requested by the product owner.

## Context

Production requires an `https` `PUBLIC_BASE_URL` (`app/config.py`), the OIDC callback and
post-logout URLs are https (ADR 0013), and Webex and WxCC deliver webhooks only to https on
port 443 with a public domain (ADR 0018). Until now the repository left TLS to "a reverse
proxy" outside it (architecture §4, marked planned), so every installation had to buy, install
and remember to renew a certificate by hand. An expired certificate stops citizens' bookings,
the admin login and every webhook at once.

Browsers must trust the certificate without configuration on citizens' devices, so it must come
from a public CA. The product owner asked for a free one with automatic renewal.

## Decision

1. **ACME with Let's Encrypt by default.** A `proxy` service in `docker-compose.yml` (profile
   `tls`) terminates TLS with [Caddy](https://caddyserver.com) (Apache-2.0), which obtains the
   certificate for `TLS_DOMAIN` over ACME, renews it on its own when a third of its lifetime
   remains, retries with back-off and staples OCSP. `ACME_CA` selects another ACME CA
   (Let's Encrypt staging for a trial run; ZeroSSL or a government CA that speaks ACME later)
   without a code change. `ACME_EMAIL` receives the CA's notices.
2. **Caddy over the alternatives.** Caddy is the de-facto standard for automatic HTTPS and
   needs no companion process. Traefik (MIT) is container-label driven and larger than one
   route needs; nginx with certbot needs a second container, a shared webroot and a reload hook,
   three moving parts that fail separately. A Python ACME client inside `web` would put a
   certificate private key in the application and conflict with the read-only image.
3. **Hardened like the app.** Own small image (`deploy/proxy/Dockerfile`) on the official
   `caddy` image. The release binary lagged behind Go security fixes (HIGH findings in
   `trivy`), so the image rebuilds the same Caddy release with the official builder image's
   current Go and a patched `golang.org/x/net`, and drops the unused `curl`. It runs as uid 10001, read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`.
   It listens on 8080/8443 inside the container (published as 80/443), so it needs no
   capability. The certificate, its key and the ACME account live in the `proxy-data` volume;
   nothing secret is in the image or the repository.
4. **Port 80 only redirects** (308 to https) and answers HTTP-01 challenges; TLS-ALPN-01 on 443
   works as well. HTTP/3 is off (UDP 443 is often blocked; nothing needs it).
5. **The proxy does the edge work the runbook asked for**: request bodies of `/g/*` and `/b/*`
   capped at 64 KB (1 MB elsewhere), read and write timeouts against slow clients, no access log (paths carry management tokens), and an
   `X-Forwarded-For` that Caddy sets itself and never takes from the client.
6. **The app trusts exactly the proxy.** `proxy` and `web` share an `edge` network with a fixed
   subnet; the proxy has a fixed address and `web` sets `FORWARDED_ALLOW_IPS` to it, so rate
   limits see the visitor's address and nobody else can forge it.
7. **Opt-in profile.** Without `--profile tls` nothing changes: development keeps
   `http://127.0.0.1:8000`, and an installation with its own load balancer (O7) keeps it.
   The proxy refuses to start without `TLS_DOMAIN` and `ACME_EMAIL`, because Caddy would then
   serve plain HTTP.

## Consequences

- The host needs ports 80 and 443 open from the internet and DNS for `TLS_DOMAIN` pointing at
  it before the first start. A `CAA` record, if any, must allow `letsencrypt.org`.
- Let's Encrypt rate limits (5 duplicate certificates per week) make the `proxy-data` volume
  part of the backup; trial runs use the staging CA.
- Let's Encrypt certificates are domain-validated. If the organisation's policy requires an
  organisation-validated (PKIoverheid) certificate, that is a separate decision; Caddy can load
  a certificate from files instead.
- Let's Encrypt sends no expiry e-mails any more: monitoring the certificate's expiry date is
  part of the runbook.
- The proxy image is built and scanned in CI (`hadolint`, `trivy`) like the app image; Renovate
  updates the `caddy` base image.
- The ACME CA learns the domain name and the operator's e-mail address, no citizen data
  (`docs/dataflow.md`).
