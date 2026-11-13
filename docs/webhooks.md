# Integrations: Webex Meetings and Webex Contact Center

Contracts verified on 2026-10-23 against developer.webex.com (Meetings API, WxCC Work Items
guide and the embedded OpenAPI specs of Create Task v2, End Task and Subscriptions v2).
ADR 0017 (video), ADR 0018 (work items), ADR 0019 (direct call).

## Authentication

One Webex **Service App**, authorised by an administrator in Control Hub, provides an access
token (valid 14 days) and a refresh token (valid 90 days). OpenPlanner refreshes with
`POST https://webexapis.com/v1/access_token` (`grant_type=refresh_token`) before expiry and
stores the tokens encrypted (ADR 0008). Scopes:

| Scope | For |
|---|---|
| `meeting:admin_schedule_write`, `meeting:admin_schedule_read` | create and delete meetings on behalf of the group's host |
| `meeting:admin_participants_read` | live status (participants) |
| read scope of `meetings` and `meetingParticipants` at admin level (org-owned webhooks; exact scope names to be confirmed when registering) | live status events |
| `cjp:task_write`, `cjp:task_read` | Create Task v2, Task Messages |
| `cjp:user` | optional, secondary: End Task v1 for cancelled bookings (ADR 0021); without it the task stays marked cancelled through v2 |
| `cjp:config_read`, `cjp:config_write` | register and list the WxCC subscription, event types, task search in `wxcc-check`, the routing configuration in `wxcc-config-check` (administrator role) |

## Outbound: Webex Meetings

`POST https://webexapis.com/v1/meetings`, on behalf of the group's host:

```json
{
  "title": "Videogesprek Demo: videogesprek",
  "start": "2026-10-26T09:00:00+01:00",
  "end": "2026-10-26T09:30:00+01:00",
  "timezone": "Europe/Amsterdam",
  "hostEmail": "host-demo@example.nl",
  "allowAnyUserToBeCoHost": true,
  "enabledJoinBeforeHost": false,
  "unlockedMeetingJoinSecurity": "allowJoinWithLobby",
  "sendEmail": false,
  "meetingOptions": {"enabledChat": true, "enabledVideo": true, "enabledFileTransfer": false},
  "attendeePrivileges": {"enabledChatHost": true, "enabledChatOtherParticipants": true}
}
```

The title never contains the participant's name. OpenPlanner keeps `id` (as `video_room_id`) and
`webLink` (as `guest_join_url` and `host_join_url`; one link, the role follows the signed-in
account). Cancelling deletes the meeting: `DELETE /v1/meetings/{id}?hostEmail=…`.

## Outbound: WxCC work item

`POST https://api.wxcc-<region>.cisco.com/v2/tasks` (beta). Response `201` with `data.id` (the
task id, stored as `wxcc_task_id` and, since ADR 0032, also as `contact_center_task_id` with
kind `webex_cc`; the old column stays the one read for WxCC until the next minor).

```json
{
  "origin": {"id": "OP-000123", "name": "Ada"},
  "destination": {"id": "<business address of the work item asset>", "type": "businessAddress"},
  "channelType": "workItem",
  "channel": "<work item channel name>",
  "flowSettings": {
    "openplannerGroup": "<routing value of the group>",
    "openplannerSkills": "<code>,<code>",
    "<variable name>": "<mapped value>"
  },
  "channelParams": {
    "type": "work-item-form",
    "message": {
      "aliasId": "<uuid>",
      "workItemData": {
        "bookingReference": "OP-000123",
        "firstName": "Ada",
        "startTime": "26 okt 2026, 09:00",
        "groupName": "Demo: videogesprek",
        "meetingLink": "https://<site>.webex.com/<…>",
        "kind": "scheduled"
      },
      "timestamp": 1792998000000
    }
  }
}
```

Limits: `workItemData` ≤ 50 keys (≤ 100 bytes), values ≤ 1024 bytes; `flowSettings` ≤ 15
tuples (key ≤ 200, value ≤ 512 bytes). Only first name and the meeting link leave OpenPlanner
(CLAUDE.md), plus the routing variables of ADR 0081.

**Routing variables** (ADR 0081): per group the organisation admin maps the subject and each
switched-on routing attribute (`preferred_language`, `channel_preference`) to a flow variable
name (`[A-Za-z_][A-Za-z0-9_]{0,63}`, never `openplannerGroup`) and a value per code
(`[A-Za-z0-9_.:-]{1,64}`). Only mapped codes are sent, as extra keys of `flowSettings`; the
participant's labels, form answers and any other booking field never are. The catalogue has two
attributes and a group one subject question, so at most four keys of the fifteen are used.
`openplannerGroup` is written last and cannot be overwritten. Create a flow variable of type
string with the same name in the client's flow to read a value.

**Skill codes** (ADR 0121 step 4b): when the booking's flow (or its subject) requires skills,
`openplannerSkills` carries the contact-center codes of those skills, sorted and joined by
commas (at most 512 bytes; each code `[A-Za-z0-9_.:-]{1,64}`). The organisation admin sets a
skill's code on the skill's page under *Skills and capacity*. A booking that requires no skill
(every legacy flow) sends no such key. A required skill without a code is never left out: the
dispatch is refused as not ready (`skill_codes`), and a flow that dispatches to the contact
center cannot be published or activated with an unmapped skill (readiness reason
`skill_unmapped`). Like `openplannerGroup` the key is written after the routing variables, and
the mapping form refuses it as a variable name. In the client's flow, test the codes with a
condition (for example `contains`) or pass them to the skill requirement of *Queue To Agent*.

**The channel-independent task contract** (ADR 0121 step 6,
`docs/service-flows-contact-center-contract-design.md`). The body above is Webex Contact
Center's form of a neutral `ContactCenterTask` (`app/domain/calls.py`): booking reference, first
name, start label, group name, host join URL, kind of call, routing, the routing variables, the
mapped skill codes and the **interaction kind** of the flow the booking was made under (`video`,
`telephone`, `in_person` or `none`; `video` for a booking without a flow). Every platform adapter
declares in its `ProviderDescriptor` what it carries (`ContactCenterCapabilities`: interaction
kinds, and whether it routes on skills); a descriptor that declares nothing carries nothing.

| Platform | Interaction kinds | Skills |
|---|---|---|
| Webex Contact Center | `video`, `none` (since step 6b) | yes (`openplannerSkills`) |

A task the platform does not carry is refused before it is built (`integration_not_ready`,
field `interaction_kind` or `skill_codes`) and again by the adapter (`not_ready`), so nothing is
sent; and a flow that dispatches to the contact center is not ready while its service has no
complete routing (`contact_center_missing`), while the platform cannot take the flow's contact
channel (`interaction_not_carried`) or, for a flow with skills, cannot route on skills
(`skills_not_carried`). A task of another kind than `video` (`none`: work without a
meeting) carries an empty `meetingLink` and its kind in the reserved flow variable
`openplannerInteraction`, written after the routing variables (the mapping form refuses the
name); a video task has no such key, so the body of every existing flow stays byte for byte what
it was (`tests/adapters/wxcc_task_bodies.json`, recorded before the contract; a deliberate change
of the body re-records that file in the same PR). The asset schema keeps its six keys; the
client's flow reads `openplannerInteraction` to route work without a meeting. A telephone
interaction never adds the participant's phone number to a payload.

**Request work at the contact center** (ADR 0121 step 6b). A booking of a new flow on a counted
pool whose flow dispatches to the contact center is sent by the dispatch job as an *attempt*: a
work item of channel `contact_center`. One active work item per request is the fence, so the own
queue and the contact center never hold the same request: the dispatch job skips work the own
queue holds (`own_queue`) and a claim of work the contact center holds is refused
(`already_claimed`). The task id is kept on the booking as for every task. A create that may have
reached the platform but got no answer (a read timeout, a broken connection, a `500`, `502`,
`503` or `504`) is never
retried: the attempt waits in `reconciliation_required`. A task event whose task OpenPlanner does
not know and whose `origin` (the booking reference sent as `origin.id`; the name beside it is
never read, and an origin that is not a reference is dropped) names that confirmed booking adopts
the task as the attempt's own; an attempt whose create is still running is never adopted. Two
installations must not share one contact-center tenant: the reference is unique per installation. After
`contact_center_reconcile_minutes` (class C, default 10) the attempt is fenced: the request goes
to the own queue, the team is told through the fallback (ADR 0082), and a task that turns up
later is marked cancelled and ended by the end-task job. Task events on an attempt:
`task:connected` starts the treatment (a unit of the booking's pool), `task:ended` completes the
request and the booking, `task:failed` puts the request back in the own queue and the fallback tells the team. Whether Webex
Contact Center's task events carry the origin is still to be seen on the test host.

**Ending a cancelled booking's task** (ADR 0021), v2 first. **Primary:** a Task Messages v2
snapshot, `POST /v2/tasks/{taskId}/messages` (`cjp:task_write`, beta, `202`), marks the task
cancelled. **Secondary:** End Task, `POST /v1/tasks/{taskId}/end` without a body (v1 only;
`cjp:user`; `202`); a refusal is recorded as `end_not_possible` with the code.

```json
{
  "mediaType": "workItem",
  "channelParams": {
    "type": "work-item-form",
    "message": {
      "aliasId": "<uuid>",
      "workItemData": {"bookingReference": "OP-000123", "kind": "cancelled"},
      "timestamp": 1792998000000
    }
  }
}
```

Only the booking reference goes out: no first name, no meeting link.

**Reading a task** (ADR 0082 point 1): `GET /v1/tasks/{taskId}` (`cjp:config_read`). Only
`status` (mapped to OpenPlanner's task state) and `lastUpdatedTime` are read; the task's
attributes and `workItemData` are ignored. A `404` is a task WxCC does not know.

**Probe work item** (ADR 0082 point 2): the group check sends the same Create Task v2 request
with `kind: "probe"`, `bookingReference: "probe-<12 hex>"`, `firstName: "Probe"`, the group's
name and the probe meeting's link (or `https://openplanner.invalid/probe`). The flow's Case
on `kind == probe` ends it; otherwise OpenPlanner ends it with End Task after
`INTEGRATION_PROBE_TIMEOUT_SECONDS`. Its `task:new` and `task:ended` are noted for the check
only (see *Inbound: WxCC subscription*); a probe never appears in the own queue, the agenda or
the overview, and a failed probe (`wxcc.probe.*`) makes the organisation's WxCC health `down`.
The set-up of the flow branch for the organisation's administrator is step 5 of the manual
topic *Contactcenter inrichten* (`docs/manual/nl/contact-center-setup.md`).

**Fallback** (ADR 0082 point 11): while a group is in fallback, its due dispatch jobs send no
Create Task request at all (they skip with reason `fallback`); when the fallback ends, bookings
not yet claimed get a fresh dispatch job and the usual request above.

## Webhook addresses

Webex calls `<WEBHOOK_BASE_URL>/webhooks/webex`. An organisation with its own contact-center
connection (ADR 0032 point 10) is called at `<WEBHOOK_BASE_URL>/webhooks/contact-center/{kind}/{org}`,
verified with that organisation's generated secret; the task is looked up among its bookings only,
and an organisation without that connection answers `404`. Otherwise a contact-center platform calls
`<WEBHOOK_BASE_URL>/webhooks/contact-center/{kind}` (ADR 0032), for Webex Contact Center
`/webhooks/contact-center/webex_cc`; a kind without an adapter answers `404`. The old address
`/webhooks/wxcc` is an alias of it and stays until the WxCC subscriptions are registered at the
new one; OpenPlanner still registers at `/webhooks/wxcc` and recognises a subscription at either
address as its own. Both addresses are the installation's, so they share the remembered events:
an event delivered to both is recorded once. An organisation's own address remembers its events
apart (see *Replays*).
`WEBHOOK_BASE_URL` is optional and defaults to `PUBLIC_BASE_URL`; it exists so that an https
tunnel or staging host can receive webhooks while the admin login (OIDC redirect) and the
links in messages stay on `PUBLIC_BASE_URL`. It must be https in production, and
`register-webhooks` refuses anything but https. **Koppelingen** shows the addresses in use.

## Managing registrations (`webhooks-list`, `webhooks-reset`, `webhooks-health`)

Operator commands (runbook, section 8) that call these APIs with the Service App token:

| Call | Command | Kept from the answer |
|---|---|---|
| `GET /v1/webhooks?max=100&ownedBy=org` (+ `Link: rel="next"` on the same https host, at most 10 pages) | list, reset | `id`, `name` (OpenPlanner's or not), `resource`, `event`, `filter`, `status`, whether `secret` is set, `targetUrl` without query (host only if foreign) |
| `GET /v2/subscriptions` | list, reset | `id`, `name`, `eventTypes`, `status`, whether `secret` is set, `destinationUrl` as above |
| `DELETE /v1/webhooks/{id}` | reset `--yes` | status only; `204` deleted, `404` already gone, `403` Webex quirk |
| `DELETE /v2/subscriptions/{id}` | reset `--yes` | status only; `403` needs `cjp:config_write` |

Ids from a listing must match `[A-Za-z0-9_=-]{1,256}` before they go into a DELETE path.
Only `STALE` registrations are deleted, never `CURRENT` or `FOREIGN` ones.

**Delivery self-test** (`webhooks-health`): no Webex or WxCC call. OpenPlanner posts to its
own endpoints through `WEBHOOK_BASE_URL`, without a bearer token:

```json
POST /webhooks/webex   X-Spark-Signature: <hex HMAC-SHA1>
{"resource":"meetings","event":"ended","data":{"id":"openplanner-selftest-<16 hex>"}}

POST /webhooks/wxcc    X-WebexCC-Signature: <hex HMAC-SHA256>   X-WebexCC-Timestamp: <ms>
                       (the registered address: alias of /webhooks/contact-center/webex_cc)
{"type":"task:ended","comciscotimestamp":<ms>,"data":{"taskId":"openplanner-selftest-<16 hex>"}}
```

Each is sent once signed with the configured secret (expected `204`) and once with a random
wrong key (expected `401`). The ids match no meeting or task, so the endpoint acknowledges
them as unknown: nothing is recorded and no replay key is stored.

## The gate every inbound webhook passes (ADR 0124)

Every route under `/webhooks/` depends on one gate, `app/web/webhook_gate.py` (the rules are in
`app/domain/inbound_gate.py`); a route declares which address it is and gets a verified, parsed
event, or the call is refused before the route runs. A test walks the application's routes and
fails for a `/webhooks/` route without the gate. The checks run in this order, cheapest first,
and the first that fails refuses:

| Step | Check | Refusal reason | Answer |
|---|---|---|---|
| 1 | The address exists: the platform has an adapter, the organisation exists and is connected to it, a secret is set | `unknown_address` | 404 |
| 2 | The source network: when a list of allowed networks applies, the caller's address is in it (below) | `network`, `source_unknown` | 403 |
| 3 | `Content-Type`, when the call sends one, is `application/json` or `application/cloudevents+json` (parameters such as `charset` ignored). A call **without** the header passes: the signature decides | `content_type` | 415 |
| 4 | The body is at most 64 KiB: a declared `Content-Length` over it refuses unread, and a body without a length (chunked) is cut off as soon as more than 64 KiB has arrived | `too_large` | 413 |
| 5 | The signature over the raw body, in constant time | `invalid_signature` | 401 |
| 6 | The signed time, where the platform signs one (WxCC), is within 300 seconds | `stale` | 401 |
| 7 | The body is a JSON object | `malformed` | 400 |
| 8 | The limits (below) | `rate` | 429 with `Retry-After` |
| 9 | The address is not paused (below): a verified event at a paused address is dropped | | 204 |
| 10 | The event was not seen before (*Replays*, below), asked by the domain after the gate; a replay is acknowledged | | 204 |

The limits come after the checks, so a call that cannot sign never uses up the budget of the
authentic ones. Both buckets have the value of `rate_limit_webhook_per_minute` (class C, default
600, 60 to 6000) and the key platform, organisation (or `-` for an address of the installation)
and the caller's address as the server derived it; a forwarded header changes nothing. An IPv4
caller counts by its address (an IPv4 address written as IPv6 is the same caller), an IPv6 caller by
its /64, so rotating addresses within one prefix never opens a fresh bucket:

- A call refused at steps 2 to 7 counts against the bucket of **refused** calls. While it has room
  the call gets its own reason; once it is used up the call gets `rate` instead.
- A call that passed steps 2 to 7 counts against the bucket of **verified** calls and is refused
  with `rate` only when that one is used up (the supplier itself sending too much). The refused
  bucket never touches a verified call: someone who can make the supplier's shared sending address
  call an organisation's address with a bad signature cannot lock its authentic deliveries out.

`Retry-After` is the window of the limit (60 seconds for a per-minute limit).

Calls to an address that does not exist have a bucket of their own: per caller (address, or /64 for IPv6) only (the
platform and organisation in the path come from the caller, so they never make a key), with the
value of `rate_limit_webhook_per_minute`. The gate always resolves the address first. Only when it
does not exist does the bucket count: up to the limit the answer is a 404 (`unknown_address`),
over it a 429 (`rate`, source `unknown`). So the bucket caps how many refusal events a prober
can start, and it never touches a call to an address that exists: a customer of the same supplier
cannot fill it from the shared address to lock real organisations out. Accepted remainder: a
call to a made-up address still costs one indexed lookup, with no ceiling in the application; the
proxy's connection limits are the ceiling.

`rate`, `unknown_address`, `network` and `source_unknown` write no log line per call (a flood or a
passer-by produces them by definition); their trace is the security event, held back when
repeated. Every other reason also logs `webhook.rejected` once per call.

Every refusal answers with an **empty body**, never the HTML error page. Step 7 runs before step 10
in the code, because the event's identifier comes from the parsed body.

### Replays (ADR 0124 point 7)

An event is remembered, so a redelivery or a replay of the same signed body changes nothing. What
is remembered is a SHA-256 digest of the platform's code, the **scope** of the address and the
event's identity (`app/domain/webhook_replay.py`):

- The scope is the organisation whose own address the call came in at (`organisation:<id>`), or
  `installation` for an installation-wide address (`/webhooks/webex`,
  `/webhooks/contact-center/{kind}` and `/webhooks/wxcc`). The same event id delivered to two
  organisations is two events; delivered twice to one it is a replay.
- The identity is the event's identifier from the platform's adapter (Webex: a digest of
  resource, event, `data.id` and the join and leave time; WxCC: a digest of type, task and the
  CloudEvent `id`). An event **without** an identifier of its own is identified by the SHA-256 of
  its verified body: the same signed body twice is a replay by definition.
- The memory is asked only after the signature held (the gate hands the domain a verified
  delivery), holds digests and never a body, and the key does not depend on which secret
  verified the call. A paused address (below) writes nothing.
- Keys are kept 30 days (`WEBHOOK_MEMORY`, a fixed rule, not a setting).

Before this step the remembered key was the adapter's digest alone, under the platform's code.
It is still read, installation-wide on purpose: an event remembered by that older key counts as
seen wherever it comes back, so a redelivery across the upgrade is still a replay, and no
pre-upgrade event can be replayed once at an organisation's address. Nothing is rewritten and no
migration was needed (ADR 0116); release 0.7.0 removes the reading, when every such key is older
than the memory.

The same platform event delivered at an installation-wide address and at an organisation's own
address is two events: the timeline gets a second row for it and the state stays as it was. That
can only happen while the installation-wide contact-center addresses exist (ADR 0032 point 11),
for example while a subscription at the old address and one at the organisation's own address
are both still registered.

### A paused connection (ADR 0124 point 9)

An organisation's connection can be paused with a button of its own on its card (*Kanalen*,
*Pause deliveries* and *Resume deliveries*; `inbound_enabled`, a field of the sealed connection
record that only this action changes; a record without it is on). A paused address passes **every** check
above, the signature included: a call that is refused for any reason is refused as for any other
address, and an unsigned call never gets a `204` from a pause. Only then the verified event is
dropped: the answer is `204` (the platform keeps its subscription), nothing is handled, nothing is
remembered (so a redelivery after resuming is handled), the event is not noted as the platform's
latest, and one log line `webhook.paused` with the platform's code and the organisation's slug is
written. Pausing or resuming changes neither the webhook secret nor the registration at the
platform, and is audited by name (`contact_center_connection.pause` and `.resume`), only when
the state changes. The button says which state it wants, so a stale page that presses *Pause*
twice pauses and never toggles. Resuming does not replay what was dropped: the periodic reconcile of the open tasks
(`task_reconcile_interval_minutes`, ADR 0082) catches up on states a missed event left behind.
The installation-wide addresses have no connection and no pause.

### Source networks (`network`, `source_unknown`)

A connection can carry a list of allowed source networks (IPv4 and IPv6, CIDR, at most 32), edited
on the organisation's connection card (*Kanalen*, contact center) next to its other fields. The
installation has a default per platform, set by the superuser under *Advanced settings*:
`WEBHOOK_SOURCE_NETWORKS_VIDEO` for the video platform's address and
`WEBHOOK_SOURCE_NETWORKS_CONTACT_CENTER` for the contact-center addresses (class C, empty by
default, comma or line separated). Which list governs an address:

| Address | List |
|---|---|
| `/webhooks/webex` | the installation's video default |
| `/webhooks/contact-center/{kind}` and `/webhooks/wxcc` (installation-wide) | the installation's contact-center default |
| `/webhooks/contact-center/{kind}/{org}` | the connection's own list if it has one (it *replaces* the default), else the contact-center default |

An empty result means **any source**: that is the behaviour before this step, and it is shown as a
finding ("not restricted") on the connection card and on System status, never as a silent state.
The host can demand a list: with `WEBHOOK_SOURCE_NETWORKS_REQUIRED=true` (class B, environment, off
by default; a protection is not switched off from the portal) an address without a list accepts no
call and answers `network`; the findings say "a list is required and missing". Changing a
connection's list alone makes no new webhook secret and does not register the address at the
platform again (a record that has no webhook secret yet takes the normal path: the secret is made
and the address registered); it is audited by field name, never by the networks. A network that
covers a whole address family (prefix length 0, `0.0.0.0/0` or `::/0`), or an IPv6 network that
holds the whole IPv4-mapped range (`::/1`, `::ffff:0:0/96`: such a network admits every IPv4
caller), is refused when a list is saved, on the card and in the two defaults, because such a list
is "any source" called "restricted"; a narrower, wide network (`0.0.0.0/1`, `128.0.0.0/1`) is the
organisation's choice, the two halves of a family are not refused on purpose. A stored list
that cannot be read (damaged, an entry that does not parse, or one that is not written the way
this application writes it) is never taken for an empty one: every call to that address is refused
with `network`, and the card and System status say the list cannot be read, and whose it is (the
connection's own list: set it again on the card; the installation's default: ask the platform
administrator, or set a list of your own on the card). Saving the list again repairs it: the card
shows an unreadable own list as an empty field, and saving that clears it, an explicit and audited
act of someone who has the right, on a state the card shows in red. The same holds for an installation default that
is stored in the portal but refused by these rules.

A caller outside the list is refused with `network`. The address is only as good as the proxy
chain, so when a list applies and the caller's address cannot be trusted the call is refused with
`source_unknown`, also when the caller would be inside the list: a restriction that cannot be
evaluated is not passed. Both answer 403 with an empty body, so a caller cannot tell them apart.
The gate takes no address from a forwarded header; the address is the one the server derived from
the trusted proxy (`FORWARDED_ALLOW_IPS`). The caller's address is held as the caller's, for this
call, unless:

- `FORWARDED_ALLOW_IPS` is `*` or names a network wider than a single host (any peer could claim any
  address), or has an entry that is neither an address nor a network (a socket path, a name: who is
  trusted is then unknown; it is a standing fault of its own on System status);
- **this call** arrived with `X-Forwarded-For` or `X-Forwarded-Proto` that the server did not use,
  because the peer that sent it is not a trusted proxy (the address held is a hop that is not
  trusted). A header forged by someone else harms only that caller's own call. A call can carry
  more than one such header line, and the lines can disagree: each is judged and the least safe
  answer wins (*ignored* beats *honoured*), whichever line comes first, so a proxy that sends its
  address as a second line instead of merging it into the first is refused, not passed;
- the caller's address is one of the trusted proxies, or a loopback or private address while the
  address webhooks are registered at is public (a hop did not hand the caller on).

All three are decided per call. The standing sighting of the proxy watch of ADR 0086 (forwarded
headers once seen from an untrusted peer) is a finding on System status and decides nothing at the
gate: otherwise anyone who can reach the web process directly could refuse every list with one
request.

An address without a list is not affected by any of this. System status (*Address and proxy*) and
the connection card show the first (a standing fact of the installation's settings) while a list
applies; System status with what to repair, the card (for the organisation) only that deliveries
are refused because of the installation's proxy setting and whom to ask. The per-call faults show as
refusals in the events and nowhere else until the card shows *last refused* (a later step).

**Three set-ups cannot use a list today.** They are refused, not passed, and the fix is not in
the list:

1. *A proxy trusted by a network instead of a single address*, such as a Kubernetes ingress whose
   pods change: `FORWARDED_ALLOW_IPS` is then wider than a single host. (The Helm chart's
   `forwardedAllowIps` takes the ingress pods; pin them as single addresses to use a list.)
2. *A content delivery network in front of the proxy*: the caller the proxy sees is the network's
   address, and listing it would admit all of the network's customers. Do not put a list on such
   an address; the signature is the protection.
3. *A supplier that calls a public name from a private address* (split DNS): the caller is then
   private while the public address is public, so a call is refused as `source_unknown`.

After you set a list, send a test delivery (`webhooks-health`) and read the events: the card does
not yet show refused calls, so a list that locks the supplier out is found in the events, not on
the card.

A list is **defence in depth and no more**: the supplier's ranges are shared by all its customers,
so a caller inside the list may be another customer of the same supplier; the signature (and the
time) is still checked for every call and is what stops a stranger. A list that has gone stale is
an outage of our own making: when the supplier changes its ranges, update the list (runbook,
*Webhooks refused for their network*). The refusals are the security event `webhook.rejected` with
reason `network` or `source_unknown`; they count against the bucket of refused calls and never
against verified deliveries. A passer-by causes them, so like `rate` and `unknown_address` they
write no log line per call: the event, held back when repeated, is the trace.

Every refusal is also a security event `webhook.rejected` (ADR 0034, 0053) with the reason, the
platform's code (`source:webex`, `source:wxcc`, or `source:unknown` when the platform in the path
has no adapter) and, at an organisation's own address, `organisation:<slug>`. The slug is public
and no personal data; it is written only for an organisation that exists, so a slug made up by a
caller never becomes a value. The caller is the pseudonym of ADR 0053; no body, header value,
signature or address is stored. A flood is one event per reason, address and caller per window.
`docs/audit-events.md` and `docs/logging.md` list the event.

## Inbound: Webex webhooks (`POST /webhooks/webex`)

Registered org-owned for `meetings` (`started`, `ended`) and `meetingParticipants` (`joined`,
`left`), **one set per group host account** with the filter `hostEmail=<host>` and a secret.
The meeting host is the group's licensed host account (known at booking); the agent WxCC
assigns joins as cohost and is never the host (ADR 0017). Without the filter an org-owned
webhook reports every meeting of the organisation; with it only meetings of OpenPlanner's host
accounts reach OpenPlanner. Org-owned webhooks need `meeting:admin_schedule_read` (meetings) and
`meeting:admin_participants_read` (participants) and an authorising admin who is a Webex site
admin or Compliance Officer. Verification: HMAC-SHA1 of the raw body
with the secret, hex, compared in constant time with `X-Spark-Signature`. Events for unknown
meetings are ignored. Webex signs no event time, so replays are caught by **deduplication**:
each event's identity, a SHA-256 of `resource`, `event`, `data.id` and `data.joinedTime`/`leftTime`
(a participant keeps its `data.id` across leave and rejoin), is remembered with its address's
scope in `webhook_events` in the same transaction as the event (*Replays*, above); an event seen
before is acknowledged (204) and ignored. An event without a `data.id` is identified by its
verified body. Keys are kept 30 days and only for OpenPlanner's own meetings. The payload's meeting id can be an instance id
(`<meetingId>_I_<…>`); it is matched on the series id. Participant names and emails are never
stored: only counts, whether a host/cohost joined, and times (ADR 0010).

**The trial call's meeting (ADR 0071, 0076).** A booking's trial call is a meeting of its own
with the same host, so its events arrive on the same webhooks. They are matched on the booking's
open trial meeting and recorded as `trial_entered` (someone joined; detail `host` when a host or
cohost did, which nobody from the team should be) and `trial_left`, never as participants of the
appointment: the appointment's call status stays untouched. The trial meeting's `started` and
`ended` are ignored, since our own jobs open and delete it. Once the trial meeting is deleted it
no longer matches, so a late event of it is ignored like any unknown meeting.

**Polling fallback (ADR 0010).** A webhook can be lost, so a status older than
`MEETING_STATUS_REFRESH_SECONDS` (default 30 s) is read from the Meetings API when an admin
opens or polls the group's agenda: `GET /meetings/{id}?hostEmail=…` for the `state`, and once
the meeting started `GET /meetingParticipants?meetingId=…&max=100&hostEmail=…` for the count
and the host/cohost flag (`state` `joined`; `end` for the first join and last leave). Only for a
confirmed booking with a meeting between 15 minutes before its start and 30 minutes after its
end. The poll sets absolute values and adds timeline events only for transitions, marked
`polled`; it needs the same participant scope as the webhooks.

## Inbound: WxCC subscription (`POST /webhooks/contact-center/webex_cc`, alias `/webhooks/wxcc`)

Registered with `POST /v2/subscriptions` (`name`, `eventTypes`, `resourceVersion: task:1.0.0` (required in v2; current versions in `GET /v2/event-types`), `destinationUrl`, `secret` of 32 to 256 characters).
`destinationUrl` must be HTTPS on port 443 with a public domain: local development needs a
tunnel or a staging host. Event types: `task:new`, `task:connected`, `task:ended`,
`task:failed`, `task-message:appended`, `task-message:append-failed`.

Verification (before reading anything else):

1. `X-WebexCC-Signature` equals hex HMAC-SHA256 of the raw body with the subscription secret
   (constant-time compare).
2. `X-WebExCC-Timestamp` equals the body's `comciscotimestamp`.
3. |now − timestamp| ≤ 300 s.

Correlation is on `data.taskId`. A booking's `task:new` is acknowledged and ignored (the
dispatch job recorded the creation); the `task:new` and `task:ended` of the group check's probe
work item are noted for the check (`integration_reports`, row `wxcc_probe`) and never touch a
booking. `task:connected` carries no agent identity, which is why the
agent's host role comes from `allowAnyUserToBeCoHost` (ADR 0017). `task:failed` carries
`reason` (for example `ENTRY_POINT_NOT_FOUND`, `ORG_DIGITAL_CONTACT_LIMIT_EXCEEDED`), shown to
admins on the booking's timeline.

Deduplication and last event (ADR 0082 point 5): every verified WxCC event of a booking's task is
identified by a SHA-256 digest of its type, `data.taskId` and the CloudEvent `id`, remembered with
its address's scope in `webhook_events` for 30 days like the Webex keys. A redelivered or replayed
event is acknowledged with 204, logged with outcome `duplicate` and changes nothing. An event
**without** an `id` is identified by the SHA-256 of its verified body (ADR 0124 point 7): the
same signed body twice is a replay, while a real second event, such as a second `task:connected`
after a transfer, has another signed time in its body and so another hash.
Every verified webhook of either platform is noted as that platform's last inbound event (time,
type, outcome), shown on **Koppelingen** and *System status*.

Reconcile (ADR 0082 point 6): when a task stays `queued` or `connected` longer than
`task_reconcile_after_minutes` past its start and its last change, the worker reads it with
`GET /v1/tasks/{taskId}` (status word and times only). A different state is written on the
booking's timeline as the matching event with detail `reconciled`, and the organisation gets the
system event `integration.webhooks_missing` (an alert) until a run finds nothing missed.
