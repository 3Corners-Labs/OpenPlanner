# Assurance checklist: hosting

For the hosting party and, where it says so, the security operations team and the penetration
tester. The application cannot see the host's network, volume encryption, clock or log pipeline.
These 15 ASVS 5.0.0 requirements and two project controls (shared trust boundaries, deletion
from files and snapshots) are therefore proven by commands and exports on the host. How to use
this file: `docs/assurance/README.md`. Requirement texts are the OWASP ASVS 5.0.0 (CC BY-SA 4.0);
this file gives the ID and a short description in our own words.

Replace `<host name>` with the public name of the installation. Keep the output of each command
with the date, the version it ran against (System status shows version and commit) and the name
of whoever ran it.

## What the application already shows

| Item | Evidence | Source |
|---|---|---|
| Public address, proxy trust and the round trip through the proxy | *Address and proxy* block on System status; `docker compose exec web python -m app.cli address-check` (findings such as `not_https`, `no_proxy_headers`, `proxy_not_trusted`) | ADR 0086, PR #397; runbook *Address and proxy* |
| Certificate status | `python -m app.cli tls-check`; System status; system event `tls.certificate` | ADR 0030, 0064; runbook *HTTPS certificate* |
| Every outbound connection, with protocol, encryption and guard | table *Outbound connections and the hop to web* | `docs/dataflow.md`; ADR 0091 |
| Cryptographic inventory | every key, algorithm, location and rotation | `docs/cryptography.md`; ADR 0094 |
| Image signature and bill of materials | `cosign verify` as in the runbook; `sbom.cdx.json` and `sbom-proxy.cdx.json` on the release page | runbook *Verifying the image signature*; ADR 0006 |
| Deletion from SQLite pages and the write-ahead log | `PRAGMA secure_delete=ON`, WAL truncation after a purge, and the marker test | `docs/dataflow.md` *Residual data after a purge*; PR #339 |
| Retention, restore and the update back-up | `docs/dataflow.md` *Retention matrix*; runbook *Backup and restore*; ADR 0102 points 7 to 10 | ADR 0102 |

The one-command evidence report of the application is built: `python -m app.cli evidence`
(runbook *Evidence report for an audit*) bundles the rows above that the application measures
itself (version and image digest, address and certificate checks, configured external hosts, log
destinations and their status, retention counts, key rotation, settings with class and source).
Not yet built, so not claimed: the standards table with the assertions of TLS versions and HSTS
in `check-proxy.sh` (ADR 0086 step 2). For what happens outside the application (TLS versions at
the edge, NTP, volume encryption, firewall) the commands below are the evidence.

## Transport security at the edge

| ID | Asks for | How to show it | Owner | Reference |
|---|---|---|---|---|
| V12.1.1 | Only current TLS versions (1.2 and 1.3) are enabled, the newest preferred | `testssl.sh --protocols https://<host name>` or the internet.nl report for the name; the Caddy version from the release SBOM | Hosting party | Bundled proxy: `deploy/proxy/Caddyfile`; an own proxy is the host's configuration (runbook *TLS from a proxy on another machine*) |
| V12.1.2 | Only recommended cipher suites, strongest first; forward secrecy for the highest level | `testssl.sh --cipher-per-proto https://<host name>` | Hosting party | As V12.1.1 |
| V12.2.1 | HTTPS for every client connection to the public services, no fall back to clear text | `curl -sI http://<host name>/` answers a redirect; `curl -si http://<host name>/api/v1/instance` is refused with 403 and no `Location` header (bundled proxy, `deploy/proxy/Caddyfile`, checked by `scripts/check-proxy.sh`); the HSTS header, if the client requires it, is reported by testssl.sh and is not set by the bundled proxy | Hosting party | ADR 0030; the Caddyfile comment on ASVS V4.1.2; runbook *HTTPS certificate* |
| V12.2.2 | The public certificate is from a publicly trusted CA | `python -m app.cli tls-check` (finding `ok`, issuer, days left) and the Certificate Transparency entry | Hosting party | ADR 0064, ADR 0070 |
| V12.3.4 | TLS between internal services uses trusted certificates, or a single-host exception is accepted | The signed acceptance of the exception: the hop from the bundled proxy to `web` is plain HTTP on the `edge` network of one host. For a proxy on another machine, the certificate and the proxy's trust setting (`WEB_TLS_CERT_FILE`, `WEB_TLS_KEY_FILE`) | Hosting party and client CISO | ADR 0091 point 3; exception recorded in `docs/threat-model.md` |

## Request boundaries over the whole chain

| ID | Asks for | How to show it | Owner | Reference |
|---|---|---|---|---|
| V4.2.1 | Every component that sees a request (load balancer, firewall, proxy, application server) finds message boundaries the same way, so a request cannot be smuggled | The list of components in the chain with their versions, and the result of the penetration test scope for it (`docs/assurance/pentest-scope.md`) | Hosting party and penetration tester | Application server and proxy: `docker-compose.yml`, `deploy/proxy/Caddyfile`; versions from the SBOM. The tunnel trial finding on proxy trust is in the pentest scope |

## Backend communication and outbound traffic

| ID | Asks for | How to show it | Owner | Reference |
|---|---|---|---|---|
| V13.2.1 | Calls between backend components that do not use the user session are authenticated with individual credentials | The list of service credentials: Webex Service App, WxCC service app, SMTP and SMS accounts, log-destination credentials; each its own, none shared between roles | Hosting party and integration administrator | `docs/cryptography.md` (*SETTINGS_ENCRYPTION_KEY*, webhook secrets); secrets are sealed in the database (ADR 0008). The database is a local file, with no network credential |
| V13.2.2 | Those connections use accounts with the least privilege | The scopes granted to the Webex Service App and the WxCC service app; the SMTP account may send only; the container user is non-root | Hosting party and integration administrator | Runbook *Webex Service App* lists the scopes the code calls; `docker-compose.yml` runs uid 10001 with `cap_drop: [ALL]` and a read-only root file system |
| V13.2.3 | No default credentials | Statement and a check that the bundled Keycloak admin password, client secret and `super` password were generated by the installer, not the development values | Hosting party | ADR 0090 point 2; the start-up check refuses `dev-only-` client secrets in production |
| V13.2.4 | A list names the external systems the application may call | The outbound table of `docs/dataflow.md` as the list to approve, and the firewall rule that enforces it | Hosting party | `docs/dataflow.md` *Outbound connections and the hop to web*; the outbound guard (`app/adapters/outbound.py`) covers the destinations the table marks *yes* |
| V13.2.5 | The host is configured so the server can reach only those systems | Egress firewall rules (or the cloud security group export) matching the table; a test from inside the `web` and `worker` containers to a host that is not on the list fails | Hosting party | The same table; the application guard is a second line, not the network control |

## Components, time and logs

| ID | Asks for | How to show it | Owner | Reference |
|---|---|---|---|---|
| V15.2.1 | No component is older than the agreed update and fix time frames | The SBOM of the running version scanned (`grype sbom:sbom.cdx.json`), the host's patch policy and the date of the last host update; System status shows the version | Supplier and hosting party | Release SBOMs (runbook *Releasing a version*); Renovate and the weekly `trivy` scan (`CLAUDE.md` quality gates); the update channel and updater (ADR 0029, 0059) |
| V16.2.2 | Log sources share a synchronised clock and use UTC or an explicit offset | `timedatectl` (or `chronyc tracking`) on the host showing synchronisation; a sample log line | Hosting party and IdP administrator | Log lines carry `@timestamp` in RFC 3339 UTC (`docs/logging.md`); the host clock is the host's (runbook *Log lines and the event schema*) |
| V16.4.2 | Logs cannot be read or changed by unauthorised people | File and volume permissions of the log store; access list of the log platform; write-once or append-only setting | Hosting party and security operations | The application keeps no access log and masks personal fields; audit events are shipped after commit (ADR 0034) |
| V16.4.3 | Logs go to a separate system for analysis and alerting | The configured log destination (the *Log destinations* screen of the superuser) and a sample event received at the log platform | Hosting party and security operations | ADR 0085 (log destinations in the portal, PR #296 and #297); runbook *Shipping audit events to a log platform* |

## Project controls

| ID | Asks for | How to show it | Owner | Reference |
|---|---|---|---|---|
| TEN-06 | The shared superuser, host and keys of one installation are named as trust boundaries, and the client has accepted them | The signed acceptance of the table *Shared trust boundaries*, the named holders of the superuser role and of host access, and the statement that the installation serves one data controller | Client CISO and hosting party | `docs/threat-model.md` *Shared trust boundaries*; decision BP-4 (one installation per data controller); `docs/dataflow.md` *Shared by all organisations of an installation* |
| PI-08 | Deleted data is also gone from files, write-ahead log, volume blocks and snapshots | (a) volume encryption at rest with a key destroyed with the volume: `cryptsetup status <mapping>` or the cloud disk encryption setting; (b) snapshot and back-up retention not longer than the retention promised in the DPIA, with the schedule; (c) the update back-up directory `data/backups` is on the encrypted volume; (d) the disaster-recovery copies meet ADR 0102 point 8 (encrypted, fixed term, named access, destruction record, yearly restore test) | Hosting party | `docs/dataflow.md` *Residual data after a purge*; ADR 0102 points 7 to 10; PR #339 (`secure_delete`). Disaster recovery is the hosting party's (open point O7) |

## Production hardening of the host

The following items complete the evidence above. Tick them at installation and again at each
yearly review. Commands are for the host, not the application.

- [ ] Network time synchronisation is on (`timedatectl`).
- [ ] The data volume, `data/backups` and the snapshots are encrypted at rest.
- [ ] The host firewall allows inbound 80 and 443 only, and outbound only the hosts of the table
  in `docs/dataflow.md`.
- [ ] `web` is not published on a public interface (runbook *Address and proxy*).
- [ ] The host updater timer is on and a `cosign verify` of the running digests passed.
- [ ] The log destination is configured and a test event arrived.
- [ ] The holders of host access and of the superuser role are named, and none of them belongs to
  another data controller (BP-4).

## Sign-off

| Owner | Name | Date | Evidence reference |
|---|---|---|---|
| Hosting party | | | |
| Security operations | | | |
| Penetration tester (V4.2.1 part) | | | |
| Client CISO (the two project controls) | | | |
