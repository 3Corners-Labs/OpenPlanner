# 0033. Context-sensitive help from one manual

Status: accepted (2026-10-27), requested by the product owner.

## Context

Features were built and shipped without a manual. Admins had to guess what a setting such as
"Doorzetten naar het contactcenter" or "Maximaal gelijktijdige gesprekken" does, and people
who do not use the tool (procurement, the DPIA, a new team lead) had nothing to read at all.

The product owner wanted context-sensitive help, with an (i) where a field needs more than a
one-line hint, and was unsure whether the manual belongs inside the application or on GitHub,
where anyone can read it without an account. Citizens must stay inside our interface: their
help has to be on the page itself, never a link to GitHub.

## Decision

1. **One source, two readers.** The admin manual is Markdown in `docs/manual/{locale}/`, one
   file per screen (a *topic*), with `nl` and `en` required (ADR 0004). GitHub renders the same
   files, so the manual is readable without the application. The application never links to
   GitHub; it renders the files itself.
2. **In the application.** `app/web/help.py` reads and renders the manual (`markdown-it-py`,
   CommonMark, raw HTML off). The admin portal shows it at `/admin/help` (topics) and
   `/admin/help/{topic}`, permission `dashboard.view` (every admin). The manual is public, so a
   topic page is not hidden from a role; the topic list only shows the screens the admin can
   open, from the same matrix (ADR 0014). Links between manual files (`group-settings.md`)
   become links to `/admin/help/...`.
3. **Three layers on a screen.**
   - *Hint*: a short line under the field, as today (`Field.hint`, i18n catalogue).
   - *(i) More information*: a `<details>` disclosure under the field. No JavaScript (ADR
     0016), keyboard and screen-reader friendly, and unlike a tooltip it meets WCAG 1.4.13
     and works on touch screens. Its text is the field's section in the manual, marked in
     the Markdown with `<!-- field: <input name> -->` on the line before the section's
     heading. GitHub hides the marker. The disclosure ends with a link to that section of
     the in-app manual.
   - *Help with this page*: a link at the top of every admin page that has a topic. It is
     derived from the route (`HelpTopic.paths`), so a route module does nothing to get it.
4. **Citizens.** Citizen pages never link to the manual, GitHub or any other site for
   help. When a citizen form needs explanation, it is text in the page from the i18n
   catalogue (hint or disclosure), so organisations can override it like any other text.
5. **Keeping it current.** Tests fail when:
   - a topic is missing in `nl` or `en`, or the two locales mark different fields;
   - a marked field does not exist on the screen, or a field of a documented screen has
     no section;
   - a manual link points to a topic that does not exist;
   - an admin page route has neither a topic nor an entry in the test's
     `NOT_YET_DOCUMENTED` list.
   That list may only shrink: an entry whose route has gained a topic fails the test. Each
   PR that adds or changes a screen updates its topic (PR checklist in `CONTRIBUTING.md`).

## Consequences

- One text serves GitHub readers, the help page and the (i) disclosure, so they cannot
  drift apart. The cost is a small convention: the marker comment and the "no headings in
  code blocks" rule of the splitter.
- `markdown-it-py` becomes a runtime dependency (MIT; already in the lock file through
  development tools; recorded in `docs/dependencies.md`). Raw HTML is off, so the manual
  cannot inject markup; it is still reviewed code, shipped in the image (`docs/manual`).
- The manual is not editable in the admin UI and has no per-organisation overrides. If an
  organisation needs its own wording, that becomes a new decision.
- Only the group settings screen is documented at first. The rest is listed in
  `NOT_YET_DOCUMENTED` and documented screen by screen.
