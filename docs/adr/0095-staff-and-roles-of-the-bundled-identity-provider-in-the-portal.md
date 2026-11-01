# 0095. Staff and roles of the bundled identity provider, managed in the portal

Status: proposed (2026-11-01), requested by the product owner. Extends ADR 0090 (bundled
identity provider), ADR 0013 (OIDC admin login), ADR 0049 (entitlements) and ADR 0083
(identity providers per organisation). Not built; see *Build status*.

## Context

Not every deployment talks to an identity provider of its own. ADR 0090 makes the bundled
Keycloak the default for `test` and `lab` installations, but leaves its staff management to
Keycloak's own admin console on the host. For a superuser who has no identity team that is a
second product to learn: staff, roles, organisations and groups are changed in one place, and
OpenPlanner shows them (the Staff screen) read-only with the note that changing happens at the
identity provider.

The product owner wants the bundled provider to feel part of the platform: accessible from the
portal and, where it is OpenPlanner's own, integrated.

## Decision

1. **One adapter, one port.** `IdentityProvider` in `app/domain/ports.py` gets a second,
   optional capability `StaffDirectory` (list, create, disable, reset password, set the claims
   of ADR 0049). Only the Keycloak adapter implements it, through Keycloak's admin REST API
   with a service-account client `openplanner-directory` (client credentials) limited to the
   realm's `manage-users` and `view-realm` roles. A provider without the capability (the
   organisation's PingFederate, any provider of ADR 0083) keeps today's read-only Staff screen.
2. **Same home, no new section.** Staff stays under Manage › Staff (ADR 0047). When the
   bundled provider is the user's source, the screen gains actions: *Invite staff member*,
   *Change roles and places*, *Disable*, *Send password reset*. A staff member from another
   provider shows no actions and names the provider that owns them. No second screen, no
   "which organisation?" question: the scope of the permission decides which rows an admin can
   change (an organisation admin only their own organisation's staff).
3. **The matrix stays the only source of rights.** New permissions `staff.manage` (create,
   change, disable) and `staff.reset_password`, granted to the superuser at platform level and
   to the organisation admin at organisation level; the claims written to Keycloak are derived
   from the matrix's role names and never grant more than the acting admin holds. Every change
   goes to `audit_events` without PII (member id, action, role names, not names or emails).
4. **The superuser can always get in.** The initial `super` of ADR 0090 stays the bootstrap;
   OpenPlanner never creates a local account, a session without OIDC or a break-glass login
   (ADR 0042 §8). The portal link *Open identity provider console* shows for the superuser
   only, as a link to the console on the public address, nothing proxied.
5. **Participants are never users.** The directory holds staff only; no participant data
   reaches Keycloak.
6. **Bundled or own is visible.** System status shows which provider is the platform's, and
   whether the directory capability is on (the service account answers).

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `KEYCLOAK_ADMIN_URL`, `KEYCLOAK_DIRECTORY_CLIENT_ID`, `KEYCLOAK_DIRECTORY_CLIENT_SECRET` | A bootstrap | filled by the installer in the bundled modes | | operator | needed before the portal can ask Keycloak anything; secret via `_FILE` |
| `staff_invite_expiry_days` | C platform | 7 | 1–30 | `settings.platform.manage` | validity of the invite and reset link |
| `staff_directory_enabled` | C platform | on when the bundled provider is configured | on/off | `settings.platform.manage` | hides the actions; the provider's data is untouched |

**Fixed on purpose:** the list of claims written (ADR 0049) and the required action
*change password at first login*: security rules, not tunable.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #310 | in PR |
| 1 | `StaffDirectory` port, fake, Keycloak adapter, service account in the realm template | | planned |
| 2 | Permissions `staff.manage`, `staff.reset_password`; matrix, `docs/authorization.md` | | planned |
| 3 | Staff screen actions, manual topic nl/en, audit events | | planned |
| 4 | Threat model (service-account secret, privilege escalation by claims), data flow, e2e | | planned |

## Consequences

- A test installation is administered from one portal; the Keycloak console is the exception.
- OpenPlanner now holds a credential that can create users in the bundled realm: it lives in
  a `_FILE` secret, is limited to the realm's user management, and the threat model gets an
  abuse case "an organisation admin grants a role above their own".
- A new dependency is not needed (`httpx`).
- Organisations with their own provider notice nothing.
