# Reporting: the mockup and what is built of it

The design of the reporting screens is the interactive mockup
[`docs/mockups/reporting.html`](mockups/reporting.html) (five views; open it in a browser).
[ADR 0117](adr/0117-reporting-on-use-from-a-store-that-holds-no-personal-data.md) decides
what is built; this file is the check that the finished reports match the mockup and that nothing
is left half built. The product owner asked for this on 2026-11-07: the reports are done when every
row below says *built*, not when the last step of the ADR has merged.

Rules:

- A step that builds part of a view updates the rows it touches in the same PR, and names the test
  that holds each row.
- A row is *built* only when a test or a Playwright check fails if it breaks. "Looks right" is not a
  state.
- A difference from the mockup is a row with the reason, never silence. The mockup shows
  Dutch text from the catalogue; the real screens use `_()` keys in `nl` and `en`.
- The mockup is a design, not code: it is never served, and its inline script and style are not
  copied into `app/`.

## View 1: Usage, Volume

| Element in the mockup | ADR 0117 step | State | Held by |
|---|---|---|---|
| Breadcrumb and the report family links (*Volume*, *Queue and calls*, *Capacity*, *Channels*, *Team*) | 5, 6, 7 | built; *Team* only for who holds `reports.staff.view`, and marked *off* until the organisation switches it on | `tests/admin/test_usage_screens.py` (`test_the_page_links_the_families_and_marks_the_current_one`), `tests/admin/test_usage_team.py` |
| Range form: from, to | 5 | built | `tests/admin/test_usage_routes.py` |
| Range form: *Split by* (none, queue method, language) | 6b | built; **subject is not offered**: subject codes are not rolled up yet (ADR 0081 is unbuilt), the row follows that ADR | `tests/domain/test_usage_report.py`, `tests/admin/test_usage_screens.py` |
| *Export CSV* of the screen | 8 | planned | – |
| *As of* line, the last 24 hours marked provisional, the threshold sentence | 5 | built | `tests/admin/test_usage_routes.py` |
| Four figure tiles: bookings in the period (U1), no-show rate (U3), cancelled by participant (U4, with the late share), confirmed after e-mail (U9, with the median bucket) | 6b | built; the median is the duration bucket it falls in, as the store holds buckets, not seconds | `tests/domain/test_usage_report.py`, `tests/admin/test_usage_screens.py` |
| Bars per day, weekday and weekend as two series told apart by pattern | 6b | built | `tests/admin/test_usage_screens.py` |
| Weekend toggle (the series toggle), download (SVG and PNG instead of *Copy SVG*), arrow keys, and *filter on this day* (Enter, an ordinary link) | 12 | built | `tests/admin/test_usage_screens.py`, `e2e/tests/admin-report-charts.spec.ts` |
| The same numbers as a table directly after each chart, with `< 5` for a hidden cell | 6 | built for the charts that exist | `tests/web/test_charts.py`, `tests/admin/test_usage_screens.py` |
| Stacked outcome bar with its table and shares | 6b | built (four series; the table keeps the finer outcomes and has a share column) | `tests/admin/test_usage_screens.py` |
| Note on complementary suppression under every table that hides a count | 6b | built | `tests/admin/test_usage_screens.py` (`test_a_table_with_a_hidden_count_says_why`) |
| Note on bookings from before reporting started (counts per day only) | 5 | built | `tests/admin/test_usage_routes.py` |

## View 2: Capacity

| Element | Step | State | Held by |
|---|---|---|---|
| Four figure tiles: utilisation (C1, with booked of total places), unused places (C3), full blocks (C2), staff shortfall (C5, with the accepted ones) | 6b | built | `tests/admin/test_usage_screens.py` |
| Busiest hours as a heat table: number in every cell, colour only as a second signal | 6 | built | `tests/admin/test_usage_screens.py` |
| Cell explanation by mouse and arrow keys | 12 | **open**: the tip works on the charts; the heat table is a table, not a chart, and gets no tip | – |
| Hour figures only over 7 days or more and only above five times the threshold, with the sentence | 5 | built | `tests/admin/test_usage_screens.py` (`test_the_heat_table_is_not_offered_for_a_short_range`) |

## View 3: Wallboard

| Element | Step | State | Held by |
|---|---|---|---|
| `view=wallboard`, full screen (`layout=kiosk`), six tiles (waiting now, longest wait, in a call, appointments today with the no-shows, fill of the current block, failed hand-overs) | 13 | built | `tests/admin/test_usage_wallboard.py` |
| Live, *long wait* and *old data* states, each in words | 13 | built | `tests/admin/test_usage_wallboard.py`, `e2e/tests/admin-wallboard.spec.ts` |
| *Pause updates*, date, *as of* on every tile | 13 | built | `tests/admin/test_usage_wallboard.py`, `e2e/tests/admin-wallboard.spec.ts` |
| Hour bars with the planned part shaded, counts only | 13 | built | `tests/domain/test_wallboard.py` |
| Text size for a 1080p screen, no sound, no blinking | 13 | built | `e2e/tests/admin-wallboard.spec.ts` (nothing moves, the number is 3.4 rem or more) |

## View 4: Display links

| Element | Step | State | Held by |
|---|---|---|---|
| Shown-once link (a read-only field to copy from; a copy button is an island and stays open) | 14 | built | `tests/admin/test_display_links.py::test_the_link_is_shown_once_and_nowhere_else` |
| Form: which wallboard, name, valid until | 14 | built | `tests/admin/test_display_links.py` |
| Who may use it: ACL per line, *pin to the first address* | 14 | built | `tests/admin/test_display_links.py`, `tests/domain/test_wallboard_link.py` |
| List of links with status, pinned address, ACL, last used, expiry and *Revoke* | 14 | built | `tests/admin/test_display_links.py::test_a_revoked_link_stops_the_screen_at_its_next_poll` |

## View 5: Digest

| Element | Step | State | Held by |
|---|---|---|---|
| New subscription form and list | 10 | built | `tests/admin/test_usage_digest_routes.py` |
| Preview of the mail (link, optional numbers) | 10 | built as the mail itself, no separate preview | `tests/domain/test_report_digest.py` |
