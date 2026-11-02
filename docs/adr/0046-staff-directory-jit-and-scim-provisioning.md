# 0046. Staff directory: just-in-time records from OIDC, optional SCIM 2.0 provisioning

Status: accepted (2026-10-28), requested by the product owner; U1–U4 settled as recommended. Amends ADR 0013 (claims are
also stored, not only held in the session) and HANDOVER B8 ("no user table for admins").
Builds on ADR 0014, 0031 and 0039. Amended on 2026-11-01 (re-evaluation): §8 and §10 (ending
a person's sessions) move to ADR 0098 §3 and §9b, the single home for admin sessions, once 0098
is accepted; 0098 uses this ADR's `staff.manage` at organisation level, not a new permission.
For the bundled identity provider only, ADR 0095 amends U1: there the portal may also create
staff and grant roles, under the same `staff.manage`. Partly built; see `docs/adr-status.md` for what is built.

## Context

Administrators want an overview of the people who use the admin portal: who they are, which
role(s) they hold, which organisation(s) and group(s) they belong to, when they last logged
in and out, and the ability to act on that list (MACD: move, add, change, delete).

Today the portal knows almost nothing about its users. Rights come from the ID-token claims
at login and live only in the signed session cookie for 8 hours (ADR 0013). The only table is
`admin_profiles` (subject, display name, last login), kept for the audit log. Logins and
logouts are audited as events (`admin.login`, `admin.logout`).

That has three gaps:

1. **No overview.** Nobody can answer "who has access to organisation X" without asking the
   identity-provider team. An access review (BIO 9.2.5, AVG art. 32) needs exactly that list.
2. **Changes in the IdP land late.** A role or membership removed in PingFederate takes effect
   at the next login; an open session keeps its old rights for up to 8 hours.
3. **People who leave never disappear.** Someone who never logs in again stays unknown to the
   portal; there is no signal "this account is dormant".

The identity provider stays the source of truth for *who someone is* and *what they may do*
(ADR 0013, 0039: built-in and custom roles are linked to claim values). This ADR does not
introduce local passwords or a second place to grant rights.

## Decision

### Staff records (just-in-time)

1. **A `staff` table** replaces `admin_profiles`: a record number (used in links instead of
   the subject), subject, display name, roles, `first_seen_at`, `last_login_at`,
   `last_logout_at` and `last_seen_at`; `staff_memberships` holds the organisations and
   groups those roles reach (the same contexts as the login audit events). Step 2 adds
   `status` and `sessions_valid_after`, step 3 `source`. No email address: the name is
   enough to recognise a colleague, and it keeps staff personal data to a minimum.
2. **Upsert at every login.** The callback writes the claims to the record. When roles,
   organisations or groups differ from the stored ones, it writes an `admin` audit event
   `staff.membership_changed` with the added and removed values as detail (codes and slugs
   only). The first login writes `staff.created`.
3. **Last seen.** Authenticated requests update `last_seen_at` at most once per
   `staff_last_seen_interval_seconds`; the session cookie remembers when it was last
   written, so there is neither a read nor a write on the other requests.

### Overview

4. **New page `/admin/staff`** with permission `staff.view` (group level, like `audit.view`,
   so a group admin's scope holds their groups). A row shows
   name, roles (built-in and custom, with their labels), organisations, groups, last login,
   last logout, last seen, source and status. Filters: organisation, group, role, status,
   "dormant" (no login for `staff_dormant_after_days`). The listing takes the `Scope` like
   every admin query: an `org_admin` or `auditor` sees staff who share at least one of their
   organisations; a `group_admin` sees staff of their groups; superuser sees everyone.
   Superusers are listed only to superusers.
5. **Detail page** per person: the same data plus their audit trail (logins, membership
   changes, actions) from `audit_events`, limited to the viewer's scope.
6. **Export** of the listing as CSV for an access review, same permission, audited
   (`staff.exported`). Not in step 1.

### MACD

The IdP decides membership and roles. The portal therefore implements MACD as follows:

| | Where | What the portal does |
|---|---|---|
| **Add** | IdP (or SCIM push) | Record appears at first login (JIT) or when SCIM creates it. |
| **Move** | IdP | New organisations/groups take effect at next login, or immediately with SCIM. Audited as `staff.membership_changed`. |
| **Change** | IdP | Same, for roles. |
| **Delete** | IdP, plus portal | IdP removal → `deprovisioned` (SCIM) or dormant (JIT). Portal can suspend and erase, see 7–9. |

7. **Suspend** (`staff.manage`, organisation level): blocks portal access for that subject
   regardless of the claims, and ends their sessions (point 10). A suspended person can be
   reinstated. An admin cannot suspend themselves, nor someone holding a role with more reach
   than their own (an `org_admin` cannot suspend a superuser); `decide` gets a rule for this.
8. **End sessions** (`staff.manage`): sets `sessions_valid_after = now`, so open sessions
   end at their next request. Also used after a role change in the IdP when it must take
   effect now.
9. **Erase** (superuser only, `staff.erase`, locked): removes name and email from a record that
   is suspended or deprovisioned. The subject stays, so the audit log still resolves to
   "erased person". Erasure also happens automatically after `staff_retention_days` without
   a login.
10. **Server-side session check.** The session cookie gains the login time it already holds;
    each admin request compares it with the record's `sessions_valid_after` and status. This
    is one indexed read per request, alongside the role catalogue already loaded (ADR 0039).

### SCIM 2.0 (optional, second step)

11. **Inbound SCIM 2.0** (RFC 7643/7644) at `/scim/v2/Users` and `/scim/v2/Groups`, so
    PingFederate's outbound provisioning (or Entra ID) can push changes the moment they
    happen. Supported: create, replace, patch (`active`, `displayName`, `emails`, group
    membership), delete, filter `userName eq` and `externalId eq`. Not supported: bulk,
    sort, `/Me`, password attributes.
12. **What SCIM sets.** Group objects in SCIM map to claim values: an organisation slug, a
    group slug or a role claim value (ADR 0039 point 6), with the same mapping as the OIDC
    claims. `active=false` or `DELETE` sets status `deprovisioned` and ends sessions
    immediately. At login the ID-token claims still win for the session; a mismatch with the
    SCIM state is audited (`staff.claims_mismatch`) and the stricter of the two applies.
13. **Authentication.** A bearer token per IdP connection, created by the superuser
    (`scim.manage`, locked), shown once, stored as hash (like API clients, ADR 0023),
    rotatable, with an optional source-IP allow-list. Rate limited. Every request is an
    audit event with source `system`.
14. **Kill switch.** `SCIM_ENABLED` (env) switches the endpoint off entirely; off by default.

### Permissions (ADR 0014 / 0039)

| Permission | Level | Locked | superuser | org_admin | group_admin | auditor |
|---|---|---|---|---|---|---|
| `staff.view` | group | no | ✓ | ✓ | ✓ (own groups) | ✓ |
| `staff.manage` | organisation | no | ✓ | ✓ | – | – |
| `staff.erase` | global | yes | ✓ | – | – | – |
| `scim.manage` | global | yes | ✓ | – | – | – |

`staff.manage` requires `staff.view`. None of these is a `pii` permission in the ADR 0039
sense (citizens' data); staff names are personal data of employees and are covered by
points 9 and the retention setting.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `staff_last_seen_interval_seconds` | C platform | 300 | 60–3600 | `settings.platform.manage` | Write throttle for `last_seen_at`. |
| `staff_dormant_after_days` | D organisation | 90 | 14–365 | `settings.organisation.manage` | Marks a person dormant in the list. |
| `staff_retention_days` | C platform | 365 | 90–1825 | `settings.platform.manage` | Erase name and email after this long without a login. |
| `SCIM_ENABLED` | B deployment | false | — | env | Kill switch for an inbound endpoint. |
| `scim_rate_limit_per_minute` | C platform | 120 | 30–1200 | `settings.platform.manage` | Per token. |
| `scim_allowed_sources` | C platform | empty (any) | CIDR list | `scim.manage` | Optional IP allow-list. |

**Fixed on purpose:** the SCIM schema URNs, the token length and hash (as ADR 0023), and the
rule that the stricter of claims and SCIM state applies.

## Consequences

- Migration: `staff` table (from `admin_profiles`, data carried over), `scim_tokens`, and
  `scim_group_mappings`. Reversible; the downgrade folds `staff` back into `admin_profiles`.
- HANDOVER B8 changes in substance: there is now a record per admin, but still no local
  credentials and no local grants. The handover document is updated by the product owner.
- `docs/dataflow.md`, `docs/threat-model.md` (SCIM endpoint: token theft, mass deprovisioning,
  privilege escalation by mapping a group to the superuser value; the superuser claim value
  can never be set through SCIM), `docs/authorization.md`, `docs/manual/{nl,en}/staff`,
  `docs/runbook.md` (PingFederate SCIM provisioner) and `docs/api.md` are updated in the
  implementing PRs.
- New abuse cases with tests: an `org_admin` suspends a superuser or someone outside their
  organisations; a suspended subject keeps working with an old cookie; a SCIM request without
  or with a revoked token; a SCIM group mapped to the superuser value.
- Memberships are derived as the login audit does today: organisation claims for roles that
  reach organisations, group claims for roles that reach groups. Because roles and scopes are
  separate claims, someone who is `org_admin` in one organisation and `auditor` in another
  becomes `org_admin` in both. The directory makes this visible but does not fix it; pairing
  role and scope in one claim value is a separate decision (a separate ADR, still to be written).
- Delivery in three PRs: (1) staff table, JIT upsert, overview and detail; (2) suspend, end
  sessions, erase, retention job; (3) SCIM.

## Decision points (settled 2026-10-28)

| # | Question | Decided |
|---|---|---|
| U1 | Is the IdP the only place to grant roles and memberships (portal: view, suspend, end sessions, erase)? | Yes. A second grant path breaks ADR 0013/0039 and the access review. |
| U2 | Does "staff" mean admin-portal users only, or also contact-center agents (who never log in here)? | Admin-portal users only; agents live in WxCC. |
| U3 | May a `group_admin` see the staff of their own groups? | Yes, read-only. |
| U4 | Build SCIM now or after the JIT overview? | After: PRs 1–2 first, SCIM when the PingFederate provisioner is confirmed (O3). |
