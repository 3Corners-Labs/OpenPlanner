# 0014. The authorisation matrix is the single source of truth for every layer

Status: accepted (2026-10-23). Amended by ADR 0031 (audit level), ADR 0039 (adjustable roles,
auditor) and ADR 0049 (role and scope in one claim). §1 lists three roles; the matrix now has
five (`auditor`, ADR 0039; `agent`, ADR 0043).

## Context

Admins act on organisations and groups they are responsible for (HANDOVER §6). The product
owner requires authorisation to be at the heart of the design: at database, reporting, logging
and UX level, not as checks scattered over routes.

## Decision

1. **One matrix in the domain** (`app/domain/authorization.py`): roles (`superuser`,
   `org_admin`, `group_admin`) × permissions (`group.view`, `schedule.manage`, …). Each
   permission declares the level it applies to (global, organisation, group). Nothing else
   grants rights.
2. **One decision function**, `decide(principal, permission, target)`. It returns allowed or
   denied with a reason code, and it is pure and fully tested. A superuser is a scope that matches
   everything, not a bypass.
3. **Database level.** `scope_for(principal, permission)` turns the matrix into a `Scope`
   (all, organisation slugs, group slugs). Every admin query takes a `Scope` and the SQL
   adapter applies it in the `WHERE` clause in one place. A test fails if a listing query in the
   `Transaction` port has no `scope` parameter.
4. **HTTP level.** Every admin route declares exactly one permission and how its target is found
   (`require_permission(permission, target=…)`). A test fails if an admin route has no
   declared permission, or if it is missing from the expectation table of the matrix test.
5. **Logging and audit.** Every decision is logged as `authz.decision` (actor subject,
   permission, target, outcome, reason; never personal data). Denials, logins and every
   change are written to `audit_events`.
6. **UX level.** Navigation and action buttons are built from the same matrix (`can(…)` in
   templates). "My rights" shows the logged-in admin's permissions with their scope. Hiding a
   control is never the protection; the server always decides again.
7. **Reporting.** The matrix is rendered from code (`python -m app.cli authz-matrix`) into
   `docs/authorization.md` and into an admin report page. CI fails when the document differs
   from the code, so the published matrix can never drift.
8. **Testing as every role.** The authorisation matrix test covers every role × every admin
   route × own and other organisation/group, against an expectation table written by hand from
   HANDOVER §6 (not computed from the implementation). Playwright logs in as each role through
   the development identity provider (ADR 0013).

## Consequences

- Adding a permission means: add it to the matrix, give routes and queries their permission,
  regenerate `docs/authorization.md`, and extend the expectation table. CI enforces each step.
- `audit_events` arrives before WP8. It holds no personal data of citizens; the actor is the
  subject identifier from the identity provider.
