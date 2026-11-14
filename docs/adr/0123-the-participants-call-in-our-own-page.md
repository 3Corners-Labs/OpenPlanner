# 0123. The participant's call in our own page, on the Webex Meetings SDK

Status: accepted, partly built (2026-11-14), requested by the product owner. Steps 1, 2, 3, 4a, 4b, 4c and 7a are built: the
page exists behind the feature switch from the start state and the device check through the click that
joins (the token, the vendored SDK offered only when it is whole, connecting with retries, the lobby,
the first moment of the call, leaving and the failure state), its design is approved, the server hands
a guest token to that click and renames the guest at the purge, and the
contact-center contract has a conversation task that nothing sends yet; no real SDK file is placed yet,
so the joining runs in the tests against a stand-in (see *Build status*); the ADR
merged together with step 1 (ADR 0119). Extends ADR 0017 (video through the Webex
Meetings API), ADR 0071 (trial call) and ADR 0075 (`join_client` gets a third value). It is the
separate ADR that ADR 0045 (*Later*) and ADR 0075 (*Consequences*, issue #162) point to. Amends
ADR 0118 §2 and §5 to §7 by naming a third kind of page next to islands and the widget, and the
stack and security-header lines of `CLAUDE.md`.

## Context

A participant joins a call today by a redirect from our join page to the Webex web app (ADR 0017,
ADR 0075). From that click on, the address, the pre-join screen and the call are Webex's: the
organisation's branding stops at our redirect, the participant types a name and an email address
into Webex, and on a phone Webex asks for its app. The product owner asked how far the meeting
platform can be hidden behind the organisation's own look, without replacing Webex Meetings.

A spike on 2026-11-12 (issue #162) ran the call inside a page of ours with Cisco's Webex Meetings
SDK for the browser (`webex` 3.13.0), against the lab host's Webex test site, with meetings
created by our own adapter and the page served with our own response headers. What it showed:

| Question | Finding |
|---|---|
| Guest access | A service app with the scopes `guest-issuer:read` and `guest-issuer:write` mints a guest token (`POST /v1/guests/token`, lifetime 64,799 s). No guest-to-guest site is needed while a licensed host owns the meeting |
| Join | The stored meeting link is accepted as the destination; no password prompt, no captcha. The guest waits in the lobby and is admitted by staff in the Webex app |
| Browsers | Chrome 157, Firefox 159 and Safari 27.2 on a computer: two-way picture and sound. Phones not tested |
| Headers | Works with `script-src 'self'`, `COEP: require-corp` and `COOP: same-origin` unchanged. Needed: five Webex host patterns in `connect-src`, and camera and microphone allowed for our own origin. A WebAssembly probe and one blob worker are refused by the browser and the call works anyway |
| Size | The SDK bundle is 3.7 MB, 919 KB gzipped, with about thirty dependencies bundled in |
| Media | H.264 and Opus, 1280 by 720 at 30 frames per second received in Chrome and Firefox, DTLS 1.2 with SRTP (AES-128), over UDP straight to Webex. A guest may share a screen. Not negotiated: AV1, 1080p, end-to-end encryption |
| Captions | Arrive as events with text, speaker and time, in Dutch among 119 languages, when the host has captions on in the meeting |
| Chat | Not possible: Cisco's chat service answers a browser from `web.webex.com` only |
| Reliability | In about twenty runs a device registration hung twice and a join once; each worked on the next try |
| What staff see | The display name we set, the label `appid.ciscospark.com` under it, and the marker for an external participant |
| What Cisco gets | A guest identity per subject that we cannot delete; contact with Cisco from the moment the SDK is initialised; an upload of the SDK's logs after a call unless `autoUploadLogs` is off; telemetry on the same hosts as the call |
| Licence | Three statements that disagree: MIT in the repository, "Cisco's General Terms" in the npm metadata, "Cisco EULA" in the published package |

What the product owner asks of the page (2026-11-12): a good way in, with camera, microphone
and speakers tested before joining; controls during the call; closed captions; chat; the
organisation's branding; a modern interface; the participant's name visible to the staff member
and the staff member's name to the participant; and no mention of the supplier anywhere on the
participant's side, which is the point of running the call in our own page.

What constrains the answer:

1. **Our front-end rules.** ADR 0118 allows own scripts under 30 KB and no third-party library
   without an ADR, forbids a package tree, and says a need none of its rules covers goes to a new
   ADR and is never solved by loosening the content security policy. The SDK fits neither an
   island nor the widget.
2. **The third-party guarantee.** `e2e/tests/no-third-parties.spec.ts` proves that no public page
   talks to another origin; the video link is the only exit and is followed only after a click.
3. **Privacy.** Webex receives the group's name and the times today, never the participant
   (`docs/dataflow.md`). A guest token changes that.
4. **Accessibility.** Today Webex's client carries the call's accessibility. A page of ours makes
   WCAG 2.2 AA for the call ours, including captions (open decision O8).
5. **The spike's own lesson.** A password field on a page that shares an origin with a login was
   filled by a browser's password manager and handed to the SDK. A call page has no such field.

## Decision

1. **A call page of our own, chosen per service, next to the redirect.** A service (the group of
   ADR 0121 point 1) whose `join_client` is `embedded` gives the participant the call in our
   page, in the organisation's branding. A flow may override its service: a flow revision
   carries an optional `join_client`, empty meaning "as the service". Today a video flow exists
   only as the wrapper of its service (ADR 0121: a new flow cannot be a video flow yet), so the
   service's value is the one that acts; the flow's field is built with the first video flow
   (*Build status*, step 12), and both are read in one place, `call_client(service, flow)`.
   Webex Meetings stays the platform; staff keep the Webex app and everything of ADR 0017 on
   their side. The default stays `browser`. No service changes by this ADR.
2. **A third kind of page: the call page.** It is neither an island (ADR 0118) nor the widget. It
   is one route, `GET /b/m/{ref}/call`, plus the trial call's equivalent (point 9). Only this kind
   of page may load the SDK. Its rules are the points below; ADR 0118 keeps applying to every
   other page unchanged, and the call page's own scripts follow ADR 0118 §9 (accessibility) and
   its ban on markup sinks.
3. **The SDK is vendored, never installed.** One file, `umd/meetings.min.js` from the published
   `webex` package, lives under `app/web/static/vendor/webex/` with its version and checksum in the
   manifest of ADR 0118 §6, so Renovate and `make pins` track it and the SBOM lists it. It is
   exempt from the 30 KB and 100 KB budgets and has its own gate: a test fails when the file
   grows past 1.2 MB gzipped, so growth is a decision. No other SDK file, no CDN, no build step.
   Until Cisco's written answer on the licence (point 18) the file is not committed and is in no
   published image: the manifest names its version and checksum, the gates run against the test
   double of step 8, and an installation that tries the page (the lab host) places the file
   itself. The bundle carries its own dependencies, which a scanner of our packages does not
   see; the weekly freshness check therefore also fails when the published package has an
   advisory for the vendored version.
   *One origin, weighed.* The call page runs the supplier's script on the origin that also
   serves the participant's own pages. A separate origin for the call page was weighed and not
   chosen now: every installation would need a second host name and certificate, and what the
   script can reach on ours is this participant's own booking, behind the session they already
   hold; it cannot reach the portal, for which a participant's browser has no session. The
   script tag carries the manifest's checksum (subresource integrity), the page holds no secret
   but the guest token, and a staff page never loads the SDK on the portal's origin (point 19).
4. **Headers, for this route only.** The call page is served with a named policy `CALL_CSP`:
   `SCRIPT_CSP` plus `connect-src` for `https://*.wbx2.com`, `wss://*.wbx2.com`,
   `https://*.webex.com`, `wss://*.webex.com` and `https://*.ciscospark.com`; and with
   `Permissions-Policy` allowing `camera`, `microphone` and `display-capture` (sharing a screen,
   point 10) for our own origin. Not added:
   `unsafe-eval`, `unsafe-inline`, `wasm-unsafe-eval`, `worker-src blob:`, `*.webexcontent.com` or
   any script, style, font or image origin. `COEP`, `COOP` and every other header stay. A test
   asserts that no other route carries either change. The route keeps
   `form-action 'self'`, `base-uri 'none'` and `frame-ancestors 'none'`, names `worker-src` and
   `media-src` explicitly, and the same test asserts that its policy holds no `unsafe-` token.
5. **Cisco is contacted only after a click.** The page first renders, from the server, what the
   join page shows today plus one button to start. The device check of point 10 uses the
   browser alone: no SDK file and no request to Cisco. The SDK file is requested and initialised
   only after the click that joins, and next to that button the page says that the call is
   carried by an external video service, with the link to the organisation's privacy statement. No text on the page names the supplier (point 14); the
   organisation's privacy statement does, and the page links to it.
   `no-third-parties.spec.ts` keeps failing on any other page and on this page before the click.
6. **The guest token comes from our server, on that click.** `VideoProvider` gains
   `guest_access(subject, display_name)`; the Webex adapter is the one place that calls
   `POST /guests/token`. The endpoint `POST /b/m/{ref}/call/token` requires the participant's
   session (ADR 0093) and the form protection of every participant post, answers only for a
   confirmed booking inside the join window (the rule of `/join/go`), and is rate limited by the
   common limiter. The token is never stored, logged, put in a URL or in a job payload. The page
   has no input field for a token, a password or a meeting. The answer carries the token
   and the meeting's guest link, never the host's, and neither is logged. Requests for a token
   are limited per booking and per address (*Settings*). The token lives for hours and an
   earlier one is not withdrawn by a new one; what a guest token can do in the organisation's
   Webex beyond joining this meeting (looking up people, writing to staff, joining other
   meetings) is established on the lab site before step 4 is built, and decides whether step 4
   goes ahead as written. The name set at minting is a label, not an identity: staff see the
   participant as a guest.
7. **One guest per booking, with a pseudonym.** The subject is an HMAC of the booking under a
   server key, so a participant who reloads reuses their guest, nothing links two bookings, and
   the subject says nothing outside our database. The display name is the participant's first
   name, so the staff member sees who they are talking to; an organisation may choose its term
   for a participant instead (*Settings*): the catalogue's word for a participant in the
   organisation's language (ADR 0077). The first name is what the contact center already
   receives; the privacy rules of `CLAUDE.md` now name this payload to the video platform too; the last name and every other field of the booking stay
   with us. When the booking's personal data is purged, the server mints once more for the same
   subject with the neutral term, so the guest identity that Cisco keeps no longer carries the
   name. Webex's own record of the meeting keeps the name it was held under, as it does today for
   a name typed into Webex (ADR 0102). Three things are settled with it. The rename at the purge is
   tried on the lab site before step 4 relies on it; if a second minting does not change the
   name, nothing is removed at the supplier and `docs/dataflow.md` says so. The purge of our own
   data never waits for the supplier: the rename is a job of its own, with retries and a system
   event when it gives up. The subject is not stored: it can be worked out again from the
   booking and the key while the booking exists, and the key comes from the management token
   key, so after that key is rotated the purge renames the guest under the current and under
   the previous key (step 4).
8. **Chat is required, and the meeting's own chat is closed to our page.** Cisco's chat service
   answers a browser from `web.webex.com` only, so the participant cannot read or write the
   meeting's chat from our origin. **Decided (product owner, 2026-11-13): the chat is a side bar
   of the call page, carried by the contact center's custom messaging task** (route (d) below,
   "bring your own messaging channel"), after it ran on the lab tenant from task to end. The
   routes that were weighed, for the record:
   (a) Cisco opens the meeting's chat to SDK clients or allows our origin: asked, not in our hands;
   (b) a Webex space between the guest and the staff member through the SDK's documented
   messaging calls: staff read it in the Webex app next to the meeting, not in the meeting's chat
   panel, and Webex keeps the messages;
   (c) a chat of our own between the call page and the staff member's page in the portal: ours to
   store and purge, and a second place for staff to look;
   (d) chat in the contact center's agent desktop, where staff already take the call: the
   contact center's own web chat channel, or the chat of route (c) shown as a widget in the agent
   desktop on the video task.
   Route (d) is the one to try first (product owner, 2026-11-12): the chat sits beside the
   picture in the same window, runs through the contact center and not through the meeting, and
   so keeps working when the video cannot be set up. A participant whose call fails is then not
   stranded: they can still reach the staff member. What a first look established (2026-11-12,
   Cisco's documentation and our own adapter; nothing tried on a tenant):
   - *The task we create today* (ADR 0018) is a work item: the staff member sees its fields and a
     link, not a conversation. Its messages are updates of those fields.
   - *The agent desktop takes custom widgets*, as a web component or an iframe, placed in the
     desktop's layout by the administrator and fed with the selected task's data. So our own chat
     can appear on the video task itself: one task, one offer, by construction. The chat is then
     ours end to end (call page, our server, widget), stored and purged by us, and the same chat
     can be shown in the portal for a group that does not use the contact center. Open: how the
     widget proves to our server which task it may read, since it runs inside Cisco's desktop.
   - *The contact center's own web chat* is a separate task, routed on its own, with Cisco's
     widget on the participant's side: it fails the one-offer condition and the branding.
   - *"Bring your own messaging channel"* is the task API we already use. Cisco's reference for
     Create Task v2 (beta) has three kinds of task: `workItem`, which we create today,
     `customMessaging` and `telephony`. A `customMessaging` task is a conversation in the agent
     desktop: it is created with a first text message, further messages of the participant are
     appended with the Task Messages call we already use for work items (text up to 20,000
     characters, an identifier per message for safe retries), and the staff member's replies are
     relayed to us by webhook. It carries the same flow settings and variables as a work item, so
     the booking reference and the meeting link can travel with it. It needs the feature
     `wxcc_digital_byoc` switched on for the organisation by Cisco and a custom messaging channel
     configured in Control Hub; without them the API answers 403. Our lab tenant's API already
     recognises the text message type.
   So the appointment's task can itself be the conversation: one task, one offer, with chat native
   in the desktop and in the contact center's reporting, and the video started from it. That is
   the first route to try. Cisco's guide for the feature describes our part as the middleware:
   - *In:* we create the task and append the participant's messages; the contact center confirms
     each through the event subscription (`task:new`, `task:failed`, `task-message:appended`,
     `task-message:append-failed`). An accepted request is not yet a created task: `task:new` is
     the confirmation.
   - *Out:* every message the flow or the staff member sends is delivered to a webhook address
     set on the asset in Control Hub, with its own secret, as `task-message:appended` with
     `messageDirection: OUTBOUND` and `senderType` `system` or `agent`, signed and timestamped
     like the events we already verify. So the product needs one public endpoint for it, per
     organisation's connection, next to the existing contact-center webhook.
   - *One conversation at a time* per participant identifier and business address; a second one
     is refused. With the booking reference as the identifier this is the one-offer rule again.
   - *Kept by the contact center:* the transcript, retrievable afterwards; card numbers in a text
     are masked. Our purge does not reach it, so the follow-up ADR states the retention.
   - *The flow* for an appointment queues at once and pops the video link when the staff member
     accepts; Cisco's sample flow, which greets and waits for a message first, is for a chatbot.
   Run on the lab tenant from task to end (2026-11-12 and 13; `docs/research/call-page-on-the-meetings-sdk.md`):
   the task was queued, offered and accepted, messages went both ways through the staff member's
   desktop, and every delivery passed our existing signature check. What the build takes from it:
   - *Two inbound paths, both needed.* Messages of the flow and of the staff member arrive at the
     asset's webhook address; the offer, the acceptance, the end and a refused message arrive
     only through the event subscription. Nothing is sent to the participant when the staff
     member ends.
   - *Accepted is not delivered.* A message appended after the end is accepted and refused a
     second later by event. The call page shows a message as sent on `task-message:appended` and
     the conversation as closed on `task:ended` or `task-message:append-failed`.
   - *Our address is a condition.* When the asset's webhook address cannot be reached, each
     message step of the flow fails after a wait and the task ends before a staff member sees
     it. An appointment's flow therefore sends no message before the queue, and the check of
     point 17 covers the address.
   - *A flow change counts when published.* The contact center's flow-error event names the
     version that ran; the check of point 17 reads the published version.
   - *No name in a reply.* A staff member's message carries their identifier at the contact
     center, not their name. Showing the name to the participant (`embedded_call_staff_name`)
     takes our own record of who accepted, or a lookup.
   - *The desktop keeps the thread.* A new task for the same identifier, after the first one
     ended, is accepted, and the staff member sees the earlier conversation above it. With the
     booking reference as identifier that is one thread per appointment; it is also why the
     retention at the contact center belongs in the follow-up ADR.
   - *The video link.* As a link in the first message it can be clicked and opens outside the
     desktop, as the work item's link does today. A screen pop set to show the link inside the
     desktop did not show the meeting there (first try, cause not found). Where the staff member
     holds the video stays as it is (point 19).
   The widget on a work item stays the fallback for an organisation that does not get the
   feature.
   One condition holds for every route (product owner, 2026-11-12): the video call and the chat
   reach the staff member as one offer, in one flow. A chat that is routed as a second task, or
   that can land with another staff member, is not acceptable.
   Which kind of task a request becomes is the administrator's choice, and never both (product
   owner, 2026-11-12). It is part of the dispatch target of the flow (ADR 0121 point 4): `form`,
   the work item of ADR 0018, or `conversation`, the messaging task above. Until flows carry it,
   it is one more attribute of the group's contact-center routing (ADR 0032), next to the channel
   and the business address. A request has at most one active work item (ADR 0121 point 7), and a
   work item is created from exactly one kind, so a staff member is never offered two tasks, or
   two channels, for the same request. A request keeps the kind of the flow revision it was made
   under; changing the kind affects new requests only. `conversation` can be chosen only for a
   service that uses the call page, and only when the contact-center check finds a messaging channel, asset and
   entry point behind the configured names; the check fails when the configured kind and the
   channel's type differ.
   *Our side of the chat.* The participant writes in the side bar; the page posts the message to
   our server, which appends it to the task, and asks our server for new messages by polling
   (`embedded_call_chat_poll_seconds`; the call page is not an island and this adds no second
   live mechanism to any other page). Messages are kept with the booking in one table,
   `call_messages` (direction, text, the provider's message identifier, a state of `sent`,
   `delivered` or `refused`, the time), so a reload shows the thread and a late reply is not
   lost. The text is personal data: it is on the one list in `app/domain/privacy.py`, deleted by
   the booking's purge like every other field, never in a log, an event or a job payload, and
   limited in length (`embedded_call_chat_max_chars`). The staff member's messages appear under
   the name of `embedded_call_staff_name`, from our own record of who accepted the task. The
   contact center keeps its own transcript, which our purge does not reach; `docs/dataflow.md`
   says so and the DPIA takes it (point 18).
   The side bar follows point 13: a labelled log that announces new messages politely, a
   labelled text field, sending by button and by Enter, focus that stays in the field, no time
   limit. When the conversation is closed (the task ended, or a message was refused) the field
   makes way for a line that says so.
   Chat is free text, which the booking form avoids (`CLAUDE.md`, delivery surface) and which
   the contact center did not receive until now (privacy rules); this ADR amends both lines.
   Before the first message the side bar asks the participant not to write identity numbers or
   other sensitive details; there is no filter on what they write. Messages are limited in
   number per minute as well as in length (*Settings*), so a participant cannot flood a staff
   member's desktop. A delivery is matched to the booking that owns the task and to nothing
   else, and a refusal never tells whether a booking exists (ADR 0124 point 12). Showing a staff
   member's account name to a participant is the organisation's choice, and its staff are told;
   the manual says so. The widget on a work item is not built before the question of how the
   widget proves which task it may read has an answer.
   Meetings of a service that uses the call page are created with `enabledChat` off, with or
   without the conversation task, so staff do not write into a panel the participant cannot
   see; the manual says so. Lobby, cohost and every other option of ADR 0017 stay.
   The deliveries of this chat come in through the gate of ADR 0124 (source networks, limits,
   replay, refusal events). This ADR adds the event types and the address for the asset's
   deliveries; it adds no second way to receive.
9. **The trial call uses the same page.** For an embedded group the trial call (ADR 0071) opens
   the call page on the trial meeting, so the participant still tries the path of the real
   appointment.
10. **The way in and the controls, all with our own texts through `app/i18n` (nl and en).**
    - *Before joining, a device check.* The participant sees their own picture, a level meter that
      moves when they speak, and a button that plays a test sound; they choose camera, microphone
      and, where the browser allows it, speakers. A refused permission is explained in plain words
      with what to click in this browser. They may join with the camera or the microphone off.
      Nothing is loaded from or sent to Webex during the check (point 5).
    - *Connecting.* A visible state and an automatic retry of registration and join (*Settings*);
      after the last attempt the page says so and offers what point 11 allows.
    - *Waiting for staff* (the lobby), with their own picture still shown.
    - *In the call.* The other side's picture and sound, their own picture, the
      staff member's name on their picture (*Settings*), and one bar of
      controls: microphone, camera, sharing their screen (in a browser on a computer; phones
      cannot), devices, captions (point 12), chat when a route of point 8 is built, and leave.
      When the staff member shares a screen, it takes the place of their picture and their
      picture moves to a small tile. A notice shows while the meeting is recorded, and a message when the
      connection is weak or being restored.
    - *Ended* and *failed*, the latter with another try and what point 11 allows.
11. **The Webex app is not offered to the participant, unless the organisation chooses so.**
    `embedded_call_webex_option` (*Settings*) decides whether a participant of an embedded group
    ever sees a way into Webex: `hidden` (the default) shows none; `on_failure` shows the redirect
    of ADR 0075 (`/join/go`, `join_link()`) only when our page cannot connect after its retries or
    cannot run in this browser; `always` also shows it as a link before joining. The link reads
    "join another way" and never names the supplier. With `hidden`, a
    failure offers another try, the chat of point 8 when it is built, and the organisation's contact details, and a browser without
    JavaScript or without camera support gets a page that says what is needed, with the same
    contact details. Phones follow this setting like every browser once step 9 has shown them to
    work; until then a phone in an embedded group gets the redirect, whatever the setting, because
    the alternative is no call at all. The redirect itself stays in the product for groups whose
    `join_client` is `browser` or `choice`.
12. **Closed captions are part of the page.** When the meeting offers captions, the control of
    point 10 switches them on for this participant; the page shows them under the picture in the
    page's language when Webex offers it, with the speaker's name, as ordinary text that is not a
    live region (a screen reader user hears the speaker). Their size follows the page's text size.
    Captions are not stored or logged. Whether they are offered is the organisation's Webex site
    setting, so the group check reports it (point 17) and the group form says when an embedded
    group has no captions.
13. **Accessibility is ours for this page.** WCAG 2.2 AA as for every page: every control has a
    visible label and a keyboard path, state changes are announced in one polite live region,
    focus moves to the heading of each state, nothing times out, motion and contrast preferences
    are honoured. The call page is listed in `e2e/pages.json` with the island gates of ADR 0118
    §10; in end-to-end tests the vendored file is replaced by a test double that lives in `e2e/`.
    A manual pass with NVDA and Firefox and with VoiceOver and Safari is recorded in
    `docs/a11y-audit.md` before any organisation may choose `embedded` on a production
    installation.
14. **The page is the organisation's.** Header, logo, colours, radius and fonts come from the
    organisation's theme (`/o/{slug}/theme.css` and the `--booking-*` tokens of ADR 0020 and ADR
    0041), like every participant page; the product footer follows ADR 0060. The supplier's
    name, mark and colours appear nowhere on the participant's side: not in a text, a control, an
    error, a link or a page title, in any state and in any setting of point 11. A test fails when
    a participant text of this page contains the supplier's name. The supplier is named in the
    organisation's privacy statement, which the page links to. What cannot be hidden is technical:
    the browser's own tools show the addresses the page talks to.
15. **A modern interface, designed before it is built.** The picture comes first and fills the
    width; the controls are one bar of labelled buttons with at least 24 by 24 pixel targets;
    the layout works from 320 pixels and at 400 % zoom, in light and dark, with forced colours
    and reduced motion. Step 3 delivers mock-ups of every state of point 10 in two organisations'
    themes; the product owner approves them before step 4 builds the page.
16. **What the SDK may send to Cisco is switched down.** `autoUploadLogs` is off and the device
    registration is temporary. What cannot be switched off (contact from initialisation, the
    telemetry on the call's own hosts, a guest identity that Cisco keeps) is written in
    `docs/dataflow.md` and is input for the DPIA.
17. **Reported early.** The service-app check gets a finding for the guest scopes, once for
    the installation (ADR 0065). From step 6 the group check repeats it for a group that uses the
    call page, and such a group cannot be saved as `embedded` while the check has not minted a
    guest token. The check uses one fixed subject, so it creates one guest and no more. While
    `embedded_call_enabled` is off no check calls the guest endpoint: an installation that does
    not use the call page creates no guest at the supplier and sees no finding about it. The
    group check also reports whether the site offers captions.
    For the conversation task of point 8 the check is part of the portal, on the screens where
    the connection and the group are configured, and walks the whole chain in the contact center
    and reports each link in plain words (product owner, 2026-11-12: these integrations are too
    easy to get almost right): the feature is on for the organisation; the channel, the asset
    and an active entry point of the messaging type exist behind the configured names; the
    asset's webhook address is ours and answers a signed test delivery; the event subscription
    points at us and includes the message events; the entry point's flow reaches a queue without
    waiting for a message first; that queue is served by a team; staff of that team have capacity
    on the channel in their profile, and their recent sessions were actually logged in to it. It
    extends the chain check that exists for work items, and, like it, never creates work a staff
    member sees.
18. **Six conditions before a production group uses it.** Cisco's written answer on which
    licence covers the published package and whether a page may leave the Webex name out; Cisco's
    answer on the licence and cost of guests; the DPIA updated for point 16. Until then
    `embedded_call_enabled` stays off on production installations (*Settings*). The licence
    exception for the package is recorded in `docs/dependencies.md` with that answer. Two more:
    the contact center's retention and deletion of conversation transcripts is known and in the
    DPIA before chat is switched on; and the manual accessibility pass of step 8 is done before
    a service hides the supplier's app (point 11, open decision O8). And a sixth (product owner,
    2026-11-14): a guest's token can do more than join. On the lab tenant a guest wrote to an
    account whose address it knew, and the message arrived
    (`docs/research/call-page-on-the-meetings-sdk.md`, section 16); our server can neither
    withdraw a token nor narrow what it may do. So before a production group uses the page,
    either the supplier gives a way to deny messaging to these guests (a narrower token, or a
    policy of the organisation) and the lab check shows it holds, or the organisation accepts
    the risk in writing. The switch can be locked
    off from the environment like every platform setting (ADR 0042), and switching it on is an
    audited change.
19. **Not in this decision.** The staff side: the staff member opens the meeting from the task as
    today, in the browser or in the supplier's app, outside the agent desktop. Whether the video
    should run inside the desktop, and whether the installed app may take over, is open (product
    owner, 2026-11-13); the supplier's meeting page allows the contact center's desktop to frame
    it, and a component of our own in the desktop layout is the other way. Also not here: phones (point 11);
    hiding that Cisco carries the call, which the privacy statement keeps naming.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `join_client` | D organisation (per service; per flow from step 12) | `browser` | `browser` / `choice` / `embedded` | `group.edit` | Existing setting (ADR 0075), third value. `embedded` can be chosen only when `embedded_call_enabled` is on and the check of point 17 passes. Group → Settings → Video (ADR 0047) |
| `embedded_call_webex_option` | D organisation (per service; per flow from step 12) | `hidden` | `hidden` / `on_failure` / `always` | `group.edit` | Whether the participant is offered the Webex app next to our page. Group → Settings → Video |
| `contact_center.task_kind` | D organisation (per service; per flow from step 12) | `form` | `form` / `conversation` | as the group's other contact-center routing fields | Which kind of contact-center task a request becomes (point 8). One kind per request, never both |
| `embedded_call_enabled` | C platform | off | on / off | `settings.platform.manage` | Whether groups on this installation may choose `embedded`. Off until the conditions of point 18 are met; a feature switch, not a protection |
| `embedded_call_display_name` | D organisation | `first_name` | `first_name` / `term` | `settings.organisation.manage` | What staff see and Cisco receives as the guest's name: the participant's first name, or the organisation's word for a participant |
| `embedded_call_staff_name` | D organisation | `name` | `name` / `term` | `settings.organisation.manage` | How the staff member is named to the participant, on the picture, in captions and in chat: the name of their account, or the organisation's word for staff |
| `embedded_call_connect_seconds` | C platform | 30 | 10–90 | `settings.platform.manage` | How long the page waits for one attempt at registration or join |
| `embedded_call_connect_attempts` | C platform | 3 | 1–5 | `settings.platform.manage` | Attempts before the page reports that it cannot connect |
| `embedded_call_chat_poll_seconds` | C platform | 3 | 2–10 | `settings.platform.manage` | How often the call page asks our server for new chat messages. Registered in step 7c |
| `embedded_call_chat_max_chars` | D organisation | 1000 | 200–4000 | `settings.organisation.manage` | Longest chat message a participant may send. Registered in step 7b |
| `embedded_call_token_per_booking_per_hour` | C platform | 10 | 5–30 | `settings.platform.manage` | Guest tokens one booking may ask for in an hour; per address the common read limit applies. Registered in step 4a. A token lives for hours, cannot be withdrawn and can write to any account whose address it knows, and the page reuses a valid token and retries at most five times, so a participant needs a handful an hour at most |
| `embedded_call_chat_per_minute` | C platform | 20 | 5–60 | `settings.platform.manage` | Chat messages one participant may send in a minute. Registered in step 7b |

Classes:

- **C, platform:** the switch and the two connection values hold for the installation; the
  superuser sets them.
- **D, organisation:** the client per service and the name sent to Cisco are the organisation's
  choice.

No A or B setting: nothing here is needed before login or tied to the host.

**Fixed on purpose:** the host patterns in `CALL_CSP` and the allowed browser features (security
rules; a setting would let an installation widen what the page may reach); the size gate of the
vendored file; chat off for embedded meetings until a route of point 8 is built; `autoUploadLogs`
off and the temporary device registration (privacy); the guest token's lifetime (set by Webex);
the pseudonym key, which is the existing bootstrap key material, not a new secret.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed; the lines of `CLAUDE.md` it amends; the row in `docs/adr-status.md`; the findings in `docs/research/call-page-on-the-meetings-sdk.md` | #787 | merged |
| 1 | `VideoProvider.guest_access`, the Webex adapter's guest token call with its fake and contract fixture, the pseudonymous subject in the domain, the guest-scope finding in the service-app check; the five call settings of this ADR in the registry. No page, no participant sees a change | #787 | merged |
| 2 | The call page as a kind of page: the closed kinds of page with `CALL_CSP` and the permissions for the one route, and the tests that no other page can get them; the route as a shell behind the feature switch; the vendored SDK's place without the file: manifest, size gate and checksum for a placed file, the test that it is not committed, the Renovate manager, the pins check, the SBOM component for a file that is shipped; the recorded exception in `no-third-parties.spec.ts`; `docs/dependencies.md` | #806 | merged |
| 2b | The SDK file itself, after the supplier's written answer on its licence (point 3): the file with its checksum in the manifest, in the repository and the image; the weekly check that fails when the published package has an advisory for that version; the SBOM entry in the image | | planned |
| 3 | Design: mock-ups of every state of point 10, the chat side bar included, in two organisations' themes, approved by the product owner (point 15) The set of sixteen states in `docs/mockups/call-page/`, approved on 2026-11-13 with three changes that the build makes (its `README.md`) | #830 | merged |
| 4a | The way in, the server side: first, on the lab site, what a guest token can do, and the rename at the purge (points 6 and 7). Then the token endpoint `POST /b/m/{ref}/call/token` with its limits and its abuse cases, `embedded_call_token_per_booking_per_hour`, the mark on the booking that a guest was minted (migration 0117), the rename job `rename_guest` that the purge adds (under the current and the previous key), the data flow and the threat model with its abuse cases. It is dark behind `embedded_call_enabled` alone until step 6 gives a service its `join_client`; the rule "only for a service that uses the call page" is enforced there. The lab checks of points 6 and 7 ran on 2026-11-13 (`docs/research/call-page-on-the-meetings-sdk.md` section 16): the rename works; a guest can write to any account whose address it knows, which is a named risk of this step in the threat model and the sixth condition of point 18 | #833 | merged |
| 4b | The way in, the page, up to the click that joins and with the browser alone: the start state and the device check with camera, microphone and speaker test (the participant's own picture, a level meter with a text, a test sound, the choice of camera, microphone and speaker, the toggles to join without either), the refused permission in plain words with what to click and a way on, the line that an external video service carries the call, and `embedded_call_webex_option` (class D, `hidden` / `on_failure` / `always`) with the link "join another way"; the join button leads to a state that says the call is not available in this page yet. No request to the video platform, no SDK file, no token. Behind `embedded_call_enabled` | #835 | merged |
| 4c | The way in, joining: connecting with retry, the lobby, the failure state with `embedded_call_webex_option`; behind `embedded_call_enabled`. The script tag carries the manifest's checksum as subresource integrity, and the page does not offer the call when the placed file's checksum is missing or differs from the manifest (the service-app check reports it); the policy of step 2 is run against the real file for the first time (no `wasm-unsafe-eval`, no blob worker, no further host). With it: the runbook entry for an SDK that stops working. Built: the click loads the SDK file (an integrity refusal ends at once in "not available in this page" and mints no token), asks `POST /b/m/{ref}/call/token` once per click (one token serves the attempts; forgotten when the joining ends or the platform refuses it), connects in `embedded_call_connect_attempts` attempts of `embedded_call_connect_seconds` each (a failed attempt leaves the meeting and unregisters; nothing starts after the page was left or restored), shows connecting, the lobby, the first moment of the call (the other side's picture and sound with a button when the browser blocks the sound, own picture, microphone and camera that give the device back when off, leave), the plain left and ended states and the failure state with a sentence for each refusal of the endpoint and the other way as `embedded_call_webex_option` allows; four own scripts that share one object (`call-page.js` the one state, `call-devices.js`, `call-connection.js`, `call-media.js`); `scripts/check_sdk_policy.py` scans a placed file; the end-to-end stack places a test double of the SDK (`e2e/vendor/webex/`) and `e2e/tests/call-page-joining.spec.ts` walks the flow. Checked only in the first real meeting, with step 2b: the policy run against the real file, the SDK's own storage (IndexedDB, its credential store), republishing a microphone after it was taken out, joining with `enableAutomaticLLM` off, and how the SDK tells that the other side's camera went off (the page reads the video track's mute) | #838 | merged |
| 5a | In the call, the first part: the control bar that is always in view (the picture gives way; works from 320 px and at 400 % zoom, with reduced motion and forced colours), the panel of devices (a disclosure with camera, microphone and, where the browser allows it, speaker; a camera or microphone is taken first, sent by the one rule of `call-media.js` and only then is the old one stopped; a choice that fails keeps the old device and says so; the speaker applies to the other side's sound at once), the notices for the recording and for the connection being restored (lasting text with an icon and a word, each change said once, a connection lost for good ends in the failure state), the ended state with its approved wording (thanks for the call, the personal data of the call is deleted 24 hours after the end of the appointment; no mention of the chat, which row 7c adds) and the failure inside the call with a sentence of its own for a picture or sound that cannot be set up and for a connection that is lost, with "try again" and the other way only as `embedded_call_webex_option` allows; seven own scripts that share one object (`call-status.js` and `call-sound.js` are split out of the page and the check, `call-events.js` holds every registration on the meeting), the one state of `call-page.js` gains `panel`, `recording` and `connection`; the test double plays recording, reconnection and a device that cannot be taken; `e2e/tests/call-page-controls.spec.ts` with the shared helpers in `e2e/call-sdk.ts`; the data flow and the threat model for the call. Checked only in a real meeting: the event names `meeting:recording:started`, `meeting:recording:stopped`, `meeting:reconnectionStarts` and `meeting:reconnectionSuccess` come from the spike's prototype, `meeting:reconnectionFailure` is an assumed name, seen neither in the prototype nor in a real meeting; that publishing a new camera or microphone stream replaces the published one (the prototype's way, `publishStreams`) and that the old stream is then only stopped, not unpublished; that the browser's `setSinkId` on the other side's sound is enough for the speaker; and what the SDK does when the connection is lost for good | #PENDING | merged |
| 5b | In the call, the second part: sharing a screen both ways (the participant's with a lasting notice and a stop button, the staff member's with the camera as a small tile), closed captions (point 12, which turns the SDK's real-time data link on), the staff member's name on the picture (the open question of the mock-ups' README) | | planned |
| 6 | `join_client = embedded` on the service's settings form with the checks of point 17 (the group check's guest finding for a group that uses the call page), `call_client(service, flow)`, meetings without chat, the trial call on the same page, the configuration package, the manual topic. `embedded_call_webex_option`, an organisation's setting until then (stricter than the per-service rule of the *Settings* table: a group's manager cannot change it yet), becomes a service's setting there; and the organisation's privacy statement address with its link on the page (point 5 asks for the link; no such field exists yet, so the page of step 4b shows the line without a link). Here the rule "`conversation` only for a service that uses the call page" is enforced, replacing the platform switch `embedded_call_enabled` that step 7a uses to offer the choice and that alone keeps the token endpoint of step 4a dark until then | | planned |
| 7a | Chat, the contract: the task kind `conversation` in the channel-independent task contract and in the Webex Contact Center adapter (create, append), its capability, `contact_center.task_kind` on the service's routing, fake and recorded fixture. In force only while the platform switch is on; a conversation is ended without the work item's cancellation mark | #827 | merged |
| 7b | Chat, inbound: the message deliveries and the task-message events through the gate of ADR 0124; `call_messages`, the privacy list and the purge; `embedded_call_chat_max_chars` and `embedded_call_chat_per_minute`; the data flow and the threat model for the chat | | planned |
| 7c | Chat, the side bar on the call page: sending, polling, the staff member's name, the closed state; `embedded_call_chat_poll_seconds`; the sentence that the chat stays available in the refused-permission and failure states of the call page (left out of step 4b, which has no chat) | | planned |
| 7d | The chain check of point 17 in the portal for the conversation task | | planned |
| 8 | The end-to-end gates with the test double; the manual accessibility pass in `docs/a11y-audit.md`. Step 4c already brings the test double of the SDK (`e2e/vendor/webex/`), its mounting in the end-to-end stack and `e2e/tests/call-page-joining.spec.ts`; what is left is the island gates with the stand-in on the states after the click, the manual pass and a run with a real meeting | | planned |
| 9 | Phones: iOS Safari and Android Chrome verified on a host with a public certificate, then no longer sent to the redirect (point 11) | | planned |
| 10 | `docs/architecture.md`; the six conditions of point 18 recorded | | planned |
| 11 | The staff member's video inside the agent desktop (point 19): a decision of its own | | follow-up |
| 12 | `join_client` and the task kind on the flow revision, with the first video flow of ADR 0121 | | planned |

Step 1 merges in the same pull request as this ADR (ADR 0119). Each later step is one pull
request and answers the review questions. Steps 7b to 7d wait for step 1 of ADR 0124.

## Consequences

- **The participant** stays on the organisation's page for the whole call on a computer, types
  nothing into Webex, and gets captions in the page when the organisation's site offers them. On a
  phone nothing changes until step 9.
- **Staff** see the participant under the organisation's term or a first name, with Cisco's
  label for a guest under it. Until step 7 they have no chat with the participant in an embedded
  group; after it, chat is where the chosen route puts it (the agent desktop, the Webex app or the
  portal), in one flow with the call, and not in the meeting's own chat panel unless Cisco opens
  that.
- **Privacy.** Less goes to Webex from the participant's own hand (no typed name or address), and
  one new thing goes from ours: a guest identity with the participant's first name, which Cisco
  keeps and our purge can rename but not delete. The page no longer tells the participant which
  supplier carries the call; the organisation's privacy statement must, and its privacy officer
  confirms that this is enough. `docs/dataflow.md` and the DPIA input change; the PII field list does not, because the
  token and the subject are not stored.
- **Security.** One route gets a wider `connect-src` and three browser features (camera, microphone, screen capture). A guest token in
  the browser is a bearer credential for about eighteen hours with more rights than a call
  needs; the threat model gets the abuse cases (token theft through a script on the page, minting
  for another booking, minting outside the join window, flooding the endpoint).
- **Front end.** The largest script in the product by two orders of magnitude, from a supplier
  that supports only its latest version and says an old one may stop working after eighteen
  months. An upgrade at least once a year is part of running the product; the runbook says what
  to do when the SDK breaks: switch `embedded_call_enabled` off, and every group falls back to
  the redirect.
- **Dependencies.** The first front-end library, and one whose licence needs an exception.
  `docs/dependencies.md` and the licence check get the entry; the exception waits for Cisco's
  answer (point 18).
- **Accessibility.** The call's accessibility moves from Cisco's client to us for embedded
  groups. That is the cost of the organisation's own look, and the reason for step 8 before
  production use.
- **Open with Cisco or the partner:** licence of the package, branding terms, licence and cost of
  guests, deletion of guest identities, whether telemetry can be switched off, whether a guest
  token's rights can be narrowed, and chat for SDK clients.
- **Not chosen.** Cisco's ready-made widgets (they need React and an SDK line that is out of
  support); an iframe of the Webex page (ADR 0075 §6 stands); Webex's *Meeting Chats* and *Meeting Messages*
  REST resources for chat (they let a compliance officer list and delete a meeting's chat, they
  cannot send, and reading them needs rights over the whole organisation's events).
