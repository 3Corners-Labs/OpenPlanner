---
name: role-manuals
description: Write and maintain manuals per role for an OpenPlanner installation, as a senior trainer and adoption specialist would: one for the beheerder (superuser and organisation admin), the planner (group admin), the medewerker (agent) and the people who book (participants). Task based, grounded in what is built, with walkthroughs on real screenshots captured from the verify-openplanner stack, produced from one Markdown source as Word, PDF and HTML, white-label. Also the adoption approach around them (steps, formats, communication and training plan, measurement). Use for "handleiding", "user guide", "training material", "adoption", "walkthrough with screenshots", "werkkaart", "FAQ for participants", or when manuals must be updated after a release.
---

# Role manuals (repo skill)

Name this skill when you use it (CLAUDE.md). It covers manuals per **role** and the adoption
approach around them. The built-in help per **screen** (`docs/manual/{nl,en}/`, ADR 0033) is a
different layer: it explains one screen; a role manual strings screens into the tasks of one
role. When a screen changes, its topic in `docs/manual/` changes first, and the role manuals
follow.

## Where the work lives

| What | Where | Why |
|---|---|---|
| Method, templates, scripts (this folder) | the repository; the scripts run from the checkout | the same for every organisation, fixed once for all of them; neutral (ADR 0115) |
| An organisation's manuals: its name for the service, its word for the people who book, what it does not want named | a **workspace outside the checkout**, passed to every script as its first argument | an organisation's words would break `tests/test_terminology.py`, and the repository names no client |
| Screenshots, evidence, built Word, PDF and HTML | the workspace (`img/`, `evidence/`, `build/`) | they are deliverables, not source |

Every script refuses a workspace that lies inside a checkout (a worktree's `../` still does), so
an organisation's screenshots and evidence never land in a repository.

Set up once, from the checkout:

```bash
(cd e2e && npm ci)                                            # Playwright, pinned there
(cd .claude/skills/role-manuals && npm ci --ignore-scripts)   # docx and marked, pinned here
W="$HOME/manuals-<organisation>"                               # outside the checkout
T=.claude/skills/role-manuals/templates
mkdir -p "$W"/sources
cp "$T"/style-guide.md "$T"/roles.md "$T"/variables.json "$W"/
cp "$T"/approach.md "$W"/sources/00-aanpak-adoptie.md
```

Then fill `variables.json` (see *Build*) and settle the open choices at the top of
`style-guide.md`: the organisation's word for the people who book, the form of address per role,
what is not named (`do_not_name`), and whether the product name may appear
(`hide_product_name`).

## Method

The approach is in `templates/approach.md`; adapt it per organisation, keep its five steps. In
short:

1. **Verkennen**: stakeholder map, audience analysis and change impact per role, key users,
   success criteria.
2. **Ontwerpen**: testable learning objectives per role, the form per role, the style guide, the
   shot list, the communication and training plan.
3. **Maken en toetsen**: write from the running software, capture real screenshots, fact check,
   review by key users, reader test (a fresh model first, then three to five people per role).
4. **Uitrollen**: train the trainer, training per role on a test installation, go live group by
   group with support on the floor and a question log.
5. **Borgen**: measure, feed questions back into the FAQ and manuals, update per release.

Models behind it: ADKAR per role (communication covers awareness and desire, the manuals
knowledge and ability, measurement reinforcement), Diátaxis for the form per role (learn, do,
look up, understand), minimalism for the writing (start with the action, one action per step,
help recover from errors) and 70-20-10 for the training mix.

### Roles

Take the roles from `app/domain/authorization.py` (rendered in `docs/authorization.md`); never
invent rights.

| Manual | Built-in role(s) | Form | Language level |
|---|---|---|---|
| Beheerder | organisation admin (functional) and superuser (application); a section for compliance and privacy (read-only) | look up and do: reference, setup checklist, admin calendar, walkthroughs | B2 |
| Planner | group admin, or a custom role copied from it | do and understand: work instructions, key terms, weekly rhythm, walkthroughs | B2 |
| Medewerker | agent | do: one-page werkkaart, one walkthrough, "wat als" | B1 |
| People who book | participants (no login) | learn and do: walkthrough, questions and answers | B1 |

`check.py` matches a manual to its roles by a word in its front matter `role` (beheerder,
planner, medewerker; anything else is the participants' manual); `roles` in `variables.json`
overrides that per word with role keys (`superuser`, `org_admin`, `group_admin`, `auditor`,
`agent`).

## Workflow

The scripts are in `scripts/` and run from the root of the checkout; `<ws>` is the workspace.

1. **Ground.** Read `docs/authorization.md`, `docs/admin-map.md`, the topics in
   `docs/manual/nl/` and the labels in `app/i18n/locales/nl.po`. Check `docs/adr-status.md`: what
   is only decided is not in a manual. The participant flows are mapped in
   `.claude/skills/verify-openplanner/features/`.
2. **Shot list.** `node .claude/skills/role-manuals/scripts/capture.mjs <ws> --list` writes
   `<ws>/shot-list.md` for the writers and `<ws>/evidence/shots.json` for the check, from the
   register of the story (`steps-*.mjs`). Writers use only those ids.
3. **Write.** One file per role in `<ws>/sources/`, from the skeletons in `roles.md`, bound to
   the style guide. Writing the four in parallel subagents works when each gets the style guide,
   the shot list, its sources and its role's rights; then read all four for one voice. Every
   walkthrough and task section starts with a marker on the line before its heading:

   ```text
   <!-- task: screen=group-staffing; permission=schedule.manage; shots=pl-02-bezetting,pl-03-tijdblok-toevoegen -->
   ```

   `screen` is the help topic of the screen (a file name in `docs/manual/nl/` without `.md`) or
   one of the participant pages (`participant:group-page`, `participant:booking-form`,
   `participant:confirm`, `participant:manage`, `participant:move`, `participant:cancel`,
   `participant:join`, `participant:trial`, `participant:email`); `permission` is a permission
   code or a participant action; `shots` are ids of the shot list. The markers make the checks
   below possible and are the contract for help in context (see *Context-driven help*).
4. **Capture.** Start a fresh stack with `verify-openplanner` (`stack.sh up`), then
   `node .../sample-data.mjs <ws>` and `node .../capture.mjs <ws>` (`--only <ids>` repeats some,
   `--log-only` rewrites the evidence log from the state, also after the stack is down). Both
   drive the stack through the client of `verify-openplanner` (`scripts/stack-client.mjs`) and
   the interface texts of its `scripts/labels.json`: they refuse an installation the run did not
   start, sign in as the development users and never keep a token or a password. The capture
   covers what `redact.js` lists, compares every screenshot with `img/previous/`
   (`evidence/diff.json`, `evidence/diff/`) and writes `evidence/log.md`. The wide overviews
   (agenda, week, rights matrix, audit log) are captured 1440 px wide so their tables are whole.
   What needs Webex Meetings is captured on a test installation that has it; until then the build
   shows "Schermafdruk volgt", and a shot taken without it gets the note from `without_webex` in
   its caption.
5. **Build.** `node .../build.mjs <ws>` is the one parse of the sources. It writes
   `build/<name>.docx`, `.html` and a tagged `.pdf` with bookmarks (`--no-pdf` skips the PDF),
   `build/manuals.json` (per manual its role, tasks, figures and bold spans, each with its line,
   and the hash of its source) and `build/help-index.json`. It builds nothing when a source breaks
   a rule: a task marker without its heading or inside a paragraph, a figure that is no
   `img/<id>.png`, or, after the variables are filled in, a term of `do_not_name` or the hidden
   product name in the text or a caption.
6. **Check.** `uv run python .../check.py <ws>` reads `build/manuals.json` (and refuses it when a
   source was added, removed or edited, or `variables.json` changed, since that build) and writes
   `evidence/check.md`; it exits with 1 on a blocking finding: a
   task whose screen, permission or shot does not exist, or whose role lacks the permission in
   `MATRIX`. It also lists the bold labels the build found that are no text of the software. Look at rendered pages
   (the Read tool shows PDF pages). Run a reader test with `anthropic-skills:doc-coauthoring`
   (its reader-testing stage) before people test.
7. **Maintain by machine.** On every release run `.claude/skills/role-manuals/scripts/update.sh
   <ws>` from the checkout. It first builds without PDF, which refuses a workspace inside a
   checkout and a source that breaks a rule in seconds, before any stack runs. It starts a fresh
   stack under a compose project of its own (`openplanner-manuals-<checksum of the checkout>`,
   ports 8020, 8100 and 8045; `VERIFY_PROJECT` and `VERIFY_*_PORT` override them), so it never
   meets or removes the stack of another session, and refuses while a stack of this checkout is
   up. It keeps the previous screenshots in `img/previous/`, loads the sample data, captures
   every id again and runs `check.py --since <commit of the last passing run> --fix`: a label the
   catalogue only renamed is written into the sources, inside bold
   labels only; a removed label, a changed help topic or a changed template is reported with the
   sections that use it. It records the new version for the title pages, moves the baseline commit
   only when the check passed, builds everything and stops the stack. A person reads
   `evidence/check.md`, looks at the differences and rewrites what the new version changed in the
   way people work.

## Build

`variables.json` in the workspace:

| Key | Meaning |
|---|---|
| `product`, `Product` | the service's name in running text and at the start of a sentence; a neutral working name until the organisation chooses one |
| `participants`, `Participants` | the organisation's word for the people who book |
| `help_desk` | who helps, as the reader should contact them |
| `software_version` | the version and commit the screenshots come from (`update.sh` writes it) |
| `commit` | the baseline of the next `check.py --since` (`update.sh` moves it after a passing check) |
| `accent_colour`, `font` | branding: six hex digits without `#`, a font family name |
| `hide_product_name` | `true` covers the product name and its mark in screenshots and blocks it in the text |
| `do_not_name` | terms that appear neither in the text nor in a screenshot (also inside compounds; a row, column or field whose label holds one is covered whole) |
| `extra_screen_texts` | bold texts the check accepts that are no catalogue text (language names, provider names); the text values of `variables.json` count as such too |
| `without_webex` | per shot id, the note its caption gets while it was taken without Webex Meetings |
| `roles` | optional: per word of a manual's `role`, the role keys it covers |

Any other text key can be written as `{{key}}` in the sources. Front matter keys: `title`,
`subtitle`, `role`, `version`, `date` (the real date), `software_version`. The Markdown subset
and the conventions for figures (`![alt](img/<id>.png "Bijschrift")`), callouts
(`> **Tip:** …`) and page breaks (`<!-- pagebreak -->`) are in `templates/style-guide.md` §5.
Links may only point to `https:`, `mailto:`, an anchor or a relative path. `build.mjs` follows
the `anthropic-skills:docx` guidance (docx-js, A4, dual table widths, numbering configs, styled
heading levels, alt text on images); the title page states how many screenshots come from the
running software and how many still follow.

## Screenshots

The manuals double as evidence that the software exists, so:

- Real user paths on a stack built from the checkout: book on the page, confirm through the
  emailed link, configure through the portal. Never the database, a seeded token or a test-only
  endpoint (the proof standards of `verify-openplanner`).
- Fictional people at example domains; never data from a test host or production.
- Development users only (`dev/keycloak/README.md`); their password is read at run time and
  never printed.
- Allowed edits: cropping, and grey boxes drawn on the page just before the capture, on the box
  the page reports, over what `redact.js` lists: the product name when hidden, the terms of
  `do_not_name`, version numbers. Switch off what a real setting can switch off first (the footer
  line on participant pages). Never replace a name with another, never a mock-up. Every capture
  and every box is in `evidence/log.md` (commit, time, role, address).
- Observations about one installation (a display fault, a failing probe) go into
  `evidence/notes.json` of the workspace (`{"<id>": ["…"]}`), not into the code; the log adds
  them to their capture.
- Dates are the real ones. Never put an earlier date on a manual, a screenshot or a log than the
  day it was made.
- **Sample data** fills agendas, the week, reports and the audit log: fictional participants book
  from the third working day after today on, confirm, move and cancel through the same real
  paths, and the planner moves and cancels a few; the worker rolls them up into the reports every
  15 minutes, before the capture reaches the report screen. Today and the two days of the story
  stay as the story needs them. Never dates before the software's first version and never a shifted clock: a
  screenshot shows what happened that day. A history over months needs a seed command in the
  product, which is a build step of its own.
- **Version numbers and commit ids** are covered on every screenshot, so screenshots do not age;
  the real software version and date stay on the title page and in the evidence log.

The shot list is generated from the register; a new screen gets a `screen(...)` entry (or a
`step(...)` when it needs actions first) in the `steps-*.mjs` file of its phase, with its id, its
title for the list, the user and how it is cut (`shots.mjs`: `Viewport`, `Full`, `Top`, `Region`,
`Email`).

## Context-driven help (design)

The manuals start outside the product; the design takes them inside, on the right screen for
the right role. `build.mjs` already writes `build/help-index.json`: per task its screen, its
permission, its shots, its manual, its title and its anchor. That is the contract a later build
step reads. The proposed decision is a fourth help layer ("Hoe doe ik …", the tasks of a screen
filtered by the matrix) on top of the three of ADR 0033, walkthroughs across screens with a
server-rendered step notice, help per participant step from the i18n catalogue (never a link
out), screenshots captured in the release lane, and tests that keep the markers true. It enters
`docs/adr/` together with its first build step (ADR 0119); until then a draft lives in the
workspace of the organisation that asked for it.

## Reused skills and tools

The skills this one leans on, and the plugins evaluated for manuals and adoption, are listed in
`docs/skills.md` (*Manuals, training and adoption*). The tools: `docx` and `marked` (MIT, pinned
in `package.json` here, installed with `npm ci --ignore-scripts`), Playwright from `e2e/` with an
installed Google Chrome for capture, comparison and PDF, and the checkout's own environment
(`uv run`) for `check.py`. Chrome does not start inside an agent's macOS sandbox: run the capture,
the build and `update.sh` outside it.
