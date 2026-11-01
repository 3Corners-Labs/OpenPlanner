# 0084. Everything an admin creates can be changed and deleted

Status: accepted (2026-10-31, product owner), partly built: steps 1, 2 and 3 built, step 4 in PR, steps 5–7 not built. Requested by the product owner. Extends ADR 0014 (one
authorisation matrix), ADR 0024 (emergency closure), ADR 0047 (admin structure) and ADR 0025
(holiday calendars).

## Context

The product owner looked for a way to delete an organisation and a group and found none. That
is not an oversight in the portal's navigation: no route, service method, permission or CLI
command deletes either. The organisation knows `create` and `update` only, the group `create`
and `settings`. The audit events `organisation.remove` and `group.remove` concern a member
leaving an organisation or group (memberships from before ADR 0049), not the object itself.

The question was whether this is a wider pattern. An inventory of every object the portal can
create (2026-10-31, `app/admin/*_routes.py`):

| Object | Create | Change | Delete | Gap |
|---|---|---|---|---|
| Organisation | `organisation.manage` | yes | **no** | delete |
| Group | `group.create` | `group.edit` | **no** | delete |
| Staffing block | `schedule.manage` | yes (and split) | yes | – |
| Staffing exception | `schedule.manage` | **no** | yes | change (remove and add again) |
| Form field (ADR 0055) | `group.edit` | yes | retire | – (retire is the decision of ADR 0055: answers refer to the field) |
| Reminder rule | `reminders.manage` | **no** | yes | change (the rule is its channel and minutes; remove and add again) |
| Holiday calendar (ADR 0025) | `holidays.manage` | **no rename** | yes, when unused | rename |
| Holiday date | `holidays.manage` | – (a date is its identity) | yes | – |
| Custom role | `roles.manage` | yes | yes | – |
| Closure scenario | `closure.manage` / `organisation.closure` | yes | yes | – |
| API client (ADR 0023) | `api_clients.manage` | yes (name, grant, rotate, enable) | **no** | delete; disabling exists, the row stays for the audit trail |
| Channel (SMTP, SMS) | `integrations.manage` | yes | yes | – |
| Logo, font, theme | `branding.manage` | yes | yes | – |
| Embedding origins, contacts, texts, settings | per permission | yes | yes (empty form, reset) | – |

Bookings are created by participants and are never deleted by an admin: they are cancelled,
and the PII purge of ADR 0024 and CLAUDE.md removes the data. Staff members come from the
identity provider and are not created here. Closures are lifted, not deleted.

So the pattern is real but small: two objects cannot be deleted at all (organisation, group),
one cannot be deleted although it can be switched off (API client), and three cannot be changed
in place (staffing exception, reminder rule, holiday calendar name). Every gap sits in the place
where admins expect the action, next to the object's edit form.

What constrains the answer:

- Organisations and groups carry data that other rows refer to with `ON DELETE RESTRICT`:
  groups under an organisation; slots, bookings, shortfall acceptances, closures and API
  client grants under a group. Bookings hold PII until the purge job runs; slots are the
  capacity snapshot of those bookings.
- `/g/{slug}` is a public address that the client's website links to. A deleted group's slug
  must not serve another group's page by accident.
- The audit log (ADR 0031, 0040) refers to groups and organisations by slug and keeps doing so
  after they are gone.
- ADR 0014: every action is one permission in the matrix, with a level, and the SQL adapter
  filters by scope. Deleting is a separate right from editing: an organisation admin who may
  change a group's settings every day should not be able to remove the group by a slip of the
  mouse, unless the role says so.

## Decision

1. **Rule.** Every object an admin can create in the portal can also be changed and deleted
   from the same screen, under a permission of the matrix, with an audit event. A new screen
   that adds a creatable object adds its change and delete routes in the same PR, or says in
   the PR (review question 7) which decision makes an exception. Allowed exceptions are the
   ones of this ADR: retire instead of delete when other data refers to the object (form
   fields, ADR 0055), and no delete for objects the audit trail needs (API clients, bookings).
2. **Two new permissions.** `organisation.delete` (level global, superuser only, like
   `organisation.manage`) and `group.delete` (level organisation, held by the superuser and
   the organisation admin, like `group.create`). Deleting is not part of `organisation.manage`
   or `group.edit`; a custom role (ADR 0046) grants it separately. Both are in the matrix,
   `docs/authorization.md` and the matrix sweep.
3. **A group is archived, not erased, while anything refers to it.** `POST
   /admin/groups/{slug}/delete` under `group.delete`:
   - Refused with the domain code `group_has_future_bookings` while a confirmed, pending or
     suspended booking has a start in the future. The screen says to close the group first
     (ADR 0024) and lets the participants move or cancel, or to cancel them from the closure
     screen.
   - Otherwise the group is **archived**: `groups.archived_at` is set, `/g/{slug}` answers
     404, the widget origin list no longer offers it, no job dispatches for it, it leaves the
     navigation and lists (an *Archived* filter on `/admin/groups` shows it, read-only), and
     the slug stays reserved: a new group cannot take it.
   - The purge job keeps running for its past bookings. When no booking, slot or closure
     refers to the group any more (the last purge is done and the retention period of the
     audit details has passed, ADR 0040), the nightly sweep **erases** the row; the cascades of
     the schema remove staffing, texts, reminders, form fields and settings. The audit log
     keeps the slug as text.
   - Archiving writes `group.archived`, erasing `group.erased`, both with the slug and the
     actor, never with PII.
4. **An organisation is deleted only when it is empty.** `POST /admin/organisations/{org}/delete`
   under `organisation.delete` is refused with `organisation_has_groups` while a group of the
   organisation exists, archived ones included. An empty organisation is erased at once, with
   its branding, channels (their encrypted secrets), embedding, contacts, texts, settings,
   scenarios and IdP registration (ADR 0083), by the cascades. The event is
   `organisation.deleted`. Staff memberships in the IdP are not touched; a login that still
   names the organisation gets no rights, as today for an unknown organisation.
5. **Confirmation, never a bare button.** Both actions are a separate confirmation page that
   names what is removed and what stays, with the object's name typed back as the confirmation
   (no JavaScript; a form field, ADR 0016 does not apply). They need a recent login, like the
   platform settings (ADR 0042 §8). The confirmation page is a sub-page of the object's
   settings tab in the navigation register (ADR 0047); no new section, tab or home.
6. **The three change gaps are closed in place.** A staffing exception gets an edit form
   (`GET`/`POST /admin/groups/{slug}/exceptions/{id}`, `schedule.manage`); a reminder rule gets
   one (`/admin/groups/{slug}/reminders/{channel}/{minutes}`, `reminders.manage`), where
   changing the channel or minutes replaces the rule and moves its pending jobs; a holiday
   calendar can be renamed (`POST /admin/holidays/{calendar}`, `holidays.manage`). Each writes
   its audit event like the add and remove routes do.
7. **An API client can be deleted when it is disabled** (`POST
   /admin/api/clients/{id}/delete`, `api_clients.manage`): the row goes, the audit events keep
   the client id. A client that is still enabled must be disabled first, so that a deletion is
   always two deliberate steps.
8. **A guard test.** A test walks the admin route tables and asserts for every `POST .../new`
   or create route that a change route and a delete (or retire) route exist for the same object,
   or that the object is on the exception list of this ADR. Adding a creatable object without
   the rest fails CI.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `group_erase_after_days` | C platform | 30 | 1–365 | `settings.platform.manage` | days after the last reference to an archived group disappears before the sweep erases the row; gives the audit a stable slug meanwhile |

**Fixed on purpose:** the confirmation by typing the name back (a protection, never off from
the portal); the recent-login requirement (ADR 0042 §8); the refusal while future bookings or
groups exist (data integrity, not a threshold).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #262, carried into `main` by #270 | merged |
| 1 | Permissions `organisation.delete` and `group.delete` in the matrix, docs and sweep; the guard test of point 8 with today's gaps on its exception list | #264, carried into `main` by #270 | merged |
| 2 | Group archive: migration `archived_at`, domain rule, route, confirmation page, 404 on `/g/{slug}`, lists and navigation, audit events, manual topic (nl/en) | #270 | merged |
| 3 | Group erase in the nightly sweep, setting `group_erase_after_days`, threat model and data flow updated | #278 | merged |
| 4 | Organisation delete: domain rule, route, confirmation page, cascades checked up and down in the migration test, manual topic | #287 | in PR |
| 5 | Change in place: staffing exception, reminder rule, holiday calendar rename | | planned |
| 6 | API client delete after disable | | planned |
| 7 | Guard test loses its exception list entries one by one as steps land; `docs/threat-model.md` abuse case "delete another organisation's group" with its matrix test | | planned |

## Consequences

- Easier: the portal does what admins expect of a create button, and a new object cannot ship
  half-done because the guard test says so.
- The threat model gets two abuse cases: deleting a group of another organisation through a
  guessed slug (the scope filter refuses, the sweep tests it for every role), and deleting a
  group with future bookings to lose PII without the purge (refused by point 3).
- The data flow diagram gets the archive and erase states of a group; the DPIA notes that
  erasing a group removes nothing the purge job did not already remove.
- Migration: one reversible column `groups.archived_at`; no change to the restrict and cascade
  rules of the schema, which already fit this decision.
- Manual topics for the two confirmation pages and the archived filter, in nl and en.
- Dependencies: none.
