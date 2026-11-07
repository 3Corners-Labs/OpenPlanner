# Islands: the scripts that enhance a page

An *island* is an own script under `app/web/static/` that only enhances content the server
already rendered (ADR 0118). The page is complete and correct without it: every task and every
number works with scripts off, and a script that fails to load leaves the content as it was.

This file is the one list of them. `tests/web/test_static.py` fails when a script under
`app/web/static/` has no `### ` entry below, when an entry names a script that
is gone, when a script is over 30 KB or all scripts together over 100 KB gzipped, or when a script
turns text into markup or code (`innerHTML` and `outerHTML` assignments, `srcdoc`, `insertAdjacentHTML`,
`createContextualFragment`, `document.write`, `eval`, `Function`). The semgrep rule
`no-script-markup-sink` (tested by `make semgrep` and CI) checks the same on `app/`; the pytest is a
second, simpler net. Neither can prove that a script is safe, only catch the usual sinks.

## The rules every island follows

- Served from our own origin with `script-src 'self'` (`SCRIPT_CSP`, set when a page renders with
  `scripts=True`); no inline script, no `eval`, no external origin.
- All text in the DOM is the server's own or comes from a code; a script sets it with
  `textContent` and never builds markup from a string. A label from outside the page (a group
  name) is never trusted.
- The browser only ever receives what the person may see: scope filtering and suppression are on
  the server; nothing hidden is sent and hidden again.
- Live updates use the one mechanism `live-region.js` (`update-progress.js` still polls on its own
  until ADR 0072 step 2 moves it); focus is never lost; a change is announced in
  a polite live region and never by moving focus.
- WCAG 2.2 AA: keyboard path for everything a pointer does, no hover-only content, targets of at
  least 24 × 24 px, `prefers-reduced-motion` and `forced-colors` honoured.
- A **new** island is an ES module that defines a custom element `op-…` wrapping server-rendered
  content, and gets an entry here with the fields below before it merges. A third-party library
  needs its own ADR first (ADR 0118 point 6).

Every entry states: what the server renders inside, how the script finds it, the keyboard map, the
roles and live regions, the browser features used, what happens without the script, and its size
(gzipped, measured by the test above; it changes, so the number is a guide).

## Existing scripts

### `live-region.js`

- **ADR:** 0072 §6 (the mechanism), 0061 §4 (shared announce element).
- **Server renders:** a region `[data-live]` holding one `[data-live-fragment]` with a poll URL
  (`data-poll`) and an interval (`data-poll-seconds`, the platform setting `QUEUE_REFRESH_SECONDS`),
  a status sentence `[data-live-status]`, and a polite live element.
- **Does:** asks the poll URL at each interval (minimum 5 s); the server answers 204 while nothing
  changed; otherwise the fragment is replaced, its status sentence is announced, and the region
  fires a bubbling `live:replaced` event. Focus that was inside the replaced part moves to the heading
  the region names (`data-heading`), so it is never lost. A failed request is retried at the next
  interval; a redirect (the session ended) or a fragment without a poll URL (the state is final) stops it.
- **Keyboard:** none of its own; focus is kept as above.
- **Live regions:** the region's own `[data-live-announce]`, or the element named in `data-announce`.
- **Without the script:** the page shows the state it loaded with and offers a *Refresh* link.
- **Used by:** the participant's waiting page, the own queue, a claimed booking, the agenda day view,
  the overview alerts.
- **Open (ADR 0118 step 3):** a stale marker after three failed polls, a pause control for updates
  that run unasked for more than five seconds, announcing only a change that matters.

### `update-progress.js`

- **ADR:** 0048; ADR 0072 step 2 plans to move it onto `live-region.js`.
- **Server renders:** `[data-update-progress]` with the host updater's steps and a poll URL, and
  `[data-update-announce]`.
- **Does:** every 5 seconds asks the poll URL, replaces the list when the updater reported something,
  announces the new status line, moves focus that was inside the replaced part to the card's heading,
  keeps asking while the web container restarts, and stops when the request ended or the session ended.
- **Keyboard:** none of its own.
- **Without the script:** the steps as loaded, with a *Refresh status* link.

### `calendar.js`

- **ADR:** 0020.
- **Server renders:** `[data-picker]` holding a month calendar as a table of links (`[data-calendar]`
  with `.calendar__day` cells) and `[data-announce]`.
- **Does:** turns the table into an ARIA grid with one tab stop (roving tabindex); choosing a day
  fetches that day's page and swaps in its times, announces the result and moves focus to the times
  heading. Any failure falls back to normal navigation.
- **Keyboard:** arrow keys move a day or a week; Home and End go to the start or end of the week; Page
  Up and Page Down change month; Enter or Space chooses the focused free day (a click does the same).
- **Live regions:** `[data-announce]`.
- **Without the script:** each free day is a link and each choice reloads the page; the other days are
  plain cells.

### `alert-popups.js`

- **ADR:** 0061 §7.
- **Server renders:** `[data-alert-popups]` with `data-popup-level`, the alerts widget items
  (`[data-alert-token]`, level, title, text, link), and the page's polite live region
  `#overview-announce`.
- **Does:** on `live:replaced`, pops up the alerts that were not on the page before, at the levels the
  admin chose (info, warning, outage; absent means none). A pop-up stays at least 8 seconds; hovering or
  focusing it pauses the time and leaving starts the full time again; Escape or its button closes it.
  Each new alert is read out once; focus is never moved.
- **Keyboard:** Escape closes a pop-up; its button is reachable with Tab.
- **Without the script:** the alerts are in the widget and, for outages, in the banner.
