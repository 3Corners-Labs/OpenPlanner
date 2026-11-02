# 0106. The bundled identity provider by default, with user names under a platform suffix

Status: proposed (2026-11-02, #425), requested by the product owner. Amends ADR 0090 point 1 (the
bundled Keycloak becomes the default in every installer mode, production included), ADR 0095
(the user name of a staff member it creates) and ADR 0083 points 11 and 13 (a reserved domain
that always routes to the platform provider). Not built.

## Context

On 2026-11-02 the product owner looked for staff management (add, change, remove, assign roles)
on docker01, which runs `0.4.0-rc.145`, and did not find it. The code of ADR 0095 is in that
candidate (#361), but nothing connects it:

- docker01 runs the older lab realm (`lab/openplanner-realm.json`), not the bundled provider of
  ADR 0090; that realm has no service account `openplanner-directory`.
- `scripts/render-keycloak-realm.sh` generates the directory secret into
  `secrets/keycloak.env`, but nothing writes `KEYCLOAK_ADMIN_URL` and
  `KEYCLOAK_DIRECTORY_CLIENT_SECRET` to the application's side; that was left to the installer
  (ADR 0090 step 2, not built). The development realm has no directory client either.
- Without those two values the Staff screen hides its actions without saying why, and System
  status does not show the directory (ADR 0095 step 5, not built).

The product owner wants staff management through the bundled provider to be what an
installation has unless it chooses otherwise, in production too, and to keep working when
organisations add their own providers (ADR 0083). Staff then need a user name that is not
their e-mail address: an organisation with its own provider routes logins by e-mail domain,
and a staff member managed in the bundled provider must not land at that provider.

## Decision

### The bundled provider is the default

1. **Every installer mode bundles Keycloak unless `--idp own` says otherwise.** This replaces
   ADR 0090 point 1 (`production` only with `--idp bundled`). The installer renders the realm,
   starts the profile `idp`, and writes `OIDC_DISCOVERY_URL`, `KEYCLOAK_ADMIN_URL`,
   `KEYCLOAK_DIRECTORY_CLIENT_ID` and the directory secret (as `_FILE`) to the application's
   side. With `--idp own` none of them are written and the Staff screen stays read-only, as
   ADR 0095 point 1 describes for any other provider.
2. **The render script fills both sides.** `scripts/render-keycloak-realm.sh` also writes the
   application's half (`secrets/keycloak-app.env`, mode 600, added to the `env_file` list of
   `web` and `worker`), so a host without the installer gets the same result. The development
   realm (`dev/keycloak/openplanner-realm.json`) gets the same `openplanner-directory` client,
   so the actions work under `docker compose --profile dev` too.
3. **A missing connection is shown, never silent.** When the platform provider is the bundled
   one (its issuer matches the realm the installer wrote) but the directory is not configured
   or not reachable, the Staff screen shows a notice to whoever holds `staff.manage` naming
   what is missing (the setting names, never a value), and System status shows the directory
   as a check with its state. This is ADR 0095 step 5, brought forward.
4. **Production runs Keycloak in production mode.** Keycloak does not support its development
   start (`start-dev`, file-based H2) in production. In `production` the profile `idp` runs
   `start` with its own database container `idp-db` (PostgreSQL, used by Keycloak only; the
   application's database stays SQLite, as CLAUDE.md requires), behind the TLS proxy of ADR
   0064, with the admin console bound to the host's loopback. `admin_mfa_required` is on by
   default (ADR 0100). `test` and `lab` keep `start-dev` with H2. The `idp-db` and `idp-data`
   volumes are part of the backup of ADR 0102 step 3 and the runbook's restore procedure.

### User names under a platform suffix

5. **A staff member's user name is `<name>@<organisation>.<suffix>`.** `<suffix>` is the
   platform setting `bundled_idp_login_suffix` (default `openplanner.internal`; `.internal` is
   reserved for private use by ICANN, unlike `.local`, which RFC 6762 gives to multicast DNS).
   `<organisation>` is the organisation's `idp_key` (ADR 0049; it is a valid DNS label). Staff
   with platform-wide roles (superuser, global custom roles) get `<name>@<suffix>`.
6. **The name is proposed, not typed freely.** The invite form proposes
   `<first name>.<last name>`, lower case, diacritics removed, characters outside `[a-z0-9.-]`
   dropped, at most 64 characters (RFC 5321), with `-2`, `-3` … on a collision; the inviting
   admin may change it within the same rule. The user name cannot be changed afterwards
   (Keycloak `editUsernameAllowed` off); a staff member who needs a new one is invited again.
7. **The e-mail address stays a real address.** The invite still asks for the staff member's
   own e-mail address: Keycloak sends the invitation, the password reset and the e-mail
   verification there, and calendar invitations (dispatch by calendar, ADR 0043) use it. The
   realm allows login by user name or by that address. Today's adapter sets the user name to
   the e-mail address (`app/adapters/keycloak_directory.py`); accounts created that way keep
   their name.
8. **An organisation admin creates names only under their own organisation.** The scope of
   `staff.manage` (ADR 0095 point 3) also bounds the user name: an organisation admin's
   invitation always gets `@<own idp_key>.<suffix>`; only the superuser creates names directly
   under `<suffix>`. A person who works for two organisations has one account under the
   organisation that invited them first and is granted roles in the other, as ADR 0095 allows.
9. **The suffix is reserved for the platform provider.** In the e-mail-first login of ADR 0083
   point 13, an address on `<suffix>` or any subdomain of it goes to the platform provider
   without a DNS check, and its `login_hint` is the user name. An organisation provider cannot
   claim `<suffix>` or a subdomain of it (ADR 0083 point 11 refuses it), and the setting cannot
   be changed to a domain that an organisation provider already claims. The field on the login
   page is labelled *Work e-mail address or user name* (`admin.login.identifier.label`, nl and
   en).
10. **Changing the suffix affects new accounts only.** Existing user names stay as they are;
    the setting's page says so and lists how many accounts carry the old suffix. Both suffixes
    keep routing to the platform provider until no account carries the old one.
11. **A user name is personal data.** It contains the staff member's name: it never appears in
    a log line, an audit event, a URL or an error message, like the e-mail address (ADR 0095
    point 3 already keeps names and addresses out of audit events). The structlog processor in
    `app/logging.py` masks the key `username` too, and its test covers it.

### docker01

12. **docker01 moves to the bundled provider.** Its lab realm is replaced by the bundled
    profile: the installer exports the existing lab users' grants, renders the realm, and the
    product owner re-invites the test users through the Staff screen (their passwords cannot
    be carried over). Until then, the directory client is added to the lab realm by hand so the
    actions can be tried; the runbook describes both.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `bundled_idp_login_suffix` | C platform | `openplanner.internal` | a domain name of 2 to 10 labels, at most 190 characters, not claimed by an organisation provider | `settings.platform.manage` | fresh login (ADR 0015); new accounts only (point 10) |
| `admin_mfa_required` | B deployment | on when the installer bundles Keycloak, in every mode | on/off | operator | unchanged key; only the default the installer writes changes |
| installer `--idp bundled\|own` | – | `bundled` in every mode | | operator | an installer flag, not a setting; replaces the per-mode default of ADR 0090 |

`KEYCLOAK_ADMIN_URL`, `KEYCLOAK_DIRECTORY_CLIENT_ID` and `KEYCLOAK_DIRECTORY_CLIENT_SECRET` keep
the class ADR 0095 gave them; the open question there (A, or a sealed integration secret) is not
answered here.

**Fixed on purpose:** the user name rule of point 6 (character set, 64 characters, the collision
counter) and the `idp_key` as the organisation label: they keep user names valid as e-mail-like
identifiers and DNS labels, which is a format rule, not a preference.

## Consequences

- An installation gets working staff management from the first start; nobody needs Keycloak's
  admin console for ordinary staff work.
- Production gains two containers (`idp`, `idp-db`) and a second database to back up and
  update. Their images are pinned and tracked by Renovate (ADR 0051) like the others. A client
  with its own identity team keeps `--idp own` and none of this.
- The application's database remains SQLite; PostgreSQL appears only as Keycloak's own store.
  CLAUDE.md's stack line is about the application and stays unchanged.
- User names are recognisable per organisation, and the e-mail-first login keeps working when
  some organisations bring their own provider.
- ADR 0090 step 2 (the installer default) and ADR 0095 step 5 are built as steps of this ADR;
  their Build status tables point here.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #425 | in PR |
| 1 | Render script writes the application's half; development realm gets `openplanner-directory`; compose `env_file` | – | in PR (follows #425) |
| 2 | Staff screen notice and System status check for an unconnected directory (point 3) | – | planned |
| 3 | User names under the suffix: setting, invite form, adapter, scope check, `username` masked (points 5–8, 11) | – | planned |
| 4 | Reserved suffix in the login routing and the provider registration; login label (points 9, 10) | – | planned |
| 5 | Installer `--idp bundled\|own` default in every mode; production `start` with `idp-db`, proxy, backup and runbook (points 1, 4) | – | planned |
| 6 | docker01 moved to the bundled provider (point 12) | – | planned |
