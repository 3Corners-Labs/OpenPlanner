# 0102. Retention: a maximum for open closures, back-ups, and what remains after a purge

Status: accepted (2026-11-01), decided by the product owner after the ASVS 5.0.0 L2 audit of
1 November 2026 (privacy chain PI-01, PI-02, PI-04, PI-05; V14.2.4). Amends the retention rule
of ADR 0024 (point 5); the rest of ADR 0024 stands. Not built; see *Build status*.

## Context

The privacy rules say that personal data leaves a booking 24 hours after its appointment. The
audit found four places where that promise has a hole:

- **Open closure (PI-02).** A suspended booking is purged 30 days after its closure ends
  (ADR 0024 point 5). While the closure has no end date there is no purge job, and the
  retention sweep leaves such a booking alone. A forgotten open closure keeps names, email
  addresses and join links without limit. A government DPIA does not accept an unlimited term.
- **Back-ups (PI-04).** The updater makes a rollback copy before each update and deletes it
  after 24 hours (`BACKUP_LIFETIME`, `app/adapters/db/backup.py`). That copy is not encrypted
  by the application. `docs/HANDOVER.md` §7 describes something else: encrypted
  disaster-recovery back-ups kept for the PII term plus seven days. The two have different
  purposes and the documents do not separate them. Disaster-recovery back-ups do not exist in
  the code (open point O7, the hosting owner).
- **Remains after a purge (PI-05).** `purge_personal_data` empties the PII fields but leaves
  `video_room_id`, `trial_room_id` and `wxcc_task_id`. Each is the key to a meeting or a work
  item at Cisco that still carries the participant's name and email address.
  `booking_events.message` holds up to 300 characters of provider text (ADR 0065) that is not
  proven never to echo an address. `management_token_hash` is no longer needed. The booking
  row itself is never deleted.
- **Classification (PI-01).** CLAUDE.md lists `note` as a PII field, but that column no longer
  exists, and it does not list the provider links. There is no single table that says, for every
  table with personal columns, what is held, for how long and who removes it.

Terminology matters here. What the purge does is *delete fields from the appointment*. It does
not make the booking anonymous: the provider links and the timestamps can identify a person
together with data held elsewhere. Documents, the manual and the interface therefore say
"deleted", never "anonymised".

## Decision

### Open closure: an absolute maximum

1. **Every suspended booking has a purge moment.** For a booking suspended by a closure:

   ```
   purge_moment = min(closure_end + 30 days,
                      original_slot_end + closure_max_retention_days)
   ```

   where `closure_end` is absent while the closure is open, so an open closure falls back to
   the second term. The original slot end is the end of the slot the booking still occupies.
   The 30 days after the end of a closure stay as ADR 0024 decided them; the maximum only cuts
   them short. A booking that is moved, reinstated or cancelled leaves the closure and follows
   the normal rules again (ADR 0024 point 5). A participant who rebooks gets a new booking with
   its own purge and falls outside the closure.
2. **The setting is `closure_max_retention_days`**: class D (the organisation is the
   controller), default 90, bounds 30 to 180, permission `settings.organisation.manage`. It
   cannot be switched off and has no "unlimited" value: removing a protection is never class C
   or D (ADR 0042). It is read when the purge moment is computed, so a change applies to
   bookings not yet purged, and the planning, the sweep and the overview use the same function.
3. **A purge job is planned for an open closure** at the moment of the maximum, when the booking
   is suspended. When the closure gets an end date, or is lifted, the job is replaced by the job
   for the earlier of the two moments. Bookings suspended before this change get their job from
   the retention sweep, which uses the same function.
4. **The organisation administrators are told in advance.** `closure_max_notice_days` before the
   maximum, the existing alert path tells them, per group, that the closure is still open and on
   what date the data of the participants will be deleted. The message contains counts and a
   date, no participant data. The closure page and the participant's management page state that
   date ("your details are deleted by … at the latest").
5. **The retention overview and the sweep** (ADR 0039, `app/domain/retention.py`,
   `app/domain/retention_sweep.py`) use this moment for suspended bookings, so an open closure
   no longer hides a booking from them.
6. **ADR 0024** gets one change, its Status line: "retention amended by 0102". Its text is not
   rewritten.

### Back-ups

7. **The updater's back-up is a rollback copy and nothing else.** It lives for 24 hours
   (`BACKUP_LIFETIME`, fixed: it is the restore window of an update, not a retention policy),
   mode 600 in a directory with mode 700, and it is not encrypted by the application. It is
   allowed only on an encrypted volume; the installation guide and the runbook say so and the
   go-live checklist asks the host to confirm it. The application cannot see volume encryption,
   so this is a requirement on the host, not a check in the code.
8. **Disaster recovery is the responsibility of the hosting party** (open point O7), not of the
   application, and not a setting. OpenPlanner states the requirements and the restore
   procedure; the host's agreement meets them and the controller can ask for proof:
   - *encrypted*: the copies are encrypted at rest and in transit;
   - *term*: a fixed maximum age, written in the agreement and consistent with the DPIA; the
     reference is `docs/HANDOVER.md` §7 (the PII term plus seven days);
   - *access*: named roles only, with a log of who read or restored a copy;
   - *destruction*: copies are destroyed at the end of the term, with a record of it;
   - *restore test*: one test per year, documented with its date and outcome.
9. **The restore procedure** (written into `docs/runbook.md` in this order):
   `maintenance on` → restore the copy → `python -m app.cli purge-overdue` → check that the
   retention overview shows 0 bookings holding data past their moment →
   `sessions end-all --reason restore` (ADR 0098) → `maintenance off`. The sweep step matters:
   a restored copy is older than the live data, so it can hold personal data that was already
   deleted on the live system.
10. **A second safety net for the rollback copies.** The worker also prunes `data/backups` in
    its housekeeping, so an installation without the updater timer does not keep a copy.
    *System status* shows the age of the oldest back-up and warns when it is older than
    `BACKUP_LIFETIME` plus a grace period.

### What remains after a purge

11. **The purge also empties** `video_room_id`, `trial_room_id` and `wxcc_task_id`, and empties
    `booking_events.message` of that booking. `management_token_hash` is replaced by a random
    value (the column is unique and not null; the old value can no longer be used to find the
    booking). The salt stays: without the token it identifies nothing. Before this is built, the
    build step checks that no job after `purge_pii` needs these values; the purge is the last
    job of every plan.
12. **The booking row is deleted after `booking_row_retention_days`**, counted from
    `pii_purged_at`. Before that, the row is merged into a count per group and per day (and
    final status), which is all that is kept for statistics; the events and the call status of
    the booking go with the row. The setting is class C, default 730, bounds 365 to 3650,
    permission `settings.platform.manage`.
13. **The PII list in CLAUDE.md** is adjusted in a build step: `note` is removed (the column no
    longer exists) and the provider links are added (`video_room_id`, `trial_room_id`,
    `wxcc_task_id`). A test checks that `privacy.DATA_FIELDS` and `purge_personal_data` name the
    same fields.
14. **One retention matrix** in `docs/dataflow.md` covers every table with personal columns
    (`bookings`, `booking_events`, `call_status`, `staff`, `staff_refused_entitlements`,
    `audit_events` with the `client:` pseudonym, `admin_sessions`, `diagnostic_modes`,
    `organisation_contacts`, `group_contacts`, `groups.video_host_email`, `jobs`,
    `webhook_events`): what is held, for how long, who removes it, and how a restore treats it.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `closure_max_retention_days` | D organisation | 90 | 30–180 | `settings.organisation.manage` | Days after the original slot end; no way to switch off |
| `closure_max_notice_days` | D organisation | 14 | 3–28 | `settings.organisation.manage` | How long before the maximum the administrators are told. Added by this ADR so the lead time is not a constant (assumption of the author; the product owner did not name it) |
| `booking_row_retention_days` | C platform | 730 | 365–3650 | `settings.platform.manage` | Days after `pii_purged_at` until the row is merged into a daily count and deleted |

**Fixed on purpose:** `BACKUP_LIFETIME` (24 hours, the restore window of an update); the 30 days
after the end of a closure (decided in ADR 0024); the 24 hours of the normal purge and the
48-hour backlog alert (CLAUDE.md privacy rules); the grace period before *System status* warns
about an old back-up; the disaster-recovery term, which is a hosting agreement and not a setting
of this application.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR accepted (decision PR) | #362 | in PR |
| 1 | P-A2: maximum for an open closure: `closure_max_retention_days`, `closure_max_notice_days`, planning, sweep, overview, notice, closure and management page text, manual | | planned |
| 2 | P-A5: purge empties the provider links, event message and token hash; retention matrix in `docs/dataflow.md`; the PII line in CLAUDE.md; terminology in docs and manual | | planned |
| 3 | P-A4: worker prunes `data/backups`; *System status* shows the oldest back-up; restore procedure in the runbook and `docs/HANDOVER.md` §7 | | planned |
| 4 | Merge the booking row into a daily count and delete it after `booking_row_retention_days` | | planned |

Step 4 is not in the product owner's list of steps; it is added because decision 12 would
otherwise have no step that builds it.

## Controls

| Control | Covered by |
|---|---|
| PI-01 classification of all personal data | decisions 13 and 14, step 2 |
| PI-02 open-ended closure | decisions 1 to 6, step 1 |
| PI-04 back-ups | decisions 7 to 10, step 3; disaster recovery is evidence from the host |
| PI-05 remains after a purge | decisions 11 and 12, steps 2 and 4 |
| V14.2.4 retention and removal of sensitive data | all of the above |

## Consequences

- A closure cannot hold personal data longer than 180 days after the appointment, whatever the
  administrator does. An organisation that needs longer must rebook the participants.
- `docs/dataflow.md` gets the matrix; `docs/runbook.md` gets the restore procedure, and
  `docs/HANDOVER.md` §7 (Dutch) is brought in line with this ADR in step 3 (the text, not the
  decision). The threat model gets the abuse case "forgotten closure" with its test, and the
  manual topics on closures and privacy say "deleted".
- A participant who opens an old management link after the purge finds a deleted appointment.
  A random token hash is not reversible, so the link stops working.
- Migrations: step 1 needs none beyond the settings; step 2 none (the columns exist; the hash is
  overwritten); step 4 adds a table for the daily counts and is reversible, because the counts
  contain no personal data and the downgrade only drops the table.
- No new dependency.
