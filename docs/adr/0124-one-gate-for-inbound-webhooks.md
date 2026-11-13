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
2. **A fixed order.** (a) The address exists: the platform is known, the organisation is known,
   its connection exists, a secret is set; otherwise 404. (b) The source network is allowed
   (point 3); otherwise 403. (c) `POST`; a content type, when the call names one, is JSON
   (`application/json` or `application/cloudevents+json`), otherwise 415; a call that names
   none goes on, because the signature decides and not every platform's deliveries were
   observed. (d) The declared length and then the bytes actually read stay under the limit
   (point 6); otherwise 413, without reading on. (e) The signature over the raw body, in
   constant time, under the current secret or the previous one inside its overlap (point 8);
   otherwise 401. (f) The signed time, where the platform signs one, is within 300 seconds;
   otherwise 401. (g) The body is an object of the expected shape; otherwise 400. (h) The rate
   limits (point 5); otherwise 429. (i) The event was not seen before (point 7); otherwise 204,
   counted as a duplicate. (j) The event is about something this organisation's connection
   created; otherwise 204, counted as unknown (point 12). Every refusal answers with an empty
   body. The limits come after the signature on purpose: see point 5. Step 1 builds (a),
   (c) to (h) and the replay check as it was; (b), the replay per organisation of (i) and
   the count of (j) come with their steps (*Build status*).
3. **Source networks.** A connection carries a list of allowed networks (IPv4 and IPv6, at most
   32, written as CIDR), edited by the organisation's administrator next to the connection's
   other fields. The installation has a default list per platform, set by the superuser; a
   connection's own list replaces it. An empty list means any source: that is today's
   behaviour, and from now on it is shown as a finding on the connection and on System status,
   never as a silent state. An installation can demand a list
   (`WEBHOOK_SOURCE_NETWORKS_REQUIRED`): then an address without one does not accept. The
   switch demands that a list applies, not that it is narrow: the organisation's own list is the
   organisation's choice and replaces the default. Only a network that covers a whole address
   family (prefix length 0), or an IPv6 network that holds the whole IPv4-mapped range
   `::ffff:0:0/96` (matching also tries the mapped reading, so such a network admits every IPv4
   caller), is refused when a list is saved, on the card, in the two defaults and in the
   `Settings` validator, because such a list is "any source" called "restricted"; the two halves of
   a family are not refused, on purpose, as how wide a list is, is its owner's choice; a
   stored list that cannot be read is never taken for an empty one: every call to that address
   is refused with `network`, and the card and System status say so, and say whose list it is
   (the connection's own, to be set again on its card; the installation's default, for the
   platform administrator, or the organisation sets a list of its own). One
   helper, `app/domain/networks.py`, parses and matches networks, handles an IPv4 address
   written as IPv6, and replaces the two near-copies that the machine API's clients and the
   display links use today (ADR 0005: extract at the second use). A list is defence in depth
   and no more: the supplier's ranges are shared by all its customers, so the signature is what
   stops a stranger, and a list that has gone stale is an outage of our own making. The
   portal therefore shows, next to a list, when the last delivery was accepted and when a call
   was last refused for its network, and a refusal for the network while deliveries were
   recently accepted is one of the things the alert of point 10 counts. Until step 3 adds
   *last accepted* and *last refused* to the card, the faults that are decided per call (point
   4: the caller is the proxy, a hop did not hand it on, no address, headers its sender was not
   trusted to send) are shown nowhere but in the security events. A caller cannot tell `network`
   from `source_unknown`: both are 403 with an empty body. It can tell 403 from 401 (the
   signature) and from 404 (no such address), so a refusal for the network does show that the
   address exists and that a list applies.
4. **The address is only as good as the proxy chain.** The gate takes the caller's address as
   the server derived it from the trusted proxy (`FORWARDED_ALLOW_IPS`) and reads no forwarded
   header for it. When an allow-list applies and the address held for a call cannot be the
   caller's, the gate refuses with the reason `source_unknown`: a restriction that cannot be
   evaluated is not passed. An address without a list is not affected. The cases, each decided
   for the one call at hand and each with its test: `FORWARDED_ALLOW_IPS` is `*` or wider than
   single hosts (every address could be spoofed); the caller's address is one of the trusted
   proxies, or a loopback or private address while the public address is not (a hop that did
   not hand the caller on); the call itself carries forwarded headers that the server did not
   use because its sender is not a trusted proxy (the address held is that hop, as in a chain
   of proxies where one is not trusted); the call has no usable address. Nothing that an
   earlier call did decides a later one: the proxy watch of ADR 0086, which remembers that
   forwarded headers once came from an untrusted sender, stays a finding on System status and
   refuses nothing, because a mark that lasts would let any stranger who reaches the
   application around its proxy switch every list off with one request (revised 2026-11-13,
   found while building step 2b). Two assumptions hold under all of it: the server takes the
   right-most address of the forwarded chain that is not a trusted proxy as the caller, and a
   trusted proxy appends to or replaces the forwarded header, never passes a caller's own value
   through. An entry of `FORWARDED_ALLOW_IPS` that cannot be read (neither `*`, an address nor a
   network, a socket path included) is a standing fault of its own, shown on System status and
   refusing a list-governed address like wide trust, never skipped in silence. Three shapes
   cannot use a list today, and are refused rather than passed: (1) a proxy trusted by a
   network instead of a single address, such as a Kubernetes ingress whose pods change: trust
   wider than single hosts is `source_unknown`, and step 5 gives the chart a way to pin the
   ingress while step 6 writes the contract; (2) a content delivery network in front of the
   proxy: the caller becomes its address, and listing it would admit all its customers, so no
   list belongs there (step 6, the contract and the test delivery that shows the address seen);
   (3) a supplier that calls a public name from a private address (split DNS): a private caller
   while the public address is public is `source_unknown` (step 6, the same). The PROXY
   protocol, where a load balancer uses it, ends at the proxy; the application never speaks it.
5. **Limits that a stranger cannot turn against an organisation.** A supplier sends from
   addresses it shares among all its customers, and any customer can make those addresses call
   ours. So a call that fails a check never counts against the deliveries that pass:
   - *Verified deliveries* have a bucket per platform, organisation and caller address,
     `rate_limit_webhook_per_minute`; only the supplier's own authentic traffic can fill it.
   - *Refused calls to an address that exists* have a bucket of their own with the same key and
     value. While it has room a call gets its own reason; when it is full the call gets 429.
     It never refuses a verified delivery, so each such call is read (at most the size limit)
     and its signature checked: that work is the price, and the proxy's connection limits are
     its ceiling.
   - *Calls to an address that does not exist* have a bucket per caller address. Over it they
     get 429 instead of 404. It never refuses a call to an address that exists.
   All three go through the common limiter and its settings; a refusal is 429 with
   `Retry-After` and no page. A bucket key uses the caller's address as the server derived it;
   from step 2 an IPv6 caller counts by its /64, in the one helper every limiter uses. A
   refusal that a full bucket turns into 429, and a refusal for an address that does not exist,
   leave an event that is held back when repeated and no log line per call; a refused call that
   still had room is logged with its reason as before, and its bucket is the ceiling of those
   lines.
6. **The size limit holds before and while reading.** 64 KiB stays the limit, as a constant: a
   real event is a few kilobytes. The bundled proxy applies the same limit to `/webhooks/*`.
7. **Replays, per organisation.** The remembered key includes the platform and the
   organisation. An event without an identifier is remembered by the hash of its verified body:
   the same signed body twice is a replay by definition. The memory is consulted only after the
   signature held, holds the hash and never the body, and its key does not depend on which
   secret verified. `docs/webhooks.md` is corrected.
8. **A secret rotates with an overlap.** After a rotation the previous secret keeps verifying
   for `webhook_secret_overlap_minutes`, for an organisation's secret and for the installation's
   own. The subscription at the platform is changed in place where the platform allows it, and
   otherwise made anew inside the overlap, so no event is refused on the way. The previous
   secret is sealed like the current one and removed when the overlap ends. Whether the previous
   secret still counts is decided by the clock at the moment of checking, not by the job that
   removes it; both secrets are always compared, without stopping at the first, so the time
   taken tells nothing about which one matched. Per platform: the video platform's webhook is
   updated in place with the new secret; the contact center's subscription is updated in place
   where its API allows and otherwise made anew inside the overlap.
9. **A connection can be paused.** `inbound_enabled` on the connection. A paused address still
   passes the gate and answers 204, and the verified event is dropped and counted as paused: a
   platform that keeps getting errors switches a subscription off, and a pause must not cost
   the subscription. A pause shows as a finding; on resuming, the reconcile of ADR 0032 brings
   the states back. The installation-wide addresses that ADR 0032 point 11 keeps for one more
   minor pass the same gate with the installation's defaults until they go.
10. **Every refusal leaves a trace, with its reason.** Through the one place that turns a
    request into a security event, with the platform, the organisation's slug (public, not
    personal data, and only of an organisation that exists) and one reason of a closed list:
    `unknown_address`, `network`, `source_unknown`, `rate`, `content_type`, `too_large`,
    `invalid_signature` (the name audit rows already carry), `stale`, `malformed`. Repeats are
    held back as today. The connection's card shows the last accepted and the last refused
    delivery with its reason, and counts per reason for the window. No body, header value,
    signature or address in clear is stored: the caller is the pseudonym of ADR 0043.
    *The alert* counts only what a passer-by cannot cause alone, because anyone who knows a
    slug can send calls that fail: a `stale` delivery (it was signed: a replay, or a clock that
    is off), and, for a connection with a list of networks, any refusal from inside that list.
    When those pass `webhook_refusal_alert_count` inside the event window, one system event for
    that organisation says so. Refusals from strangers show in the counts and raise nothing.
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
    before relying on a list. Three more rules for the proxy: its rule for
    `/webhooks/*` carries a deadline for reading a body, since the application has none of its
    own; when `WEBHOOK_BASE_URL` names its own host and the main name still serves
    `/webhooks/*`, that is a finding on System status; and `check-proxy.sh` asserts that the
    proxy logs no bodies, where it can be asserted.
12. **What comes in must answer what went out.** An organisation's address accepts only events
    about work its own connection created; an event about unknown work is counted and joins the
    alert of point 10, because it usually means a subscription that points at the wrong
    address. The addresses we register are built from `WEBHOOK_BASE_URL` and the organisation's
    slug only, and a test keeps it so. An API host typed in the portal is resolved and must be
    public every time it is used, through the outbound guard that the login providers already
    use, not only checked as a name when it is saved. The guard connects to the address
    it resolved and checked, so a name that changes between the check and the connection gains
    nothing, and it follows no redirect.
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
    it. The deliveries are signed by the platform with that secret, as the events are
    (measured on the lab tenant, ADR 0123). Shown once means: on a page that is not stored by
    the browser, never in an event, a log, an address or a job; lost is solved by making a new
    one, and a new one overlaps the old as in point 8. The chat of ADR 0123 is text only, so
    the 64 KiB limit holds; a message with an attachment is refused. These bodies are a
    participant's words: every refusal stays without a body, and the replay memory keeps a
    hash that includes the organisation, never the text.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `rate_limit_webhook_per_minute` | C platform | 600 | 60–6000 | `settings.platform.manage` | The value of each of the three buckets of point 5. Today a constant |
| `webhook_source_networks_video` | C platform | empty | at most 32 networks | `settings.platform.manage` | The installation's allowed sources for the video platform's webhooks |
| `webhook_source_networks_contact_center` | C platform | empty | at most 32 networks | `settings.platform.manage` | The installation's default for contact-center connections |
| connection: allowed networks | D organisation (per connection) | empty (the installation's default) | at most 32 networks | as the connection's other fields | Replaces the default for this connection |
| connection: `inbound_enabled` | D organisation (per connection) | on | on / off | as the connection's other fields | Pauses the address: verified events are dropped, the platform still gets 204; a pause is a finding |
| `webhook_secret_overlap_minutes` | C platform | 60 | 0–1440 | `settings.platform.manage` | How long the previous secret keeps verifying; 0 is at once |
| `webhook_refusal_alert_count` | C platform | 20 | 5–1000 | `settings.platform.manage` | Refusals of the kinds point 10 counts, for one connection inside the event window, before the alert |
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
accepted content types; the closed list of refusal reasons. `Retry-After` is 60 seconds while the limit
counts per minute.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed; the row in `docs/adr-status.md` | #791 | merged |
| 1 | The gate, with today's checks moved into it and: the limit as a setting, with separate buckets for verified deliveries, refused calls and calls to addresses that do not exist, and a machine answer, the declared and the streamed size limit, the content type, refusal events with reason, platform and organisation; the route sweep; `docs/webhooks.md` corrected | #791 | merged |
| 2a | The shared network helper `app/domain/networks.py`, with the machine API's clients and the display links moved onto it; one caller key for every limiter, an IPv6 caller counted by its /64 | #818 | merged |
| 2b | Source networks: the list per connection and the installation's defaults, refusing on an untrusted proxy chain, `WEBHOOK_SOURCE_NETWORKS_REQUIRED`, the findings | #821 | merged |
| 3a | Replays per organisation and by body hash (`app/domain/webhook_replay.py`); the pause (`inbound_enabled` in the connection's record, set with a button of its own on the card through `set_inbound`, dropped in the gate after every check, the card's chip and sentence) | #823 | merged |
| 3b | The refusal alert; the connection card's status, with *last accepted* and *last refused*, counts per reason and the per-call source faults; the count of events dropped while paused, and the pause on the status screens | | planned |
| 4 | Secret rotation with an overlap | | planned |
| 5 | The bundled proxy: the webhook rule, the host of its own, `WEBHOOK_PROXY_NETWORKS`, `check-proxy.sh`; the chart's webhook host and network policy, and a way to pin the ingress as single addresses (shape 1 of point 4) | | planned |
| 6 | The contract for an organisation's own proxy in the runbook and the Kubernetes guide; the signed test delivery that reports the source address it saw; the contract says why a content delivery network (shape 2) and a supplier calling a public name from a private address (shape 3) cannot use a list; the threat model's abuse cases, each with its test | | planned |
| 7 | The outbound guard for an API host typed in the portal, at use | | planned |
| 8 | An address per organisation for the video platform's webhooks | | follow-up |
| 9 | Dropping the installation-wide contact-center addresses (ADR 0032 point 11) | | follow-up |

Step 1 merges in the same pull request as this ADR (ADR 0119).

## Consequences

- A new webhook route is a declaration, and cannot forget a check.
- An organisation can narrow who may call its address, and sees when someone else tries.
- An installation whose proxy does not hand on addresses is told so before a list locks its own
  supplier out, and is refused rather than passed when a list cannot be evaluated.
- No migration in step 1 or in step 3a. The replay key changes its derivation, not its table
  (the old shape is read until 0.7.0, ADR 0116). A connection's networks and pause live in the
  sealed record that holds the connection today.
- `docs/webhooks.md`, `docs/threat-model.md` (abuse cases: a call from outside the list, a
  spoofed forwarded address, a flood per organisation, a replay across organisations, a secret
  used after its overlap, an oversized body sent slowly), `docs/dataflow.md`, the runbook, the
  Kubernetes guide and the manual topic of the connection card change with their steps.
- Rotation with an overlap means two secrets verify for a while: the overlap is short by
  default and can be set to nothing.
