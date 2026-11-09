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
   its caption. `freeze.sh` accepts only the shots of the capture it freezes, so such a shot
   needs a record in that capture's state before it can enter a frozen version (not built yet).
5. **Build.** `node .../build.mjs <ws>` is the one parse of the sources. It writes
   `build/<name>.docx`, `.html` and a tagged `.pdf` with bookmarks (`--no-pdf` skips the PDF),
   `build/manuals.json` (per manual its role, tasks, figures and bold spans, each with its line,
   and the hash of its source) and `build/help-index.json`. It builds nothing when a source breaks
   a rule: a task marker without its heading or inside a paragraph, a figure that is no
   `img/<id>.png`, or, after the variables are filled in, a term of `do_not_name` or the hidden
   product name in the text or a caption.
6. **Check.** `uv run python .../check.py <ws>` reads `build/manuals.json` (and refuses it when a
   source was added, removed or edited, or `variables.json` changed, since that build) and writes
   `evidence/check.md`, with its verdict in `evidence/check.json` (the number of blocking
   findings and the sha256 of every file it read); it exits with 1 on a blocking finding: a
   task whose screen, permission or shot does not exist, or whose role lacks the permission in
   `MATRIX`, and a right that the roles of a manual hold (the participant actions, for the
   participants' manual) but no task in any manual describes and `rights_without_task` gives no
   reason for, so a right the software adds never goes unnoticed. It also lists the bold labels
   the build found that are no text of the software. Look at rendered pages
   (the Read tool shows PDF pages). Run a reader test with `anthropic-skills:doc-coauthoring`
   (its reader-testing stage) before people test.
7. **Maintain by machine.** On every release run `.claude/skills/role-manuals/scripts/update.sh
   <ws>` from a checkout of its tag (*A version per release*). It first builds without PDF, which refuses a workspace inside a
   checkout and a source that breaks a rule in seconds, before any stack runs. It starts a fresh
   stack under a compose project of its own (`openplanner-manuals-<checksum of the checkout>`,
   ports 8020, 8100 and 8045; `VERIFY_PROJECT` and `VERIFY_*_PORT` override them), so it never
   meets or removes a stack of another compose project, and refuses while a stack of this
   checkout is up (one run per checkout at a time). It keeps the previous screenshots in `img/previous/`, loads the sample data, captures
   every id again and runs `check.py --since <commit of the last frozen version> --fix`: a label
   the catalogue only renamed is written into the sources, inside bold
   labels only; a removed label, a changed help topic or a changed template is reported with the
   sections that use it. It collects what the release changed per role
   (`release.py notes`, *Release notes per release*) and records the release the capture shows
   for the title pages (`release.py record`), builds everything and stops the stack. A person
   reads `evidence/check.md` and `evidence/release-notes.md`, looks at the differences and
   rewrites what the new version changed in the way people work. Both Python scripts run with
   the checkout's own Python (`uv run`).

## A version per release

The manuals describe the software of one release and do not change after it: one version per
minor or major release (`vX.Y.0`: 0.6.0, 0.7.0, … 1.0.0, 2.0.0), made from a checkout of its tag
and shipped with a document that says what is new in it. A patch release gets a version only when
its check reports a changed text, help topic or screen, or its release notes list a change a
reader can notice. Between releases the last version stands; a run on anything other than a tag
is a trial, never a version. The manuals' own version numbers (1.0, 2.0, …) count the versions,
not the software: the title page and `OVERZICHT.md` say which release each one shows.

1. **Check out the release** next to the main checkout, and install there once:
   `git worktree add ../openplanner-v0.7.0 v0.7.0`, then the two `npm ci` lines of the set-up.
2. **Run** `update.sh <ws>` from that checkout. Its check reports what changed since the last
   frozen version. It takes the release from the capture's own record (`evidence/state.json`:
   the commit and image fingerprint of its stack) and the tags (`release.py record`), never from
   `pyproject.toml`, which names the previous release until the next one is cut: `v0.7.0` gives
   "0.7.0" on the title pages, a release candidate "0.7.0-rc.3, testversie van 0.7.0", and a
   release before its candidate on the same commit. An image built from other files than the
   tag's (`stack.sh fingerprint` of `verify-openplanner`: what `.dockerignore` lets into the
   build) adds "met latere wijzigingen" and the commit: that capture is a trial, and
   `freeze.sh` refuses it.
3. **Read** `evidence/check.md`, rewrite what the release changed in the way people work, and
   settle every blocking finding. A right without a task gets one, or a line in
   `rights_without_task` that says why it has none.
4. **Write what is new** in `sources/90-wat-is-nieuw.md`, from `evidence/release-notes.md`
   (*Release notes per release*). Every point names its source, and the check blocks on a new
   feature that is neither named nor left out.
5. **Settle** the version. Set `version` and `date` in the front matter of every source, with the
   real dates: `version` starts with the version number (`2.0, vastgesteld op <date>`), `date`
   is the day of the capture. Write `evidence/afhechting.md`: what was reread and what stays
   open.
6. **Freeze** it: `.claude/skills/role-manuals/scripts/freeze.sh <ws>` from the same checkout.
   It records the release the capture shows again, collects the release notes again, builds
   Word, HTML and PDF, checks, and stops on a blocking finding. Then `release.py freeze` copies
   the version into
   `<ws>/versies/<capture date>_software-<release>_handleiding-<version>/`: build, sources,
   screenshots and evidence without the previous capture, `variables.json` and the workspace's
   own Markdown files but not `README.md`, a `LEESMIJ.md` from `templates/leesmij.md`, a
   `MANIFEST.sha256` over every file, read-only, and a zip with its own checksum. It builds the
   copy beside `versies/` and moves it into place only when it is whole. It refuses a trial
   capture, title pages that name another release, a version that exists, sources that do not
   all state the same version number, a missing `afhechting.md`, release notes of another range
   than the baseline to the capture, a screenshot in `img/` that
   this capture did not take (its shot failed, so the image is an earlier capture's: take it
   again, or remove it so the manual says it follows), and a check that blocked or read other
   files than there are now (`evidence/check.json`). Last, it sets `commit` to the release's
   commit, the baseline of the next version's check and release notes; nothing else moves it.
   A baseline is always the commit of a release tag, which every later release descends from.
   It then rewrites `versies/OVERZICHT.md`: every frozen version with its release, dates,
   number of changes and zip checksum, generated, never edited by hand.

   Anyone can check a frozen version later with `shasum -a 256 -c MANIFEST.sha256` in its folder.

## Release notes per release

Every version of the manuals comes with the answer to "what is new for me?", per role: the
document `sources/90-wat-is-nieuw.md`, built like the manuals (Word, PDF, HTML) and sent by the
communication plan when the release goes live. What it says is the writer's work; what it rests
on is machine work, so it is neither invented nor incomplete.

- **Input.** `release.py notes <ws>` (run by `update.sh` and `freeze.sh`) takes the commits on
  the main line from the baseline (the release of the last frozen version) to the commit of the
  capture, which is one commit per pull request. It writes `evidence/release-notes.md` for the
  writer and `evidence/release-notes.json` for the check and the freeze. Types `feat`, `fix`,
  `security` and `perf` are changes a reader can notice; `docs`, `test`, `chore`, `ci`, `build`
  and `refactor` are listed apart, as a count and a list. The project's own `CHANGELOG.md` text
  for the release comes along, as the highlights it wrote itself.
- **Sorting.** A change lands under a role when the help topic of a screen changed
  (`docs/manual/nl/<screen>.md`, which every admin change must update) and a task of that role
  uses the screen, or when a participant page or the widget changed. What shows neither signal
  is listed under *Nog te plaatsen*, never dropped. It is a proposal: the writer decides.
- **The document.** `release.py notes` puts the template (`templates/wat-is-nieuw.md`) in the
  sources when a reader can notice a change and there is no document yet; build again, then
  fill it in. Per role: *Nieuw*, *Anders*, *Wat moet je doen*; plus *Kleine verbeteringen*. Write
  what people notice, task first, in the language level of the role (B2 for beheerder and
  planner, B1 for medewerker and the people who book), never how it was built. Name a screen
  text exactly as the software shows it, in bold: the check holds it against the catalogue like
  any label.
- **Sources.** After each point, on a line of its own, an HTML comment readers never see:
  `<!-- bron: #644, #673 -->`. A change left out on purpose: `<!-- niet opgenomen: #583 reden
  -->`. A change without a pull request is named `c:` and its seven-character hash.
- **Gate.** `check.py` blocks on: a `> Invullen:` line of the template that is still there; a
  source number this release does not have (a point left over from the version before); a new
  feature (`feat`) that is neither named nor left out; and a missing document while a reader can
  notice a change. Other changes that are neither named nor left out are listed, not blocking.
  `freeze` refuses release notes that were not collected from the baseline to this capture, and
  a verdict that did not read them. Its first version has no baseline, so no release notes.
- **What the files hold.** `evidence/release-notes.*` quote the commit subjects as committed (in
  English, unedited, `security:` ones included); they are the repository's public history. The
  template assumes the four default roles; when an organisation's manuals use other role names,
  change the headings of the document to the roles of its sources, which are the headings
  `release-notes.md` uses.
- **Afterwards.** Record in `evidence/afhechting.md` what was left out and why in one line each,
  next to what was reread. The document and the notes go into the frozen version.

## Build

`variables.json` in the workspace:

| Key | Meaning |
|---|---|
| `product`, `Product` | the service's name in running text and at the start of a sentence; a neutral working name until the organisation chooses one |
| `participants`, `Participants` | the organisation's word for the people who book |
| `help_desk` | who helps, as the reader should contact them |
| `software_version` | the release the screenshots come from, from the capture's record and the tags (`release.py record` writes it; see *A version per release*) |
| `commit` | the commit of the last frozen version: the baseline of `check.py --since` (`release.py freeze` moves it) |
| `rights_without_task` | per right that a role of the manuals holds but no task describes, the reason why (a task of the supplier, a screen of `do_not_name`, part of another task); `check.py` blocks on a right with neither |
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
  `do_not_name`, version numbers, the year. Switch off what a real setting can switch off first (the footer
  line on participant pages). Never replace a name with another, never a mock-up. Every capture
  and every box is in `evidence/log.md` (commit, time, role, address).
- Observations about one installation (a display fault, a failing probe) go into
  `evidence/notes.json` of the workspace (`{"<id>": ["…"]}`), not into the code; the log adds
  them to their capture.
- Dates are the real ones. Never put an earlier date on a manual, a screenshot or a log than the
  day it was made, and never another year into a screenshot: its year is covered, not changed.
- **Sample data** fills agendas, the week, reports and the audit log: fictional participants book
  from the third working day after today on, confirm, move and cancel through the same real
  paths, and the planner moves and cancels a few; the worker rolls them up into the reports every
  15 minutes, before the capture reaches the report screen. Today and the two days of the story
  stay as the story needs them. Never dates before the software's first version and never a shifted clock: a
  screenshot shows what happened that day. A history over months needs a seed command in the
  product, which is a build step of its own.
- **Version numbers, commit ids and the year** (the capture's year, the one before and the five
  after it) are covered on every screenshot, so screenshots do not age; day, month and time
  stay, and the real software version and date stay on the title page and in the evidence log.

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
