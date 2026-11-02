# 0103. Self-describing log lines: one shape, a readable message and a register of actions

Status: proposed (2026-11-02), requested by the product owner after the first syslog test on
docker01. Extends ADR 0040 (part 2, the event schema: `openplanner.event/2` becomes
`openplanner.event/3`) and ADR 0085 (what a destination receives). Not built yet: step 1 (the
register) is in PR #416.

## Context

On 2026-11-02 the product owner connected docker01 to a syslog server (a Synology log centre)
through the log destination of ADR 0085 and read the lines in its viewer. The delivery works;
the lines do not explain themselves. What the viewer showed, checked against
`app/logging.py`, `app/domain/event_schema.py` and `app/adapters/log_senders.py`:

1. **Two shapes.** Audit, system and security events carry the full envelope (`schema`,
   `event.category`, `event.action`, `event.outcome`, `outcome`). Every other line (about 90
   actions: `sessions.cleaned`, `storage.degraded`, `updates.checked`, `authz.decision`, ...)
   carries only `message`, `log.level`, `service.*` and `trace.id`. A filter on
   `event.action` or `event.category` misses half the stream.
2. **`message` holds a code, not a sentence.** On ordinary lines it is the action
   (`storage.degraded`); on audit lines it is empty, or the provider's reason for a failed
   job (ADR 0065). Nobody reading the line learns "disk space is low: 6% free".
3. **`detail` is a packed string**: `changed:syslog:1:source,min_level`,
   `free_percent:6,database_mb:1`, `update.auto_request,from:0.4.0-rc.131,to:0.4.0-rc.134`. A
   log platform cannot search on its parts; a person has to know the format.
4. **`installation` is empty** on docker01: `INSTALLATION_NAME` is not set, and nothing says so.
   A syslog server that receives several installations cannot tell them apart except by the
   syslog host name.
5. **The actor says nothing.** `actor` is the identity provider's subject, a pseudonym on
   purpose (no names in logs), but the line does not even say whether it was a person, the
   system or the host, or under which role the action was allowed.
6. **Noise.** Every admin page writes one or two `authz.decision` lines at `info`. CLAUDE.md
   requires every decision to be logged, so they stay; but a destination cannot leave them out,
   because they belong to no category.
7. **Lines of libraries** (uvicorn's "Waiting for application shutdown.") have no action and no
   category at all.
8. **The syslog header carries little**: `MSGID` is `audit` or `log`, so a syslog viewer
   cannot filter on the kind of event without opening the JSON.

The cause is one gap: an action is a free string at the call site, with no place that says what
it means, which fields it has, how severe it is, and what a person should do about it.

## Decision

### One register of actions

1. **Every action is registered once** in `app/domain/log_events.py`: the action code
   (`storage.degraded`), its category (`audit`, `system`, `security`, `authz`, `operations`),
   its level, its fields (name and kind, from a closed list), an English message template with
   `{placeholder}`s from those fields only, what it means, and what to do (a sentence, or
   "nothing"). A test scans `app/` and fails when a log call, an `AuditEvent` or a trace point
   uses an action that is not registered, or when a template names a field the entry does not
   declare. Trace points (ADR 0085 part 2) move into the same register.
2. **`docs/logging.md` is generated from the register** (`python -m app.cli logging-doc`), one
   row per action with its category, level, fields, meaning and what to do, and CI fails when
   it differs from the code, as with `docs/settings.md`. The runbook links to it instead of
   repeating actions.

### One shape for every line: `openplanner.event/3`

3. **Every line carries the envelope**: `schema`, `@timestamp`, `log.level`, `event.category`,
   `event.action`, `event.outcome` (where the action has one), `message`, `service.name`,
   `service.version`, `installation` and, inside a request or job, `trace.id`, `organisation`
   and `group`. Ordinary lines and audit lines differ only in `log_type` and in the fields only
   audit events have (`event_id`, `outcome`, `reason`, `booking_id`).
4. **`message` is a readable English sentence** rendered from the register's template:
   "Disk space is low: 6% free, database 1 MB." The code moves to `event.action`. The message is
   rendered from registered fields only and passes the PII mask like everything else, so it
   cannot carry more than the fields do. English, because the log is read by operators and log
   platforms; the portal's event screens render the same entry through `app/i18n`
   (`log.event.<action>`, nl and en), never the English line.
5. **The provider's reason for a failure** (ADR 0065, today in `message`) moves to
   `error.message` (ECS), masked and capped as now.
6. **`detail` becomes fields.** The line carries the action's fields as `labels.<name>` (ECS
   `labels`: flat, strings and numbers) — `labels.free_percent: 6`,
   `labels.changed: "source,min_level"`, `labels.from: "0.4.0-rc.131"`. The table and the CSV
   export keep the `detail` column, written from the same fields as `name=value; ...`, so no
   migration of existing rows is needed and old rows stay readable.
7. **The actor is described without a name**: `actor` stays the subject; `actor.type` is
   `user`, `system`, `host` (CLI on the host) or `api` (machine API, ADR 0023); for a user,
   `actor.role` is the role that granted the permission (`superuser`, `org_admin`, ...). Never a
   display name or email.
8. **Lines of libraries** get `event.action` `library.<logger>` (`library.uvicorn.error`),
   category `operations`, and keep their own text as `message`.

### What a destination and syslog get

9. **Authorisation decisions have their own category `authz`.** They stay logged at `info`
   (CLAUDE.md: every decision is logged); a denial is `warning`. A log destination chooses the
   `authz` category like the others (default off for a new destination; existing destinations
   keep receiving them until the superuser changes it, so nothing disappears silently).
10. **Syslog `MSGID` is `event.category`** (`audit`, `system`, `security`, `authz`,
    `operations`), so a syslog viewer filters by kind without parsing the message.
11. **`installation` is never empty without a warning.** The installer writes
    `INSTALLATION_NAME` (default: the host's short name) into `.env`; *System status* shows a
    warning while it is empty and a log destination exists.

### Compatibility

12. **Version 3 is announced, not slipped in.** The schema name in every line changes to
    `openplanner.event/3`; `docs/audit-events.md` lists the changes against version 2; the
    release notes say that searches on `message` for a code must use `event.action`. Version 2
    is not produced in parallel: the installations with a destination today are test and lab.

## Settings

None new. Checked:

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `INSTALLATION_NAME` | B deployment | `""` → installer writes the host's short name | `^[A-Za-z0-9_.-]{0,64}$` (unchanged) | host | Exists (ADR 0040); tied to the host, so it stays B. Only the installer's default and the warning are new. |

The `authz` category per destination is part of the destination (ADR 0085 point 3), not a
setting.

**Fixed on purpose:** the register itself (actions, levels, fields, templates) is code, not a
setting: a level or a message an admin could change would make the log unreliable as evidence,
and a template an admin could edit could pull in a field that carries PII. The categories are a
closed list for the same reason.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #412 | in PR |
| 1 | The register (`app/domain/log_events.py`) with every current action and trace point, the scan test, `docs/logging.md` generated | #416 | in PR |
| 2 | `openplanner.event/3`: envelope on every line, message from the template, `error.message`, `labels.*`, `actor.type` and `actor.role`, library lines; `docs/audit-events.md`, CSV export | | planned |
| 3 | Category `authz` for destinations, syslog `MSGID` from the category | | planned |
| 4 | Installer writes `INSTALLATION_NAME`; warning on *System status* | | planned |
| 5 | Portal event screens render the register through `app/i18n` (nl, en) | | planned |

## Consequences

- A log platform and a person read the same line: the action to filter on, a sentence to read,
  fields to search on, and in `docs/logging.md` what it means and what to do.
- Every new log call needs a register entry; that is deliberate (one home per concern, ADR
  0005) and the scan test enforces it. Step 1 is mostly mechanical: about 90 existing actions.
- Saved searches on a log platform that match `message` against a code, or that parse
  `detail`, break once with version 3; the release notes say so.
- Lines grow by the envelope fields; the volume of `authz` lines can be cut per destination.
- `docs/logging.md`, `docs/audit-events.md`, `docs/dataflow.md` (fields that leave the
  installation: `actor.role` is new, not PII) and the threat model (template rendering from
  registered fields only) change with the build steps.
