# 0039. Adjustable roles within fixed bounds, and a read-only auditor role

Status: accepted (2026-10-27), requested by the product owner. Amends ADR 0014 (where role
definitions live, how a role's reach is derived) and ADR 0031 (who holds `audit.view`). The
product owner settled decision points B1 to B8 on 2026-10-27 by taking every recommendation.
Point 6 superseded by ADR 0049 for the `entitlements` claim mode (the separate claim value is
used only in `legacy` and `shadow` mode).

## Context

The authorisation matrix (ADR 0014) is fixed in code: three roles, their permissions and their
reach are a Python dictionary. That gives strong guarantees. One source feeds routes, SQL
scopes, navigation and `docs/authorization.md`, and CI tests every role × route × own/other
group against a hand-written table. But to an administrator it looks fixed: a contact center
that wants, for example, a team lead who can edit opening hours but not texts has no way to set
that up. The page that shows the matrix also explains itself in terms of the build ("this table
comes straight from the code") rather than what an administrator can do.

The product owner wants the structure to be adjustable, or at least to feel adjustable within
clear bounds, with only the superuser allowed to change rights.

A second gap: government clients have people who must be able to check the system without
changing anything or seeing citizens' data. These are the data protection officer (FG), the
CISO and internal or external auditors (AVG, BIO). Today the only way to give them the audit
log is `org_admin`, which also grants write access and `bookings.view` with personal data.

## Decision

### Adjustable roles

1. **The building blocks stay in code.** The list of permissions, the level of each
   (`LEVELS`), and two new properties per permission are code, changed only by PR:
   - `locked`: only the superuser can ever hold it. These are `organisation.manage`,
     `holidays.manage`, `integrations.manage` (secrets, ADR 0008), `api_clients.manage`,
     `system.update` and the new `roles.manage`. Rule of thumb: a permission that affects
     more than one organisation, or that touches secrets, is locked.
   - `pii`: holding it shows citizens' personal data. Today this is only `bookings.view`.
     The role editor marks it, and the auditor role (below) can never hold it.
   - `requires`: permissions it depends on. For example `group.edit`, `schedule.manage`,
     `texts.manage`, `reminders.manage` and `closure.manage` require `group.view`, and
     `organisation.closure` requires `closure.manage`.
2. **Built-in roles stay in code and are read-only.** `superuser`, `org_admin`, `group_admin`
   and the new `auditor` keep their definitions in `MATRIX`, keep their hand-written
   expectation table, and serve as templates.
3. **Custom roles are data.** The superuser can create a role as a copy of `org_admin` or
   `group_admin`, give it a name (nl and en), and switch delegable permissions on or off. It is
   stored in a `roles` table with its permission set and a version number. Each custom role
   has a **reach** that is fixed when it is created:
   - `organisations`: scoped by the organisation claim (like `org_admin`);
   - `groups`: scoped by the group claim (like `group_admin`).
   A custom role can never reach everything; that stays the superuser's.
4. **The domain validates, the adapter stores.** `app/domain/authorization.py` gets a pure
   `validate_role(definition) -> list[ErrorCode]` that refuses:
   - a locked permission;
   - a permission above the reach (an organisation-level permission in a `groups` role);
   - a missing requirement;
   - a role without the permissions every admin holds (`dashboard.view`, `rights.view`,
     `matrix.view`).
   `decide` and `scope_for` take a `RoleCatalogue` (built-in plus custom roles), which the
   database adapter loads once per request. The domain stays free of SQLAlchemy.
5. **Reach replaces hard-coded role names.** `decide` and `scope_for` no longer test for
   `ORG_ADMIN` or `GROUP_ADMIN` by name. They read each role's reach:
   - `everything` for the superuser;
   - `organisations` for `org_admin`, `auditor` and organisation-reach custom roles;
   - `groups` for `group_admin` and group-reach custom roles.
   Behaviour of the existing roles does not change, and the existing expectation table proves
   it.
6. **Linking to the identity provider.** Built-in roles keep their claim values in env
   (`OIDC_ROLE_*`, ADR 0013). A custom role gets a claim value set by the superuser in the
   admin UI. A value that equals a built-in role's value, or another custom role's, is refused.
   Until PingFederate sends that value (O3, identity management), nobody holds the role.
   The editor says so.
7. **Effect and audit.** A change takes effect on the next request; nobody has to log in again,
   because the permission set is looked up each time, not stored in the session. Every create,
   change and delete writes an `admin` audit event (`role.created`, `role.updated`,
   `role.deleted`) at global level, with the permission codes added and removed as detail. The
   matrix page shows this change history, with version, time and actor subject, to everyone who
   holds `matrix.view`: role definitions are not secret, and seeing who changed them is the
   point. Deleting a role that is still linked to a claim value leaves its holders with no role
   (`no_role`).
8. **Documentation and the page.** `docs/authorization.md` stays generated from code. It shows
   the built-in roles, the `locked`, `pii` and `requires` properties and the reach rules, so
   reviewers see the bounds. The admin page shows the actual state: built-in roles, custom
   roles, a lock icon on locked permissions, a marker on PII permissions, and the change
   history. The page's introduction is rewritten for administrators. New keys in nl and en:

   - nl: "Overzicht van wat elke rol mag, per organisatie en per groep. Rechten met een slotje
     zijn vast. Alleen de superuser kan rollen aanpassen. Elke wijziging wordt vastgelegd."
   - en: "What each role may do, per organisation and per group. Permissions with a lock are
     fixed. Only the superuser can adjust roles. Every change is recorded."

### Auditor role

9. **A new built-in role, `auditor`** (label nl "Compliance en privacy (alleen lezen)", en
   "Compliance and privacy (read-only)"). It has organisation reach through the organisation
   claim and holds only reading permissions:
   - `dashboard.view`, `rights.view`, `matrix.view` (with the change history of item 7);
   - `audit.view`: admin and system events of the own organisation and its groups, exactly as
     an `org_admin` sees them (ADR 0031);
   - `group.view`: settings of the groups, without changing them;
   - `retention.view` (new, organisation level): per group, how many bookings still hold
     personal data past their purge moment, the `purge_pii` backlog, and when the nightly sweep
     last ran. Counts and times only, never a booking's content. Also granted to the superuser
     and `org_admin`.
10. **Never personal data, by test.** The auditor holds no `pii` permission and no write
    permission. A test asserts both on `MATRIX[Role.AUDITOR]`. In the audit log, a booking id
    links to the booking's timeline only for admins with `bookings.view` (ADR 0031), so the
    auditor sees the id but not the timeline.
11. **Built-in, not custom.** The auditor is in code rather than a custom role. Its guarantee
    (read-only, no personal data) is part of the privacy story for the DPIA and must not depend
    on how a superuser configures it. A superuser can still make a custom role with more read
    rights if a client asks for that.

## Consequences

- A contact center can shape roles to its own teams without a release. The bounds that matter
  for security and privacy stay in code and in CI.
- The expectation table of ADR 0014 now covers four built-in roles. Custom roles are covered by
  tests on the rules instead: a property-based test generates role definitions and asserts that
  any definition `validate_role` accepts holds no locked permission and nothing above its
  reach, and that `decide` never allows more than the definition.
- One migration adds `roles` (id, key, names, reach, claim value, version, permission set). It
  is reversible; the downgrade drops custom roles, and their holders fall back to no role.
- `audit.view` is held by `superuser`, `org_admin`, `group_admin` and now also `auditor`.
  `docs/authorization.md`, `docs/manual/{nl,en}/` (matrix page, new role editor),
  `docs/threat-model.md` and `docs/dataflow.md` are updated in the implementing PRs.
- New abuse cases, each with a test:
  - a non-superuser posts to the role editor;
  - a role definition smuggles in a locked permission or a permission above its reach;
  - a claim value collides with the superuser's value;
  - an auditor reaches a PII route.
- The superuser becomes more powerful: they can now hand out rights without a PR. This is
  mitigated by the locked list, by the audit history being visible to every admin and the
  auditor, and by ADR 0034 (audit events to an external log platform), not by a technical
  four-eyes check (see B5).

## Decision points (settled 2026-10-27)

The product owner took the recommendation on every point.

| # | Question | Decided |
|---|---|---|
| B1 | May the superuser change the built-in roles, or only make custom ones? | Only custom roles. Built-in roles stay read-only templates: a known, tested baseline whose meaning is the same for every client. |
| B2 | Are custom roles defined once for the whole installation, or per organisation? | One catalogue for the installation, maintained by the superuser. Per organisation can follow later without changing the rules. |
| B3 | Is the locked list in item 1 complete? | Yes, as listed. `branding.manage` stays superuser-only through the built-in roles and is reconsidered with O5. |
| B4 | Does the auditor also exist across all organisations? | No: own organisation only. A `platform_auditor` with `everything` reach follows only if O7 (hosting) says one party operates for several organisations. |
| B5 | Do rights changes need a second superuser's approval (four eyes)? | No. The audit history and the external log platform (ADR 0034) are the control. Revisit after the pentest (O10). |
| B6 | Should the auditor see a booking's timeline without personal data? | No, ids only for now. A PII-free timeline (`bookings.timeline_view`) is a later follow-up. |
| B7 | Name of the role in the portal. | "Compliance en privacy (alleen lezen)" / "Compliance and privacy (read-only)". |
| B8 | Order of delivery. | In steps, each its own PR: (1) the auditor role, reach per role, the `locked` and `pii` markers and the matrix page text; (2) `retention.view` and its page; (3) custom roles: migration, role editor, change history, manual. |
