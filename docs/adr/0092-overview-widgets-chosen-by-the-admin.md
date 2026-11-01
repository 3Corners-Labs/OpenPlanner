# 0092. Overview widgets chosen by the admin

Status: proposed (2026-11-01), requested by the product owner. Extends ADR 0047 (admin
structure) and the overview of ADR 0044. Not built; see *Build status*.

## Context

The overview shows the widgets of the admin's role in one fixed default layout (queue,
bookings today, changes, tasks, status, rights). The overview carried a *New group* button,
which does not belong there: it is an action of the groups list and of the organisation page,
where it still lives. What the overview itself needs is control over its own content: add a
widget, remove one, put them in the order that fits the admin's work. Today that does not exist.

## Decision

1. **A per-admin layout.** Each staff member has an ordered list of widget keys. Without a list
   the default layout of their role applies, as today. The list is a personal preference, not
   a setting: it is stored on the staff member, holds keys only (no PII), and is never shared.
2. **Two controls on the overview, in the page header:** *Add widget* (a list of the widgets
   the admin's rights allow and that are not shown yet) and *Edit overview* (reorder with
   plain up/down buttons, remove). Server-rendered forms, no JavaScript needed (ADR 0016
   allows HTMX only where a form is not enough); keyboard-only operable (WCAG 2.2 AA, no
   drag-only interaction, 2.5.7).
3. **The matrix still decides.** A widget is offered and rendered only when the viewer holds its
   permission at the scope; a stored key the admin lost the right for is skipped silently.
4. **A reset.** *Restore default layout* deletes the list.
5. **Widgets are declared once** in `overview_routes.py` (key, title, permission, default
   position); the control reads from that declaration, no second list.
6. **No action buttons that belong elsewhere.** The overview carries *Refresh* and the two
   layout controls only; creating things happens on the list that owns the object.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `overview_max_widgets` | C platform | 12 | 4–24 | `settings.platform.manage` | upper bound on a personal layout |

**Fixed on purpose:** the widget catalogue is code; a new widget is a code change with its own
permission.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #310 | in PR |
| 1 | Remove *New group* from the overview | #310 | in PR |
| 2 | Layout storage (migration, reversible), service in `app/domain/`, tests | | planned |
| 3 | *Add widget* / *Edit overview* screens, manual topic nl/en, e2e | | planned |

## Consequences

- One migration (a layout column or table keyed by staff member), reversible.
- `docs/admin-map.md` gains the two sub-pages of the overview; `docs/manual/{nl,en}/` gets a topic.
- The data flow is unchanged: widget keys carry no personal data.
