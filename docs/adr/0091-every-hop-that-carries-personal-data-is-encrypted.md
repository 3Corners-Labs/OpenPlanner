# 0091. Every hop that carries personal data is encrypted, or a recorded exception

Status: accepted (2026-11-01), requested by the product owner after the ASVS 5.0.0 L2 audit of
1 November 2026 (V4.1.2, V12.3.1, V12.3.3, V1.3.6, V13.1.1). Amends ADR 0030 and ADR 0064
(the bundled proxy), ADR 0022 (SMTP channel) and ADR 0086 (own reverse proxy). Not built; see
*Build status*.

## Context

The audit of commit `bf14c54e` found three places where data travels, or may travel, without
transport encryption:

- **Port 80 redirects everything.** The bundled proxy answers every request on port 80 with
  `308` to HTTPS (`deploy/proxy/Caddyfile`). That is right for a person who types the address,
  and wrong for a machine: a contact-center bot configured with `http://` sends its client
  secret to `/api/agent/v1/oauth/token` in clear text, the `308` makes it send the same body
  again over HTTPS, and the call succeeds. Nobody notices that the secret crossed the network
  unencrypted. The same holds for `/webhooks/*` and `/api/v1/widget/*`. ASVS V4.1.2 asks that
  only endpoints meant for a browser redirect.
- **Proxy to web is plain HTTP.** `reverse_proxy web:8000` runs over the `edge` bridge network
  of one Docker host (ADR 0030); `docs/dataflow.md` shows it, but nothing records it as an
  accepted risk (V12.3.3). On one host the hop never leaves the machine, and whoever can read
  the bridge can also read the TLS key on the proxy volume, so internal TLS adds little there.
  It is different with an own reverse proxy on another machine (ADR 0086,
  `FORWARDED_ALLOW_IPS`): then the hop crosses a real network.
- **SMTP may run without TLS in production.** `SmtpSecurity.NONE` is allowed when no username
  is set, "for a relay on a trusted network" (`app/domain/channel_settings.py`). Every
  confirmation and reminder carries the participant's name, email address and join link. Log
  destinations already refuse clear-text transports in production (ADR 0085,
  `app/domain/log_destinations.py`); mail, which carries more personal data than a log line,
  does not.

The audit also asked for one list of every outbound connection (V1.3.6, V13.1.1). Since #296
the worker and web ship log lines to syslog, Splunk, Elastic or HTTP destinations, which the
existing data flow does not list yet.

## Decision

1. **Port 80 refuses machine paths.** The bundled proxy answers `/api/*` and `/webhooks/*` on
   port 80 with `403` and a short plain-text body, without `Location`. Every other path keeps
   its `308` to HTTPS; ACME HTTP-01 challenges keep working. `scripts/check-proxy.sh` asserts
   both, in both passes. The runbook tells administrators of an own reverse proxy to do the
   same.
2. **The bundled proxy-to-web hop stays HTTP on one host, as a recorded exception.** Reasons:
   the `edge` network has two members (proxy and web, fixed addresses); the hop never leaves
   the host; an attacker on the bridge has the host and therefore the keys. This exception is
   named in `docs/threat-model.md` and `docs/dataflow.md` and holds only for the bundled proxy
   on the same host.
3. **An own reverse proxy on another machine gets TLS to the application.** Uvicorn serves
   HTTPS when `WEB_TLS_CERT_FILE` and `WEB_TLS_KEY_FILE` are set (both or neither; web refuses
   to start with one). System status shows an error when `FORWARDED_ALLOW_IPS` trusts an
   address other than the bundled proxy's and web runs without TLS. The installation decides
   where the certificate comes from; the runbook describes an internal CA and the renewal.
4. **SMTP without TLS is refused in production.** In production the channel form and the
   `SMTP_*` fallback refuse `SmtpSecurity.NONE`, unless the host sets
   `SMTP_PLAINTEXT_RELAY_ALLOWED=true`. Upgrade path: in the first minor release that ships
   this, an existing `NONE` configuration keeps sending and System status shows it as an
   error with the fix; from the next minor release on, sending refuses it like saving does.
   Outside production nothing changes (Mailpit runs without TLS).
5. **One list of outbound connections.** `docs/dataflow.md` gets a table with every outbound
   connection: destination, protocol, who configures it (class), the encryption required in
   production, and whether the outbound guard (`app/adapters/outbound.py`) applies. It covers
   SMTP, SMS gateway, Webex, WxCC, the identity providers, log destinations, ACME, the release
   channel and the DNS checks. A PR that adds an outbound connection adds its row.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `SMTP_PLAINTEXT_RELAY_ALLOWED` | B deployment | `false` | bool | — | switching a protection off is never C or D; tied to the host's network (a relay on the same host or LAN) |
| `WEB_TLS_CERT_FILE` | B deployment | empty | path to a PEM file | — | tied to the host; only for an own proxy on another machine |
| `WEB_TLS_KEY_FILE` | B deployment | empty | path to a PEM file, not readable by others | — | as above; the `X_FILE` rules of ADR 0042 §9 apply |

**Fixed on purpose:** the list of machine paths (`/api/*`, `/webhooks/*`) and the status `403`:
they follow from the routes and are not a tuning value.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR accepted | #309 | merged |
| 1 | Port 80 refuses machine paths; edge checks in `check-proxy.sh` (also XFF spoofing, TRACE, `/static/` listing, `no-store`); runbook for an own proxy | | planned |
| 2 | SMTP without TLS refused in production; `SMTP_PLAINTEXT_RELAY_ALLOWED`; System status error | | planned |
| 3 | TLS from an own proxy to web (`WEB_TLS_*`), System status error; the exception of point 2 in threat model and data flow | | planned |
| 4 | The list of outbound connections in `docs/dataflow.md` | | planned |

## Consequences

- A bot or WxCC webhook configured with `http://` now fails loudly instead of leaking quietly;
  `docs/agent-api.md` and `docs/webhooks.md` say so.
- An installation that relays mail in clear text has to choose: TLS on the relay, or the
  explicit host setting. Both are visible on System status.
- The threat model gets rows for the port 80 refusal, the single-host exception and SMTP.
- Step 3 touches `docker-compose.yml`; it waits until the open configuration-package PRs that
  change that file have landed.
