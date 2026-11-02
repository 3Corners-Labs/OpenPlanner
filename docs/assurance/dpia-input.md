# Assurance checklist: DPIA input and processors

For the data controller (verwerkingsverantwoordelijke), its data protection officer or privacy
adviser, and the processors. OpenPlanner supplies facts about the processing; whether a DPIA is
required, what it concludes, and which agreements exist are the controller's to establish. This
file collects what OpenPlanner can give as input and the questions that remain open, in a form
that can be filled in. How to use this file: `docs/assurance/README.md`. Requirement texts are
the OWASP ASVS 5.0.0 (CC BY-SA 4.0) for the one ASVS item; the project controls are our own.

Nothing here is a DPIA, a legal assessment or a signed agreement.

## The four open items

| ID | Asks for | How to show it | Owner | Reference |
|---|---|---|---|---|
| V14.2.3 | Defined sensitive data does not go to untrusted parties such as trackers | (a) Processor agreements for every recipient in the processor templates below; (b) the statement that the public pages and the widget load nothing from other origins, with a browser network log of a booking made in a test installation. The automated browser test for (b) is work package WP-C3 and is not built yet | Controller and suppliers | `docs/dataflow.md` *Outbound connections and the hop to web*; `docs/widget.md` for the CSP |
| PI-06 | Copies that leave the installation (mail, SMS, Webex, WxCC, team calendar) fall under demonstrable agreements | One filled-in processor block below per recipient, with the agreement reference | Controller and processors | The processor template below; the local purge does not reach these copies (`docs/dataflow.md` *Residual data after a purge*) |
| PI-07 | Staff and audit data have their own legal basis and term | The controller's written basis and purpose for staff data, access, term, rights of data subjects and the deletion procedure; the retention rows for `staff`, `staff_memberships`, `audit_events` and `admin_sessions` in the matrix, accepted or changed | Controller | `docs/dataflow.md` *Retention matrix*; ADR 0046 step 2 (PR #402), ADR 0040, ADR 0098; the setting `AUDIT_RETENTION_DAYS` in `docs/settings.md` |
| PI-10 | A DPIA assessment and the roles of controller and processors are established | The controller's decision whether a DPIA is required and its outcome; the decisions on exceptions below; the signed processor roles | Controller with its data protection officer or privacy adviser | This file; `docs/dataflow.md`; `docs/threat-model.md`; `docs/HANDOVER.md` section 7 (Dutch, stakeholder document) |

## Facts to put in the DPIA

### The retention matrix

The matrix of every table that holds personal columns, with what is held, for how long, who
deletes it and what a restore does, is in `docs/dataflow.md`, section *Retention matrix* (ADR
0102 point 14). It is not copied here, so it cannot drift. The same file holds the data flows
per channel (*Outgoing email*, *SMS reminders*, *Video meetings and WxCC work items*), the
*Residual data after a purge*, and *Shared by all organisations of an installation*.

Wording rule for the DPIA and the privacy statement: the purge **deletes fields from the
appointment**. It does not make the booking anonymous; the row, its times and its reference stay
until the row is merged into a daily count and deleted (ADR 0102 point 12, build step 4; its
state is in the *Build status* table of that ADR).

### Decisions BP-1 to BP-4

These were put to the product owner after the ASVS 5.0.0 audit of 1 November 2026. Where an ADR
or the threat model records the decision, that text is the source and is the one to quote.

| Decision | What was decided | Where it is recorded | State |
|---|---|---|---|
| BP-1 Maximum term for an open emergency closure | Every suspended booking has a purge moment: the earlier of the end of the closure plus 30 days and the original slot end plus `closure_max_retention_days` (class D, default 90, bounds 30 to 180, cannot be switched off). Organisation administrators are told in advance; the participant's pages state the date | ADR 0102 points 1 to 6; PR #394 | Built (ADR 0102 step 1) |
| BP-2 Back-ups | The updater's copy is a rollback copy for 24 hours, on an encrypted volume. Disaster recovery belongs to the hosting party and must be encrypted, have a fixed term, named access, a destruction record and a yearly restore test. The restore procedure ends with `purge-overdue` | ADR 0102 points 7 to 10 | Procedure in the runbook (*Backup and restore*); worker pruning and System status age are step 3 (see the *Build status* of ADR 0102); the hosting party's disaster-recovery agreement is open (O7) and is a row in `docs/assurance/hosting.md` |
| BP-3 What remains after a purge | The purge also empties the provider links and the event message and randomises the management token hash. The booking row is deleted after `booking_row_retention_days` (class C, default 730, bounds 365 to 3650) | ADR 0102 points 11 to 13; PR #395 for the purge; step 4 for the row | Purge built; row deletion is step 4 (see the *Build status* of ADR 0102) |
| BP-4 Several organisations on one installation | One installation per data controller. Several organisations of the same controller on one installation stay allowed; organisations of different controllers never share an installation | `docs/threat-model.md` *Shared trust boundaries* (the decision and the six criteria for an installation of its own) | Decided; the shared items are accepted within one controller only |

The audit analysis also proposed a fifth decision (BP-5): the client names an owner for each
group of external evidence and makes that a go-live condition. It is not recorded in an ADR; it
is the reason for the sign-off tables in this folder.

Open for the controller to establish, not decided by OpenPlanner: the legal basis for staff data
(PI-07), whether a DPIA is required and its conclusion (PI-10), and the retention of mail and
SMS at the providers.

## Processor template

Fill in one block per recipient. The lines marked *known* come from `docs/dataflow.md` and are
pre-filled so the processor only completes the rest. Copy the block for a recipient that is not
listed; adding a recipient to OpenPlanner adds its row to the *Outbound connections* table in
`docs/dataflow.md` in the same pull request (CLAUDE.md), and a block here.

```
Recipient:
Role (processor / sub-processor / own system of the controller):
Agreement (title, date, reference):

Purpose:
Data (known, from the dataflow):
Term at the recipient:
Deletion mechanism (how a deletion request or the end of the term reaches the copy):
Region and transfers outside the EU/EEA:
Sub-processors:
Security evidence offered (certificate, report):
Contact for requests:
Filled in by / date:
```

### Recipients known to OpenPlanner

Each row gives the known part. The term, deletion mechanism, region and sub-processors are
asked of the recipient.

| Recipient | Purpose | Data (known) | Source of the known part |
|---|---|---|---|
| Mail provider of the organisation (its own SMTP), or the platform SMTP fallback | Confirmation, reminders, change and closure mail | First name, email address, date, time, team, management link; for a confirmation request only email, date, time, team and the confirm link | `docs/dataflow.md` *Outgoing email* |
| SMS provider (today Webex Connect, Cisco) and the mobile operators | SMS reminders and change notices | Phone number, first name, date, time, team, management link | `docs/dataflow.md` *SMS reminders* |
| Webex Meetings (Cisco) | Video meeting for a group appointment | Group name, times, time zone, host account; the participant enters their own details when joining | `docs/dataflow.md` *Video meetings and WxCC work items*; `docs/assurance/cisco.md` |
| Webex Contact Center (Cisco) | Task for the agents | Booking reference, first name, start time, group name, meeting link, routing variables | The same section; ADR 0081 |
| Team mailbox (calendar invite, own queue only) | Invite for the team that serves the group | First name, booking reference, group, start and end, queue link; never the meeting link, email address or phone number | `docs/dataflow.md` *Own queue*; it stays in the team's mailbox outside the OpenPlanner purge |
| Identity provider of the client (PingFederate) or the bundled Keycloak | Login of staff | Staff identifiers and claims; the bundled Keycloak holds staff accounts and password hashes in its volume | `docs/dataflow.md`; ADR 0090; `docs/assurance/idp.md` |
| Log platform of the client | Operational, audit, system and security events | Ids, slugs and codes; no personal fields (masking processor), caller addresses only as a keyed pseudonym | `docs/logging.md`; ADR 0034, 0053, 0085 |
| ACME certificate authority (Let's Encrypt, if used) | Public HTTPS certificate | Domain name, the operator's email address, public key | `docs/dataflow.md` *HTTPS certificate* |
| Hosting party | Running the installation, volumes, snapshots and back-ups | Everything on the host, by role | `docs/assurance/hosting.md`; `docs/threat-model.md` *Shared trust boundaries* |

## Sign-off

| Owner | Name | Date | Evidence reference |
|---|---|---|---|
| Controller (verwerkingsverantwoordelijke) | | | |
| Data protection officer or privacy adviser | | | |
| Processors (one line per signed block) | | | |
