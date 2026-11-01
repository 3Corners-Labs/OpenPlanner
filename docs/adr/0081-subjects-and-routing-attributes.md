# 0081. Subjects and routing attributes next to the group

Status: accepted (2026-10-31), requested by the product owner; not built. Extends ADR 0032
(provider-neutral contact center) and ADR 0043 (own queue); amends the privacy rule on outbound
payloads in `CLAUDE.md` and ADR 0032. Relates to ADR 0055 (custom form fields) and open point
O4 (agents in more than one group).

## Context

A group (`app/domain/scheduling.py::Group`) is three things at once: the agenda a participant
books in, the team that staffs it, and the one contact-center queue its bookings go to
(`wxcc_routing_value`, or the own queue of ADR 0043). The product owner noted on 2026-10-31
that the word "group" hides this: what the participant chooses is a *subject* (onderwerp), what
a staff member has is a *skill*, and where the work goes is a *queue*. Clients also want to
capture more than the subject, for example the participant's preferred language, and either
act on it in OpenPlanner or hand it to the routing of their contact center so the choice of
agent is made there.

Today nothing of that can be captured for routing. The privacy rule in `CLAUDE.md` says that
outbound payloads to a contact-center provider contain only the first name and the host join
URL, so even a non-personal code such as a language could not be sent. ADR 0055 lets a group
add a choice field to its form, but the answer is personal data by default and never leaves
the booking.

Renaming "group" now would touch the citizen URL `/g/{slug}`, the entitlement values of
ADR 0049, the i18n keys, the manual and a long list of ADRs, for a word that is right for two
of its three meanings. The decision is therefore to keep the group and put the missing layer
next to it, and to record now that subjects and routing attributes may be used, so that the
privacy rule does not have to be reopened when the layer is built.

## Decision

1. **The group stays.** It remains the agenda, the team and the default queue, and keeps its
   slug, entitlement value and settings. Nothing in this ADR renames it.
2. **Subjects per group.** An organisation admin may give a group a list of *subjects*: short
   labels (nl and en, through the text layer of ADR 0074) with a stable code
   (`[a-z0-9][a-z0-9-]{0,39}`). With one or more subjects the booking form asks the participant
   to choose one, as its first question; with none the form is as today. The chosen subject is
   stored on the booking as its code, is shown in the agenda and the own queue, and is sent to
   the contact center (point 5). A subject is not personal data: it is one of a fixed list
   that the organisation wrote.
3. **Routing attributes from a closed catalogue.** A group may switch on attributes from a
   catalogue that ships with OpenPlanner and holds only non-personal, enumerated values:
   `preferred_language` (the locales with a catalogue), `channel_preference` (`video`,
   `phone`) and later others added by ADR. Each attribute is asked on the form as a choice;
   its answer is stored as the code, never as free text. The catalogue is code, like the field
   type catalogue of ADR 0055; an organisation cannot add an attribute, only switch one on
   and map it (point 5). Free-text fields and the custom fields of ADR 0055 are never routing
   attributes.
4. **Own use.** A subject or attribute may narrow the agenda the participant sees: a subject
   may be bound to a staffing block (ADR 0072), so only blocks that offer it are shown. The own
   queue (ADR 0043) shows subject and attributes per row so an agent picks what they can
   handle. How a block declares what it offers is settled in the build step; it reuses the
   block's offer of ADR 0072 rather than a second mechanism.
5. **Mapping to the contact center.** Per group, the organisation admin maps each subject and
   each switched-on attribute to a *routing variable* of the contact center: a variable name
   and the value sent for each code (for Webex Contact Center a flow variable or a work-item
   attribute; the name is what the client's flow reads, ADR 0007 point 3). Unmapped subjects
   and attributes are not sent. The dispatcher (`ContactCenterDispatcher`, ADR 0032) receives
   the mapped variables as a plain `{name: value}` mapping of codes and sends them with the
   task; it never receives the labels, the form answers or any other field of the booking.
6. **Privacy rule amended.** The rule "outbound payloads contain only the first name and the
   host join URL" becomes: *the first name, the host join URL, and the routing variables of
   this ADR, which hold only codes from a list the organisation or OpenPlanner defined, never
   a free-text or personal-data value*. `CLAUDE.md`, ADR 0032 and `docs/dataflow.md` say so;
   a test asserts that the dispatched payload contains no other booking field and that every
   routing value is one of the catalogue's or the group's codes.
7. **Admin structure (ADR 0047).** Subjects and attributes are a sub-section *Onderwerpen en
   routing* / *Subjects and routing* on the group's *Settings* tab, next to the booking form
   link; the mapping to the contact center sits in the same sub-section, under the existing
   integration fields. No new section, top-level entry or tab.
8. **Agents in more than one group (O4)** stays open. This ADR gives the contact center the
   attributes to choose an agent across groups; it does not move staffing or capacity between
   groups.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `max_subjects_per_group` | C platform | 20 | 1–100 | `settings.platform.manage` | How many subjects a group may define; keeps the form a choice, not a search. |
| Subjects of a group (code, labels, block binding) | D organisation (per group) | none | code grammar of point 2, at most the platform maximum | `group.edit` | Data, not a registry setting; listed because an org admin owns it. |
| Switched-on attributes and the routing mapping of a group | D organisation (per group) | none | attributes from the catalogue only; variable name `[A-Za-z_][A-Za-z0-9_]{0,63}` | `group.edit` | Data; the variable names are what the client's flow reads. |

**Fixed on purpose:** the attribute catalogue and its values (adding one is an ADR, because
each one must be shown to be non-personal), that answers are stored and sent as codes only,
that free-text and ADR 0055 fields never route, and that unmapped values are not sent. Each is
a privacy rule, not a preference.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR, privacy rule in `CLAUDE.md` and `docs/dataflow.md` | #179 | merged |
| 1 | Subjects per group: domain, migration, form question, agenda and queue display | | planned |
| 2 | Attribute catalogue (`preferred_language`, `channel_preference`), form questions | | planned |
| 3 | Routing mapping per group and the dispatcher payload, with the payload test of point 6 | | planned |
| 4 | Subject bound to a staffing block (ADR 0072 offer) | | planned |

## Consequences

- **Privacy.** The outbound payload grows by a mapping of codes. `docs/dataflow.md` gets a
  row for subject and routing attributes (stored on the booking as codes; leave the system to
  the contact center as routing variables; purged with the booking). The DPIA input changes
  accordingly. `docs/threat-model.md` gets a row under *video meetings, work items and direct
  calls*: a crafted form value is refused unless it is one of the codes, so nothing typed by a
  participant reaches the contact center.
- **Docs.** `docs/HANDOVER.md` §3 gains the three words (onderwerp, skill, wachtrij) and what
  the group is; `docs/webhooks.md` documents the routing variables in the task payload; the
  manual topic `group-settings` gets the sub-section; `docs/widget.md` the new attribute of
  the embedded form if the subject can be preselected by the host page.
- **Machine API (ADR 0023).** Availability may later be asked per subject; that is an
  additive change to `/api/v1` and needs no new ADR unless it breaks the spec.
- **Not decided here:** skills of staff members in OpenPlanner itself. The contact center keeps
  skill-based routing; OpenPlanner only supplies the attributes. If a client without a contact
  center needs skills in the own queue, that is a new ADR.
