---
name: verify-openplanner
description: Launch this OpenPlanner checkout as an isolated local stack (web, worker, Mailpit and the development Keycloak on 127.0.0.1:8010, 8090 and 8035, next to any other stack), check that it is usable, drive the participant booking page, the emailed management link and the admin portal the way a participant or staff member does, keep screenshots and ARIA snapshots as evidence, and remove the stack again. Use to see a change working in the real app before a PR, to prove a fix, or when the run skill looks for a project skill that launches OpenPlanner.
---

# Verify OpenPlanner (repo skill)

Builds the checkout you are in, starts it next to whatever else runs on the machine, proves
behaviour through the paths a participant or staff member really takes, and keeps the evidence
after the stack is gone. Name this skill when you use it (CLAUDE.md). Everything below is
literal: commands run from the root of the checkout, texts are the Dutch interface texts.

Three files do the work:

- `scripts/stack.sh`: `up`, `doctor`, `compose`, `down` for the stack;
- `scripts/journey.mjs`: the journeys that drive it, with Playwright from `e2e/`;
- `scripts/stack-client.mjs`: the client they share with the `role-manuals` skill: the run
  record, the instance guard, the development users, Mailpit, the slot picker and the shared
  waits and helpers;
- `scripts/labels.json`: every interface text the scripts of both skills drive, by catalogue key,
  checked against the catalogue by `tests/test_skill_labels.py`;
- `features/`: the map, one file per feature with its handles, its proof and its traps. Read
  `features/README.md` before you drive anything.

## When to use it, and when not

- Use it after a change that a participant or staff member can see: the booking pages, the
  emails, the management link, the login, an admin screen. Run the journey that crosses the
  change, and put its summary table and run folder in the PR under *How tested*.
- It does not replace `make check` or the Playwright suite (`make e2e`): those cover every
  browser, keyboard-only flows, axe and Lighthouse. This skill proves one real path on one built
  image, with evidence a reviewer can open.
- It never touches the test host (docker01) or production. For those, CLAUDE.md asks what runs
  there, not what runs here.

## Rules

- Drive only the stack this skill started. `journey.mjs` refuses an installation whose
  `/api/v1/instance` differs from the one `up` recorded; never point it at port 8000 (a
  developer's own stack) or at a shared host.
- Test values only. Staff are the development users of `dev/keycloak/README.md`; the journeys
  read their password from `dev/keycloak/openplanner-realm.json`, so it never goes into chat or
  evidence. The participant is the made-up one of the run, in `stack.env`: first name
  `Proefdeelnemer`, last name `Run-<run id>`, email `verify.<run id>@example.org` (a reserved
  domain). Never type real personal data into the stack.
- No booking-link token, no password and no participant value in evidence. The journeys blank
  tokens (`/b/<token>`) and the realm's test passwords, mask the participant's values in
  screenshots and replace them in text. Do not add raw emails, HAR files or Playwright traces:
  they hold tokens and cookies.
- The stack runs the settings of `.env.example` and nothing else: no Webex, no Webex Contact
  Center, no real SMTP. Mail stays in Mailpit on this machine.

## Launch

Needs Docker with compose 2.24 or newer, git, curl, Node, the e2e tooling (`make setup`, or
`cd e2e && npm ci`) and Google Chrome (or Playwright's own Chromium, see *Baseline
preconditions* in `features/README.md`).

```bash
.claude/skills/verify-openplanner/scripts/stack.sh up
```

`up` refuses to start when a run of this checkout is already up, when the project has
containers, when one of its ports is taken, or when a port or the project name is malformed.
Then it:

1. creates the run folder `.verify-openplanner/<run id>/` with `stack.env` (ports, made-up
   participant) and points `.verify-openplanner/current` at it;
2. renders the dev realms of `dev/keycloak/` with this stack's web port as redirect address;
3. records the commit and a fingerprint of the files the image is built from (the allow-list in
   `.dockerignore`, plus the `Dockerfile` and `.dockerignore` themselves), then builds
   `openplanner:verify-<project>-<run id>`, a tag of its own so that stacks of other worktrees
   never retag it (log: `build.log`; a first build takes a
   few minutes, later ones use the cache);
4. starts web, worker, Mailpit and Keycloak as compose project `openplanner-verify` with
   `compose.verify.yml` on top of `docker-compose.yml`, and waits until web and worker are
   healthy (web runs the migrations on an empty database);
5. waits for Keycloak's realm and `/readyz`, runs `python -m app.cli seed-demo`, records the
   installation id of `/api/v1/instance`;
6. runs `doctor` and prints the addresses.

Ready means `doctor: usable` at the end of `up`, not merely a process that answers.

On a machine that is far over its CPU count (a load average in the hundreds, for example while
several worktrees run `make check`), the migrations take many minutes, web's own health check
(5 seconds) times out, and `up` stops with `container openplanner-verify-web-1 is unhealthy`.
That is the machine, not the change: run `down`, wait until `uptime` shows a normal load, then
`up` again. Do not stop other sessions' processes to make room.

| Service | Address | Change with |
|---|---|---|
| web (participant pages, admin portal) | http://127.0.0.1:8010 | `VERIFY_WEB_PORT` |
| Keycloak (realm `openplanner`) | http://127.0.0.1:8090 | `VERIFY_KC_PORT` |
| Mailpit (inbox and API) | http://127.0.0.1:8035 | `VERIFY_MAIL_PORT` |

Isolation: its own project name, volumes, image tag and ports, and no `edge` network (another
stack of this repository holds that fixed subnet), so a developer's stack on 8000, 8080 and
8025 keeps running untouched. One run per checkout at a time; a second worktree that runs one at
the same time sets `VERIFY_PROJECT` and the three ports to other values. With `VERIFY_PROJECT`
set, `down` refuses a recorded run of another project, so a script that names its project never
removes the stack of another session.

## Doctor

```bash
.claude/skills/verify-openplanner/scripts/stack.sh doctor
```

Read-only, and appended to the run's `doctor.txt`. Run it first whenever anything looks off,
and again before driving after you edited code. Every line must be PASS:

| Check | Passes when |
|---|---|
| containers | web and worker `running healthy`, keycloak and mailpit `running` |
| ports | 8010, 8090, 8035 are published by this project, not by something else |
| instance | the web port answers the installation id recorded at `up` |
| ready | `/readyz` is `ok` (`degraded` is a warning: the data volume is low) |
| build | web runs the image built at `up`, and the files it is built from are unchanged |
| demo | `/g/demo` shows `Demo: videogesprek` |
| login | Keycloak announces issuer `http://127.0.0.1:8090/realms/openplanner`, and web reads its discovery document with that issuer |
| mail | Mailpit answers, and web reaches `mailpit:1025` |

A `build` FAIL means the stack runs older code than the checkout: `down`, then `up`. Any other
FAIL: read the line, then `stack.sh compose logs --tail 50 web worker`; when in doubt, start
over with `down` and `up`.

## Drive

```bash
node .claude/skills/verify-openplanner/scripts/journey.mjs all
node .claude/skills/verify-openplanner/scripts/journey.mjs booking agenda
```

| Journey | Feature file | Needs |
|---|---|---|
| `booking` | `features/participant-booking.md` | a stack that is up |
| `agenda` | `features/admin-agenda.md` | a booking of this run |
| `contact` | `features/booking-contact.md` | a booking of this run, no earlier request for it in the audit log |
| `manage` | `features/manage-booking.md` | a confirmed booking of this run |
| `admin-login` | `features/admin-login.md` | a stack that is up |

`all` runs `booking agenda contact manage agenda admin-login`: the second agenda sees the moved
booking cancelled. Journeys run in the order given, each in a fresh browser context, and the first
failure stops the run. It prints one `PASS` or `FAIL` line per step and a summary table. Exit
code 0: every journey passed; 1: a journey failed; 2: nothing could run (no stack, not this
run's stack, Playwright missing).

To explore what no journey covers yet, open the addresses above in any browser tool (the
built-in browser pane, Claude in Chrome, gstack `/browse`), with the handles of the feature
files, and read the participant's mail in Mailpit (`/api/v1/search?query=to:"<email>"`). Then
add the steps to the journey and the map (*Maintaining the map*), so the next run proves it
again; screenshots in chat are not evidence.

## Evidence

Everything stays in `.verify-openplanner/<run id>/` at the root of the checkout. The folder is
ignored by git, stays on the machine and survives `down`.

| Path | What it proves |
|---|---|
| `stack.env`, `compose.env` | ports, image id, commit and fingerprint built from, made-up participant values, state |
| `build.log`, `compose-up.log`, `seed.txt` | how the stack was built, started and seeded |
| `doctor.txt` | every doctor run, with its time |
| `<journey>/NN-<step>.png`, `.aria.yml` | the page after each step: screenshot (participant values masked) and ARIA snapshot (values replaced by `<first_name>`, `<last_name>`, `<email>`) |
| `<journey>/NN-<name>-email.json` | an email the step waited for: subject, recipient (blanked), links with the token blanked, attachments; never the body |
| `journey.jsonl` | one line per step: journey, step, observation, address, artifacts |
| `summary.md` | one table per invocation of `journey.mjs` |
| `state.json` | the run's booking as the journeys left it (reference, day, time, status) |
| `logs/app.log`, `logs/scan.txt` | written by `down`: the web and worker logs with tokens and participant values blanked, and how many lines held them |

Proof standards:

- Real user paths only: the booking is confirmed through the link in the email the worker sent,
  never a seeded token, a test-only endpoint or a database write.
- Capture the action and the state it led to: every step has its screenshot and snapshot, not
  just the last page.
- Prove a change from a second view: the email that follows it, and the admin agenda for the
  booking.
- Mailpit is the only stand-in, at the SMTP boundary that already isolates mail delivery.
- `logs/scan.txt` must count 0 lines with a booking-link token and 0 with a participant value;
  any other count is a privacy finding (CLAUDE.md, *Privacy rules*): report it, do not hide it.
  It also counts the error lines by `event.action`; on this stack `tls.certificate` and
  `public_address` are expected (plain http on 127.0.0.1, see the next section), any other is a
  finding to look into.
- A journey that fails for an unmet precondition is reported as not verified, with the reason;
  never as verified through another path.

## Cleanup

```bash
.claude/skills/verify-openplanner/scripts/stack.sh down
```

`down` writes `logs/`, removes the containers, network and volumes of project
`openplanner-verify` (and nothing else: it never stops containers by name), deletes the run's
`scratch/` (the rendered realms), clears `.verify-openplanner/current`, and then confirms that
the run folder still exists and how many files it holds. It also removes the run's image tag; the
build cache stays for the next build. Run `down` after a failed
`up` or journey too, before trying again. When no run is recorded but the project still has
containers (its `current` file was lost), `down` removes the containers, networks and volumes
that carry the project's compose label, and only those. Delete old run folders yourself when you
no longer need them.

## Helpers

| Helper | Invocation |
|---|---|
| `scripts/stack.sh` | `stack.sh up`, `stack.sh doctor`, `stack.sh down`, `stack.sh --help` |
| `scripts/stack.sh compose` | docker compose for this stack only, e.g. `stack.sh compose logs --tail 50 worker` or `stack.sh compose exec web python -m app.cli --help` |
| `scripts/journey.mjs` | `node .claude/skills/verify-openplanner/scripts/journey.mjs <journey>...`, `--help` |
| `scripts/stack-client.mjs` | imported by `journey.mjs` and by `role-manuals/scripts/`; never run on its own |
| `compose.verify.yml` | read by `stack.sh` only; its header says what differs from the development compose and why |

## Stand-in versus a real installation

The proof runs on a local Docker stack in development mode. These steps happen here and not on
the test host or in production, so they stay unverified until a run there covers them:

- `ENVIRONMENT=dev`: keys generated in the data volume instead of `secrets/secrets.env` or a
  secret store, `dev-only-` secrets accepted, plain http, no TLS proxy, no forwarded headers;
  the worker's certificate check and public-address round trip fail (one error line each,
  `tls.certificate` and `public_address`), because 127.0.0.1 inside a container is not the host;
- Keycloak in `start-dev` with the development realm, no second factor
  (`ADMIN_MFA_REQUIRED=false`), no organisation identity providers;
- Mailpit instead of an SMTP relay: no SPF, DKIM or DMARC, no delivery;
- no Webex and no contact center: no meeting is created and no work item is dispatched, so the
  trial call, join link, direct call and T−5 dispatch are not driven;
- the demo seed on an empty database instead of an organisation's data, and no upgrade from an
  older release;
- an image built from the checkout, unsigned and without `APP_VERSION`, instead of the signed
  release image.

## Maintaining the map

The map is the maintained source for what this skill proves. When a change alters a mapped path
(a route, a label or catalogue text, an email subject, the demo seed), update the feature file
and the texts in `scripts/labels.json` in the same PR, and run the journeys again. CI holds every
text there against the catalogue (`tests/test_skill_labels.py`), so a pull request that renames a
text the scripts use fails until its label is updated. The `role-manuals` skill drives more screens
with the same client and labels; `capture.mjs <workspace> --only <id>` proves one again.
A new user-facing flow gets its own feature file, a line in `features/README.md` and a journey.
If the seed lacks a precondition a journey needs, the journey fails and the reason goes under
that feature's *Gotchas*; never weaken a journey to make it pass.

The method follows the `create-verification-skill` generator of pstack (MIT, Copyright (c) 2026
Lauren Tan, https://github.com/cursor/plugins); none of its text is copied here.
