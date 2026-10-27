# Development identity provider (Keycloak)

Stands in for PingFederate in development and CI (ADR 0013). Started by
`docker compose --profile dev up` and imported from `openplanner-realm.json`.

Every password in this realm is the development-only value `dev-only-password`; the client
secret is `dev-only-openplanner-admin-secret`. They exist only for local testing and never
reach production, which uses PingFederate.

| User | Role | Scope | Purpose |
|---|---|---|---|
| `super` | superuser | everything | full access |
| `orgadmin` | org_admin | organisation `demo` | own organisation and its groups |
| `groupadmin` | group_admin | group `demo` | own group only |
| `auditor` | auditor | organisation `demo` | compliance and privacy: reads the log, no personal data (ADR 0037) |
| `outsider` | org_admin | organisation `andere` | checks that `demo` is out of reach |
| `norole` | none | – | logged in without rights: sees "no access" |

Keycloak admin console: http://127.0.0.1:8080 (user `admin`, password `admin`, development only).

`e2e/tests/admin-roles.spec.ts` logs in as each of these users (`cd e2e && npm test`); CI starts
this realm with `scripts/run-keycloak-container.sh`.
