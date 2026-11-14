# 0125. The hop from web and worker to the bundled identity provider is a recorded exception

Status: accepted (2026-11-14), found while documenting the application architecture: a
production installation with the installer's default identity provider refuses to start.
Amends ADR 0091 point 2 (a second recorded exception) and brings the start-up check in line
with ADR 0112 point 4 and ADR 0106 points 1 and 4. Built in the pull request that adds it; see
*Build status*.

## Context

Since ADR 0106 step 5b the installer bundles Keycloak in every mode, production included,
unless `--idp own` says otherwise. `scripts/render-keycloak-realm.sh` hands web and worker the
login in `secrets/keycloak-app.env`, which Compose reads after `.env`. With an https public
address, which production always has, that file says:

- `OIDC_DISCOVERY_URL=http://idp:8080/idp/realms/openplanner/.well-known/openid-configuration`:
  the compose service `idp` on the internal network, as ADR 0112 point 4 decided ("web still
  talks to Keycloak on the internal network");
- `OIDC_ISSUER=https://<host>/idp/realms/openplanner`: the address browsers use through the
  proxy, pinned as ADR 0097 requires.

The start-up check (`Settings.production_problems`, ADR 0042 §11) refuses every discovery URL
that does not start with `https://` (`oidc_incomplete`), and in production it raises. Web,
worker and the CLI commands the installer runs therefore refuse to start: **a production
installation with the installer's default never comes up**. Nothing caught it. The installer's
tests run against a fake `docker compose` that never starts the image; the installer's own
check reads `.env` and `secrets/config.env`, where the bundled login never is; and test hosts
such as docker01 do not run in production mode, so there the check only listed the login on
System status as something production would refuse.

ADR 0091 says that every hop that carries personal data is encrypted, or a recorded
exception, and point 2 records one: the bundled proxy to web on one host. The hop to the
bundled provider carries the client secret, the authorisation code and the ID token with a
staff member's name and e-mail address; the staff directory (`KEYCLOAK_ADMIN_URL`, ADR 0095)
takes the same hop with the staff list. ADR 0112 built that hop but did not record it, and
`docs/dataflow.md` still says that production refuses an http discovery URL.

Two other ways out were weighed:

- **Render an https discovery URL**, `https://<host>/idp/...`, so web fetches through the
  proxy. The login would then depend on reaching the installation's own public address from
  inside the container (hairpin routing through a load balancer or a split DNS), and on
  trusting the certificate there: an own certificate from an organisation's authority, or the
  internal authority of ADR 0112, is not in web's trust store. A login that works on one
  network and not on the next is worse than the exception, and it reverses ADR 0112 point 4.
- **TLS on the internal hop**: Keycloak serving https on the compose network, with a second
  certificate, its authority and its renewal, for a hop that never leaves the host.

## Decision

1. **The hop from web and worker to the bundled identity provider stays plain HTTP on one
   host, as a recorded exception** next to the one of ADR 0091 point 2, for the same reasons:
   the compose network holds this installation's containers only, the hop never leaves the
   host, and whoever can read that network has the host, with the secrets files and the
   provider's database. It covers the back channel only: the login's discovery document,
   token endpoint and key set, and the staff directory's admin API.
2. **Keyed on the bundled provider, never on plain http as such.** In production the start-up
   check accepts an http `OIDC_DISCOVERY_URL` only when it is exactly the bundled provider's
   internal address behind the proxy,
   `http://idp:8080/idp/realms/openplanner/.well-known/openid-configuration` (the compose
   service on its fixed port, the `/idp` path ADR 0112 fixes, the realm of the template),
   **and** `OIDC_ISSUER` is set and starts with `https://`. The issuer is the address the
   browser is sent to, where the password and the one-time code travel, so the exception never
   reaches the browser's hop. Every other http discovery URL, and this one with an http or a
   derived issuer, is refused as before. The rule is `bundled_idp_hop` in
   `app/domain/web_transport.py`, beside the proxy hop of ADR 0091.
3. **The pinned issuer stays as ADR 0097 decided.** The discovery document and every ID token
   must carry `OIDC_ISSUER` exactly. Because the exception requires `OIDC_ISSUER`, the issuer
   of the bundled login is never derived from the internal address.
4. **Recorded where the first exception is**: the outbound connections of `docs/dataflow.md`
   (with the staff directory's row, which was missing), `docs/threat-model.md`, the runbook,
   and the text of `oidc_incomplete` on System status. `oidc-check` already reports the
   internal back channel as expected (ADR 0112).

## Settings

None. The internal address is fixed on purpose: the compose file, the proxy route and the
render script agree on it (ADR 0112 fixes `/idp`), and a setting would turn the exception into
any http address the host names. There is no switch either: an installation that does not
want the hop chooses `--idp own`.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #833 | merged |
| 1 | `bundled_idp_hop` in the start-up check; the files a production install writes tested against it (`tests/scripts/test_install.py`), the render script tied to the same address; data flow, threat model, runbook and the System status text (nl, en) | #833 | merged |

## Consequences

- A production installation with the installer's default starts; one with `--idp own` is
  unchanged.
- A test or lab installation with the bundled provider behind the proxy no longer lists
  `oidc_incomplete` on System status, because production would accept it.
- The exception assumes that the name `idp` is this installation's compose service. While that
  container is down, Docker passes the name to the host's resolver, which may complete it with
  the host's search domains. The login then fails on the discovery document or on the pinned
  issuer, unless someone controls that name in the host's DNS. The same was already true for
  test and lab installations and for the staff directory in every mode; closing it (for
  example by checking that the name resolves on the compose network) is not part of this
  decision.
- `KEYCLOAK_ADMIN_URL` is still not checked at start-up: the staff directory exists only for
  the bundled provider (ADR 0095), and the render script writes its internal address.
