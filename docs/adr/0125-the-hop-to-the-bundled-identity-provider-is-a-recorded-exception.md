# 0125. The hop from web and worker to the bundled identity provider is a recorded exception

Status: accepted, partly built (2026-11-14), found while documenting the application
architecture: a production installation with the installer's default identity provider refuses
to start. Amends ADR 0091 point 2 (a second recorded exception) and brings the start-up check in
line with ADR 0112 point 4 and ADR 0106 points 1 and 4. Step 1 is built in the pull request that
adds it; step 2 (a fixed address, point 5) is planned; see *Build status*.

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
takes the same hop with the staff list and its own client secret. ADR 0112 built that hop but
did not record it, and `docs/dataflow.md` still says that production refuses an http discovery
URL.

**The name `idp` is not pinned.** Docker's own resolver answers `idp` only while the container
is attached to the network. When it is not (stopped, being recreated, or held back by an
unhealthy `idp-db`), Docker passes the name to the host's resolver, which may complete it with
the host's search domains. Whatever answers there receives the plain-HTTP requests meant for the
provider. Because the key set is fetched from the same name, an impostor can sign an ID token
that carries the pinned issuer (a public string) and any roles, and so log in as a superuser;
it also receives the platform's client secret and the staff directory's secret, and once the
cached metadata expires (an hour) browsers are sent to the authorisation endpoint it names. The
proxy's `/idp` route (`IDP_UPSTREAM=idp:8080`) resolves the same name, in every mode, and test
and lab installations have used this login since ADR 0112; production was spared only because
it did not start.

Two other ways out were weighed:

- **Render an https discovery URL**, `https://<host>/idp/...`, so web fetches through the
  proxy. The login would then depend on reaching the installation's own public address from
  inside the container (hairpin routing through a load balancer or a split DNS), and on
  trusting the certificate there: an own certificate from an organisation's authority, or the
  internal authority of ADR 0112, is not in web's trust store. A login that works on one
  network and not on the next is worse than the exception, and it reverses ADR 0112 point 4.
- **TLS on the internal hop**: Keycloak serving https on the compose network, with a second
  certificate, its authority and its renewal, for a hop that is meant never to leave the host.

## Decision

1. **The hop from web and worker to the bundled identity provider stays plain HTTP on one
   host, as a recorded exception** next to the one of ADR 0091 point 2, for the same reasons:
   the compose network holds this installation's containers only, the hop is meant never to
   leave the host, and whoever can read that network has the host, with the secrets files and
   the provider's database. It covers the back channel only: the login's discovery document,
   token endpoint and key set, and the staff directory's admin API. Point 5 makes "never leaves
   the host" hold also while the container is down.
2. **Keyed on the bundled provider, never on plain http as such.** In production the start-up
   check accepts an http `OIDC_DISCOVERY_URL` only when it is exactly the bundled provider's
   internal address behind the proxy,
   `http://idp:8080/idp/realms/openplanner/.well-known/openid-configuration` (the compose
   service on its fixed port, the `/idp` path ADR 0112 fixes, the realm of the template),
   **and** `OIDC_ISSUER` is set and starts with `https://`. The genuine provider derives the
   issuer and the endpoints it sends browsers to from one host name (`KC_HOSTNAME`), so with an
   https issuer the password and the one-time code travel over https. Every other http
   discovery URL, and this one with an http or a derived issuer, is refused as before. The rule
   is `bundled_idp_hop` in `app/domain/web_transport.py`, beside the proxy hop of ADR 0091.
3. **The pinned issuer stays as ADR 0097 decided.** The discovery document and every ID token
   must carry `OIDC_ISSUER` exactly. Because the exception requires `OIDC_ISSUER`, the issuer
   of the bundled login is never derived from the internal address.
4. **Recorded where the first exception is**: the outbound connections of `docs/dataflow.md`
   (with the staff directory's row, which was missing), `docs/threat-model.md`, `docs/asvs.md`
   (V12.3.3), the runbook, and the text of `oidc_incomplete` on System status. `oidc-check`
   already reports the internal back channel as expected (ADR 0112).
5. **The provider is reached at a fixed address, never through a name the host could answer.**
   The bundled provider gets a fixed address on an internal network that web, worker and the
   proxy share, as the proxy has on `edge` (ADR 0091 point 2). The rendered login, the staff
   directory, the exception of point 2 and the proxy's route use that address, the installer
   moves an existing host over, and a check on a real Docker host (`scripts/check-proxy.sh`)
   shows that the hop fails closed while the container is down. Until this is built, the risk
   in the Context holds in production too; a production host that does not accept it runs
   `--idp own`.
6. **Leaving the bundled provider removes its login.** With `--idp own` the installer deletes
   `secrets/keycloak-app.env`: Compose reads it after `.env`, so its login and directory would
   otherwise override the ones the operator chose, now that production accepts that login.
   Nothing is lost: the render script writes the file again from `secrets/keycloak.env` when
   the host returns to `--idp bundled`.

## Settings

None. The internal address is fixed on purpose: the compose file, the proxy route and the
render script agree on it (ADR 0112 fixes `/idp`), and a setting would turn the exception into
any http address the host names. There is no switch either: an installation that does not
want the hop chooses `--idp own`.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #833 | merged |
| 1 | `bundled_idp_hop` in the start-up check; the files a production install writes tested against it (`tests/scripts/test_install.py`), the render script tied to the same address; `--idp own` deletes `secrets/keycloak-app.env` (point 6); data flow, threat model, ASVS, runbook and the System status text (nl, en) | #833 | merged |
| 2 | A fixed address for the bundled provider, used by the login, the staff directory, the exception and the proxy route; the installer moves a host over; the real-Docker check (point 5) | | planned |

## Consequences

- A production installation with the installer's default starts. One with `--idp own` keeps
  its own login, also when it ran the bundled provider before.
- A test or lab installation with the bundled provider behind the proxy no longer lists
  `oidc_incomplete` on System status, because production would accept it.
- Until step 2, production carries the risk in the Context: while the `idp` container is down,
  whoever answers for that name through the host's resolver can log in as a superuser. The
  product owner weighs that against production not starting at all with the default.
- `KEYCLOAK_ADMIN_URL` is still not checked at start-up: the staff directory exists only for
  the bundled provider (ADR 0095), and the render script writes its internal address. Step 2
  moves it to the fixed address with the login.
