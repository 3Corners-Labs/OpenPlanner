# 0045. Dedicated Webex site and an enforced meeting policy

Status: proposed (2026-10-28), requested by the product owner. Extends ADR 0017 (video through
the Webex Meetings API) and ADR 0019 (direct video call). Settings per ADR 0042. Touches open
point O8 (accessibility of the video call).

## Context

Video meetings run today on the organisation's standard Webex meeting site, the same site
staff use for internal meetings. A citizen's appointment needs stricter and different controls
than an internal meeting: a fixed background for the agent, no files in the chat, captions,
no recording, and a careful answer on AI summaries. ADR 0017 already pushes a few options per
meeting (`app/adapters/webex_meetings.py`), but those are only *starting values*. The host or
a cohost can change most of them during the meeting, and Webex has no hook that applies a
policy when a meeting starts.

What Webex offers (verified 2026-10-28 against the Webex developer reference as mirrored by
the `wxc_sdk` documentation and the apis.io schema of the meeting object, and the Webex Help
Center; not yet tried on the customer's tenant):

- **Per meeting, on create and update (`POST /meetings`, `PUT`/`PATCH /meetings/{id}`).**
  `enabledAutoRecordMeeting`, `enabledJoinBeforeHost`, `enableConnectAudioBeforeHost`,
  `unlockedMeetingJoinSecurity` (`allowJoin`, `allowJoinWithLobby`, `blockFromJoin`),
  `enableAutomaticLock` + `automaticLockMinutes`, `allowAnyUserToBeCoHost`,
  `enabledBreakoutSessions`, `enabledLiveStream`, `enabledWebcastView`, `publicMeeting`,
  `enabledAudioWatermark`, `enabledVisualWatermark` + `visualWatermarkOpacity` (5–80),
  `requireAttendeeLogin` and `restrictToInvitees` (webinars only), `sessionTypeId`, and on
  create `siteUrl` and `templateId`.
  `meetingOptions`: `enabledChat`, `enabledVideo`, `enabledNote`, `noteType`,
  `enabledClosedCaptions`, `enabledFileTransfer`, `enabledUCFRichMedia`.
  `attendeePrivileges`: `enabledShareContent`, `enabledSaveDocument`, `enabledPrintDocument`,
  `enabledAnnotate`, `enabledViewParticipantList`, `enabledViewThumbnails`,
  `enabledRemoteControl`, `enabledViewAnyDocument`, `enabledViewAnyPage`,
  `enabledContactOperatorPrivately`, `enabledChatHost`, `enabledChatPresenter`,
  `enabledChatOtherParticipants`.
  `audioConnectionOptions`: `audioConnectionType`, `entryAndExitTone`,
  `allowHostToUnmuteParticipants`, `allowAttendeeToUnmuteSelf`, `muteAttendeeUponEntry`, call-in
  numbers.
- **During the meeting (`GET`/`PUT /meetings/controls`).** Only `locked`, `recordingStarted` and
  `recordingPaused`, and only for the host. A reported `403` on recording control means
  OpenPlanner cannot count on stopping a recording through the API.
- **Webhooks.** `meetings` started/ended and `meetingParticipants` joined/left (ADR 0010, 0017).
- **Not per meeting.**
  - *Virtual backgrounds.* Control Hub, *Organisation settings → Virtual backgrounds*, can
    **enforce** a virtual background and upload approved images, for internal, external or
    both kinds of meeting, with an **override per Webex site**.
  - *Cisco AI Assistant.* Summaries, transcripts and "Ask AI Assistant" are set per
    organisation, settings template or user, including "Allow external participant for AI
    Assistant". The meeting object has **no** AI field. Summaries can be read and deleted
    through the Meeting Summaries API, but not changed. Webex does not say how long it keeps
    meeting summaries.
  - *Site-wide rules.* Control Hub site settings are mostly not readable through the API.

So there are three places to control a meeting: the site and Control Hub (hard, set by
Webex administration), the meeting itself (a starting value OpenPlanner sends), and the live
meeting (a narrow API plus webhooks). A policy that holds needs all three, and on the
standard site the hard layer cannot be made strict without also changing every internal
meeting of the organisation.

## Decision

### 1. A dedicated Webex site for OpenPlanner

1. Each organisation that uses video gets its own **OpenPlanner meeting site** within its
   existing Webex organisation, for example `<org>-afspraak.webex.com`. The group host accounts
   (ADR 0017) are licensed on that site only.
2. OpenPlanner sends `siteUrl` on every create and checks it on every read. A meeting that
   Webex reports on another site counts as drift (§4).
3. The site carries the **floor**: the rules no host or cohost can change in a meeting. The
   runbook gets one checklist (section 2, "Webex site settings") with the required value of
   every floor setting, and who owns it:

   | Floor rule | Where in Control Hub |
   |---|---|
   | Recording off (cloud and local) | site settings |
   | File transfer in chat off | site settings |
   | Guests from the browser without an account; guests through the lobby | site settings |
   | Enforced virtual background with the approved images, for meetings on this site | Organisation settings → Virtual backgrounds → Override for this site |
   | Cisco AI Assistant (summaries, transcripts, Ask AI) off for the host accounts; "Allow external participant for AI Assistant" off | settings template for the OpenPlanner hosts, or per user |
   | Slido, live streaming and breakouts off | site settings |

4. The agents use their own Webex account. The enforced background reaches them when they
   join a meeting on the OpenPlanner site. Their other meetings are not affected, because the
   override is per site.

### 2. One meeting policy, owned by OpenPlanner

1. `app/domain/meeting_policy.py` declares `MeetingPolicy`. It is pure and immutable. It is
   built from the organisation's settings (§Settings) plus fixed protections, and maps 1:1
   onto the Webex fields above. The adapter sends **every** field explicitly on create and
   never relies on a Webex or template default. `MEETING_OPTIONS` in the adapter goes away.
2. **Fixed protections** are constants (see *Fixed on purpose*). They are always sent and
   always checked. A change needs a new ADR.
3. **Tunable choices** are class D settings per organisation, within platform bounds.
   Switching a protection off is never one of them (ADR 0042 §7).
4. A **meeting template** (`templateId`) per group stays optional. It may only add what the
   policy does not cover (for example a session type). On a conflict the policy wins, because
   the policy fields are sent explicitly.
5. The same policy applies to booked meetings and to direct calls (ADR 0019).

### 3. Verify after create

1. Right after `POST /meetings`, the adapter reads the meeting back (`GET /meetings/{id}` with
   `hostEmail`) and compares it with the policy, field by field. This uses the existing scope
   `meeting:admin_schedule_read`.
2. **Drift** is any difference. It goes into a `MeetingPolicyCheck` result with the drifting
   field *names* only: no values and no PII. That result is stored with the booking's meeting
   reference as `policy_state` (`ok`, `drift`, `unknown`) and `policy_checked_at`.
3. On drift the adapter tries one `PATCH /meetings/{id}` with the drifting fields and reads the
   meeting back again. If drift remains, OpenPlanner writes a system event
   `video.policy_drift` (ADR 0040) with the meeting id and field names, and the booking stays
   confirmed. The citizen gets their link; the join gate (§6) decides at join time.

### 4. Reconcile before the start

1. A job `meeting_policy_check` runs at `start − video_policy_check_lead_minutes`. Following
   the job rules in CLAUDE.md it stores `pinned_start` and exits silently when the booking is
   no longer confirmed or the start has moved. Cancelling or rescheduling deletes it in the
   same transaction.
2. The job reads the meeting, compares it with the **current** policy, patches the drift and
   updates `policy_state`. A policy change in the portal therefore reaches every future meeting
   at its next check, without a mass update.
3. A direct call (ADR 0019) has no lead time. It relies on §3 and §5.
4. A Webex error leaves `policy_state` as it was and retries with the job runner's back-off.
   After the last attempt it becomes `unknown` and raises `video.policy_check_failed`.

### 5. Check on start

1. The `meetings` / `started` webhook (already verified and handled, ADR 0017) enqueues a
   `meeting_policy_check` with `trigger=started`. The webhook handler only enqueues, so it
   still answers within Webex's time limit.
2. This check also reads `GET /meetings/controls`. If `recordingStarted` is true, it writes the
   system event `video.recording_detected` and alerts the superuser. It does not try to stop
   the recording: the API may refuse that, and the site floor is the real guard.
3. If the meeting's host is not the group's host account, or the site is not the configured
   site, it writes `video.policy_drift` with that field name.
4. `meetingParticipants` events are not used for this ADR.

### 6. Join gate for fixed protections

The citizen's join page (`/b/{token}/join/go`, ADR 0017) forwards to Webex only when
`policy_state` is not `drift` **on a fixed protection** (§Fixed on purpose). Otherwise it
runs one synchronous check-and-patch (§3.3), at most once per booking per
`video_join_recheck_seconds`. Within that interval it reuses the last result, so a reloaded or
abused join link cannot drive calls to Webex; the existing rate limit on the public join
routes stays in front of it. If that succeeds the citizen continues; if it
fails the page tells the citizen, in the page itself, that the call cannot start yet and that
the organisation will contact them. It also raises `video.join_blocked`. Drift on a tunable
choice (for example chat) never blocks the citizen. It only raises the event.

### 7. Diagnostics and the floor review

1. `wxcc-check` (runbook section 6) gains Webex video checks:
   - every group host account has the configured site (`GET /meetingPreferences/sites`);
   - every configured `templateId` exists on that site (`GET /meetings/templates`);
   - a test meeting created on the site, read back with an empty drift list, then deleted.
2. The floor itself cannot be read through the API. The superuser therefore records in
   **Koppelingen** the date of the last floor review against the runbook checklist. The portal
   warns when it is older than `webex_floor_review_days`. The review is audited.

### 8. AI summaries and automatic captions are a separate decision

The floor keeps Cisco AI Assistant off. Summaries and transcripts of a conversation with a
citizen are personal data held by Cisco, outside `purge_pii`, for an unknown period.
Switching them on needs its own ADR, an updated `docs/dataflow.md` and DPIA input, and is
never a portal setting. Manual captions (a captioner, `enabledClosedCaptions`) are part of
this policy. Automatic Dutch live captions remain with O8. If Webex offers them without
summaries or stored transcripts, the O8 decision can switch them on in the floor.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `webex_site_url` | D organisation | empty (host's default site, today's behaviour) | `https://<name>.webex.com` | `integrations.manage` (superuser only, ADR 0039) | external address; a new value is saved only after the site check of §7 passes; once set it **cannot be cleared** in the portal, because the floor lives on that site |
| `video_template_id` (per group) | D organisation | empty | must exist on the site (§7) | `integrations.manage` | optional, cannot override the policy |
| `video_chat_enabled` | D organisation | on | on/off | `integrations.manage` | ADR 0017 default |
| `video_private_chat_enabled` | D organisation | on | on/off | `integrations.manage` | `enabledChatOtherParticipants`, e.g. citizen and interpreter |
| `video_attendee_share_enabled` | D organisation | on | on/off | `integrations.manage` | citizen may show a document |
| `video_closed_captions_enabled` | D organisation | on | on/off | `integrations.manage` | manual captions (captioner); accessibility |
| `video_auto_lock_minutes` | D organisation | 0 (off) | 0–60 | `integrations.manage` | an extra on top of the fixed lobby; 0 is the platform default, not a protection being switched off |
| `video_visual_watermark` | D organisation | off | on/off | `integrations.manage` | blocks local recording but keeps old clients out; test before enabling |
| `video_policy_check_lead_minutes` | C platform | 30 | 5–240 | `settings.platform.manage` | §4 |
| `video_join_recheck_seconds` | C platform | 60 | 30–600 | `settings.platform.manage` | §6; bounds keep the join gate from becoming a Webex call per click |
| `webex_floor_review_days` | C platform | 90 | 30–365 | `settings.platform.manage` | §7.2 |

Until the registry of ADR 0042 exists, the class D keys live in `app/config.py` with their
class in a comment, as CLAUDE.md allows.

**Fixed on purpose** (protections, sent and checked on every meeting; changing one needs a
new ADR):

| Field | Value | Why |
|---|---|---|
| `enabledAutoRecordMeeting` | false | no recording of citizens |
| `enabledJoinBeforeHost`, `enableConnectAudioBeforeHost` | false | nobody alone with the citizen before staff |
| `unlockedMeetingJoinSecurity` | `allowJoinWithLobby` | guests are admitted (ADR 0017) |
| `allowAnyUserToBeCoHost` | true | staff become cohost (ADR 0017) |
| `publicMeeting` | false | not on a public calendar |
| `sendEmail` | false | OpenPlanner sends the mail (ADR 0017) |
| `meetingOptions.enabledFileTransfer`, `enabledUCFRichMedia` | false | no files to or from citizens |
| `meetingOptions.enabledNote` | false | no notes kept in Webex |
| `attendeePrivileges.enabledRemoteControl` | false | nobody controls another's computer |
| `attendeePrivileges.enabledSaveDocument`, `enabledPrintDocument` | false | shared documents stay on screen |
| `enabledBreakoutSessions`, `enabledLiveStream`, `enabledWebcastView` | false | one-to-one service conversation |
| `requireAttendeeLogin` | false | citizens never log in |
| `siteUrl` | `webex_site_url` | the floor lives on that site |

## Consequences

- **Webex administration.** A second site per organisation, host licences on it, and the
  floor checklist kept current. The runbook section 2 is rewritten around the site, and the
  Service App's authorising administrator must be site admin of the new site.
- **Code.** New domain module `meeting_policy.py`; the adapter gains `get_meeting`,
  `patch_meeting` and `get_controls`; `VideoProvider` in `ports.py` grows by those methods; a
  `FakeVideoProvider` in `tests/fakes/` can drift on request. A new job kind, a new column pair
  on the booking's meeting reference (`policy_state`, `policy_checked_at`) and a reversible
  Alembic migration. No new dependency.
- **Scopes.** Reading controls needs a meeting-controls scope, and the site list a
  preferences scope. Their exact admin names are confirmed against the tenant before the code
  PR; the runbook scope table is updated then.
- **Tests.** Policy-to-body mapping per field; drift detection with names only; the join gate
  blocks on fixed drift and not on tunable drift; the job exits on a moved or cancelled
  booking; contract tests with recorded `GET /meetings/{id}` and `/meetings/controls` replies.
- **Docs.** `docs/dataflow.md` (no new data to Webex, AI off), `docs/threat-model.md` (abuse
  case: a host changes options during a meeting; recording started by a cohost),
  `docs/webhooks.md`, `docs/manual/{nl,en}/` for the new fields in Koppelingen, and
  `docs/architecture.md` for the check flow.
- **Limits that remain.** A cohost can still change some things live (for example chat). The
  floor and the start check narrow that, but only Control Hub can forbid it. Recording is
  detected, not prevented, by OpenPlanner. The site floor prevents it.
- **Later.** A citizen client on the Webex Web SDK would give OpenPlanner the whole citizen
  side (no chat UI, own caption display). That is a larger build and a separate ADR.
