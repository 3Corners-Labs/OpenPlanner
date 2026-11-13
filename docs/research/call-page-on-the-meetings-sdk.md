# The participant's call in our own page on the Webex Meetings SDK

Research note, 2026-11-12 and 2026-11-13. It records what a spike found when the participant's video call ran in a page of our own on the Webex Meetings SDK, and when chat ran through the contact center's "custom messaging" task. It is the evidence for ADR 0123. Identifiers of the test tenant (organisation, flow, task, queue, agent and channel names, hosts, addresses) are left out on purpose; the facts that depended on them are described neutrally.

The spike was throwaway work and was never merged. Runs were made on 2026-11-12 against the lab host's Webex test site, with SDK `webex` 3.13.0 (`umd/meetings.min.js`, hash-verified against the npm registry).

## 1. How it was run

- A small server served the page with the application's own response headers, read from `app/api/security_headers.py` and `app/web/templates.py`.
- The probe meetings were created on the lab host with the application's Webex adapter, so they carry `MEETING_OPTIONS` and the token of the existing service app.
- Guests ran in headless Chrome 157 with Chrome's fake camera and microphone, and once in Firefox 159 with a real camera.

## 2. Join and lobby

| Question | Result |
|---|---|
| Guest token from a service app with only `guest-issuer:read` and `guest-issuer:write` | works, lifetime 64,799 s; no guest-to-guest site needed |
| Destination | the meeting `webLink` we already store; the SDK reports the password as not required |
| Password prompt, captcha | none |
| Lobby | the guest waits (`meeting:self:lobbyWaiting`) and is admitted by a host in the Webex app |
| Media | the guest sends camera and microphone; the host saw the picture; the guest received 1024 to 1920 px |
| Hangs | of about twenty runs, one join did not return within 20 s and two device registrations did not return within 60 s; each worked on the next try. Cause unknown. A participant page needs a waiting state and a retry here |

Authorising an app that also carries `guest-meeting:rw` makes Control Hub demand a dedicated guest-to-guest site. That scope is not needed when a licensed host owns the meeting.

### Browsers and network

| Question | Result |
|---|---|
| Firefox 159, real camera | passed, same refusals as Chrome (section 3) |
| Safari 27.2 on a computer, real camera, page served over HTTPS from the lab host | passed: lobby, admission, own picture 640 px, host's picture 1280 px |
| Firefox on the lab-hosted page | joined and was admitted; the run ended before media was added, so incomplete (the earlier local Firefox run passed) |
| Edge | not installed on the test machine |
| iOS Safari, Android Chrome | parked. The lab host uses its own certificate authority and resolves on the lab network only, so a phone cannot use it without extra setup |
| Signalling cut of 15 s and 40 s (HTTP and WebSocket only) | the call carried on, no reconnect event |
| Real network drop including media | open; needs a person switching the network |
| Host ends the meeting | not observed from the page yet |
| A real booking on the timeline, webhooks | open. The adapter's status read did report `in_progress` with one participant |

## 3. Response headers

The call works with `script-src 'self'`, `COEP: require-corp` and `COOP: same-origin` unchanged. The whole change to today's headers:

- `connect-src`: add `https://*.wbx2.com wss://*.wbx2.com https://*.webex.com wss://*.webex.com https://*.ciscospark.com`.
- `Permissions-Policy`: `camera=(self), microphone=(self)`.

Refused by the browser while the call still worked: one WebAssembly probe at load (`wasm-eval`), one blob worker (`worker-src`). What they are for is unknown; they may matter for quality features such as noise reduction.

No `unsafe-eval`, no `unsafe-inline`, nothing fetched from `unpkg.com` or any non-Cisco host. The bundle is 3.7 MB (919 KB gzipped), far over the islands budget of ADR 0118.

## 4. Media, as negotiated in two real calls

Read from the browser's own statistics fifteen seconds into a call (Chrome 157 and Firefox 159 on a computer, default SDK settings, camera asked at 1280x720).

| Topic | Measured |
|---|---|
| Video codec | H.264 constrained baseline both ways; AV1 is in the SDK's code but was not negotiated |
| Audio codec | Opus with in-band error correction |
| Picture received | 1280x720 at 30 frames per second in both browsers |
| Picture sent | Chrome 1280x720 at 30; Firefox 856x482 at 12 |
| Media encryption | DTLS 1.2 with `TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256`; SRTP `AES128_CM_HMAC_SHA1_80` |
| Route | UDP straight to Webex's media node, no relay |
| Multistream | off (the SDK's default): Webex sends one composed picture |
| Guest's rights | may share a screen or an application, chat and get captions |

Not measured: a 1080p request, multistream, a network that forces TCP or TLS, end-to-end encryption (only traces of it in the SDK's code).

## 5. Captions

Captions work once the host has captions on in the meeting: 119 caption languages are offered, Dutch among them, `setCaptionLanguage("nl")` is accepted, and caption events arrive with spoken language `nl` and the fields `text`, `speaker`, `timestamp`, `isFinal`, `translations`. In meetings where the host had no captions the SDK reports "not supported" and nothing arrives.

## 6. Screen sharing

The guest's rights include sharing a screen or an application (section 4). In the prototype (section 7) the product owner reported that screen sharing worked in real calls.

## 7. The prototype of the call page

A prototype page (the page itself, a module with the real call and a stand-in for demonstration) covers ADR 0123 points 10 to 15: a device check with camera, microphone level and a test sound, connecting with retry, the lobby, and the call with controls for microphone, camera, captions, screen sharing and devices, with two organisation themes, in Dutch and English. The product owner ran it in real calls in Chrome and Firefox on 2026-11-12 and reported that the lobby, the controls, captions and screen sharing all worked. Chat was a disabled button. Screenshots of every state were taken.

## 8. Chat inside the meeting

Not possible from our own page. Chat lives in the meeting's container at `meeting-container-*.wbx2.com`. That service answers a browser's cross-origin preflight only for `https://web.webex.com`; for any other origin it answers 401 without CORS headers, so the browser blocks the request (call control at `locus-*.wbx2.com` accepts any origin). The guest's policy does say `canChat=true`, and container housekeeping events (person added, person left) reach the page and decrypt, but no chat message was ever received and none could be sent.

## 9. Chat through the contact center

The product owner wants chat beside the picture in the same window, running through the contact center, so that it survives a failed video and reaches the staff member as one offer with the call.

### 9.1 The routes, from the documents (nothing tried on a tenant)

| Route | What the documents say | One offer? |
|---|---|---|
| The work item we create today (ADR 0018) | shows fields and a link in the agent desktop; its "messages" update those fields | yes, but it is not a conversation |
| Our own chat as a custom widget on that task | the desktop takes web component and iframe widgets, placed in the layout by the administrator and fed with the selected task's data | yes, by construction: it is the same task |
| The contact center's own web chat | a separate chat task, with Cisco's widget on the participant's side | no |
| "Bring your own messaging channel" = a `customMessaging` task on the Create Task v2 API we already use (beta) | a conversation task: created with a first text message, participant messages appended with Task Messages (`mediaType: customMessaging`, `type: text`), replies relayed by webhook; carries flow settings and variables like a work item; needs the feature `wxcc_digital_byoc` and a custom messaging channel in Control Hub (403 without) | yes, if the appointment's task is created as `customMessaging` in place of `workItem`: the task is the conversation and the video starts from it |

Checked on the lab tenant without creating anything (requests that validation refuses, as `wxcc-check` does): the API accepts the message types `work-item-form` and `text`; the event types offered are `task-message:appended` and `task-message:append-failed`, with none yet for a staff member's reply. These probes ran into the tenant's rate limit once.

The feature is on for the lab tenant (confirmed 2026-11-12): a well-formed `customMessaging` task for a channel that does not exist is refused with 400 "Invalid input channel", not with the 403 of a tenant without the feature. At that point the tenant had no custom messaging channel: its only channel and asset were the work item ones, and no entry point or queue had that type. Its one event subscription delivered `task:new`, `task:connected`, `task:failed` and `task:ended` to a temporary tunnel address, not to the lab host, and had no message events.

### 9.2 The model, from Cisco's guide "Bring Your Own Custom Messaging Channel"

- We are the middleware.
- Inbound goes through Create Task and Task Messages, confirmed by subscription events.
- Outbound (flow and staff member) goes to the asset's webhook address with its own secret, as `task-message:appended` with `messageDirection: OUTBOUND` and `senderType` `system` or `agent`.
- There is one open conversation per participant identifier and business address.
- Transcripts are kept by the contact center.
- There is no event when an outbound delivery fails.

### 9.3 The runs, in order

**First run (2026-11-12)**, after the product owner configured a custom messaging channel, asset, entry point, flow and queue.

- Create Task v2 with `channelType: customMessaging` was accepted (202) and the task entered the new entry point as `customMessaging`. The channel's name selected it; the work item asset with the same business address did not interfere.
- Task Messages with `mediaType: customMessaging`, `type: text` was accepted (202).
- The task ended in the flow as `self_service` and was never queued. The flow is Cisco's sample: it sends a greeting, waits up to ten minutes for a message, waits, sends "please wait", and only then queues. For an appointment the flow should queue at once, as the work item flow does.
- Why it ended (read from the flow and the asset afterwards, with a read-only script): the flow is `NewContact`, send greeting, wait for a message, queue, "please wait" every ten seconds; on any error it sends an error message and disconnects. Messages the flow or a staff member sends go to the webhook address set on the asset, which pointed at the public website's address, where nothing accepts them. The greeting failed, and the error handler disconnected the task. (The third run corrects the part about the queue step.)
- The test agent's desktop shows capacity 0 on every channel but voice, and its sessions are logged in to telephony only, although profile, team, site and licence allow more. Not solved.

**Second run**, with the asset's webhook address pointed at a receiver of ours through a temporary tunnel.

- The flow's first outbound message arrived as `task-message:appended`, `messageDirection: OUTBOUND`, `senderType: system`, with the header `X-WebexCC-Webhook-Version: task-message:1.0.0`. The third run shows it was the flow's error text, not the greeting.
- Its signature verified with the asset's secret by the product's existing check: HMAC-SHA256 of the body, hex, in `X-WebexCC-Signature`; `X-WebExCC-Timestamp` equal to the body's `comciscotimestamp`.
- The task then still ended as `self_service` with a queue count of 0.

**Third run**, with three temporary subscriptions (task, task-message, flow) delivering to the same receiver.

- The contact center's own events, in order: `task-message:appended` (inbound, our first message), `task:new`, `flow:error` at the activity `SendCustomMessage`, an outbound `task-message:appended` from `system`, `task:ended` with reason "System Error".
- So the message that reached us in the second run was not the greeting but the flow's error text (58 characters, "There was a server error...").
- The greeting step was configured as "text with attachments" with the sample's placeholder attachment, and failed; the flow's error handler then sent the error text and disconnected.
- Correction to the first run: the queue step was never reached.
- `flow:error` carries the activity's name and a debug address but no description.
- An append to a task that has already ended is still accepted with 202; only the `task-message:appended` event proves a message was taken.

**Fourth run**, the morning after, once the flow was said to be changed.

- The same five events and the same error. `flow:error` names the flow version that ran (`flowVersionId`, tag "Latest"); it was the version of the night before, and a read-only export of the flow still showed the greeting step as "text with attachments".
- So an edit in the flow editor does nothing for new tasks until the flow is published; the version in `flow:error` is how to tell which one ran.
- A configuration check in the portal can use the same two reads: the export for the step's settings, the event for the version.

**Fifth run**, after the flow was published with the greeting as plain text.

- The flow works up to the queue. Events: `task:new`, our first message appended, the greeting delivered to us as an outbound message from `system` (60 characters, the flow's own text).
- The flow then waits for a message from the participant. After our second message (`append`, 202) came `task:parked` with the queue's identifier, and the search API showed the task as `parked`, active, in the conversation queue, with the booking reference as origin.
- From then on the flow's queue step sends "Please wait while we connect you with an agent." every ten seconds, each as its own outbound message to our address. For an appointment that is noise, and the texts are English and the sample's.
- An appointment's flow wants no greeting, no wait for a first message and no repeating queue text: straight to the queue.
- Every delivery verified with the asset's secret.

The same task, two minutes later, with a staff member signed in to the agent desktop in the queue's team:

- `task:connect` (the offer), six seconds later `task:connected` (accepted); the search API names the owner and the team.
- In the desktop the conversation carries the origin's name (the first name) as its title and shows the whole transcript, the repeated queue text included.
- The staff member's replies reach our address as `task-message:appended`, `messageDirection: OUTBOUND`, `senderType: agent`, with a `senderId` (the staff member's identifier at the contact center) and **no name**. Showing the staff member's name to the participant takes a lookup or our own record of who accepted.
- Shape of the event: `taskId`, `origin`, `destination`, `channel`, `channelType`, `channelParams.type` and `.message` (`aliasId`, `text`, `timestamp`, `attachments`), `createdTime`.
- A participant message appended while connected is accepted, reported back as an inbound `task-message:appended`, and shown in the desktop under the origin's name with the mark "Received" (seen by the staff member).
- Each message arrives once, although the asset's address and the message subscription point at the same receiver.
- The desktop's side panel (customer journey) shows an error on this tenant; it is the desktop's default panel, not ours.
- So chat both ways through the staff member's own desktop works, with our existing signature check.

The end, from the staff member's side ("End" in the desktop):

- One `task:ended` event with `reason: "Agent Left"` and `terminatingParty: "Agent"`; the search API then shows the task as `ended`, termination `normal`, with queue and connected durations.
- Nothing is sent to the participant at the end: no message reaches the asset's address, so **the end is only known through the task subscription**. A conversation task therefore needs both: the asset's address for messages and the event subscription for the end (the product already has the second for work items).
- The desktop goes to wrap-up (a reason to pick, an automatic wrap-up after a countdown); the staff member is not free for the next task until then.

A message appended after the end:

- It is accepted with 202 like any other, and refused afterwards: `task-message:append-failed` with `reason: "NOT_FOUND"` ("the task was not found"), carrying only the task's identifier, not the message's.
- So the answer to the request proves nothing. The participant's page may show a message as sent only after `task-message:appended`, and on `append-failed` or `task:ended` it must say that the conversation is closed.

**Sixth run**, for the video link.

- The published flow has no variable for the link (its variables are the sample's three message variables) and its screen pop still opens the sample's placeholder address inside the desktop. So a flow setting named `meetingLink` goes nowhere until the flow gets a variable of that name and the screen pop points at it.
- Tried instead: the link inside the task's first message, which needs no flow change.
- A new task for the same reference after the first one ended is accepted (`task:new`); with a staff member available the offer follows at once (`task:connect` without a `task:parked` before it). The offer was not accepted within two minutes and stayed in `connect` with no further event in that time.
- It was accepted after two and a half minutes (`task:connected`). The desktop shows the link in the first message as a link, and following it **takes the staff member out of the agent desktop** (product owner, at the desktop). That is what the work item's form does today as well, and it is not the "one run" asked for.

**Seventh run**, for the screen pop: the flow now has a variable `meetingLink` and its screen pop (in the flow's event flows, shown inside the desktop) points at it.

- The asset's webhook address still named the dead tunnel (the tunnel and the receiver had ended with an interrupted session; the three temporary subscriptions were deleted afterwards, and the address stayed set until changed in Control Hub).
- The task never reached the queue: `flow:error` at the greeting step fifteen seconds after the task started, a second `flow:error` at the error handler's own message step seventeen seconds later, then `task:ended` with "System Error". The search API shows `ivr-connected` and then `ended`, termination `self_service`.
- So **when our webhook address cannot be reached, every message step of the flow fails after a wait and the task ends**. Our address being up is a condition for a flow that sends messages, and one more reason for an appointment's flow to send none before the queue.
- This was seen only after a new tunnel and the three temporary subscriptions were set up again; without the flow events the task simply ends with no reason.

**Eighth run**, once the asset had the new tunnel's address.

- The greeting was delivered, the offer came at once, and it was accepted two minutes later.
- The screen pop step is linked to the event "agent contact assigned" and raised no flow error, yet the product owner saw **no meeting inside the desktop**. Not found out why: the desktop's side panel showed its customer journey tab and a "More Tabs" menu (not opened), and whether a tab opened elsewhere was not reported. The first message showed the real link, clickable.
- The desktop also showed **the earlier conversations for the same reference above the new one** ("Conversation CLOSED", then "Conversation RE-QUEUED"): the thread is kept per origin identifier and business address, across tasks. That is one thread per appointment when the identifier is the booking reference, and it is free text of a participant that stays at the contact center after our purge (a question for the supplier: how long, and how it is deleted).
- Four minutes after the acceptance the same task was queued again (`task:parked`, then the waiting text every ten seconds) without an end: a connected conversation goes back to the queue when its staff member leaves it. Cause not confirmed; it fits a link that replaces the desktop in its own browser tab, which is one more reason the link must not take the staff member away.

### 9.4 What the build takes from the runs

- One offer with the call is possible: create the appointment's task as `customMessaging`, with the video link in the task, and chat both ways runs through the staff member's own desktop with the existing signature check.
- The appointment's flow must queue at once: no greeting, no wait for a first message, no repeating queue text, no message step before the queue.
- Our webhook address being reachable is a condition for any flow that sends messages.
- The end of the conversation is known only through the task subscription; the participant's page shows a message as sent only after `task-message:appended`, and says the conversation is closed on `task-message:append-failed` or `task:ended`.
- The staff member's reply carries a `senderId` and no name; showing a name needs a lookup or our own record of who accepted.
- A flow edit counts only once published; the `flowVersionId` in `flow:error` shows which version ran.
- A flow variable for the link and a screen pop pointing at it are needed for a link inside the desktop (seventh and eighth runs).

## 10. The staff side and the video link

Can the meeting open inside the desktop? Measured on a probe meeting's page (created and deleted again): the page answers with `X-Frame-Options: DENY` and with a content security policy whose `frame-ancestors` names, among others, `*.cisco.com` and `*.webexcc.com`. The agent desktop is served from a `cisco.com` host, and a browser that knows `frame-ancestors` follows it and ignores the older header, so the supplier lets its own contact center desktop show the meeting page in a frame. With `launchApp=true` (ADR 0075) the answer is the same.

Not measured: whether camera and microphone work in that frame. That depends on the permissions the desktop gives the frame; for a frame placed by the desktop layout a community answer (not the supplier's documentation) names an `allowedParams` attribute with `camera; microphone`.

Three ways to keep the staff member in the desktop, cheapest first:

1. The flow's screen pop "inside the desktop" with the link from a flow variable.
2. A frame in the desktop layout with those permissions and the task's link.
3. A web component of our own in the layout with the meetings SDK (a community sample does exactly this), which is also the only one whose look is ours.

Open with the product owner (2026-11-13): whether the staff member's video should run inside the desktop at all, or the installed app may take over as it does today.

## 11. What stays visible, and what Webex keeps

- Staff see the display name we set, the label `appid.ciscospark.com` under it, and the marker "Extern", in the lobby, the participant list and on the video tile.
- The SDK contacts Cisco at initialisation, before any call. To keep the rule that a participant's address reaches a third party only after a click, initialise it on that click.
- The SDK uploads its logs to Cisco after a call by default (`*.webexcontent.com`); the setting `meetings.autoUploadLogs: false` stops it. Telemetry to `client-logs-*.wbx2.com` shares its domain with the call and cannot be blocked by the policy.
- Nothing is left in `localStorage` after leaving. IndexedDB was not looked at.
- Every minted token with a new subject creates a guest identity at Webex that we cannot delete. These runs created about sixteen, each with a random subject and the name "Deelnemer". Minting again with the same subject reuses the guest (`?subject=` on the page).

## 12. Lessons

The spike page had a password-type field for pasting a token by hand, and it was served on the lab application's own address. Firefox filled that field with a password saved for the address, the page handed it to the SDK as a token, and the SDK contacted Cisco with it before rejecting its format. For a real call page this means:

- No input field that a password manager can fill.
- The token only ever comes from our server.
- Cisco's SDK should not run on the same origin as a login page.

The field was removed from the spike.

Seen in the existing product along the way (not investigated):

- Webex answers 403 with an empty reason to a delete of a meeting that is in progress.
- After a meeting ended before its scheduled end, Webex reported it as `scheduled` again.

## 13. Licence

Three statements disagree: the repository says MIT, the npm metadata says "Cisco's General Terms", the published package's `LICENSE` file says "Cisco EULA". Under the dependency rule the package does not qualify without an exception. This is for Cisco to answer.

## 14. Questions for Cisco or the partner

1. Are guest joins through the SDK covered by the site's licence, and is there a cost per guest?
2. How are guest identities deleted, and how long does Webex keep them?
3. Which licence applies to the published SDK package?
4. Do the branding terms allow a page that does not show the Webex name?
5. Captions for an SDK guest: which site setting or licence do they depend on, and can a guest switch them on when the host has not?
6. In-meeting chat: the meeting-container service accepts browser requests from `web.webex.com` only. Is chat for SDK clients planned, or can an origin be allow-listed?
7. Can the telemetry to `client-logs` and `metrics` be switched off?
8. Can the scopes of a guest token be narrowed to joining a meeting? If not, can an organisation's policy deny messaging to the guests of a guest issuer? A guest can write to any account whose address it knows (section 16).
9. How long does the contact center keep a conversation's transcript and the thread per participant identifier and business address, and how is it deleted?

## 15. What was not tried

- A real network drop including media, and a network that forces TCP or TLS.
- A 1080p request, multistream and end-to-end encryption.
- iOS Safari, Android Chrome and Edge; Firefox with media on the lab-hosted page.
- The host ending the meeting, as seen from the page; a real booking on the timeline with webhooks.
- The meeting inside the agent desktop with camera and microphone (screen pop tab, a frame in the layout, or a component of our own), and an end of the conversation from the participant's side.
- For the conversation task: a flow whose first step does not fail and that queues at once, how the staff member gets the video link from the task, and channel capacity (the test agent's desktop showed capacity 0 outside voice).
- For the widget route: how a widget running inside Cisco's desktop proves to our server which task it may read; framing or cross-origin rules for our side (an iframe widget needs CSP headers on our response, a web component needs a CORS allow-list as the booking widget has); and trying it on a tenant.
- IndexedDB contents after a call.

## 16. What a guest's token can do, and the rename (lab tenant, 2026-11-13)

ADR 0123 points 6 and 7 ask for two things to be established before the way in (step 4) is
built. A script minted a guest for a made-up subject with the service app's token and asked the
platform's API with the guest's token; it recorded status codes and counts only.

- **Looking up people:** not possible. A search by name is allowed and returns nothing, for
  every first letter. A list without a filter is refused (400).
- **Meetings, the organisation, licences, recordings, meeting preferences:** refused (403). A
  guest cannot create a meeting.
- **Things of its own:** a guest can create a room, write in it, delete it, and register a
  webhook of its own (and delete it). Rooms, memberships, teams, webhooks and devices list empty.
- **Messages to others:** a message to an address that cannot exist is refused (400). A message
  to a real account whose owner agreed to the test was **accepted** (200): the platform made a
  direct room between the guest and that account, and the guest cannot leave it (403). So a
  guest can write to anyone whose address it knows. Whether the message was shown to the
  account's owner is confirmed by the owner, not by the API.
- **Tokens:** a second minting for the same subject returns another token for the same guest;
  the earlier token keeps working.
- **The rename:** a second minting with another name changes the guest's name, also as seen with
  the earlier token, within a few seconds (a read straight after the minting still showed the
  first name). The purge can rely on it (point 7).

What follows for the build: the token is a credential of a limited account in the
organisation's video platform, not a pass for one meeting. A participant who takes it from the
page can, for the hours it lives, send messages under the organisation's guest identity to any
address they know, staff included; our server cannot withdraw a token or narrow what it may do.
The token is handed to the page only on the click that joins, for a confirmed booking inside
its join window, never stored or logged (point 6), and limited per booking and per address.
That limits who gets a token, not what a token can do. Whether the platform can deny messaging
to these guests (a policy of the organisation, or a narrower kind of token) is a question for
the supplier, and a condition to settle before a production group uses the page.
