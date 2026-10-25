# Architecture

How OpenPlanner is built: context, containers, code structure, domain model, runtime flows,
security and quality assurance. The *why* behind each choice is in the ADRs (`docs/adr/`);
the stakeholder design is in `docs/HANDOVER.md` (Dutch).

Status: October 2026, after work package 3 (first demo slice). Parts drawn with a **dashed
border** are designed but not built yet; the roadmap at the end says when.

## 1. Purpose and scope

Citizens book a video call with a team of a government contact center (a *group*, one
Webex Contact Center queue). The service offers free time slots, takes the booking, sends
confirmation and reminders, hands the call to the queue just before the start, and deletes
personal data 24 hours after the appointment.

Out of scope: the organisation's decision tree (its website links to the group page), agent
scheduling (the WxCC queue assigns agents), and the video call itself (Webex).

## 2. Quality goals

| Priority | Goal | How |
|---|---|---|
| 1 | Privacy | PII only on bookings, purged at end + 24 h; no PII in logs, URLs, jobs or errors |
| 2 | Security | Deny by default, one authorisation dependency, mandatory CI gates, external pentest |
| 3 | Accessibility | WCAG 2.2 AA: works without JavaScript and without a mouse; axe and Lighthouse in CI |
| 4 | Maintainability | Pure domain behind ports, enforced layers, docstrings, no copy-paste |
| 5 | Simplicity | Two containers, SQLite, no message broker; about 80 bookings per working day |

## 3. System context

```mermaid
flowchart LR
    citizen([Citizen])
    website[Organisation website<br/>decision tree]
    admin([Group / organisation admin])
    agent([Contact center agent])

    subgraph op [OpenPlanner]
        service[Booking service]
    end

    smtp[Mail provider<br/>SMTP]
    wxcc[Webex Contact Center<br/>work items, Create Task v2]
    video[Webex Meetings API]
    idp[Identity provider<br/>OIDC]
    sms[SMS route]
    whatsapp[WhatsApp]

    website -- links to /g/slug --> service
    citizen -- books, changes, cancels --> service
    service -- confirmation, reminders --> smtp --> citizen
    admin -. manages groups, texts, settings .-> service
    service -- work item with meeting link at T-5 or now --> wxcc
    wxcc -- offers work item --> agent
    service -- creates meeting, webhooks back --> video
    citizen -- joins in the browser --> video
    agent -- joins as cohost, Webex app --> video
    admin -. login .-> idp
    service -. reminders .-> sms
    service -. roadmap .-> whatsapp

    classDef planned stroke-dasharray: 5 5
    class sms,whatsapp planned
```

| External system | Direction | Purpose | Decision |
|---|---|---|---|
| Mail provider (SMTP) | out | Confirmation, change, reminder, cancellation | ADR 0009; per organisation in the admin UI (ADR 0008) |
| Webex Contact Center | out, in | Work item per booking or direct call; task events by signed webhook | ADR 0018 (supersedes 0007 in part) |
| Webex Meetings | out, in | Meeting per booking on behalf of the group's host; status by signed webhook | ADR 0017, 0010 |
| Identity provider | in | Admin login with roles | HANDOVER B8, O3 |
| SMS route / WhatsApp | out | Reminders | ADR 0009 (WhatsApp on the roadmap) |

## 4. Containers and deployment

```mermaid
flowchart TB
    browser([Browser])
    proxy[Reverse proxy<br/>TLS, body limit, forwarded IPs]

    subgraph host [One VM]
        web["web<br/>FastAPI + uvicorn<br/>pages, migrations on start"]
        worker["worker<br/>job loop, heartbeat"]
        volume[("data volume<br/>SQLite (WAL)")]
        mailpit["mailpit<br/>dev profile only"]
    end

    smtp[Mail provider]

    browser --> proxy --> web
    web <--> volume
    worker <--> volume
    worker -- SMTP --> smtp
    worker -. dev .-> mailpit

    classDef planned stroke-dasharray: 5 5
    class proxy planned
```

- **One image, two roles.** `web` runs `alembic upgrade head` and then uvicorn; `worker` runs
  `python -m app.jobs.worker` and starts only when `web` is healthy. Both run as uid 10001
  with a read-only root filesystem, no capabilities and `no-new-privileges`.
- **SQLite in WAL mode** on a shared volume: readers never wait; writers take
  `BEGIN IMMEDIATE`. Postgres is not a target (decided 2026-10-23).
- **Health.** `web`: `/healthz` (liveness) and `/readyz` (database). `worker`: a heartbeat file
  checked by `python -m app.jobs.healthcheck`.
- **Configuration** comes from the environment only. `ENVIRONMENT` defaults to `prod`, which
  requires `MANAGEMENT_TOKEN_KEY` and an `https` `PUBLIC_BASE_URL`.
- **Images** are built in CI, pushed to GHCR, signed keyless with cosign and carry an SBOM
  attestation (ADR 0006). Hosting and backups: open point O7.

## 5. Code structure

### Layers

```mermaid
flowchart TB
    main["app.main<br/>composition root"]

    subgraph entry [Entry points, never import each other]
        api["app.api<br/>public pages, health"]
        adminpkg["app.admin<br/>admin portal, RequirePermission"]
        jobs["app.jobs<br/>worker, runner, messages"]
        cli["app.cli<br/>seed-demo"]
    end

    web["app.web<br/>locale, templates, render(),<br/>protection, error pages"]
    adapters["app.adapters<br/>db (SQL, unit of work, job queue, scope filter),<br/>smtp, oidc, clock"]
    domain["app.domain<br/>pure business rules, authorisation matrix, ports"]
    i18n["app.i18n<br/>negotiation, translator,<br/>catalogues, dates"]

    main --> entry
    api --> web
    adminpkg --> web
    jobs --> web
    entry --> adapters
    web --> adapters
    adapters --> domain
    entry --> domain
    web --> i18n
    jobs --> i18n

```

import-linter enforces these arrows in CI (ADR 0005):

- `app.domain` imports no framework and no other `app` package.
- `app.i18n` imports nothing from the rest of `app` (ADR 0004).
- The entry points (`api`, `admin`, `jobs`, `cli`) never import each other.

### Ports and adapters

```mermaid
flowchart LR
    subgraph domain [app.domain]
        service[BookingService · AdminService]
        ports{{"ports.py<br/>Clock · Transaction · UnitOfWork ·<br/>MessageSender · VideoProvider ·<br/>ContactCenterDispatcher · IdentityProvider"}}
        service --> ports
    end

    clock[adapters.clock<br/>SystemClock]
    uow[adapters.db.uow<br/>SqlUnitOfWork]
    smtp[adapters.smtp<br/>SmtpMailSender]
    videoad[adapters.webex_meetings<br/>WebexMeetings]
    wxccad[adapters.wxcc<br/>WxccTasks]
    idpad[adapters.oidc<br/>OidcIdentityProvider]

    clock -.implements.-> ports
    uow -.implements.-> ports
    smtp -.implements.-> ports
    videoad -.implements.-> ports
    wxccad -.implements.-> ports
    idpad -.implements.-> ports

```

Tests replace every adapter with a fake from `tests/fakes/`. The in-memory unit of work has the
same commit and rollback behaviour as the SQL one.

### Module map

| Concern | Module |
|---|---|
| Opening hours, groups | `app/domain/scheduling.py` |
| Slot calculation (time zones, daylight saving) | `app/domain/availability.py` |
| Bookings, contact validation | `app/domain/booking.py` |
| Book, cancel, reschedule (use cases) | `app/domain/services.py` |
| Which jobs, when | `app/domain/job_plan.py` |
| Channels, reminder rules | `app/domain/channels.py` |
| Management tokens | `app/domain/tokens.py` |
| Error codes | `app/domain/errors.py` |
| Authorisation matrix, scopes (ADR 0014) | `app/domain/authorization.py` |
| Admin use cases, audit events | `app/domain/admin.py`, `app/domain/audit.py` |
| Groups, opening hours, exceptions, preview (admin) | `app/domain/planning.py` |
| What leaves for Webex/WxCC, timeline, call status, join window | `app/domain/calls.py` |
| Recording platform events, admin timeline and agenda | `app/domain/integration.py` |
| Creating meetings and work items (worker and direct calls) | `app/domain/call_service.py`, `app/jobs/calls.py` |
| Webex and WxCC adapters, encrypted tokens, webhooks | `app/adapters/webex_auth.py`, `webex_meetings.py`, `wxcc.py`, `webhooks.py`, `crypto.py`, `db/credentials.py` |
| Operator commands: `register-webhooks`, `wxcc-check` probes | `app/cli.py`, `app/adapters/webhook_registration.py`, `app/adapters/diagnostics.py` |
| Join page, direct call, webhook endpoints | `app/api/join_pages.py`, `direct_pages.py`, `webhooks.py` |
| Agenda, booking timeline, integrations page | `app/admin/call_routes.py`, `app/admin/routes.py` |
| OIDC login (ADR 0013) | `app/adapters/oidc.py` |
| Admin routes, permission dependency, session | `app/admin/routes.py`, `app/admin/authz.py`, `app/admin/session.py` |
| Group and planning screens, admin forms, shared page plumbing | `app/admin/group_routes.py`, `app/admin/forms.py`, `app/admin/pages.py` |
| Admin design (fixed look, no JavaScript, ADR 0016) | `app/web/static/admin.css`, `app/admin/templates/admin/` |
| Generated matrix document | `app/web/authz_report.py`, `app/cli.py` (`authz-matrix`) |
| Tables, migrations | `app/adapters/db/models.py`, `migrations/versions/` |
| Transactions, write lock | `app/adapters/db/uow.py`, `app/adapters/db/engine.py` |
| Job queue | `app/adapters/db/jobs.py` |
| Citizen pages | `app/api/booking_pages.py`, `app/api/templates/` |
| CSRF and rate limits | `app/web/protection.py` |
| Rendering and security headers | `app/web/templates.py`, `app/api/security_headers.py` |
| Translations and locale | `app/i18n/` |
| Messages and calendar files | `app/jobs/messages.py`, `app/jobs/calendar.py` |
| Job execution | `app/jobs/runner.py`, `app/jobs/worker.py` |

## 6. Domain model

```mermaid
erDiagram
    ORGANISATION ||--o{ GROUP : owns
    GROUP ||--o{ SCHEDULE_RULE : "opens weekly"
    GROUP ||--o{ SCHEDULE_OVERRIDE : "changes a date"
    GROUP ||--o{ REMINDER_RULE : reminds
    GROUP ||--o{ SLOT : "has booked"
    SLOT ||--o{ BOOKING : holds
    BOOKING ||--o{ JOB : schedules
    BOOKING |o--o| BOOKING : "rescheduled from"

    ORGANISATION {
        int id
        string slug
        string default_locale
    }
    GROUP {
        int id
        string slug
        string timezone
        int slot_minutes
        int min_notice_minutes
        int horizon_days
        int dispatch_lead_minutes
        string phone_mode
        string wxcc_queue_id
    }
    SCHEDULE_RULE {
        int weekday
        time opens
        time closes
        int capacity
        date valid_from
        date valid_to
    }
    SCHEDULE_OVERRIDE {
        date day
        time opens
        time closes
        int capacity
    }
    REMINDER_RULE {
        string channel
        int minutes_before
        bool enabled
    }
    SLOT {
        datetime start
        datetime end
        int capacity_snapshot
    }
    BOOKING {
        string status
        string name "PII"
        string email "PII"
        string phone "PII"
        string locale
        string management_token_salt
        string management_token_hash
        string guest_join_url "PII"
        string host_join_url "PII"
        datetime pii_purged_at
    }
    JOB {
        string type
        datetime run_at
        datetime pinned_start
        string channel
        string status
        int attempts
    }
```

Rules that matter for the rest of the system:

- **Capacity** is one number per opening window, because the WxCC queue assigns the agent
  (HANDOVER B5). A slot row is created by the first booking and snapshots that capacity.
  Later schedule edits change new slots only.
- **An override replaces every rule on its date.** A closed override blocks new bookings,
  even in slots that already exist.
- **The dispatch lead** (`dispatch_lead_minutes`, default 5) sets two moments: when the WxCC
  task is created, and when the booking becomes read-only. The minimum notice can never be
  shorter than it.
- **All times are stored in UTC.** They are converted to the group's time zone only when
  rendering and parsing.

## 7. Runtime views

### Booking

```mermaid
sequenceDiagram
    autonumber
    actor C as Citizen
    participant W as web (booking_pages)
    participant S as BookingService
    participant DB as SQLite
    participant K as worker (JobRunner)
    participant M as Mail provider

    C->>W: GET /g/demo
    W->>S: availability(week)
    S->>DB: read (deferred BEGIN, never blocked)
    W-->>C: slot picker
    C->>W: POST /g/demo/book (same-origin, rate-limited, honeypot)
    W->>S: book(start, contact, locale)
    S->>DB: BEGIN IMMEDIATE
    Note over S,DB: offered? capacity left? create slot,<br/>booking (salt + token hash), plan jobs
    S->>DB: COMMIT
    S-->>W: receipt (token, only in memory)
    W-->>C: 303 /b/{token}?status=booked
    loop every poll interval
        K->>DB: claim due jobs (write lock)
        K->>DB: read booking (skip if stale)
        K->>M: confirmation + .ics + manage link
        K->>DB: done / retry (1, 5, 15 min) / failed
    end
```

### Admin login and an authorised request

```mermaid
sequenceDiagram
    autonumber
    actor A as Admin
    participant W as web (app.admin)
    participant P as Identity provider
    participant S as AdminService
    participant DB as SQLite

    A->>W: GET /admin/groups/demo (no session)
    W-->>A: 303 /admin/login?next=…
    W-->>A: 302 to the provider (state, nonce, PKCE S256)
    A->>P: sign in
    P-->>A: 302 /admin/callback?code&state
    A->>W: callback
    W->>P: token exchange (code + verifier)
    W->>W: verify ID token (JWKS, alg allow-list, iss/aud/nonce/exp)
    W->>S: record_login(principal)
    S->>DB: audit event per organisation/group + admin profile
    W-->>A: fresh session cookie, 303 next
    A->>W: GET /admin/groups/demo
    W->>S: authorize(principal, group.view, demo)
    S->>S: decide() on the matrix
    alt allowed
        W-->>A: page, menu and buttons from can()
    else denied
        S->>DB: audit event (denied, reason)
        W-->>A: 403 "Geen toegang" inside the portal
    end
```

Listings never ask "may I see this row?" one by one: the matrix turns the permission into a
`Scope`, and the SQL adapter filters with it. A test checks exhaustively that the scope and the
single decision never disagree.

A change (WP7b) adds two steps to the authorised request: the route needs a login of at most
60 minutes (ADR 0015), and `PlanningService` authorises again before it validates the whole
schedule, writes and audits in one locked transaction.

### What happens to one booking over time

```mermaid
flowchart LR
    book(["booked"]) --> confirm["create video room<br/>send confirmation<br/>now"]
    confirm --> remind["reminders<br/>per group rule<br/>e.g. T-24h"]
    remind --> dispatch["T-dispatch lead<br/>WxCC task created,<br/>booking read-only"]
    dispatch --> meeting(["call<br/>from start to finish"])
    meeting --> purge["slot end + 24h<br/>purge PII"]

    cancel(["cancelled"]) --> cend["mark WxCC task cancelled (v2)<br/>then End Task (v1), ADR 0020<br/>delete meeting"] --> cmail["cancellation mail"] --> cpurge["cancel + 24h<br/>purge PII"]

```

Every job that is tied to a start time stores that start (`pinned_start`). On run it does
nothing if the booking is no longer confirmed, its start changed, or its data was purged.
Cancelling or moving a booking replaces its pending jobs in the same transaction, including
deleting its meeting.

### A call: meeting, work item, agent and citizen

```mermaid
sequenceDiagram
    autonumber
    actor C as Citizen
    participant O as OpenPlanner
    participant W as Webex Meetings
    participant X as WxCC
    actor A as Agent

    O->>W: create meeting on behalf of the group's host (job, or at once for a direct call)
    W-->>O: meeting id, join link
    O-->>C: email with OpenPlanner join link
    O->>X: work item: reference, first name, start, meeting link (T-5, or now)
    X-->>O: task id
    X->>A: offer work item (flow routes on openplannerGroup)
    A->>X: accept
    X-->>O: webhook task:connected (signed)
    A->>W: open meeting link in the Webex app, cohost
    C->>O: GET /b/{token}/join/go
    O-->>C: 303 to the Webex link
    C->>W: join in the browser, waits in the lobby
    W-->>O: webhooks: started, host joined, guest joined (signed)
    A->>W: admit citizen
    W-->>O: webhook: meeting ended
```

Every arrow back into OpenPlanner becomes a timeline event and updates the booking's call
status; the agenda in the admin portal shows it per day.

### Booking status

```mermaid
stateDiagram-v2
    [*] --> confirmed: book
    confirmed --> cancelled: cancel (before the dispatch lead)
    confirmed --> cancelled: reschedule (old booking; a new one is confirmed)
    confirmed --> completed: after the call (WP6)
    confirmed --> no_show: task not picked up (O2)
    cancelled --> [*]
    completed --> [*]
    no_show --> [*]
```

### Job status

```mermaid
stateDiagram-v2
    [*] --> pending: planned in the booking transaction
    pending --> running: claimed (write lock)
    running --> done: executed or skipped as stale
    running --> pending: failure, retry after 1, 5, 15 min
    running --> failed: fourth failure
    running --> pending: claim older than 10 min (worker crashed)
    pending --> [*]: replaced by cancel or reschedule
```

## 8. Security architecture

```mermaid
flowchart LR
    subgraph internet [Internet, untrusted]
        browser([Browser])
        bot([Bot / attacker])
    end

    subgraph edge [Edge]
        proxy[Reverse proxy<br/>TLS, body limit]
    end

    subgraph app [Application, trusted]
        mw[Security headers<br/>CSP, HSTS, COOP/COEP/CORP,<br/>Permissions-Policy]
        prot[require_same_origin<br/>rate limits, honeypot]
        pages[Booking pages]
        domain[Domain rules<br/>validation, error codes]
        db[(SQLite<br/>PII only in bookings)]
    end

    subgraph ext [Third parties]
        smtp[Mail provider]
        wxcc[WxCC]
        webex[Webex Meetings]
    end

    browser --> proxy --> mw --> prot --> pages --> domain --> db
    bot -. blocked .-> prot
    domain -. via worker .-> smtp
    domain -- first name + meeting link only --> wxcc
    domain -- group name + times only --> webex
    wxcc -- signed webhooks --> pages
    webex -- signed webhooks --> pages

    classDef planned stroke-dasharray: 5 5
    class proxy planned
```

| Control | Where | Reference |
|---|---|---|
| Strict response headers: CSP without scripts, HSTS, frame denial, COOP/COEP/CORP, Permissions-Policy | `app/api/security_headers.py`, `app/web/templates.py` | threat model |
| CSRF through Fetch Metadata (`Sec-Fetch-Site`), with an Origin fallback | `app/web/protection.py` | threat model |
| Rate limits (10 writes/hour, 60 reads/minute) and a honeypot field | `app/web/protection.py`, `booking_pages.py` | CLAUDE.md |
| Management tokens: 256 bits, HMAC-derived, only the salt and hash stored | `app/domain/tokens.py` | ADR 0011 |
| No PII in logs (masking processor and semgrep rule), errors, URLs or jobs | `app/logging.py`, `.semgrep/` | CLAUDE.md |
| Authorisation matrix: one `RequirePermission` per admin route, scoped SQL, audit of denials and logins | `app/domain/authorization.py`, `app/admin/authz.py` | ADR 0014 |
| OIDC with PKCE, nonce, asymmetric algorithms only; RP-initiated logout | `app/adapters/oidc.py` | ADR 0013 |
| Encrypted, write-only integration secrets | WP7 | ADR 0008 |
| Signed images, SBOM, image and dependency scanning | CI | ADR 0006 |

The STRIDE analysis per flow, with a test for every abuse case, is in
`docs/threat-model.md`.

## 9. Privacy

- **Personal data:** name, email, phone, guest and host join URL. These live only in
  `bookings`; jobs, slots and logs hold IDs.
- **Purge:** the `purge_pii` job sets them to NULL at slot end + 24 h, or 24 h after a
  cancellation. The nightly sweep and alert follow in WP8.
- **What leaves the system:** mails carry the first name, date, team and the management link;
  the calendar file carries no personal data. WxCC will receive only the first name and the
  host link.
- **The management page** shows no contact details, so a forwarded link reveals only the
  appointment.
- The DPIA input is in `docs/dataflow.md`.

## 10. Quality assurance

```mermaid
flowchart LR
    pr([Pull request]) --> lint[ruff incl. docstrings<br/>mypy strict<br/>import-linter<br/>jscpd]
    pr --> tests["pytest: domain/i18n coverage ≥ 90%<br/>overall ≥ 75%; migrations up/down"]
    pr --> sec[bandit, semgrep,<br/>pip-audit, licenses,<br/>gitleaks]
    pr --> image[hadolint, trivy,<br/>schemathesis, SBOM + grype]
    pr --> a11y[Playwright keyboard flow,<br/>axe, Lighthouse ≥ 95]
    lint & tests & sec & image & a11y --> review{human review}
    review --> main([merge to main])
    main --> zap[OWASP ZAP baseline]
    main --> mut[mutmut ≥ 80%]
    zap & mut --> publish[build, push to GHCR,<br/>cosign sign, SBOM attest]
```

Per work package the plan is reviewed (`/plan-eng-review`) and the diff is checked with the
repository's `security-review` skill before the PR (`docs/skills.md`).

## 11. Decisions

| ADR | Decision |
|---|---|
| 0001 | Record decisions as numbered ADRs |
| 0002 | Runtime layout: web migrates, worker heartbeat, health endpoints, deny-by-default headers |
| 0003 | certifi (MPL-2.0) as the only license exception |
| 0004 | Self-contained i18n module: Dutch default, browser detection, admin overrides |
| 0005 | Modularity and docstrings, enforced in CI |
| 0006 | Test tooling outside the runtime; images signed with cosign |
| 0007 | WxCC tasks through the Tasks API (no dedicated Webex Connect) |
| 0008 | Integration secrets in the admin UI, encrypted and write-only |
| 0009 | Channels as data, reminder rules, templates as text blocks |
| 0010 | Live meeting status for group admins (aggregates only) |
| 0011 | Management tokens derived from a per-booking salt and a server key |
| 0012 | Claude Code merges only on explicit instruction of the product owner, with green CI |
| 0013 | Admin login with OIDC (PingFederate; Keycloak in development), httpx + PyJWT |
| 0014 | The authorisation matrix as the single source of rights |
| 0015 | Admin changes need a recent login (60 minutes) |
| 0016 | Admin forms without JavaScript; HTMX only where it adds something |
| 0017 | Video calls through the Webex Meetings API (cohost for staff, lobby for guests) |
| 0018 | Dispatch to WxCC as work items (Create Task API v2) |
| 0019 | Direct video call ("now") per group |

## 12. Status and roadmap

| Work package | Content | Status |
|---|---|---|
| WP1 | Skeleton, CI, containers, logging, health | done |
| i18n core | Language module, home page | done |
| WP2 | Domain, persistence, write lock | done |
| WP3 (slice 1) | Citizen pages, mail, worker, demo | done |
| WP3 (rest) | Embeddable widget (grid calendar), branding (O5) | planned |
| WP4 | SMS reminders, message editors | planned |
| WP5 | Webex Meetings, join page, live status (O1: Meetings API) | done |
| WP6 | WxCC work items, task events, direct call (O9) | done (O2 follow-up open) |
| WP7a | Admin portal login, authorisation matrix, audit log | done |
| WP7b | Groups, opening hours, exceptions and capacity in the admin portal | done |
| WP7c–d | Texts and reminders, encrypted integration settings (O3) | planned |
| WP8 | Purge sweep and alerting, audit retention | planned |
| WP9 | Hardening, pentest, accessibility audit (O10) | planned |

The open decisions (O1–O10) are listed in `docs/HANDOVER.md` §11.
