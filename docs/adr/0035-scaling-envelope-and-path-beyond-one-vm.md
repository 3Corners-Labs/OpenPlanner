# 0035. Scaling envelope, and the path beyond one VM

Status: proposed (2026-10-27). Confirms the decision of 2026-10-23 (SQLite, no Postgres) for
now and states when and how it would be revisited.

## Context

OpenPlanner runs on one VM: `proxy`, `web` and `worker` containers sharing one SQLite database
in WAL mode (ADR 0002, 0030). The design target is about 80 bookings per working day, and the
HANDOVER asks for 99.5 % availability during office hours.

Within that, the current shape has room:

- SQLite in WAL mode serves concurrent readers without blocking; writers take
  `BEGIN IMMEDIATE` and wait up to 5 s (`busy_timeout`). Booking writes are a few rows each.
- More worker processes can already run on the same host: jobs are claimed under the write
  lock, and a claim older than 10 minutes is taken over.
- One installation already serves many organisations and groups (multitenant through the
  authorisation matrix, ADR 0014).

Beyond one VM it does not scale today, and architects reviewing the system ask what would
change:

- SQLite is a file on one host; a second `web` on another host cannot share it.
- The rate limiter keeps its counters in process memory (`app/web/protection.py`), so two `web`
  processes would each allow the full limit.
- Backups and the updater's restore use SQLite's backup API (ADR 0029).
- The write lock (`BEGIN IMMEDIATE`) and one partial index are SQLite-specific; both live in
  `app/adapters/db/` only.

## Decision

1. **Stay on one VM with SQLite** while the load and availability targets hold. Scale
   vertically (CPU, memory, faster disk) first. This keeps the operational burden at one
   container host, which matters for the clients' own operations teams (O7).
2. **Name the triggers.** The step beyond one VM is taken, through its own ADR, when one of
   these is true:
   - sustained load above 2,000 bookings per working day, or p95 booking latency above 1 s;
   - `database is locked` errors (busy timeout reached) in production;
   - an availability requirement above what one VM with a tested restore can deliver
     (recovery time objective under 15 minutes, or availability outside office hours);
   - a client that requires hosting on their managed Postgres or on a container platform
     without persistent single-host volumes.
3. **Keep the seams ready, without switching.** Three small changes so that the step is a
   contained adapter change, not a redesign:
   - the rate limiter's storage becomes a setting (`RATE_LIMIT_STORAGE_URI`, default
     `memory://`), so a shared store can be configured when there is more than one `web`;
   - the write lock is reached only through the unit of work; the SQLite form stays in
     `engine.py`, and a contract test states the guarantee (no double booking of the last
     place) without naming SQLite;
   - backup and restore go through one interface in `app/adapters/db/backup.py`, so another
     database can bring its own.
4. **Measure.** Web and worker log the duration of booking transactions and lock waits (no
   PII), and the count of busy-timeout errors is visible, so the triggers above are observed
   rather than guessed.
5. **What the step would be** (for the future ADR, not decided now): Postgres with
   `SELECT … FOR UPDATE` on the slot and `FOR UPDATE SKIP LOCKED` for job claims; two or more
   `web` replicas behind the proxy; workers on any host; a shared rate-limit store; backups by
   the database service. The domain, the ports, the routes and the templates do not change.

## Consequences

- The decision of 2026-10-23 stands; this ADR explains its limits and gives reviewers a
  concrete answer to "does it scale?".
- The three seams cost little and are testable now: the contract test for the write lock runs
  against the SQL unit of work and the in-memory fake alike.
- A client with a hard high-availability requirement today cannot be served by this shape; the
  answer is to take the step in point 5 first, not to stretch one VM.
- `docs/runbook.md` gets the triggers and where to read the measurements.
