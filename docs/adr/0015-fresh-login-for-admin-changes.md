# 0015. Admin changes need a recent login

Status: accepted (2026-10-23). Setting bounds per ADR 0042 §7. The consequence "without
server-side sessions; emergency revocation by rotating `SESSION_SECRET`" is to be replaced by
ADR 0098 (proposed).

## Context

Roles come from the identity provider's claims at login and live in the session for up to
8 hours (ADR 0013). The security review of WP7a found that a role revoked in PingFederate keeps
working until the admin logs in again. That was acceptable for a read-only portal. From WP7b
on, admins change groups, routing to the WxCC queue and opening hours.

## Decision

1. Every form and change in the admin portal declares `RequirePermission(..., fresh=True)`.
2. A fresh request needs a login of at most `ADMIN_REAUTH_MINUTES` (default 60). An older
   session is sent to the identity provider and back to the page it came from. With an active
   SSO session that is an invisible round trip that refreshes the roles.
3. Reading (overview, reports, audit log) keeps the 8-hour session.
4. A POST returns to the page that sent the form (referer path). The login accepts only paths
   under `/admin`, so a forged referer cannot send the admin elsewhere.

## Consequences

- A revoked role stops granting changes within an hour, without server-side sessions.
- Submitted values are lost when a fresh login interrupts a POST. The form page itself already
  requires a fresh login, so this only happens for forms left open for more than an hour.
- Emergency revocation is still rotating `SESSION_SECRET` (runbook).
