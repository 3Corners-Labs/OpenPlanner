# NNNN. Role tasks and walkthroughs in the built-in help, shown in context

Status: proposed (2026-11-08), requested by the product owner. Extends ADR 0033. Not built.

<!--
Draft kept outside the repository until it can merge with its first build step (ADR 0119).
Choose NNNN when the pull request opens: check origin/main and the open pull requests first.
-->

## Context

ADR 0033 gives every admin screen three layers of help: a hint, an (i) per input and a link to
the screen's topic. That explains a screen. It does not tell a role how to do its work across
screens: how a planner solves a shortage, how a beheerder sets up a group, what a medewerker
does when a call cannot go ahead.

Manuals per role now exist outside the repository, for a first organisation, written with the
`role-manuals` skill: one each for the beheerder, the planner and the medewerker, and one for
the people who book. They are task based, with walkthroughs on screenshots of the running
software, and double as evidence that the software exists. Each task carries a marker,

```text
<!-- task: screen=group-staffing; permission=schedule.manage; shots=pl-02,pl-03 -->
```

that ties it to a help topic, to the permission it needs and to its screenshots. A checker
compares the manuals with the catalogue (`nl.po`), the matrix (`docs/authorization.md`) and the
help topics, reports what a new version changed, replaces renamed labels, and compares new
screenshots with the previous ones.

The product owner wants this guidance in the product in the end, on the right portal and
participant pages, chosen by context, and screenshots and guidance that update by machine.

Constraints: one home per concern (ADR 0005, 0047); the matrix is the only source of rights
(ADR 0014); pages are complete without JavaScript (ADR 0118); participants get help in the page
and never a link out (ADR 0033 point 4); `nl` and `en` (ADR 0004); a neutral repository
(ADR 0115); organisation words through organisation texts (ADR 0009); only what is built is
described (ADR 0119).

## Decision

1. **A fourth layer: tasks for your role.** A manual topic may hold task sections, marked like
   input sections: `<!-- task: <key>; permission=<code>; shots=<ids> -->` on the line before the
   heading. `app/web/help.py` parses them next to the input markers. On a screen with a topic,
   the help panel lists, under "Hoe doe ik …", the tasks of that topic whose permission the admin
   holds (the matrix decides); each opens its section in the in-app manual. No new permission.
2. **Walkthroughs across screens.** A walkthrough is a topic in
   `docs/manual/{nl,en}/walkthroughs/` whose steps carry `<!-- step: screen=<topic> -->`. The
   manual shows it as an ordered list with a link per step to the step's screen, resolved
   through the navigation register. On that screen a server-rendered notice ("Stap 2 van 5: …",
   previous, next, stop) shows while `?walkthrough=<slug>&step=<n>` is in the address; both are
   checked against the walkthrough's steps, and anything else is ignored. No modal, no script.
3. **Participants: help in the page.** Each participant step (group page, form, confirmation,
   management page, move, cancel, join, trial call) may show a disclosure "Hulp bij deze stap".
   Its text is an i18n key (`participant.help.<step>`), overridable per organisation and group
   like every participant text, with allow-listed placeholders only. A short questions-and-answers
   block per group comes from organisation texts. Nothing links out.
4. **One source, also outside the product.** The same Markdown builds the role manuals as Word,
   PDF and HTML outside the application (repo skill `role-manuals`), with an organisation's words
   as variables. In the product, those words come from organisation data: the service name from
   the organisation's branding (open point O5), the help desk from the organisation's contact
   block, the word for the people who book from the organisation's text overrides.
5. **Screenshots by machine.** Screenshots are not committed. A job in the release lane starts
   the verify stack, captures every shot id through real user paths with fictional data,
   compares the set with the previous release and attaches it, with the built manuals, to the
   release, like the SBOM. The image ships the set for the walkthrough pages; alt texts come from
   the manual.
6. **Kept current by tests** (extends ADR 0033 point 5). Tests fail when a task marker names a
   permission that does not exist, or a screen without a topic; when no role that can open the
   screen holds the task's permission; when a walkthrough step names an unknown screen; when a
   shot id is not in the shot list; when a bold label in a task is not a catalogue text and not
   on the allow list; when `nl` and `en` differ in task keys.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `help_tasks_enabled` | C platform | true | on, off | `settings.platform.manage` | Shows "Hoe doe ik …" on screens; off leaves the manual itself in place |
| `participant_step_help_enabled` | D organisation | true | on, off | `settings.organisation.manage` | Shows "Hulp bij deze stap" on the participant pages |

**Fixed on purpose:** the marker syntax (the tests read it), and that participant help never
links out (ADR 0033).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | | planned |
| 1 | Task markers parsed; "Hoe doe ik …" on the group's staffing screen and the queue; the tests of point 6 | | planned |
| 2 | Walkthroughs with the step notice | | planned |
| 3 | Participant step help and the questions-and-answers block | | planned |
| 4 | Screenshots captured in the release lane and attached to the release | | planned |
| 5 | The role manuals built as PDF and attached to the release | | planned |

## Consequences

- `docs/manual/{nl,en}/` gains task sections and walkthroughs; the per-screen topics stay.
- The organisation's own editions (its words, branding, what it does not want named) stay
  outside the repository and are built by the skill.
- Threat model: the walkthrough parameters are validated against known steps; no new data;
  screenshots hold fictional data only.
- Accessibility: the step notice is an `aside` with a heading, not a dialog; focus stays where
  it is; the disclosure is `<details>`.
- The release lane takes a few minutes longer for the capture job.
