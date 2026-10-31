# Development identity provider (Keycloak)

Stands in for PingFederate in development and CI (ADR 0013). Started by
`docker compose --profile dev up` and imported from `openplanner-realm.json`.

Every password in this realm is the development-only value `dev-only-password`; the client
secret is `dev-only-openplanner-admin-secret`. They exist only for local testing and never
reach production, which uses PingFederate.

| User | Role | Scope | Purpose |
|---|---|---|---|
| `super` | superuser + org_admin + group_admin + auditor + agent | everything (also organisation `demo` and group `demo`) | full access; holds every built-in role at once, so the portal can be tried as an admin with several roles |
| `orgadmin` | org_admin | organisation `demo` | own organisation and its groups |
| `groupadmin` | group_admin | group `demo` | own group only |
| `auditor` | auditor | organisation `demo` | compliance and privacy: reads the log, no personal data (ADR 0039) |
| `agent` | agent | group `demo` | the agent's view of the agenda and queue |
| `outsider` | org_admin | organisation `andere` | checks that `demo` is out of reach |
| `mixed` | org_admin + auditor | organisation `demo` (org_admin) and `andere` (auditor) | two roles on two organisations: entitlements mode only; legacy mode refuses it (ADR 0049 point 14) |
| `refused` | org_admin | organisation `demo` | one malformed entitlement value, shown as refused on the staff page |
| `norole` | none | – | logged in without rights: sees "no access" |

## Organisation provider `andere` (ADR 0083)

A second realm, `andere` (`andere-realm.json`), stands in for an organisation's own identity
provider. Its client `openplanner-admin` (secret `dev-only-andere-secret`) sends only the
entitlement claim and the standard `email` claims, because an organisation provider resolves in
entitlements mode only. Discovery: http://127.0.0.1:8080/realms/andere/.well-known/openid-configuration.
Registering it in the portal comes with ADR 0083 step 2; until then the realm exists for tests.

| User | Entitlements | Email | Purpose |
|---|---|---|---|
| `beheer` | `org_admin:andere` | `beheer@andere.test` | the organisation's own admin |
| `team` | `group_admin:andere/andere-team` | `team@andere.test` | a group admin of that organisation |
| `overmoedig` | `superuser`, `org_admin:demo`, `org_admin:andere` | `overmoedig@andere.test` | B1/B2: only `org_admin:andere` may come through |
| `buitendomein` | `org_admin:andere` | `bas@openplanner.test` | B3: an address outside the provider's domain is refused |
| `onbevestigd` | `org_admin:andere` | `onbevestigd@andere.test`, not verified | B4: `email_verified` false is refused |

Keycloak admin console: http://127.0.0.1:8080 (user `admin`, password `admin`, development only).

`e2e/tests/admin-roles.spec.ts` logs in as each of these users (`cd e2e && npm test`); CI starts
this realm with `scripts/run-keycloak-container.sh`.
