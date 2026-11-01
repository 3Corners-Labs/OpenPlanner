# 0086. The public address and the reverse proxy: visible, verified end to end, and by the standards

Status: accepted (2026-10-31, `po:approved` on #265), requested by the product owner. Extends ADR
0064 (HTTPS by default, the certificate watched on System status), ADR 0070 (certificate source
in the portal), ADR 0030 (TLS proxy) and ADR 0042 §11 (System status); builds on ADR 0038 (system
events from the worker). Not built.
Amended by ADR 0091 (own reverse proxy).

## Context

Every link OpenPlanner hands out (confirmation email and SMS, the manage page `/b/{token}`,
the OIDC redirect, the widget loader, the agent API's token URL, the calendar invite) is built
on `PUBLIC_BASE_URL`, never on the address of the request that came in. That is right: behind
a reverse proxy the request address is the proxy's, and a link built from it would never
arrive. It also means that one environment value decides whether any participant ever reaches
the platform, and nothing in the product checks that value against the world:

- **Nobody sees it.** `PUBLIC_BASE_URL`, `WEBHOOK_BASE_URL` and `FORWARDED_ALLOW_IPS` are
  class B (environment). The certificate block on *System status* (ADR 0064) shows the host
  it probes, but not the three values together, nor which of the three shapes the installation
  has: the bundled proxy, a reverse proxy of the organisation's own (`install.sh --no-tls`),
  or none (a lab). The superuser who reads *certificate ok* cannot tell whether the links in
  the emails point at this installation at all.
- **Nobody checks the round trip.** The certificate probe (ADR 0064) proves that *some* server
  on that name serves a valid certificate. It does not prove that the name reaches *this*
  installation (a stale DNS record, a migrated host, a copied `.env`), that the proxy passes
  the scheme (a link built as `https` while the app sees `http` makes the session cookie's
  `Secure` flag refuse to travel), or that the proxy is trusted for the visitor's address
  (`FORWARDED_ALLOW_IPS` wrong means every visitor shares the proxy's address and one bot
  exhausts the booking limit for everyone; the runbook warns, nothing measures).
- **The standards are followed, but nowhere stated or asserted.** The proxy runs Caddy's
  defaults, the app sends HSTS, the key of an own certificate is made where it is used (ADR
  0070). Which standard each of these satisfies is written down nowhere, and `check-proxy.sh`
  asserts the redirect and the body limit but not the TLS versions or the HSTS header. The
  product owner asked on 2026-10-31 that the certificate, proxy and address processes follow
  the applicable industry standards and that this is visible, not assumed. The first client is
  a Dutch government contact center, for which the NCSC TLS guidelines and the Forum
  Standaardisatie *pas toe of leg uit* list are binding.

What stays as it is: links are built on `PUBLIC_BASE_URL` only (no request-derived URL
anywhere in `app/`, and a test keeps it so); the certificate source is managed as ADR 0070
says; the installer asks for the address and the proxy address in `--no-tls` mode (ADR 0064
decision 1).

## Decision

1. **The public address stays in the environment (class B), and the portal shows it.**
   `PUBLIC_BASE_URL`, `WEBHOOK_BASE_URL` and `FORWARDED_ALLOW_IPS` are not moved to class C as
   ADR 0067 did for the update channel, for a reason that holds for these three and not for
   that one: the OIDC redirect URI is derived from the public address, so a wrong portal value
   locks every administrator out at the next login, with no login left to undo it; and the
   proxy's address is the host's network, not the application's. The certificate block on
   *System status* (platform → advanced settings → system status, ADR 0047; no new screen)
   gains an *Address and proxy* part above the certificate: the shape of the installation
   (*bundled proxy*, *own reverse proxy*, *no proxy*; from `TLS_PROXY_BUNDLED` and
   `FORWARDED_ALLOW_IPS`), the public address, the webhook address when it differs, the
   trusted proxy address, and the derived OIDC redirect URI and widget loader URL as text to
   compare with the IdP registration and the embedding site. Each row says *set on the server*
   like every class B row (ADR 0042 §2); the manual topic *Advanced settings* says where to
   change them and that a change needs a restart.

2. **The worker verifies the round trip, not only the certificate.** On the same cycle as the
   certificate probe (`tls_check_interval_hours`, ADR 0064) the worker requests
   `GET {PUBLIC_BASE_URL}/api/v1/instance?probe={nonce}` through the public address, exactly as
   a participant's browser would, and, when `WEBHOOK_BASE_URL` is set, the same on that
   address. The nonce is random per run and stored before the request. The endpoint answers
   every caller with the installation's `instance_id` (a random UUID made at first start and
   stored in the database, never editable, never a secret); only a request that carries the
   pending nonce also gets the detail: the scheme the app saw (`https` or `http`), and whether
   the forwarded headers were *honoured* (the app's client address is the first hop of
   `X-Forwarded-For`), *ignored* (the header was there, the app kept the proxy's address) or
   *absent*. No address is ever in the response. The worker judges one finding per address:
   `ok`, `unreachable`, `other_instance` (the name reaches another installation: DNS, a
   migrated host, a copied `.env`), `not_https` (the app saw `http`: the proxy does not pass
   `X-Forwarded-Proto`, or `PUBLIC_BASE_URL` says `https` for a plain host), `proxy_not_trusted`
   (`FORWARDED_ALLOW_IPS` does not name the proxy), `no_proxy_headers` (a proxy that sets none,
   or `web` published directly), `redirected` (the address answers with a redirect to another
   name, for example `www` for the apex). The latest finding per address is stored next to
   `certificate_checks` and shown in the *Address and proxy* part with the time of the check; a
   finding other than `ok` is a system event `public_address` the first time and every
   `tls_alert_repeat_hours` while it lasts, the return to `ok` once, like `tls.certificate`.
   `python -m app.cli address-check` runs the same probe on demand and exits 1 on a finding,
   so the installer and the runbook's go-live checklist can call it. A network that cannot
   reach its own public name from inside (NAT without hairpin) switches the probe off with
   `PUBLIC_ADDRESS_CHECK_ENABLED=false`; the part then says *not checked: switched off on the
   server*, never silently *ok*.

3. **The endpoint is public, small and rate-limited.** `/api/v1/instance` needs no
   authentication (the worker calls it as a stranger would), falls under the availability-read
   limiter of ADR 0042 §7, sends no cookie and sets `Cache-Control: no-store`. Without the
   pending nonce it reveals the instance id only, which identifies the installation to nobody
   who does not already run it. The threat model gets the endpoint as a flow with its abuse
   cases (nonce guessing: 128 random bits, one pending at a time, consumed on first use;
   using the detail to learn how the proxy is configured: only the holder of the nonce, which
   lives in the database, gets it) and each abuse case gets a test.

4. **Links are built on the public address, and a test keeps it so.** A test asserts that no
   module under `app/` reads `request.base_url`, `request.url_for` or `request.url` to build an
   absolute URL, and that every absolute URL in a message, a calendar invite, the widget loader,
   the agent API and the OIDC flow starts with `PUBLIC_BASE_URL` (or `WEBHOOK_BASE_URL` for a
   webhook registration). What is checked today by reading is then checked by CI.

5. **The standards each process follows are named, and `check-proxy.sh` asserts them.** The
   standards below are the yardstick for the TLS proxy, the certificate processes and the
   proxy headers. Each one is named in the runbook section it governs and, where a test can
   assert it, in `scripts/check-proxy.sh` (CI step *proxy check*, `make proxy-check`):

   | Process | Standard | What OpenPlanner does | Asserted by |
   |---|---|---|---|
   | Obtaining and renewing a certificate | ACME, RFC 8555, `http-01` challenge; CAA, RFC 8659 | Caddy as ACME client; renewal at a third of the lifetime; the runbook asks for a CAA record that allows the CA | `check-proxy.sh` pass 1 against Pebble |
   | Certificate contents and chain | X.509, RFC 5280; server names in `subjectAltName`, RFC 6125 | An uploaded chain is validated with `cryptography` for order, validity, names and key match (ADR 0070); the probe verifies the served chain against the system CA bundle (ADR 0064) | `tests/domain/test_own_certificate.py`, `tls-check` |
   | Private key of an own certificate | NCSC TLS guidelines (key types and sizes); PKIoverheid CPS: the subscriber generates the key | ECDSA P-256 made on the host, never transported, never in the database or a backup (ADR 0070) | `tests/domain/test_tls_source.py` |
   | TLS versions and cipher suites | NCSC-NL *ICT-beveiligingsrichtlijnen voor TLS* v2.1 (settings rated *Goed*, *Voldoende* at most), RFC 8446 (TLS 1.3) | TLS 1.3 and 1.2 only, AEAD suites, OCSP stapling: Caddy's defaults, which the Caddyfile never weakens; no setting | new: `check-proxy.sh` asserts TLS 1.0 and 1.1 refused, 1.2 and 1.3 accepted (`openssl s_client`) |
   | HTTPS and HSTS | Forum Standaardisatie *pas toe of leg uit* (HTTPS and HSTS); RFC 6797; `max-age` of at least one year | Port 80 redirects with 308 and otherwise answers only ACME; the app sends `Strict-Transport-Security: max-age=63072000; includeSubDomains` (two years) | `check-proxy.sh` (redirect today; new: the HSTS header on a proxied response) |
   | Proxy headers | `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host` (de facto standard; RFC 7239 `Forwarded` is not read by uvicorn, the runbook says so); OWASP: trust them from named proxy addresses only | Caddy sets them and drops what the client sent; uvicorn honours them from `FORWARDED_ALLOW_IPS` only, never `*` | new: the round trip of decision 2 in `check-proxy.sh`, expecting `ok` through the proxy and `proxy_not_trusted` with a wrong address |
   | HTTP security headers | OWASP ASVS 4.0 V14.4 | CSP, HSTS, `X-Frame-Options` on admin, `Referrer-Policy`, `X-Content-Type-Options` from the app, not the proxy, so an own reverse proxy changes nothing | `tests/api/test_security_headers.py` |
   | Responsible disclosure | RFC 9116 | `/.well-known/security.txt` (ADR 0003) | existing test |
   | External check of the public address | internet.nl *Website test* (Forum Standaardisatie, NCSC) | The runbook's go-live checklist and the yearly review run the test against the public address and record date and score in `docs/runbook.md` (*TLS audit*); a score below 100 on the TLS, HTTPS or security-headers parts is a finding to fix or explain there | runbook step, by a person |

   A standard that changes (a new NCSC version, a change on the Forum list) is a new ADR that
   amends this table; the table is the one place that says which version is followed.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `PUBLIC_BASE_URL` | B deployment | `http://127.0.0.1:8000` | https URL in production | — | unchanged; stays B on purpose (decision 1: the OIDC redirect depends on it, a wrong portal value locks every administrator out) |
| `WEBHOOK_BASE_URL` | B deployment | empty | https URL | — | unchanged; probed too when set |
| `FORWARDED_ALLOW_IPS` | B deployment | the bundled proxy's address | one or more addresses, never `*` | — | unchanged; shown on System status |
| `PUBLIC_ADDRESS_CHECK_ENABLED` | B deployment | `true` | bool | — | kill switch for a network that cannot reach its own public name from inside (NAT without hairpin); tied to the host's network, so B; the page says it is off |
| `tls_check_interval_hours` | C platform | 6 | 1–168 | `settings.platform.manage` | unchanged (ADR 0064); the round trip runs on the same cycle, one probe cycle for the whole public address |
| `tls_alert_repeat_hours` | C platform | 24 | 1–168 | `settings.platform.manage` | unchanged (ADR 0064); also the repeat period of `public_address` events |

**Fixed on purpose:** the minimum TLS version (1.2) and the cipher suites (Caddy's defaults,
rated *Goed* by the NCSC), because weakening a protection is never a portal choice (ADR 0042);
the HSTS `max-age` (two years, above the Forum's minimum of one year), because a lower value
is a weaker protection and a higher one changes nothing; the nonce length (128 bits); the
instance id (made once, never changed, so an address that reaches another installation is
always told apart from one that reaches this one).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #265 | merged |
| 1 | Decisions 1–4: *Address and proxy* on System status, `instance_id` (migration), `/api/v1/instance`, the round-trip probe in the worker, `address-check` CLI, the link-building test, system event `public_address`, manual topic | | planned |
| 2 | Decision 5: the standards table in the runbook, `check-proxy.sh` asserts TLS versions, HSTS and the round trip, internet.nl in the go-live checklist, threat model and data flow | | planned |

## Consequences

- No new screen and no new setting class: the part lives in the existing certificate block of
  *Advanced settings* (ADR 0047: platform → advanced settings → system status); one new
  deployment kill switch, registered in `app/domain/settings_registry.py` and `docs/settings.md`.
- One new public endpoint `/api/v1/instance` (no `require_permission`: public by design, like
  `/healthz`; rate-limited; no PII in or out), in the OpenAPI spec and `docs/api.md`, and one
  new table `public_address_checks` plus the `instance_id` row (migration, reversible: the
  downgrade drops both; a new id is made at the next start). `docs/threat-model.md` gets the
  endpoint's flow and abuse cases with tests; `docs/dataflow.md` gets the probe (no personal
  data flows).
- The worker gains a second probe on the certificate cycle; a lasting finding costs one event
  per repeat period, as the certificate does. An installation whose network cannot hairpin
  sets the kill switch and keeps the certificate probe.
- `scripts/check-proxy.sh` grows three assertions (TLS versions, HSTS, round trip through the
  proxy with a right and a wrong `FORWARDED_ALLOW_IPS`) and so does CI's *proxy check* time by
  about a minute. No new dependency: `openssl` and `curl` are already required by the script;
  the probe uses `httpx`.
- `docs/runbook.md` gains *Address and proxy* (what each finding means and what to change on
  the host), the standards table with the version followed, and a *TLS audit* record for the
  internet.nl test; `docs/install.md` names `address-check` as the last step of an
  installation; the manual topic *Advanced settings* (nl, en) describes the new part; i18n
  keys for the shape, the findings and the row labels in `nl` and `en`.
- `docs/architecture.md` adds the probe to the worker's diagram. ADR 0064's and ADR 0070's
  build tables are untouched; this ADR extends them.
