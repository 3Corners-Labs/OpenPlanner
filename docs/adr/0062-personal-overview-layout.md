# 0062. Personal overview layout

Status: superseded by ADR 0096 (2026-11-01, re-evaluation); not built. Proposed on 2026-10-30, requested by the product owner;
never accepted. ADR 0096, accepted on 2026-11-01, decides the same per-admin layout as an
ordered list of widget keys. The richer parts of this ADR (columns, sizes, heights, auto-scroll,
widgets locked by the organisation) are not adopted; each would come back as an amendment to
ADR 0096. Builds on ADR 0061 (widgets) and ADR 0046 (staff records); see `docs/adr-status.md` for what is built.

## Context

With widgets on the overview (ADR 0061), admins want to choose what they see, in what order and at what size. A group admin may need the queue first; an auditor may hide everything except changes. ADR 0047 §9 keeps personal display choices in the session because there was no per-user record yet; a layout must survive logouts and devices, so it needs a stored preference.

## Decision

1. **A layout is a per-user preference**, stored on the staff record of ADR 0046 as a validated JSON document (`overview_layout`, schema `openplanner.layout/1`): column count (auto, 1–4) and an ordered list of widgets with size (columns), height, view, maximum rows and auto-scroll.
2. **Defaults per role.** Each role has a default layout in `app/web/widgets.py`. "Restore default" removes the stored layout.
3. **Locked widgets.** An organisation admin may lock widgets on for a group's admins and agents (for example *Alerts*). A locked widget cannot be hidden, only moved and resized. Lock lists are organisation settings (class D).
4. **Validation on the server.** Unknown widget keys, widgets the user may not see, and values outside the bounds are dropped, not stored. The layout never grants anything: visibility still comes only from the matrix on every request.
5. **Editing works without JavaScript.** "Customise overview" opens a form: a checkbox per widget, up and down buttons, selects for size, height, view and rows. Each button is a submit with post/redirect/get. With the script of ADR 0061, the same controls apply without reload, and a drag handle offers resizing with the pointer. Dragging is never the only way (WCAG 2.5.7). Every change is announced in the live region.
6. **Columns adapt to the screen.** The chosen column count is a maximum: 2 on tablet, 1 on phone. A widget is never wider than the grid.
7. **Audit.** Layout changes are not audit events (personal display, no effect on others). Lock changes are audited.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `OVERVIEW_LOCKED_WIDGETS` | D organisation | `["alerts"]` | keys from the widget register | `settings.organisation.manage` | Widgets that group admins and agents of this organisation cannot hide. |

**Fixed on purpose:** the maximum of 4 columns and 20 rows, and the breakpoints. They follow the design system, not the installation.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed; backlog section 6; waits for ADR 0046 step 2 | #130 | merged |
| 1 | LY-1: `staff_members.overview_layout`, schema `openplanner.layout/1`, server-side validation | | planned |
| 2 | LY-2: *Customise overview* form without script | | planned |
| 3 | LY-3: the same controls live with the ADR 0061 script; pointer resize as an extra | | planned |
| 4 | LY-4: `OVERVIEW_LOCKED_WIDGETS` and the lock in the form | | planned |

## Consequences

- Migration: column `staff_members.overview_layout` (nullable JSON), reversible.
- `docs/dataflow.md`: the layout is not PII, but it is linked to a staff record and removed with it.
- Tests: schema validation, dropping of forbidden widgets, the lock rule, the no-JavaScript form flow.
- Manual: topic *Customise the overview*.
