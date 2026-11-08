---
name: role-manuals
description: Write and maintain manuals per role for an OpenPlanner installation, as a senior trainer and adoption specialist would: one for the beheerder (superuser and organisation admin), the planner (group admin), the medewerker (agent) and the people who book (participants). Task based, grounded in what is built, with walkthroughs on real screenshots captured from the verify-openplanner stack, produced from one Markdown source as Word, PDF and later HTML, white-label. Also the adoption approach around them (steps, formats, communication and training plan, measurement). Use for "handleiding", "user guide", "training material", "adoption", "walkthrough with screenshots", "werkkaart", "FAQ for participants", or when manuals must be updated after a release.
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
| Method, templates, scripts (this folder) | the repository | the same for every organisation; neutral (ADR 0115) |
| An organisation's manuals: its name for the service, its word for the people who book, what it does not want named | a **workspace outside the repository** | an organisation's words would break `tests/test_terminology.py`, and the repository names no client |
| Built Word, PDF and HTML files, screenshots, evidence log | the workspace (`build/`, `img/`, `bewijs/`) | they are deliverables, not source |

Set up a workspace once:

```bash
W=../<workspace>                          # outside the checkout
mkdir -p "$W"/{bron,img,build,bewijs,scripts}
T=.claude/skills/role-manuals/templates
cp "$T"/{redactiestatuut,opnamelijst,rollen}.md "$T"/variabelen.json "$W"/
cp "$T"/00-aanpak-adoptie.md "$W"/bron/
cp .claude/skills/role-manuals/scripts/* "$W"/scripts/
(cd "$W"/scripts && npm install --no-fund --no-audit docx@9 marked@15)
```

The scripts refuse to run from inside `.claude/skills/`, so an organisation's screenshots and
evidence never land in the repository.

Then fill `variabelen.json` (see *Build*) and settle the open choices at the top of
`redactiestatuut.md`: the organisation's word for the people who book, the form of address per
role, which integrations are shown and which are not named, and whether the product name may
appear.

## Method

The approach is in `templates/00-aanpak-adoptie.md`; adapt it per organisation, keep its five
steps. In short:

1. **Verkennen**: stakeholder map, audience analysis and change impact per role, key users,
   success criteria.
2. **Ontwerpen**: testable learning objectives per role, the form per role, the redactiestatuut,
   the shot list, the communication and training plan.
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

Take the roles from `docs/authorization.md`; never invent rights.

| Manual | Built-in role(s) | Form | Language level |
|---|---|---|---|
| Beheerder | organisation admin (functional) and superuser (application); a section for compliance and privacy (read-only) | look up and do: reference, setup checklist, admin calendar, walkthroughs | B2 |
| Planner | group admin, or a custom role copied from it | do and understand: work instructions, key terms, weekly rhythm, walkthroughs | B2 |
| Medewerker | agent | do: one-page werkkaart, one walkthrough, "wat als" | B1 |
| People who book | participants (no login) | learn and do: walkthrough, questions and answers | B1 |

## Workflow

1. **Ground.** Read `docs/authorization.md`, `docs/admin-map.md`, the topics in
   `docs/manual/nl/` and the labels in `app/i18n/locales/nl.po`. Check `docs/adr-status.md`: what
   is only decided is not in a manual. The participant flows are mapped in
   `.claude/skills/verify-openplanner/features/`.
2. **Write.** One file per role in `bron/`, from the skeletons in `templates/rollen.md`, bound
   to the redactiestatuut. Writing the four in parallel subagents works when each gets the statuut, the
   shot list, its sources and its role's rights; then read all four for one voice. Every
   walkthrough and task section starts with a marker on the line before its heading:

   ```text
   <!-- task: screen=group-staffing; permission=schedule.manage; shots=pl-02,pl-03 -->
   ```

   `screen` is the help topic of the screen (a file name in `docs/manual/nl/` without `.md`) or
   one of the participant pages (`participant:group-page`, `participant:booking-form`,
   `participant:confirm`, `participant:manage`, `participant:move`, `participant:cancel`,
   `participant:join`, `participant:trial`, `participant:email`); `permission` is a code from
   `docs/authorization.md`; `shots` are ids from the shot list. The markers make the checks
   below possible and are the contract for help in context (see *Context-driven help*).
3. **Capture.** Start the stack with `verify-openplanner` (`stack.sh up`), set
   `OPENPLANNER_CHECKOUT` to the checkout, load the sample data with
   `scripts/voorbeeldgegevens.py`, then run `scripts/capture.py` (`--list` shows the steps in
   story order, `--only <ids>` repeats some, `--log-only` rewrites the evidence log). The script
   checks it drives the stack `up` started, logs in as the development users, covers the product
   name, the contact-center terms listed in its docstring and version numbers, and writes
   `bewijs/bewijslog.md`. For an organisation that may name other things, or must not name
   others, adjust that list in the workspace copy; `check.py` reads `niet_noemen` for the text. The wide screens (agenda, week, rights matrix, audit log) are captured at
   1440 px so their tables are whole. What needs Webex Meetings or another integration is captured
   on a test installation that has it; until then the build shows "Schermafdruk volgt", and a shot
   taken without it gets the note from `opnames_zonder_webex` in its caption.
4. **Build.** `node scripts/build.mjs` writes `build/<name>.docx` and `build/<name>.html`;
   `python3 scripts/pdf.py` prints the HTML to tagged PDF with Chrome. Chrome does not start
   inside the Seatbelt sandbox: run `pdf.py` and `capture.py` outside it.
5. **Check.** `python3 scripts/check.py --repo <checkout>` writes `bewijs/controle.md` and exits
   with 1 on a blocking finding: a marker whose screen, permission or shot does not exist, a task
   whose role lacks the permission in the matrix, a term the organisation does not want named
   (`niet_noemen` in `variabelen.json`) or the product name. It also lists bold labels that are no
   text of the software. Render pages and look at them (`fitz` from PyMuPDF:
   `page.get_pixmap(dpi=60)`). Run a reader test with `anthropic-skills:doc-coauthoring` (its
   reader-testing stage) before people test.
6. **Maintain by machine.** On every release run `scripts/bijwerken.sh <checkout>`: it starts the
   stack, keeps the previous screenshots in `img/vorige/`, captures every id again, runs
   `check.py --sinds <commit of the last run> --herstel` (a label the catalogue only renamed is
   replaced in the sources; a removed label, a changed help topic or a changed template is
   reported with the sections that use it; every screenshot that differs by more than 0.2 % gets a
   picture of the difference in `bewijs/verschil/`), builds Word, HTML and PDF, records the new
   commit and version in `variabelen.json`, and stops the stack. A person reads the report, looks
   at the differences and rewrites what the new version changed in the way people work.

## Build

`variabelen.json` in the workspace:

| Key | Meaning |
|---|---|
| `product`, `Product` | the service's name in running text and at the start of a sentence; a neutral working name until the organisation chooses one |
| `helpdesk` | who helps, as the reader should contact them |
| `softwareversie` | the version and commit the screenshots come from |
| `accentkleur`, `lettertype` | branding: hex colour without `#`, font family |

Add a key for any other word that differs per organisation (such as the word for the people who
book) and write `{{key}}` in the sources. The Markdown subset, the front matter and the
conventions for figures (`![alt](img/<id>.png "Bijschrift")`), callouts (`> **Tip:** …`) and page
breaks (`<!-- pagina -->`) are in `templates/redactiestatuut.md` §5. `build.mjs` follows the
`anthropic-skills:docx` guidance (docx-js, A4, dual table widths, numbering configs, styled
heading levels, alt text on images); the title page states how many screenshots come from the
running software and how many still follow.

## Screenshots

The manuals double as evidence that the software exists, so:

- Real user paths on a stack built from the checkout: book on the page, confirm through the
  emailed link, configure through the portal. Never the database, a seeded token or a test-only
  endpoint (the proof standards of `verify-openplanner`).
- Fictional people at example domains; never data from a test host or production.
- Development users only (`dev/keycloak/README.md`); the script reads their password at run time
  and never prints it.
- Allowed edits: cropping, and covering the product name with a plain grey box when the
  organisation does not want it shown. Switch off what a real setting can switch off first (the
  footer line on participant pages). Never replace a name with another, never a mock-up. Log every
  capture and every box in `bewijs/bewijslog.md` (commit, time, role, address).
- Dates are the real ones. Never put an earlier date on a manual, a screenshot or a log than the
  day it was made.
- **Sample data** fills agendas, the queue, reports and the audit log: `scripts/voorbeeldgegevens.py`
  lets fictional participants book, confirm, move and cancel through the same real paths, and a
  planner move and cancel a few, all around the day of capture; then `python -m app.cli
  usage-backfill` in the web container brings the reports up to date. Never dates before the
  software's first version and never a shifted clock: a screenshot shows what happened that day.
  A history over months needs a seed command in the product, which is a build step of its own.
- **Version numbers and commit ids** are covered on every screenshot, so screenshots do not age;
  the real software version and date stay on the title page and in the evidence log.

`templates/opnamelijst.md` is the shot list: one id per screenshot with role and state; the
writers use only those ids, and `capture.py` has one function per id.

## Context-driven help (design)

The manuals start outside the product; the design takes them inside, on the right screen for
the right role. `build.mjs` already writes `build/hulpindex.json`: per task its screen, its
permission, its role's manual, its title and its anchor. That is the contract a later build step
reads. `templates/adr-role-tasks-in-context.md` is the proposed decision: a fourth help layer
("Hoe doe ik …", the tasks of a screen filtered by the matrix) on top of the three of ADR 0033,
walkthroughs across screens with a server-rendered step notice, help per participant step from
the i18n catalogue (never a link out), screenshots captured in the release lane, and tests that
keep the markers true. It enters the repository only together with its first build step
(ADR 0119); choose its number when that pull request opens.

## Reused skills

| Step | Skill | Use |
|---|---|---|
| Screenshots | `verify-openplanner` (repo) | the stack, users, mail and the proof standards |
| Word | `anthropic-skills:docx` | docx-js conventions; checks in its *Verify the output* |
| Reader test | `anthropic-skills:doc-coauthoring` | its third stage: a reader without context answers the role's tasks from the manual |
| Flows as pictures | `diagram` (gstack) | a role's process as Mermaid, SVG and PNG |
| After a release | `/document-release` (gstack) | lists documentation debt per change; then step 6 above |
| Usability of the pages | `ux-heuristics` | find friction that the FAQ or the texts must cover |

Evaluated in the plugin directory, not installed (install per need): Customer Support
`kb-article` (FAQ and help articles for participants), Operations `process-doc` (work
instructions with an exceptions table), Product Management `stakeholder-update` (rollout updates
per audience), Human Resources `onboarding` (first week of a new staff member). The open skills
directory had nothing for plain language, instructional design or change management; this skill
covers that.

## Tools

- `docx` and `marked` from npm (both MIT), installed in the workspace, never in the repository.
- Python with `playwright` and Google Chrome for capture and PDF, PyMuPDF to render pages,
  Pillow for the grey boxes.
