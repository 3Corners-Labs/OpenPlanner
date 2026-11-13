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

## The gates of an island page

A page that loads an island carries `"island": true` in `e2e/pages.json`. `e2e/tests/islands.spec.ts`
then runs, in every locale of that page: scripts off (heading, content and axe), a keyboard run
through every control, 320 px without sideways scroll (which is also 400 % zoom, WCAG 1.4.10), forced
colours with reduced motion and axe, and no content-security-policy or script error in the console
(ADR 0118 point 10). A new island sets the flag on the pages that show it before it merges.

## The call page, a third kind of page

Besides the page without scripts and the island page there is one more kind (ADR 0123 point 2):
the **call page**, `GET /b/m/{ref}/call`, the only page that may load a third-party script, the
vendored SDK below. It is not an island: it is not an enhancement of a complete page. It is
selected by `PageKind.CALL` in `render()`, which gives the response `CALL_CSP` (`SCRIPT_CSP` plus
the call's five hosts in `connect-src`, `media-src` and `worker-src` named, no `unsafe-` token) and a
`Permissions-Policy` with camera, microphone and screen capture for our own origin. No other route
can pass that kind (`tests/web/test_page_kinds.py`), the route does not exist while
`embedded_call_enabled` is off, and the page contacts nobody before the participant's click. The
call page's own scripts follow the accessibility rules above and the ban on markup sinks. In this
step the page is a shell without any script.

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
  A region that names `data-stale-after="3"` (the wallboard, ADR 0117 step 13) counts failed requests in
  a row and, at that count or when the session ended or was refused (401 or 403, which also stops it), sets `data-stale` on the region and announces
  the fragment's `[data-live-stale-status]` sentence once; the stylesheet shows the old-data state in
  words, and the next good answer removes it. A button `[data-live-pause]` in the region (rendered
  `hidden`, revealed by the script) pauses and resumes the asking, with `aria-pressed` and
  `data-paused` on the region. A status sentence is written to the polite element only when it
  differs from what stands there, so an unchanged state is not read out again.
- **Keyboard:** none of its own; focus is kept as above.
- **Live regions:** the region's own `[data-live-announce]`, or the element named in `data-announce`.
- **Without the script:** the page shows the state it loaded with and offers a *Refresh* link.
- **Used by:** the participant's waiting page, the own queue, a claimed booking, the agenda day view,
  the overview alerts, the wallboard (ADR 0117 step 13).
- **Open (ADR 0118 step 3):** the stale marker, the pause control and announce-on-change are built for the
  wallboard only (they act on regions that opt in); the other pages adopt them, and the tests of the
  mechanism itself, in that step.

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

### `brand-motion.js`

- **ADR:** 0060 §7 (build step 2); the first island in the form ADR 0118 asks for: an ES module
  defining the custom element `op-brand-mark`.
- **Server renders:** the product mark as inline SVG (`_brand.html`, macro `brand_mark`) inside
  `<op-brand-mark data-sign-in="…">` in the admin top bar; `data-sign-in` is the session's sign-in
  moment in epoch seconds (no secret). The animation itself is CSS (`brand-mark--settle`, in
  `admin.css` and `site.css`), so the loading loop and the one-shot settle on the booking
  confirmation run without any script.
- **Does:** on connect, unless `prefers-reduced-motion: reduce` is set, adds `brand-mark--settle`
  once per sign-in and tab (remembered in `sessionStorage`; refused storage means no motion), so
  the open tile crosses the grid once and settles in its place.
- **Keyboard:** none; the mark is decorative (`aria-hidden`) and never takes focus.
- **Live regions:** none; nothing is announced.
- **Browser features:** custom elements, ES modules, `matchMedia`, `sessionStorage`, CSS animation
  of `transform` on SVG (all Baseline widely available); `forced-colors` gets system colours and no
  change of meaning. The shared rules are in `brand-mark.css`.
- **Without the script:** the static mark, exactly as before.
- **Used by:** the overview (loaded with the page's other scripts when the overview refreshes
  itself).
- **Size:** under 1 KB gzipped.
- **Open:** the island checks of ADR 0118 point 10 run once that ADR's step 2 adds the `island`
  flag to `e2e/pages.json`; the overview is listed there already for axe and keyboard runs.


### `report-charts.js`

- **ADR:** 0117 point 19 (build step 12), 0118.
- **Server renders:** the usage charts of `admin/_report_chart.html`: `<op-report-chart>` with the
  words in `data-toolbar`, `data-svg`, `data-png` and `data-hint` (in the page's language), a
  figure with an SVG whose points carry `data-i` (the category, the table row beside the chart) and
  `data-s` (the series), and directly after it the table with the same numbers. For a chart of days
  the row header is an ordinary link to the same page for that one day (suppression applies there too).
- **Does:** a tip on hover and on keyboard focus whose text is the table row (set with
  `textContent`); series toggles as buttons with `aria-pressed` (the scale stays); download as SVG
  (with its colours inlined) or as PNG (drawn on a canvas, since the CSP allows no image from a
  blob); Enter on a focused day follows its link.
- **Keyboard:** the chart frame is a tab stop; arrow keys, Home and End move between categories,
  Enter opens a day, Escape closes the tip; the buttons are reachable with Tab.
- **Live regions:** a polite status element announces the tip's words; focus is never moved.
- **Browser features:** custom elements, ES modules, `XMLSerializer`, `canvas`, `Blob`
  (all Baseline widely available); `forced-colors` gets system colours.
- **Without the script:** the chart, its table and the day links as the server drew them.
- **Used by:** the *Usage* pages of an organisation and a group (script CSP on these pages only).
- **Size:** about 3 KB gzipped.

### `vendor/webex/meetings.min.js`

- **ADR:** 0123 point 3. Not an island and not ours: a vendored third-party file, listed here so that
  every script under `app/web/static/` has one entry.
- **Server renders:** nothing yet; the call page of step 2 is a shell without a script tag.
- **Does:** runs the call for the participant (built in later steps), loaded only after the click that joins.
- **Without the script:** the page says the call is not available and links back to the join page.
- **Used by:** the call page only (`CALL_CSP`).
- **Size:** about 919 KB gzipped, with its own gate of 1.2 MB (`tests/web/test_vendor.py`); exempt from
  the budgets above. The file is not in the repository until Cisco has answered on the licence
  (ADR 0123 point 18); the manifest next to it names the version and the checksum.
