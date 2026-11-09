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
| Range form: *Split by* (none, queue method, subject, language) and *Show* | 5 | **open**: no step names it | – |
| *Export CSV* of the screen | 8 | planned | – |
| *As of* line, the last 24 hours marked provisional, the threshold sentence | 5 | built | `tests/admin/test_usage_routes.py` |
| Four figure tiles: bookings in the period (U1), no-show rate (U3), cancelled by participant (U4, with the late share), confirmed after e-mail (U9, with the median) | 5 | **open**: the screen shows tables only | – |
| Bars per day with weekend toggle, *Copy SVG*, arrow keys and *filter on this day* | 6 (SVG), 12 (script) | **open**: *Volume* got no chart in step 6 | – |
| The same numbers as a table directly after each chart, with `< 5` for a hidden cell | 6 | built for the charts that exist | `tests/web/test_charts.py`, `tests/admin/test_usage_screens.py` |
| Stacked outcome bar with its table and shares | 6 | **open**: the outcome is a table | – |
| Note on complementary suppression when two cells are hidden | 5 | **open**: only the general rule sentence exists, not the per-table note | – |
| Note on bookings from before reporting started (counts per day only) | 5 | built | `tests/admin/test_usage_routes.py` |

## View 2: Capacity

| Element | Step | State | Held by |
|---|---|---|---|
| Four figure tiles: utilisation (C1), unused places (C3), full blocks (C2), staff shortfall (C5) | 6 | **open**: the screen has a utilisation line and the heat table; the tiles are not there | – |
| Busiest hours as a heat table: number in every cell, colour only as a second signal, legend, cell explanation by mouse and arrow keys | 6 (table), 12 (explanation) | **open**: check the screen; the explanation needs the script | – |
| Hour figures only over 7 days or more and only above five times the threshold, with the sentence | 5 | built | `tests/admin/test_usage_screens.py` (`test_the_heat_table_is_not_offered_for_a_short_range`) |

## View 3: Wallboard

| Element | Step | State | Held by |
|---|---|---|---|
| `view=wallboard`, full screen, six tiles (waiting now, longest wait, in call, calls today, utilisation of the current block, failed hand-overs) | 13 | planned | – |
| Live, *long wait* and *old data* states, each in words | 13 | planned | – |
| *Pause updates*, clock, *as of* on every tile | 13 | planned | – |
| Day bars with the planned part shaded, counts only | 13 | planned | – |
| Text size for a 1080p screen, no sound, no blinking | 13 | planned | Playwright check at step 13 |

## View 4: Display links

| Element | Step | State | Held by |
|---|---|---|---|
| Shown-once link with copy button | 14 | planned | – |
| Form: which wallboard, name, valid until | 14 | planned | – |
| Who may use it: ACL per line, *pin to the first address* | 14 | planned | – |
| List of links with status, pinned address, ACL, last used, expiry and *Revoke* | 14 | planned | – |

## View 5: Digest

| Element | Step | State | Held by |
|---|---|---|---|
| New subscription form and list | 10 | planned | – |
| Preview of the mail (link, optional numbers) | 10 | planned | – |

## Open rows

Every row marked **open** above that no step names gets a step. They are added to the *Build
status* table of ADR 0117 as step 6b (Volume tiles, day chart, outcome bar, split by) so that the
reports are not declared done while the *Volume* screen is still tables. The Capacity rows are
checked against the running screen in the same step and turned into *built* or into their own row.
