# 0049. Role and scope paired in one claim value

Status: accepted (2026-10-28), requested by the product owner; E1–E4 settled as recommended;
partly built (steps 0–3 in one PR, 2026-10-31; see *Build status*). Amends ADR 0013 (point 3:
which claims carry rights, and that their values are slugs), ADR 0014 (how `decide` and
`scope_for` combine roles and scopes), ADR 0039 (points 3 and 6: how a custom role is linked
to the identity provider) and ADR 0046 (a membership carries a role). Relates to open point O4
(agents in multiple groups) and O3 (IdP claim names).

## Context

The admin portal takes its rights from three separate ID-token claims (ADR 0013 point 3):

```
roles         = [openplanner-org-admin, openplanner-auditor]
organisations = [gemeente-a, gemeente-b]
groups        = [...]
```

`app/adapters/oidc.py::principal_from_claims` turns them into a `Principal` with three
independent sets, and `app/domain/authorization.py::decide` combines them as a cross product:
a role grants its permissions in *every* organisation of the organisation claim. Someone who is
`org_admin` in organisation A and `auditor` in organisation B therefore becomes `org_admin` in B
as well. That is a privilege escalation: write access, integrations and `bookings.view` with
citizens' personal data in an organisation where the person should only read, without personal
data (ADR 0039 point 10).

Today it is latent, because everyone works for one organisation. It becomes real as soon as:

- people work for several organisations or several groups with different roles (O4);
- custom roles exist (ADR 0039), so one person easily holds two roles of the same reach;
- the staff directory (ADR 0046) records memberships and shows them in an access review: it
  would show the escalated rights as if they were intended. ADR 0046 already names this gap and
  defers it to this ADR.

There is a second, smaller problem. The claim values are organisation and group **slugs**. A
slug is part of the citizen's URL (`/g/{slug}`) and may change: since PR #56 a new group's slug
is derived from its name, and renaming a group or organisation is an ordinary admin task. A
renamed slug silently takes access away from everyone whose claim still holds the old value, or
gives it to whoever's claim happens to hold the new one.

Third, unknown values are ignored without a trace (`principal_from_claims`: "unknown role values
are ignored"). A typo in PingFederate gives someone no rights and nobody sees why.

The product owner asked how to make sure claims map correctly onto organisation-level and
group-level access, and what can be configured and seen in the portal.

Constraints: the identity provider stays the only place where rights are granted (ADR 0046
U1); no login bypass (ADR 0042 §8, decided 2026-10-28); the matrix stays the only source of
rights (ADR 0014); the domain stays free of frameworks.

## Decision

### Entitlement claim

1. **One claim carries role and scope together.** Each value of the entitlement claim (default
   name `openplanner_entitlements`, a multi-valued claim or a space-separated string) is one
   *entitlement*:

   | Role reach (ADR 0039) | Value | Example |
   |---|---|---|
   | everything | `<prefix>:superuser` | `openplanner:superuser` |
   | organisations | `<prefix>:<role>:<org>` | `openplanner:org_admin:gem-a` |
   | groups | `<prefix>:<role>:<org>/<group>` | `openplanner:group_admin:gem-a/burgerzaken` |

   - `<prefix>` is a setting (default `openplanner`), so an acceptance installation that shares
     the production IdP uses `openplanner-acc` and production entitlements never work there, or
     the other way round.
   - `<role>` is the role's **key**: `superuser`, `org_admin`, `group_admin`, `auditor`, or the
     key of a custom role (ADR 0039 point 3). Keys are immutable once a role is created.
   - `<org>` and `<group>` are the **IdP values** of point 4, never slugs.
   - A group entitlement names its organisation too. The pair is checked: the group must belong
     to that organisation.

2. **Strict grammar, exact match.** A value is recognised only if it matches
   `^<prefix>:(superuser|[a-z][a-z0-9_]{1,39}:[a-z0-9][a-z0-9-]{0,62}(/[a-z0-9][a-z0-9-]{0,62})?)$`
   exactly. No case folding, no trimming beyond the claim's own separators, no wildcards
   (`gem-a/*` is not a value). The reach of the role decides which form is valid:

   - the superuser takes no scope (`openplanner:superuser:gem-a` is refused);
   - an organisation-reach role takes an organisation, never a group;
   - a group-reach role takes an organisation and a group, never an organisation alone.

   Giving an organisation-reach role (such as `auditor`) for a single group is not possible in
   this ADR; see decision point E2.

3. **Parsing is domain code.** `app/domain/entitlements.py` gets a pure
   `parse_entitlements(values, prefix, directory) -> ParsedEntitlements` that returns the
   recognised `Grant`s and the refused values, each with a reason code. `directory` is a small
   read-only view (role keys with their reach, organisation and group IdP values with their
   ids) that the database adapter loads once per request, like the role catalogue of ADR 0039.
   The OIDC adapter only extracts the claim; it no longer decides what a value means.

### IdP values per organisation and group

4. **A separate, stable value.** Organisations and groups get a column `idp_key`, unique
   (organisation: across the installation; group: within its organisation), with the grammar of
   point 2. It is filled from the slug when the organisation or group is created and from then
   on is independent of it: renaming, or changing the slug, never changes the IdP value.
   - The **organisation** value is set by the superuser on the organisation page
     (`organisation.manage`, already locked to the superuser).
   - The **group** value is set by the org admin of that organisation through a new permission
     `idp_mapping.manage` (organisation level, held by `superuser` and `org_admin`, not by
     `group_admin`: a group admin changing their own group's value can only lock colleagues out).
   - A change requires a fresh login (ADR 0015), is refused if it collides, and writes an
     `admin` audit event (`organisation.idp_key_changed`, `group.idp_key_changed`) with old and
     new value. The form warns, with the number of people the directory (ADR 0046) last saw
     holding an entitlement with the old value, that they lose access until PingFederate sends
     the new value.
   - The migration fills `idp_key` from the current slug, so existing values in PingFederate
     stay valid when the installation switches to entitlements.

### Unrecognised values: refused and visible

5. **Refused, never guessed.** A value that starts with `<prefix>:` but is not recognised gives
   no right at all. Reason codes: `malformed`, `unknown_role`, `unknown_organisation`,
   `unknown_group`, `group_not_in_organisation`, `scope_not_allowed_for_role` (point 2),
   `deleted` (the organisation or group was deleted). Values without the prefix are ignored
   without a reason, because PingFederate may fill the claim from a shared attribute (for
   example all AD groups of a person).
6. **More than a limit means no login.** A claim with more than
   `oidc_entitlements_max_values` values (with or without prefix) refuses the login with
   `too_many_entitlements` and an audit event. Fail closed: a truncated list could drop a
   restriction as easily as a right.
7. **Audited.** A login with at least one refused value writes an `admin` audit event
   `auth.entitlement_refused` with the refused values and reason codes as detail. The values
   hold only prefixes, role keys and IdP values, never personal data. The event is written at
   the level of the organisation the value names, when that organisation exists; otherwise at
   global level, which only the superuser sees.
8. **Shown where people look.**
   - The staff detail page (ADR 0046 point 5) gets a block *Niet herkende rechten* / *Rights not
     recognised* with each refused value, its reason, and when it was last received. A viewer
     sees a refused value when it names an organisation inside their scope; values naming an
     unknown organisation are shown to the superuser only.
   - The staff list (ADR 0046 point 4) gets a filter "has unrecognised rights".
   - The person's own *My rights* page lists their refused values with the reason, so they can
     tell the IdP team what to fix.
   - A login whose values are *all* refused fails as today with `no_role`; the error page says
     "your account has rights for OpenPlanner that this installation does not recognise" and
     names no value.

### Domain model

9. **`Principal` holds grants, not three sets.**

   ```python
   @dataclass(frozen=True)
   class Grant:
       role: str  # role key, built-in or custom
       organisation: str | None  # organisation slug; None for the superuser
       group: str | None  # group slug; only for group-reach roles


   @dataclass(frozen=True)
   class Principal:
       subject: str
       display_name: str = field(repr=False)
       grants: frozenset[Grant]
   ```

   The session cookie stores the recognised entitlement *values* (not slugs). Each request
   resolves them to `Grant`s with the directory of point 3, so a slug rename or a deleted group
   takes effect on the next request and a session never holds a stale slug.

10. **`decide` looks at one grant at a time.** A permission is allowed on a target when one
    single grant both holds the permission (through its role) and covers the target:
    superuser covers everything; an organisation grant covers its organisation and that
    organisation's groups; a group grant covers that group of that organisation, at group level
    only. Global permissions need a grant whose role holds them. There is no step that combines
    a role from one grant with a scope from another.
11. **`scope_for` is the union of covering grants.** For a permission, the `Scope` holds the
    organisations of organisation grants whose role holds the permission, and the groups of
    group grants whose role holds it. `Scope.groups` becomes a set of `(organisation, group)`
    pairs, so the SQL adapter filters on both, not on a group slug alone. `Scope.contains` and
    the adapter's filter change with it; the `Scope` shape stays otherwise the same, so the
    callers of `require_permission` do not change.
12. **Membership carries a role.** ADR 0046's `staff_memberships` row becomes
    `(staff_id, role_key, organisation_id, group_id NULL)`: one row per grant, by id, not by
    slug. `staff.membership_changed` lists the added and removed entitlements. The staff list
    shows "org_admin in Gemeente A, auditor in Gemeente B" rather than two unrelated lists.
    Scoping the staff list (ADR 0046 point 4) uses these rows: a viewer sees a person when at
    least one of that person's memberships falls inside the viewer's scope for `staff.view`.

### Migration from the three claims

13. **A deployment switch, `OIDC_CLAIM_MODE`, with three values.**
    - `legacy` (the default in the release that ships this ADR): rights come from the three
      claims as today, with the safety net of point 14. The entitlement claim is ignored.
    - `shadow`: rights still come from the three claims, but every login also parses the
      entitlement claim and compares the grants. A difference writes an `admin` audit event
      `auth.entitlement_mismatch` (both sets as detail, codes and IdP values only) and is shown
      on the staff detail page and, as a count, on the system status screen (ADR 0042 §11). The
      IdP team uses this to prove PingFederate is right before the switch.
    - `entitlements`: rights come only from the entitlement claim. The role, organisation and
      group claims are ignored.

    The class is B because switching back towards `legacy` weakens a protection, which the
    portal may never do (ADR 0042 §7). The rollback is the same switch: set `legacy` and
    restart. A later release makes `entitlements` the default; the release after that removes
    `legacy` and `shadow` in a new ADR.
14. **Safety net in `legacy` mode.** Until the switch, a login whose three claims are
    ambiguous is refused with `ambiguous_claims` and audited: that is, two or more roles of the
    same reach (counting custom roles) while the claim that scopes that reach has more than one
    value. This is exactly the case where the cross product hands out a right nobody gave.
    Unambiguous logins (one org-reach role, any number of organisations) work as before.
    The error page tells the person to ask for entitlements; the runbook tells the IdP team how.
15. **Custom roles in `entitlements` mode** are linked through their key (point 1). The
    separate claim value of ADR 0039 point 6 is used only in `legacy` and `shadow` mode and
    disappears with them. The role editor shows the entitlement form of the role next to it.
16. **`app.cli oidc-check`** (ADR 0042 §10) gains `--claims FILE`: it parses a JSON file of
    sample claims with the current mode and directory and prints each recognised grant and each
    refused value with its reason, so the IdP team can test an attribute contract without
    logging in.

### Page "Koppeling identiteitsprovider"

17. **New read-only page `/admin/idp-mapping`**, title *Koppeling identiteitsprovider* /
    *Identity provider mapping*, permission `idp_mapping.view` (organisation level; held by
    `superuser`, `org_admin` and `auditor`, because the list is an input for the access review).
    It shows, within the viewer's scope:
    - the claim mode, the entitlement claim name and the prefix;
    - per organisation: name, slug, IdP value, and the exact entitlement value per
      organisation-reach role (built-in and custom);
    - per group: name, slug, IdP value, and the exact entitlement value per group-reach role;
    - for the superuser only: `<prefix>:superuser`;
    - per value: how many people the directory last saw holding it, and when, so dead values in
      PingFederate stand out;
    - the refused values received in the last `idp_mapping_refused_window_days` within scope,
      grouped by reason.

    The values are shown as plain text in `<code>`, selectable without JavaScript (ADR 0016). A
    download of the same list as a text file (one value per line, with a comment line per
    organisation and group) is offered for the IdP team; it is audited
    (`idp_mapping.exported`). Editing an IdP value happens on the organisation and group pages
    (point 4), not here, so the page has no write action.
18. **Text.** All labels via `app/i18n` with nl and en, for example:
    - nl: "Deze waarden moet de identiteitsprovider meesturen in de claim
      {claim}. Eén waarde per rol per organisatie of groep."
    - en: "The identity provider must send these values in the claim {claim}. One value per
      role per organisation or group."

### Permissions (ADR 0014 / 0039)

| Permission | Level | Locked | superuser | org_admin | group_admin | auditor |
|---|---|---|---|---|---|---|
| `idp_mapping.view` | organisation | no | ✓ | ✓ | – | ✓ |
| `idp_mapping.manage` | organisation | no | ✓ | ✓ | – | – |

`idp_mapping.manage` requires `idp_mapping.view`. Neither is a `pii` permission. The
organisation IdP value stays under `organisation.manage` (locked).

### Abuse cases, each with a test

| # | Abuse | Expected | Test level |
|---|---|---|---|
| A1 | **A role from organisation B leaks into A.** Entitlements `org_admin:gem-a` and `auditor:gem-b`. | `decide(bookings.view, gem-b)` and every write permission in `gem-b` are denied; `scope_for(bookings.view)` holds only `gem-a`; `scope_for(audit.view)` holds both. | domain, plus the matrix test per route |
| A2 | Same in reverse for groups: `group_admin:gem-a/g1` and a custom read-only group role on `gem-a/g2`. | Write permissions only in `g1`. | domain |
| A3 | Ambiguous legacy claims (`roles=[org_admin, auditor]`, `organisations=[gem-a, gem-b]`). | Login refused, `ambiguous_claims` audited. | adapter + HTTP |
| A4 | Group entitlement with a foreign organisation: `group_admin:gem-b/<group of gem-a>`. | Refused, `group_not_in_organisation`. | domain |
| A5 | Scope smuggled onto the superuser: `superuser:gem-a`, or a custom role with key `superuser`. | Refused (`scope_not_allowed_for_role`); the key is refused by ADR 0039's validation. | domain |
| A6 | Reach mismatch: `org_admin:gem-a/g1`, `group_admin:gem-a`. | Both refused. | domain |
| A7 | Case, whitespace or wildcard tricks: `OpenPlanner:ORG_ADMIN:gem-a`, `openplanner:org_admin:gem-a/*`, `openplanner:org_admin:gem-a ` with a trailing control character. | Refused or ignored; never a grant. | domain (property test over generated strings: anything not matching the grammar yields no grant) |
| A8 | Production entitlement presented to acceptance (other prefix). | Ignored; no grant. | domain |
| A9 | Slug rename (PR #56) or a new group whose slug equals an old IdP value. | Access unchanged for holders of the IdP value; nobody gains access through the new slug. | HTTP |
| A10 | IdP value hijack: an org admin of `gem-a` sets a group IdP value equal to one in `gem-b`, or a group admin changes their own group's value. | Uniqueness holds per organisation and every entitlement names its organisation, so no cross-organisation match; the group admin is denied `idp_mapping.manage`. | HTTP |
| A11 | Claim flood: 5 000 values. | Login refused, `too_many_entitlements`. | adapter |
| A12 | Open session after the IdP value of its group changes, or after the group is deleted. | Next request resolves no grant for it; denied. | HTTP |
| A13 | `shadow` mode with a richer entitlement claim than the legacy claims. | Rights follow `legacy` only; never the union. | adapter |
| A14 | A refused value naming an unknown organisation shown to an org admin. | Not shown; superuser only. | HTTP |

These are added to `docs/threat-model.md` under *admin portal login and authorisation*, with
the abuse-case list kept in step with the tests.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `OIDC_CLAIM_MODE` | B deployment | `legacy` | `legacy`, `shadow`, `entitlements` | env | Moving back to `legacy` weakens a protection, so not in the portal. Default changes to `entitlements` in a later release. |
| `oidc_entitlement_claim` | C platform | `openplanner_entitlements` | claim name, 1–64 chars | `settings.platform.manage` | Same rules as the other claim names (ADR 0042 §8): dry run on the superuser's own fresh login, lockable from env, resettable from the CLI. |
| `oidc_entitlement_prefix` | C platform | `openplanner` | `[a-z][a-z0-9-]{1,30}` | `settings.platform.manage` | Separates installations that share one IdP. Dry run as above. |
| `oidc_entitlements_max_values` | C platform | 200 | 20–1000 | `settings.platform.manage` | Values in the claim above which the login is refused. |
| `idp_mapping_refused_window_days` | C platform | 30 | 7–365 | `settings.platform.manage` | How far back the mapping page lists refused values. |
| Organisation IdP value (`organisations.idp_key`) | C platform (per organisation) | the slug at creation | grammar of point 2, unique | `organisation.manage` (locked) | Data, not a registry setting; listed here because it is configuration a superuser owns. |
| Group IdP value (`groups.idp_key`) | D organisation (per group) | the slug at creation | grammar of point 2, unique within the organisation | `idp_mapping.manage` | Data, not a registry setting; owned by the org admin. |

**Fixed on purpose:** the entitlement grammar and separators (`:` and `/`), exact
case-sensitive matching, that the superuser takes no scope, that a role's reach decides the
form of its value, that a group entitlement must name its organisation, that unrecognised
values are refused rather than repaired, that a claim over the limit fails closed, and the
legacy safety net of point 14. Each of these is a security rule, not a preference, and a
setting for any of them could only make it weaker.

## Consequences

- **Security.** The cross-product escalation disappears in `entitlements` mode and is refused
  at login in `legacy` mode. Access no longer depends on a slug that changes with a name.
  Mistakes in PingFederate become visible instead of silently giving no rights.
- **Code.** `app/domain/entitlements.py` (new, pure), `Principal`, `Grant`, `decide`,
  `scope_for`, `Scope.groups` as pairs, `permissions_for` (*My rights* shows rights per grant),
  `principal_from_claims` split into claim extraction (adapter) and parsing (domain). The
  expectation table of ADR 0014 gets rows for a principal with mixed grants across two
  organisations and two groups; the property test of ADR 0039 extends to "`decide` never allows
  a permission that no single grant gives".
- **Migration.** One Alembic migration adds `idp_key` to `organisations` and `groups` (filled
  from the slug, unique indexes) and changes `staff_memberships` to carry `role_key` and ids
  (filled from current rows; legacy rows get the cross product, which is what those people
  hold today, so the directory shows it truthfully). Reversible: the downgrade drops the
  column and folds memberships back. If ADR 0046's migration has not merged yet, the membership
  shape is taken into that PR instead.
- **SCIM (ADR 0046 point 12).** SCIM group objects map to entitlement values, not to separate
  role and scope values. `<prefix>:superuser` can never be set through SCIM, as ADR 0046 already
  states for the superuser value.
- **`docs/threat-model.md`**: abuse cases A1–A14 under the login and authorisation flow; the
  entitlement claim as a new trust boundary input.
- **`docs/dataflow.md`**: the entitlement claim from PingFederate into the session and into
  `staff_memberships` and `audit_events`. It holds role keys and IdP values linked to an
  employee's subject; no citizen data.
- **`docs/runbook.md`**: a section *PingFederate attribute contract* with the claim name, that
  it must be multi-valued in the ID token, how to build values from directory groups (one
  directory group per entitlement is simplest), how to test with `oidc-check --claims`, and the
  switch procedure: `legacy` → `shadow` → no `auth.entitlement_mismatch` events for a full
  working week → `entitlements`; rollback by setting `legacy`; and, before the
  safety net ships, how to find admins whose legacy claims are ambiguous.
- **`docs/authorization.md`** (generated): the two new permissions and the grant rule of
  points 10–11.
- **Manual `docs/manual/{nl,en}/`**: new topic `idp-mapping`; updates to `organisation`
  (IdP value), `group` (IdP value), `staff` (unrecognised rights) and `my-rights`.
- **Keycloak realm (development)**: protocol mapper for the entitlement claim, and test users
  for A1 (org admin in one organisation, auditor in another) and for a refused value, so CI and
  Playwright walk these cases.
- **ADR 0013 point 3** ("claim values are slugs") and **ADR 0039 point 6** (custom-role claim
  value) are superseded for `entitlements` mode; both stay valid for `legacy` until it is
  removed.
- **Cost.** The IdP team must maintain one value per role per organisation or group instead of
  three lists. That is more values, but each one is exactly one grant, which is what an access
  review asks for.
- **Before the safety net is released** (E3): the IdP team checks whether any current admin
  holds two roles of the same reach while the claim that scopes that reach has more than one
  organisation or group. Such a person is refused at login (`ambiguous_claims`) until
  PingFederate sends entitlements for them or their roles are split. The runbook explains the
  check and both remedies.
- **Delivery.** (0) the `legacy` safety net of point 14 on its own, with test A3 (E3); (1)
  domain: `Grant`, parsing, `decide`/`scope_for`, tests A1, A2, A4–A8, A11, A13; (2) `idp_key` columns and forms, `shadow` mode, audit, staff detail block,
  A9, A10, A12, A14; (3) the mapping page, `oidc-check --claims`, runbook and manual.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | Legacy safety net (point 14, `ambiguous_claims`), test A3 | #179 | in PR |
| 1 | Domain: `Grant`, `Principal` with grants, `parse_entitlements`, `decide`/`scope_for` per grant, `Scope.groups` as pairs, resolution per request (`LoginService`); tests A1, A2, A4–A8, A11, A13 | #179 | in PR |
| 2 | `idp_key` on organisations and groups (migration 0076) with the forms, `OIDC_CLAIM_MODE` with `shadow`, refused values on the staff record and page, the staff filter, *My rights*, audit events; tests A9, A10, A12, A14 | #179 | in PR |
| 3 | Page *Koppeling identiteitsprovider* with the download, `oidc-check --claims`, runbook, manual, Keycloak realm users | #179 | in PR |
| 4 | Count of `auth.entitlement_mismatch` on the system status screen; memberships by id instead of slug (point 12); the `deleted` reason code (point 5); `entitlements` as the default and removal of `legacy`/`shadow` (point 13, E4) | | follow-up |

Deviations recorded while building: the role segment of the grammar (point 2) also allows a
dot, because a custom role's key is `custom.<id>`; `staff_memberships` carry the role key next
to the slugs (not ids), since the record is refreshed at every login and is never a source of
rights; a login with more than 60 recognised values is refused (`too_many_entitlements`, a
fixed bound of the 4 kB session cookie); the `deleted` reason is not distinguished from
`unknown_*`, because a deleted organisation or group leaves no trace to tell them apart.

## Decision points (settled 2026-10-28)

The product owner took the recommendation on every point.

| # | Question | Decided |
|---|---|---|
| E1 | Role segment: the role's key (`org_admin`) or its configured claim value (`openplanner-org-admin`)? | The key: stable, readable, one per role; the claim values only exist for the legacy claims. |
| E2 | May an organisation-reach role (for example `auditor`) be given for a single group? | Not now. It needs a rule for which organisation-level permissions apply; reconsider when a client asks. |
| E3 | Should the legacy safety net (point 14) ship before the rest, as a separate small PR? | Yes: it closes the escalation for existing installations right away and has no IdP dependency. |
| E4 | When does `entitlements` become the default? | In the first release after PingFederate sends the claim in production and `shadow` has run clean for a week. |
