# 0129. The bundled proxy is the security shell, also behind another edge

Status: accepted (2026-11-14), requested by the product owner in the conversation of
2026-11-14. Partly built: steps 0 and 1 in the pull request that adds this ADR; steps 2 to 5 are
planned; see *Build status*. Extends ADR 0030 and ADR 0064 (the proxy as the default), ADR 0070
(the certificate in the portal), ADR 0086 (the public address), ADR 0091 (encrypted hops) and
ADR 0125 (fixed addresses on the host).

## Context

The bundled reverse proxy (Caddy) runs in every production and test installation unless the
operator says `--no-tls` (ADR 0030, ADR 0064). It already works as a shell around the app:

- it terminates TLS;
- it redirects browsers to https, and refuses `/api/` and `/webhooks/` over http;
- it caps request bodies and cuts slow clients;
- it keeps no access log, because paths carry management tokens;
- it is the only address `web` accepts `X-Forwarded-For` from (`FORWARDED_ALLOW_IPS`).

The app adds its own layer: security headers, CSRF and rate limits (`docs/threat-model.md`).

Four gaps showed up on 2026-11-14, while the product owner placed OpenPlanner behind the edge of
an organisation that has its own reverse proxies and WAFs:

1. **Behind another edge every visitor shares one address.** The proxy trusts no upstream (no
   `trusted_proxies`), so that a visitor cannot forge their address. Behind a reverse proxy, WAF
   or load balancer it therefore sees the edge as the visitor. All rate limits share one bucket,
   so ten bookings an hour for everyone. Every caller gets one pseudonym (ADR 0053). The source
   networks of ADR 0124 see the edge, not the supplier. Only two shapes work today:
   - the bundled proxy is the edge;
   - the bundled proxy is off (`--no-tls`), and the other proxy talks https to `web`, named in
     `FORWARDED_ALLOW_IPS` (ADR 0091).
2. **The proxy is no WAF.** It inspects no requests. Filtering `/admin/` and `/idp/` on source
   network is left to the organisation's edge (`docs/install.md`, *Address and proxy*).
3. **The proxy is managed on the host.** Its settings live in `proxy.env`. Only the certificate
   source is in the portal (ADR 0070). Its own log lines go to the container's stdout, outside
   OpenPlanner's logging (ADR 0034, 0040, 0085) and out of sight in the portal.
4. **Links name one public address.** Every link in e-mail, sms, calendar invitations and pages
   comes from `PUBLIC_BASE_URL`. Behind an edge that must be the outermost name. An organisation
   hosted by a service provider may need a name of its own.

Ways to add a WAF were weighed on 2026-11-14, from the projects' own pages:

| Option | Licence | Fit |
|---|---|---|
| OWASP Coraza in Caddy ([coraza-caddy](https://github.com/corazawaf/coraza-caddy), v2.6.2 of 2026-10-08) with the [OWASP Core Rule Set](https://github.com/coreruleset/coreruleset) (v4.30.0 of 2026-10-02) | Apache-2.0, both | Built into the proxy image the way it is built now (xcaddy); actively maintained |
| nginx with [ModSecurity v3](https://github.com/owasp-modsecurity/ModSecurity) | Apache-2.0 | Maintained under OWASP, but replaces Caddy and its certificate management |
| [BunkerWeb](https://docs.bunkerweb.io/latest/) | AGPL-3.0 | Replaces Caddy; the licence needs an ADR of its own |
| [open-appsec](https://github.com/openappsec/openappsec) | Apache-2.0, a model under its own licence | No confirmed support for Caddy |

## Decision

1. **The bundled proxy is the security shell by design.** A host installation runs it unless the
   operator says `--no-tls`. The shell has three layers:
   - the organisation's own edge (optional): filtering per path and source, and DDoS protection;
   - the bundled proxy: TLS, limits, the trusted chain of point 2 and, when chosen, the WAF of
     point 5;
   - the app.

   `web` keeps trusting only the bundled proxy's fixed address. On Kubernetes the cluster's
   ingress is the shell (ADR 0114), and this ADR changes nothing there.
2. **A chain of trusted upstreams.**
   - `PROXY_TRUSTED_NETWORKS` names the reverse proxies, WAFs and load balancers in front of the
     bundled proxy. Empty, the default, trusts none, as today.
   - From a trusted peer the proxy takes the visitor's address from `X-Forwarded-For`, read right
     to left (`trusted_proxies_strict`). A visitor who adds a value of their own therefore cannot
     choose their address.
   - The proxy hands `web` exactly one address, the visitor's (`header_up X-Forwarded-For
     {client_ip}`). The route to the bundled identity provider gets the same.
   - Without trusted networks the visitor's address is the peer's, as before.
   - The proxy refuses to start on anything but IP addresses and CIDR ranges, on a range of
     length 0 (`0.0.0.0/0`, `::/0`) and on more than 32 entries.
   - A load balancer at layer 4 hands the address on with the PROXY protocol instead. That comes
     with step 2.
3. **Proxy settings and certificates in the portal.**
   - **Place.** They live in the platform scope, next to the certificate source of ADR 0070, on
     one page (ADR 0047). That page covers the trusted networks, the WAF mode and the public
     names. Advanced settings sit on a sub-page under the same portal address, never on another
     host or port.
   - **What may be edited.** Only structured fields with validation, never a free Caddyfile, for
     the same reason that admins never edit templates (ADR 0009).
   - **How it reaches the proxy.** `web` writes a validated document to the volume the proxy
     already reads (`portal-certs`, ADR 0070). The proxy's watcher renders it and reloads. When a
     reload fails it keeps the last good configuration, and System status says so.
   - **Caddy's own admin API** stays on `127.0.0.1` inside its container.
   - **Protections.** A protection is never switched off from the portal (ADR 0042). The host's
     `proxy.env` sets each setting's floor and can lock it; the portal can only tighten. Every
     change is an audit event.
4. **The proxy logs through OpenPlanner.**
   - **What and where.** The proxy writes its own lines as JSON to the shared volume: start,
     reload, certificate, refusals, and from step 4 the WAF's findings. The worker reads them.
   - **Masking.** They pass the same masking as every other line: no path, so no token, and no
     visitor address in clear, only the caller pseudonym of ADR 0053.
   - **Where they arrive.** In the system and security logs, at the log destinations (ADR 0085),
     and on System status in the portal.
   - **No access log of requests**, as today.
5. **A WAF in the proxy, by choice.**
   - **What.** OWASP Coraza with the OWASP Core Rule Set, built into the proxy image with
     xcaddy.
   - **Modes and rules.** Off, detect or block; paranoia level 1.
   - **Exclusions:** the webhook routes, whose JSON is checked by signature (ADR 0124); the
     import of the configuration package; and the portal's text fields.
   - **Audit log.** Coraza's audit log stays off. A finding reaches the logging of point 4 as rule
     id, phase and a path class, never as a body, a header or a path.
   - **Rule updates.** CRS releases join Renovate (ADR 0051).
6. **Links name the outermost public name.**
   - **Today's name.** Every link in a message or page is built from `PUBLIC_BASE_URL`. Behind
     an edge that is the edge's host name, and the address check of ADR 0086 proves the round
     trip through the whole chain.
   - **A name per organisation.** An organisation may get a public base URL of its own: the name
     of its service provider, or of the organisation itself. Its participant links and messages
     use that name, and the proxy serves it with a certificate of its own. The portal and the
     login callback stay on `PUBLIC_BASE_URL`.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `PROXY_TRUSTED_NETWORKS` | B deployment (`proxy.env`) | empty: trust none | IP addresses and CIDR ranges, comma or space separated; at most 32; no range of length 0 | host | Step 1. Class B because the proxy reads it before the app runs and because it describes the host's network. Step 2 shows it in the portal; the superuser may set it there within the host's floor. |
| WAF mode | C platform, with a floor in `proxy.env` | off until step 4; then detect | off, detect, block | `settings.platform.manage` | Step 4. The portal can raise it above the host's floor, never lower it. |
| Public base URL of an organisation | C platform | none: `PUBLIC_BASE_URL` | an https URL whose host the proxy serves | `settings.platform.manage` | Step 5. It changes the platform's names and certificates, so it is not class D. |

**Fixed on purpose:**
- the strict right-to-left reading of `X-Forwarded-For`;
- one address handed to `web`;
- no access log;
- Caddy's admin API on `127.0.0.1`;
- the limit of 32 entries, as for the source networks of ADR 0124.

These protect the visitor's address and the tokens in paths, and are not tuning.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #865 | merged |
| 1 | `PROXY_TRUSTED_NETWORKS`, read strictly right to left, with one address handed to `web` and to the bundled identity provider. Also: the entrypoint's validation, the installer's `--trusted-proxies`, a pass of `scripts/check-proxy.sh` with a trusted peer, and the install guide, runbook and threat model | #865 | merged |
| 2 | Proxy settings and the certificate in the portal: the document on the shared volume, the watcher's render and reload with the last good configuration kept, floors and locks in `proxy.env`, an advanced sub-page under the same address; the PROXY protocol for load balancers at layer 4 | | planned |
| 3 | The proxy's log lines through OpenPlanner's logging, masked, at the log destinations and on System status | | planned |
| 4 | The WAF: Coraza and the Core Rule Set in the image, the modes, exclusions, the audit log off, findings through step 3 | | planned |
| 5 | A public base URL per organisation, served by the proxy and used in that organisation's links and messages | | planned |

## Consequences

- **Behind an edge the visitor's own address comes back** for:
  - the rate limits;
  - the caller pseudonym;
  - the source networks of webhooks;
  - the warning about untrusted proxy headers on System status.
- **A trusted range that also holds visitors lets them choose their address.** The strict reading,
  the bounds, the threat model's row and, from step 2, an audit event on every change keep that
  narrow. The operator lists the edge's own addresses, not a whole office network.
- **`--no-tls` behind an own proxy keeps working.** The bundled proxy simply stays out of that
  chain.
- **With the WAF, the proxy image grows** by Coraza and the rule set, and false positives become
  a support question. Hence detect before block, and the exclusions.
- **Follow-ups.** The architecture document (7.7) and the plates (R-11) follow the steps as they
  are built.
