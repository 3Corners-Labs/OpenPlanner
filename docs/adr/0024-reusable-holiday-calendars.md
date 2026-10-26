# 0024. Reusable holiday calendars with overrides per organisation and group

Status: accepted (2026-10-25)

## Context

Until now every group closed its holidays one by one as exceptions (`ScheduleOverride`).
National holidays are the same for nearly every team, so each group admin had to enter
Nieuwjaarsdag, Pasen, Koningsdag and the rest every year, and a forgotten date meant citizens
could book on a day nobody works. The product owner asked (2026-10-25) to configure national
holidays once at superuser level and let organisations and groups reuse them, while keeping
the possibility to override.

## Decision

1. **Holiday calendars are global reference data**, maintained by the superuser with a new
   global permission `holidays.manage`. A calendar (`holiday_calendars`) holds dates with a
   name (`holidays`, one per date per calendar). There can be more than one (national,
   regional such as carnival, an organisation's own closing days).
2. **Filling a year is one click.** The domain generates the Dutch national holidays of a
   year (`holidays.dutch_national_holidays`, Easter by the anonymous Gregorian algorithm):
   the days of the Algemene termijnenwet, both Easter and Whit Sundays, and Bevrijdingsdag
   every year as in the CAO Rijk; Koningsdag moves to 26 April when 27 April is a Sunday.
   Goede Vrijdag is left out; the superuser adds it where it applies. Existing dates are
   skipped, so the action is idempotent. Names are stored in the language of the admin who
   generated them (they are admin content, like group names).
3. **Organisations choose, groups inherit.** A new organisation-level permission
   `holidays.assign` (superuser and organisation admins) picks the calendars an
   organisation's groups follow. A group inherits that choice by default.
4. **Overrides, from coarse to fine**, all under `schedule.manage` (group admins too):
   - a group can stop inheriting and pick its own calendars, or none at all;
   - a group can keep a single holiday open as usual (its weekly rules apply);
   - a group's own exception on a holiday date always wins, so a holiday can also open
     with special hours.
   Resolution on one date: own exception → holiday of an effective calendar not kept open
   (closed) → weekly rules. It is pure domain code (`GroupHolidays.calendars`, `closures`,
   `with_holidays`); holidays become closed overrides before `windows_on`, so availability,
   booking, direct calls and the admin preview all follow without changes of their own.
5. **Calendars are listed without a scope.** `Transaction.get_holiday_calendars` has no
   `Scope` parameter because a global permission has none (ADR 0014); every admin who may
   choose calendars sees all of them. Organisation listings on the holiday pages still go
   through `scope_for(holidays.assign)`.
6. **A calendar in use cannot be deleted.** The domain refuses (`holiday_calendar_in_use`);
   the database backs this with `ON DELETE RESTRICT` on both choice tables.

## Consequences

- One action per year by the superuser keeps every inheriting group correct; the group
  page lists the coming year's holidays with their state, so a group admin sees what
  citizens will get.
- Existing data does not change: no organisation has a calendar after migration 0015, and
  existing exceptions keep working. Groups that closed holidays by hand can remove those
  exceptions once their organisation follows a calendar (both together do no harm).
- Global calendar changes are audited with organisation and group empty, so only the
  superuser sees them in the audit log; organisation and group choices are audited in their
  own context.
- Organisation-level "keep open" is not offered: an organisation that wants different days
  chooses another calendar (or the superuser makes one). If that turns out too coarse, an
  `organisation_holiday_open_days` table mirrors the group one.
- Holiday names are not shown to citizens yet; a closed day simply has no times. Showing
  "gesloten: Koningsdag" in the citizen calendar is a later, separate change.
