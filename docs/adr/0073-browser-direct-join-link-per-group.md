# 0073. A join link that opens the Webex web app directly, per group

Status: accepted (2026-10-30), requested by the product owner after the question whether a
guest can be given the browser option only. Extends ADR 0017 (video through the Webex Meetings
API) and ADR 0071 (trial call); settings per ADR 0042. Steps 1 and 2 built on a branch,
step 3 not: see *Build status*.

## Context

A citizen who opens the booking's Webex link on Windows or macOS first sees Webex's own page
with two choices, *download the Webex app* or *join from this browser*. Many citizens take the
first, install nothing in the end, and arrive late or not at all. The product owner asked
whether OpenPlanner can put a page in between that offers the browser only, and whether that
can be set per group.

OpenPlanner already has that page: `/b/{token}/join` shows one button, and `/b/{token}/join/go`
records the click and redirects to the meeting's `webLink` ([join_pages.py](../../app/api/join_pages.py)).
We cannot change what Webex's page shows (another origin, no iframe), but we can choose which
link we redirect to. Webex offers nothing per meeting or per invitation for this: the preferred
join method is a site-wide Control Hub setting that only reorders the two choices, and the
guest's own browser remembers the last choice.

A trial on 2026-10-30 on the test site `rijksvideotest.webex.com` (the group's host, a probe
meeting scheduled exactly as a booking's meeting, then deleted) established:

1. The short link `…/j.php?MTID=…` answers `302` to
   `…/wbxmjs/joinservice/sites/{site}/meeting/download/{meeting id}?MTID=…`, and **keeps any
   extra query parameter** in that redirect.
2. That page reads `launchApp` from the query. With `launchApp=true` its own script "skips
   launch detect and launches the web app directly" (its words): the citizen lands on the
   web app's *Prepare to join* screen (name, microphone, camera) without the app choice. That
   is the same request Webex's own *join from this browser* button makes (a form post to
   `?launchApp=true`). Without the parameter the choice page appears, every time.
3. On a phone (Android user agent, mobile viewport) Webex offers no web app at all: with and
   without the parameter the page says the Webex app is required, with *download* and *join
   with the app* only. The parameter changes nothing there.
4. The *Join a Meeting* API (`POST /meetings/join`) is not a way around this: its `joinLink`
   leads to the same page, and for a guest it needs the citizen's e-mail address and display
   name, which Webex then puts **in the link's query string**. Our rules forbid PII in URLs.
5. Found in passing, and more urgent: the site **ignored the trial call's options**. The probe
   was created with `enabledJoinBeforeHost`, `enableConnectAudioBeforeHost` and
   `unlockedMeetingJoinSecurity: allowJoin` (`TRIAL_OPTIONS`, ADR 0071), and Webex stored
   `false`, `false` and `allowJoinWithLobby`; a second probe five minutes ahead, with and
   without `joinBeforeHostMinutes`, came back the same. A guest who joined through the web app
   sat in the lobby "waiting for the host to start the meeting". On this site the trial call of
   ADR 0071 therefore does not let the citizen in; the group check reports `webex.trial_ok`
   because it only looks at whether the meeting was created. Webex also refuses to delete a
   meeting while a guest is in its lobby (`403 Meeting is in progress`).

The parameter is not documented by Webex. It is the mechanism of Webex's own button, so it
is as stable as that page, and when Webex drops it the page falls back to the choice it shows
today: nothing breaks, the citizen only gets the choice back. The group check can tell us when
that happens.

## Decision

1. **The join link is built in one place.** `app.domain.calls.join_link(link, client)`
   returns the link the citizen is sent to: the meeting's `webLink` as stored for
   `JoinClient.CHOICE`, and the same link with `launchApp=true` set for `JoinClient.BROWSER`
   (other query parameters kept, a value that says the opposite replaced, applying it twice
   changes nothing). It returns `None` for anything but an `https` link, so a poisoned stored
   value never becomes a redirect. The parameter is the constant `BROWSER_LINK_PARAMS` next to
   it, since it is Webex's mechanism, not a tunable; it lives in the domain module because the
   builder does and the domain imports nothing from an adapter. The stored `webLink` does not
   change; the parameter is added when the link is handed out.
2. **Per group, `join_client`, default `browser`.** The appointment's link
   (`/b/{token}/join/go`) and the trial call's link (`/b/{token}/test/go`) use the same
   builder, so the trial call tests the path the appointment takes (ADR 0071 point 1). A group
   whose citizens mostly use the Webex app, or an organisation that prefers it, sets `choice`.
3. **The pages and the mail say what the citizen will see.** With `browser`: "the call opens in
   your browser; on a computer there is nothing to install". With `choice`: "if Webex asks to
   open the app, choose *join from this browser*". Both add that on a phone or tablet Webex
   asks for the Webex app, since that is true in either case (finding 3). The texts of the
   confirmation mail (`message.join`), the join page (`join.tips.browser`) and the trial page
   (`trial.browser`) come from the catalogue as before; the organisation override stays
   possible.
4. **The group check verifies the browser link** when the group has `browser`: it follows the
   probe meeting's short link with the parameter, anonymously and without following further
   than the first redirect, and reports `webex.browser_link_ok` when the `302` keeps
   `launchApp=true`, else the warning `webex.browser_link_lost` ("Webex no longer carries the
   browser option through; citizens get the choice page"). When Webex cannot be reached for the
   visit it is the warning `webex.browser_link_unreachable`, so a network error is not taken
   for a lost parameter. The visit happens before the probe is deleted, uses no credentials,
   follows no redirect and is reported to the request log like every probe request (ADR 0068),
   with its path and without its query. No name or e-mail is sent. It was tried against the
   test site with the check's own client: with the parameter Webex answered `302` and kept it,
   without it Webex answered `302` without it.
5. **The group check verifies the trial call's options** (finding 5). After creating the
   trial probe it compares the keys of `TRIAL_OPTIONS` in Webex's answer with what was asked;
   when the site stored something else it reports the warning `webex.trial_lobby` naming the
   values it kept, instead of `webex.trial_ok` ("the site does not allow entry before the
   host: the citizen waits in the lobby and nobody comes"). A key the answer leaves out is not
   a refusal. It is a warning, not a failure, because the appointment's own meeting works; and
   as for every warning (ADR 0065) the automatic check stores it with the group's latest
   check but raises no system event, so the administrator sees it on the check page. The trial
   probe's cleanup already reports a refused deletion (such as `403 Meeting is in progress`)
   as the warning `webex.trial_left` naming the meeting; nothing more is needed there.
6. **No page of ours imitates Webex.** The join page keeps one button; it never shows a
   fake "app or browser" choice of its own, and never embeds the Webex page.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `join_client` | D organisation (per group) | `browser` | `browser` / `choice` | `group.edit` | Which Webex link the citizen gets. Not a protection. Shown on the group's *Video* settings next to the trial-call switch (ADR 0047: group → Settings → Video). |

**Fixed on purpose:** the parameter `launchApp=true` (`BROWSER_LINK_PARAMS`) is Webex's
mechanism and stays a constant; the check of decision 4 tells us when it stops working. The
probe meeting's title, lead and length stay as in the group check today.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | `join_client` on `groups` (migration 0072, reversible), `JoinClient`, `join_link()`, both redirects, group form and manual topic, texts per client in nl and en | | built on the branch, PR pending |
| 2 | Group check: `webex.browser_link_*` and `webex.trial_lobby`, i18n of the findings | | built on the branch, PR pending |
| 3 | Confirm on docker01 with the Webex app installed on the tester's computer that `browser` opens the web app and `choice` the Webex page; record in this table | | pending |

## Consequences

- Migration 0072 (reversible): `groups.join_client` text, default `browser`; the downgrade drops
  the column. It revises 0070; PR #148 claims 0071, so whichever merges second changes its
  `down_revision`.
- `docs/settings.md` unchanged (a group setting, not a `Settings` field); `docs/admin-map.md`
  unchanged (same screen); `docs/manual/{nl,en}/group-settings.md` gets the setting;
  `docs/authorization.md` unchanged.
- Threat model: no new endpoint. The redirect target is still the stored `webLink` plus a
  constant; the `https://` guard in `join_go` stays. The check's anonymous request carries no
  data of a citizen. `docs/dataflow.md` unchanged.
- ADR 0071 is not amended, but its build status is corrected by step 2: on a site that
  forbids entry before the host the trial call puts the citizen in the lobby, and only the
  warning tells the administrator. Whether the production site allows it is a question for the
  client's Webex administrator (open point for the go-live checklist in `docs/runbook.md`).
- The heavier alternative, running the call in our own page with the Meetings Web SDK, stays
  on the list as issue #162; this ADR makes it unnecessary for the app-choice problem.
