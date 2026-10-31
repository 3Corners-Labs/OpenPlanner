# 0080. Page width: two classes, declared in the register, and a week that never wraps

Status: accepted (2026-10-31), requested by the product owner ("leg vast en bouw", after a screenshot of the group overview in which the weekly schedule wrapped into six days and one). Builds on ADR 0047 (the navigation register decides where a screen lives; it now also decides how wide it is). Built; see *Build status*.

## Context

Every admin page sat in one container capped at 72 rem beside the side navigation. On a wide screen a quarter of the window stayed empty, and the weekly schedule, a grid that filled as many columns of at least 8.75 rem as fitted, wrapped into six days and a lonely seventh. The cause was one cap for all pages, not the size of the screen.

A fix per page would repeat the problem: the stylesheet has 2 500 lines and eight places that cap the width of a component, and nothing says which pages may grow. The product owner asked for one common framework, enforced, not a one-off.

What the sources say. The NL Design System pages consulted (the paragraph component and the form guidelines) state the WCAG reflow rules (1.4.4 and 1.4.10: nothing outside the screen at 400 % zoom or 320 px, no horizontal scrollbar) but give no rule for page width or line length. The NHS service manual advises lines of 70 to 80 characters for text, and the Flemish design system advises against form fields over the full width of a desktop screen. So this decision is ours, built on WCAG 1.4.10 and on common practice, and it does not claim the NL Design System as its source.

## Decision

1. **Two page widths, as tokens in `admin.css`.** `--adm-width-standard` (72 rem, the reading width of forms, settings, details and text; the default) and `--adm-width-wide` (96 rem, for data views). Nothing else sets the width of a page: a page container never carries a rem value of its own.
2. **The navigation register decides the width of a screen.** `Screen.width` (`Width.STANDARD` or `Width.WIDE`) applies to the screen's own path. A sub-page (detail or edit page) is read or filled in, so it stays standard unless the screen lists it in `Screen.wide_subpages`. `Home.width` carries the answer, `page()` passes it as `content_width`, and the layout writes it on `<main data-width="…">`. `admin.css` keys on that attribute only. A shortcut owns no route and declares no width. `docs/admin-map.md` shows the width of every screen.
3. **Which screens are wide.** A screen whose main content is an overview, list, table, schedule, agenda or widget grid is wide; a screen whose main content is a form or text is standard. Wide today: the overview, the audit log, staff, the rights matrix, and the group tabs overview, hours, agenda (with the week agenda) and queue. Every other screen stays standard until a data view on it needs the room; adding it is one line in the register.
4. **A form in a wide page keeps the reading width.** A `form.card` directly in a wide page is capped at the standard width, so a long form never spreads across 90 rem.
5. **Left-aligned at the navigation, never centred.** The content does not jump when the navigation collapses to the rail or comes back.
6. **Components may cap themselves, pages may not.** Popovers, the public sign-in card, the branding preview and message cards keep their own caps. They are listed in `tests/web/test_shell_styles.py`; a new cap in rem or ch fails that test until it is added there on purpose, with a look at whether it should be a page width instead.
7. **A grid with a fixed number of items is that many columns or stacked, never "as many as fit".** The weekly schedule is seven columns when its own container is at least 66 rem wide and one row per day under that (the name beside its blocks, which flow side by side; under 32 rem the blocks stack under the name). The measure is a container query on `.week-frame`, not the screen, so it holds for every width of the navigation, the page and the card. A grid with an open number of items (cards, tiles) keeps `auto-fill` with a minimum width.
8. **Reflow stays whole.** Nothing needs a horizontal scrollbar from 320 px up (WCAG 1.4.10); the admin shell test of SH-1 keeps checking 375, 768 and 1280 px.
9. **Guarded by tests.** The register (default, sub-pages, wide sub-pages are sub-pages), the markup (`data-width` on `<main>` equals the register), the stylesheet (tokens, the page container uses them, the form cap, the allow-list of component caps, no `auto-fit` or `auto-fill` on the week, the frame) and `docs/admin-map.md` (generated; CI fails if it differs).

## Settings

None. The two widths and the 66 rem threshold are design-system values like the limits of ADR 0047 §7: they change how a page looks, not what an installation does, and a width that differs per installation or organisation would make the manual's screenshots wrong.

**Fixed on purpose:** 72 rem, 96 rem, 66 rem, 32 rem and the seven columns (a week has seven days).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR accepted | | in PR |
| 1 | `Width` in the register and `Home`, `data-width` on `<main>`, tokens and rules in `admin.css`, the week frame, `docs/admin-map.md`, the guarding tests | | in PR |
| 2 | Move further list screens to wide when the test host shows a table that needs the room | | follow-up |

## Consequences

- A new admin screen gets a width decision with its register entry; the default is standard, so forgetting it keeps today's look.
- `docs/admin-map.md` has a *Width* column, reviewed with every screen.
- No change to authorisation, data, settings, migrations, dependencies or the threat model. No new script; the container query needs a browser from 2023 on, the same as the rest of the stylesheet's `light-dark()`.
- The rule for a fixed-count grid applies to later views too (a month calendar is seven columns or a list, never six plus one).
- Not changed: the citizen pages and the widget, which have their own tokens (ADR 0020).
