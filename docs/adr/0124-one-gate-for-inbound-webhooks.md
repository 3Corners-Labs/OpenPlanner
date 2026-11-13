# 0124. One gate for inbound webhooks, also behind a reverse proxy

Status: accepted, partly built (2026-11-13), requested by the product owner. Step 0 and step 1
are built (see *Build status*); the rest is planned. Extends ADR 0032 (a contact-center
connection per organisation), ADR 0034 and ADR 0053 (security events), ADR 0064 and ADR 0086
(the proxy and the public address). ADR 0123 (chat through the contact center) depends on it.

## Context

Systems we call answer by calling us: the video platform reports that a meeting started or a
participant joined, the contact center that a task was created, accepted or ended. ADR 0123 adds
a third stream, the messages of a conversation. Each call arrives from the internet, without a
login, at an address under `/webhooks/`, usually through a reverse proxy: the bundled one, or the
organisation's own proxy or ingress.

What exists (`app/api/webhooks.py`, `app/adapters/webhooks.py`): four routes; a signature over
the raw body compared in constant time; for the contact center a signed time that may be 300
seconds off; a memory of event identifiers for 30 days; a 64 KiB limit; a rate limit of 600 a
minute per address; a secret per organisation's connection, sealed; a security event for a
refused signature, held back when repeated.

What is missing, found by reading the code on 2026-11-13:

- No restriction on where a call comes from, in the application or in the bundled proxy.
- The rate limit is a constant in the route module, outside the common limiter's settings; all
  organisations behind one supplier address share one bucket; a refusal is the HTML error page.
- The body is read in full before its size is checked. The content type is not checked.
- A refusal before the signature (unknown organisation, no secret, too large, malformed) leaves
  no trace. A refused signature does not say for which organisation, and a stale time is not
  told apart from a wrong signature. Nothing alerts on repeated refusals.
- A rotated secret stops working at once, and the subscription is deleted and made again, so
  events in between are lost.
- A connection cannot be paused. The replay memory is not scoped by organisation, and an event
  without an identifier is never recognised as a replay (`docs/webhooks.md` says otherwise).
- Every check lives in the route that needs it. A new route has to remember all of them.

The product owner's request (2026-11-13): every webhook fully supported and safe to deploy, also
behind a reverse proxy, with a restriction on source networks and every other guardrail that
fits, for the calls that answer what an organisation's own connection started, in an
installation with several organisations.

## Decision

1. **One gate, one home.** Every route under `/webhooks/` passes one gate: the rules are pure
   functions in `app/domain/inbound_gate.py`, the HTTP part is a dependency of the router in
   `app/web/webhook_gate.py`. A route declares what it is (platform, organisation or
   installation, which secret, which freshness rule) and gets a verified, parsed event or
   nothing. No route verifies on its own. A test walks the application's routes and fails for a
   `/webhooks/` route without the gate, the way the authorisation sweep does for admin routes.
2. **A fixed order, cheapest first.** (a) The address exists: the platform is known, the
   organisation is known, its connection exists and is not paused, a secret is set; otherwise
   404. (b) The source network is allowed (point 3); otherwise 403. (c) The rate limit (point
   5); otherwise 429. (d) `POST` with a JSON content type (`application/json` or
   `application/cloudevents+json`); otherwise 415. (e) The declared length and then the bytes
   actually read stay under the limit (point 6); otherwise 413, without reading on. (f) The
   signature over the raw body, in constant time, under the current secret or the previous one
   inside its overlap (point 8); otherwise 401. (g) The signed time, where the platform signs
   one, is within 300 seconds; otherwise 401. (h) The body is an object of the expected shape;
   otherwise 400. (i) The event was not seen before (point 7); otherwise 204, counted as a
   duplicate. (j) The event is about something this organisation's connection created;
   otherwise 204, counted as unknown (point 12). Every refusal answers with an empty body.
   Calls to an address that does not exist count against a limit of their own per caller; over
   it they get 429 instead of 404 and start no further event. That limit never refuses a call
   to an address that exists: several organisations, and strangers, can share one supplier
   address, and one of them must not be able to lock the others out.
3. **Source networks.** A connection carries a list of allowed networks (IPv4 and IPv6, at most
   32, written as CIDR), edited by the organisation's administrator next to the connection's
   other fields. The installation has a default list per platform, set by the superuser; a
   connection's own list replaces it. An empty list means any source: that is today's
   behaviour, and from now on it is shown as a finding on the connection and on System status,
   never as a silent state. An installation can demand a list
   (`WEBHOOK_SOURCE_NETWORKS_REQUIRED`): then an address without one does not accept. One
   helper, `app/domain/networks.py`, parses and matches networks, handles an IPv4 address
   written as IPv6, and replaces the two near-copies that the machine API's clients and the
   display links use today (ADR 0005: extract at the second use).
4. **The address is only as good as the proxy chain.** The gate takes the caller's address as
   the server derived it from the trusted proxy (`FORWARDED_ALLOW_IPS`) and reads no forwarded
   header itself. When an allow-list applies and the address cannot be trusted (the proxy
   watch of ADR 0086 reports forwarded headers from an untrusted hop, or the caller appears to
   be the proxy itself), the gate refuses with the reason `source_unknown`: a restriction that
   cannot be evaluated is not passed. System status says what to repair. An address without a
   list is not affected.
5. **The rate limit goes through the common limiter.** `rate_limit_webhook_per_minute` joins
   the limiter's settings; the bucket is per platform, organisation and source address, so
   organisations behind one supplier address no longer share one; a refusal is 429 with
   `Retry-After` and no page.
6. **The size limit holds before and while reading.** 64 KiB stays the limit, as a constant: a
   real event is a few kilobytes. The bundled proxy applies the same limit to `/webhooks/*`.
7. **Replays, per organisation.** The remembered key includes the platform and the
   organisation. An event without an identifier is remembered by the hash of its verified body:
   the same signed body twice is a replay by definition. `docs/webhooks.md` is corrected.
8. **A secret rotates with an overlap.** After a rotation the previous secret keeps verifying
   for `webhook_secret_overlap_minutes`, for an organisation's secret and for the installation's
   own. The subscription at the platform is changed in place where the platform allows it, and
   otherwise made anew inside the overlap, so no event is refused on the way. The previous
   secret is sealed like the current one and removed when the overlap ends.
9. **A connection can be paused.** `inbound_enabled` on the connection; a paused address
   answers 404 and shows as a finding. The installation-wide addresses that ADR 0032 point 11
   keeps for one more minor pass the same gate with the installation's defaults until they go.
10. **Every refusal leaves a trace, with its reason.** Through the one place that turns a
    request into a security event, with the platform, the organisation's slug (public, not
    personal data) and one reason of a closed list: `unknown_address`, `network`,
    `source_unknown`, `rate`, `content_type`, `too_large`, `invalid_signature` (the name audit rows already carry),
    `stale`, `malformed`.
    Repeats are held back as today. When the refusals for one connection pass
    `webhook_refusal_alert_count` inside the event window, one system event for that
    organisation says so. The connection's card shows the last accepted and the last refused
    delivery with its reason, and counts per reason for the window. No body, header value,
    signature or address in clear is stored: the caller is the pseudonym of ADR 0043.
11. **Behind a proxy.** *The bundled proxy* gets a rule for `/webhooks/*`: `POST` only, the size
    limit, and, when `WEBHOOK_PROXY_NETWORKS` is set, only those source networks, refused before
    the application sees the call. When `WEBHOOK_BASE_URL` names a host of its own, the proxy
    serves that name with `/webhooks/*` alone and the main name answers `/webhooks/*` with 404,
    so the portal can stay on an internal name while only the webhook name faces the internet.
    `scripts/check-proxy.sh` asserts each of these. *An organisation's own proxy or ingress*
    follows a written contract in the runbook and the Kubernetes guide: let through `POST
    /webhooks/*` and nothing else from outside when the portal is internal; hand on the caller's
    address from a trusted hop (or the PROXY protocol at the load balancer); the size limit; no
    bodies in an access log; an allow-list at the edge where the edge can do it. The chart gets
    values for a separate webhook host and its network policy. *Proof:* the existing signed
    test delivery (`webhooks-health`) runs through `WEBHOOK_BASE_URL` and reports which source
    address the application saw, so an administrator sees whether the chain keeps addresses
    before relying on a list.
12. **What comes in must answer what went out.** An organisation's address accepts only events
    about work its own connection created; an event about unknown work is counted and joins the
    alert of point 10, because it usually means a subscription that points at the wrong
    address. The addresses we register are built from `WEBHOOK_BASE_URL` and the organisation's
    slug only, and a test keeps it so. An API host typed in the portal is resolved and must be
    public every time it is used, through the outbound guard that the login providers already
    use, not only checked as a name when it is saved.
13. **In the portal, no new home.** The organisation's contact-center connection card (ADR
    0032, ADR 0047) shows the address, when the secret was made and until when the previous one
    is valid, the allowed networks, paused or not, and the status of point 10. System status
    shows the installation's defaults and the findings. The chain check of ADR 0123 point 17
    reads the same state.
14. **The video platform's webhooks** stay one address for the installation, registered per
    host account as today, behind the same gate with the installation's defaults. An address
    per organisation for them is a follow-up.
15. **The message deliveries of ADR 0123** get an address of their own per connection,
    `/webhooks/contact-center/{kind}/{org}/messages`, with a secret of its own that is shown
    once when it is made (the administrator pastes it into the platform's console; our own
    subscription secret never leaves the server). It passes this gate; ADR 0123 step 7b builds
    it.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `rate_limit_webhook_per_minute` | C platform | 600 | 60–6000 | `settings.platform.manage` | Per platform, organisation and source address. Today a constant |
| `webhook_source_networks_video` | C platform | empty | at most 32 networks | `settings.platform.manage` | The installation's allowed sources for the video platform's webhooks |
| `webhook_source_networks_contact_center` | C platform | empty | at most 32 networks | `settings.platform.manage` | The installation's default for contact-center connections |
| connection: allowed networks | D organisation (per connection) | empty (the installation's default) | at most 32 networks | as the connection's other fields | Replaces the default for this connection |
| connection: `inbound_enabled` | D organisation (per connection) | on | on / off | as the connection's other fields | Pauses the address; a pause is a finding |
| `webhook_secret_overlap_minutes` | C platform | 60 | 0–1440 | `settings.platform.manage` | How long the previous secret keeps verifying; 0 is at once |
| `webhook_refusal_alert_count` | C platform | 20 | 5–1000 | `settings.platform.manage` | Refusals for one connection inside the event window before the alert |
| `WEBHOOK_SOURCE_NETWORKS_REQUIRED` | B deployment (env) | off | on / off | host | When on, an address without allowed networks does not accept |
| `WEBHOOK_PROXY_NETWORKS` | B deployment (env) | empty | networks | host | Enforced by the bundled proxy, before the application |

Classes:

- **B, deployment:** the proxy's list is enforced outside the application and belongs to the
  host; whether a list is mandatory is a protection, and a protection is not switched off from
  the portal.
- **C, platform:** the limit, the installation's default sources, the overlap and the alert.
- **D, organisation:** a connection's own sources and its pause, next to the fields the
  organisation's administrator already edits.

**Fixed on purpose:** the order of the checks; the 64 KiB limit; the 300 seconds of freshness and
the 30 days of replay memory (security rules); refusing when the source cannot be known; the
accepted content types; the closed list of refusal reasons.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed; the row in `docs/adr-status.md` | #791 | merged |
| 1 | The gate, with today's checks moved into it and: the limit as a setting with its own bucket and a machine answer, the declared and the streamed size limit, the content type, refusal events with reason, platform and organisation; the route sweep; `docs/webhooks.md` corrected | #791 | merged |
| 2 | Source networks: the shared network helper (machine API clients and display links moved onto it), the list per connection and the installation's defaults, refusing on an untrusted proxy chain, `WEBHOOK_SOURCE_NETWORKS_REQUIRED`, the findings | | planned |
| 3 | Replays per organisation and by body hash; the pause; the refusal alert and the connection card's status | | planned |
| 4 | Secret rotation with an overlap | | planned |
| 5 | The bundled proxy: the webhook rule, the host of its own, `WEBHOOK_PROXY_NETWORKS`, `check-proxy.sh`; the chart's webhook host and network policy | | planned |
| 6 | The contract for an organisation's own proxy in the runbook and the Kubernetes guide; the signed test delivery that reports the source address it saw; the threat model's abuse cases, each with its test | | planned |
| 7 | The outbound guard for an API host typed in the portal, at use | | planned |
| 8 | An address per organisation for the video platform's webhooks | | follow-up |
| 9 | Dropping the installation-wide contact-center addresses (ADR 0032 point 11) | | follow-up |

Step 1 merges in the same pull request as this ADR (ADR 0119).

## Consequences

- A new webhook route is a declaration, and cannot forget a check.
- An organisation can narrow who may call its address, and sees when someone else tries.
- An installation whose proxy does not hand on addresses is told so before a list locks its own
  supplier out, and is refused rather than passed when a list cannot be evaluated.
- No migration in step 1. The replay key changes its derivation, not its table. A connection's
  networks and pause live in the sealed record that holds the connection today.
- `docs/webhooks.md`, `docs/threat-model.md` (abuse cases: a call from outside the list, a
  spoofed forwarded address, a flood per organisation, a replay across organisations, a secret
  used after its overlap, an oversized body sent slowly), `docs/dataflow.md`, the runbook, the
  Kubernetes guide and the manual topic of the connection card change with their steps.
- Rotation with an overlap means two secrets verify for a while: the overlap is short by
  default and can be set to nothing.
