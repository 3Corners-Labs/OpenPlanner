# Installing OpenPlanner on a host

> Kubernetes instead of a host? That is `docs/kubernetes.md` (trial, one replica, ADR 0114).

One script installs, repairs and re-checks a host: `scripts/install.sh`. Run it after a fresh
clone, after a manual change, or whenever something looks wrong; a second run changes nothing
that is in order. This page explains what it does, where everything lives and what to do when it
stops. Operating the running service is in `docs/runbook.md`. Only want to look at it on a laptop?
That is `docs/try-it.md` (Docker only, any operating system).

## Where everything lives

The installation directory is the git checkout itself: **`/home/<user>/openplanner`**, where
`<user>` is the operator account that runs Docker. There is no `/opt`; the script works from
wherever the checkout is, and writes that path into the updater's configuration.

| Path | Written by | Holds | Mode |
|---|---|---|---|
| `~/openplanner/` | `git clone`, at a release tag | `docker-compose.yml`, `deploy/proxy/`, `scripts/` | |
| `~/openplanner/.env` | installer, then you | the application's settings and integration secrets (`.env.example` lists every one) | 600 |
| `~/openplanner/secrets/secrets.env` | installer (or `app.cli init`) | the bootstrap keys `SETTINGS_ENCRYPTION_KEY`, `MANAGEMENT_TOKEN_KEY`, `SESSION_SECRET` (ADR 0042 §9); back it up apart from the database | 600, directory 700 |
| `~/openplanner/secrets/config.env` | installer, with `--configuration` | the login (`OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_SCOPES`) from a SQLite configuration package (ADR 0089, read for one more release); a JSON document (ADR 0099) carries no login and writes none; Compose reads it before `.env`, so a value in `.env` wins | 600 |
| `~/openplanner/proxy.env` | installer (with the HTTPS proxy) | `TLS_DOMAIN`, `ACME_EMAIL` (or the own certificate's files) for the HTTPS proxy, no secrets | 600 |
| `~/openplanner/.env.update` | installer, host updater | `OPENPLANNER_IMAGE` and, with the HTTPS proxy, `OPENPLANNER_PROXY_IMAGE` (pinned digests), `COMPOSE_PROFILES`, `OPENPLANNER_TLS`, `OPENPLANNER_MODE`; `OPENPLANNER_EDGE_IPV6=false` only on a kernel without IPv6 | 644 |
| `~/openplanner/.env.bak.<time>` | installer, when it repaired `.env` | the `.env` as it was before | 600 |
| `~/openplanner/docker-compose.override.yml` | you, optional | host-specific additions, such as a published port on a lab host | |
| `/usr/local/sbin/openplanner-update` | installer | the host updater (`scripts/updater/openplanner-update.sh`) | 755 |
| `/etc/systemd/system/openplanner-updater.{service,timer}` | installer | runs the updater every minute | 644 |
| `/etc/systemd/system/openplanner-updater.service.d/override.conf` | installer | lets the updater write only `~/openplanner` under `/home` | 644 |
| `/etc/openplanner/updater.env` | installer | `COMPOSE_DIR=/home/<user>/openplanner`, the two images, signing identity | 600 |
| `/var/lib/openplanner-updater/` | updater, installer | `previous.env` (rollback), lock, a copy of the ghcr.io login, cosign's cache | 700 |
| Docker volume `openplanner_data` | web, worker | the SQLite database and its update backups (personal data) | |
| Docker volume `openplanner_proxy-data` | proxy | certificate, private key, ACME account | |
| Docker volume `openplanner_portal-certs` | web (writes), proxy (reads) | the private key of an own certificate made or imported in the portal, its signing request and the served pair (ADR 0070); never in the database backup; empty with Let's Encrypt | |

Every Compose command on the host runs from `~/openplanner` with the pin file:

```sh
cd ~/openplanner
docker compose --env-file .env.update ps
```

The pin file also carries the profiles (`tls` with the HTTPS proxy, `dev` in a lab), so this
one form starts exactly the services of the installation. Without `--env-file .env.update`,
Compose would build local images instead of running the signed release: the application
image and, since ADR 0064 step 2, the HTTPS proxy image (`ghcr.io/<owner>/<repo>-proxy`,
released and signed with every version) are both pulled and verified; a host never builds
Caddy.

## Modes

| `--mode` | `ENVIRONMENT` | Services | For |
|---|---|---|---|
| `production` | `prod` | web, worker, HTTPS proxy (Let's Encrypt); the bundled identity provider on its own database, or your own | real participants; public DNS name, ports 80 and 443 |
| `test` | `test` | web, worker, HTTPS proxy (Let's Encrypt), the bundled Keycloak under `/idp` | acceptance on its own public DNS name; `--no-tls` behind your own proxy |
| `lab` | `test` | web, worker, the bundled Keycloak, Mailpit; HTTPS proxy with `--tls-domain` | internal demo and integration tests (as on docker01) |

HTTPS is the standard shape (ADR 0064): `production` and `test` run the proxy unless `--no-tls`
says a load balancer of your own does TLS; a `lab` runs it when `--tls-domain <host>` names a
public DNS name. The script remembers the mode and that choice in `.env.update`, so later runs
need neither.

## The identity provider (ADR 0106 step 5)

`--idp bundled|own` decides who the staff log in with. **`bundled`** (the default in every mode) runs the Keycloak of ADR 0090: the installer renders its realm with generated secrets
(`scripts/render-keycloak-realm.sh`), the proxy serves it under `https://<host>/idp`
(`IDP_UPSTREAM` in `proxy.env`), `web` and `worker` read the login from `secrets/keycloak-app.env`
(nothing is asked or copied into `.env`), and its mail server is set at the first start: Mailpit
in a `lab`, the installation's `SMTP_*` otherwise. Staff are then managed in the portal
(*Manage › Staff*). The first password of the user `super` is `KC_SUPER_PASSWORD` in
`secrets/keycloak.env`; Keycloak asks for a new one and an authenticator at the first login.
**`own`** asks for the login of your provider (PingFederate, Entra, ...), as before. On a host
that ran the bundled provider it also deletes `secrets/keycloak-app.env`, whose login would
otherwise win over the one in `.env`, removes the `/idp` route from `proxy.env` and stops the
`idp` and `idp-db` containers; their volumes and `secrets/keycloak.env` stay (ADR 0125).

In `production` Keycloak runs in production mode (`start`, not `start-dev`) on its own
PostgreSQL container `idp-db` (the application's database stays SQLite), the second factor is
required (`ADMIN_MFA_REQUIRED=true`), and the volumes `idp-data` and `idp-db-data` belong in the
backup (runbook, *Bundled identity provider*). A production host that already has a login of
its own in `.env` (or names one in the environment of the run) stays on `own`; `--idp bundled`
moves it, and the users are re-invited. Without the HTTPS proxy the bundled provider answers on the
host only (`127.0.0.1:8080`): logging in from another machine needs `--tls-domain`. The choice is
remembered in `.env.update` (`OPENPLANNER_IDP`). `web` and `worker` reach the bundled provider on
the internal network as `http://idp:8080/idp/...`, a recorded exception that production accepts
for exactly that address with an https `OIDC_ISSUER` (ADR 0125). **Until its step 2 gives the
provider a fixed address, that name can leave the host while the `idp` container is down**,
and whatever answers it can receive staff passwords and log in as a superuser: the installer
warns in `production`, and the advice for a production host that goes live before step 2 is
`--idp own`.

## Prerequisites

- Ubuntu 24.04 LTS. Debian 12 works too, with Docker from Docker's repository (Debian's own
  `docker.io` has no Compose v2 package). On another system the script checks the
  prerequisites but does not install packages; install them yourself (below).
- An operator account with `sudo`. Run the script as that account, never as root.
- Outbound HTTPS to `github.com`, `api.github.com`, `ghcr.io`, `download.docker.com` and the apt
  mirrors.
- While the repository and its image are private: a GitHub token of an account with read
  access. A fine-grained token with *Contents: read* (clone, release check) and a classic token
  with `read:packages` (images on ghcr.io), or one classic token with `repo` and
  `read:packages`.
- Production only: a DNS name (`A`/`AAAA`) pointing at the host, ports 80 and 443 open from the
  internet, and a `CAA` record (if any) that allows `letsencrypt.org`.
- With an `AAAA` record: Docker Engine 27 or newer with `ip6tables` on (its default), so that
  visitors over IPv6 keep their address; the script warns otherwise (runbook, *Visitors over
  IPv6*). The host needs no IPv6 address for the rest; a kernel without IPv6 is fine too.

Everything else the script installs or brings up to date:

| Component | Source | Every run |
|---|---|---|
| `curl`, `flock` (util-linux), `ca-certificates`, `gnupg` | apt | upgraded to the newest version in the archive |
| Docker Compose plugin | apt (`docker-compose-plugin`, or Ubuntu's `docker-compose-v2` next to Ubuntu's `docker.io`) | upgraded; 2.24 or newer required |
| cosign | latest release of `sigstore/cosign`, `.deb` checked against the release's `cosign_checksums.txt` | replaced when a newer release exists |
| Docker Engine | Docker's apt repository, release key checked by fingerprint | installed when missing; upgraded only with `--upgrade-docker` |

An engine upgrade restarts every container on the host, other applications included, so the
script never does it on its own: it reports waiting engine updates and you run it again with
`--upgrade-docker` at a quiet moment. Keep Ubuntu's `unattended-upgrades` on for the rest of the
system.

## Installing

1. Clone the release you want into the operator's home:

   ```sh
   git clone --branch v0.3.0 https://github.com/3Corners-Labs/OpenPlanner.git ~/openplanner
   cd ~/openplanner
   ```

   For a private repository git asks for a user name and the token as password.

2. Run the installer:

   ```sh
   ./scripts/install.sh --mode production
   ```

   It asks for the host name, the operations mailbox, the identity provider (discovery URL,
   client secret), the SMTP fallback and, for a private repository, a GitHub token for the
   release check. Secrets are read without echo. It generates `MANAGEMENT_TOKEN_KEY`,
   `SESSION_SECRET` and `SETTINGS_ENCRYPTION_KEY` into `secrets/secrets.env`, apart from `.env`,
   as `python -m app.cli init` does (no need to run that too). **Copy that file somewhere apart
   from the database backup**: without `SETTINGS_ENCRYPTION_KEY` the secrets stored in the
   database cannot be read, and a backup holding both undoes the encryption.

3. If the script installed Docker, it adds you to the `docker` group and stops. Log out and in
   again, log in to the registry, and run it again:

   ```sh
   docker login ghcr.io -u <github user>
   ./scripts/install.sh
   ```

   (`docker login` asks for the token with `read:packages`.) The same holds for any stop: fix
   what it names, run it again.

4. Check the result: the script ends with the address and waits until `web` and `worker` are
   healthy. `docker compose --env-file .env.update ps` shows them, and
   `systemctl list-timers openplanner-updater.timer` shows the updater. As the last step, once
   the name resolves to this host, run `docker compose --env-file .env.update exec web python -m
   app.cli address-check` (ADR 0086): it asks `PUBLIC_BASE_URL` for `/api/v1/instance` the way a
   participant's browser would and exits 1 on a finding (another installation answers, the proxy
   is not trusted, the app sees `http`); the findings are explained in `docs/runbook.md`,
   *Address and proxy*.

With the HTTPS proxy, a trial run against Let's Encrypt's staging CA first keeps you clear of its
rate limits: add `--acme-staging` on the first run, then follow *HTTPS certificate* in
`docs/runbook.md` to switch to the real CA; *System status* in the portal shows which
certificate is served and whether browsers trust it. From there the superuser can also request
an own certificate (the key stays on the host), place its chain, switch to it and back
(ADR 0070), without the host.

Without prompts, for automation, set the answers as environment variables named after the
setting and add `--non-interactive`:

```sh
TLS_DOMAIN=afspraak.example.nl ACME_EMAIL=ops@example.nl \
OIDC_DISCOVERY_URL=https://idp.example.nl/.well-known/openid-configuration \
OIDC_CLIENT_SECRET=... SMTP_HOST=smtp.example.nl SMTP_USERNAME=... SMTP_PASSWORD=... \
UPDATE_CHECK_TOKEN=... ./scripts/install.sh --mode production --version 0.3.0 --non-interactive
```

Webex and Webex Contact Center are configured afterwards: `docs/runbook.md`, *Webex meetings and
Webex Contact Center*.

## Installing from a configuration document

A configuration document (ADR 0099) is the JSON file `python -m app.cli config export` or the
portal (*Advanced settings › Configuration package*) writes: organisations, groups, texts,
channels, identity providers, API clients and platform settings, each object under its own
key, so you can read it, edit it and keep it in version control. With it a new installation is
complete after one command:

```sh
cd ~/openplanner
./scripts/install.sh --mode production --configuration ~/openplanner-configuration.json
```

The script finds out by the file's content whether it is a document (JSON) or an old SQLite
package (below). For a document it:

1. asks the usual questions, the identity provider's included: a document carries no login
   (the login is the installation's own, `.env` or the bundled provider), so `secrets/config.env`
   is not written.
2. starts web and worker, waits until both are healthy, copies the document into the web
   container and **plans** it (`python -m app.cli config plan`). The plan is printed: every
   change by action, kind and key, never a value, and its hash.
3. **applies** it with that hash (`config apply --plan-hash`), so what is written is what was
   printed, all or nothing, and removes the copy from the container, whatever the outcome.

A document holding sealed secrets (an export with secrets) needs the passphrase it was
exported with, from a file whose first line it is, never on a command line:

```sh
printf '%s\n' 'the passphrase the document was exported with' > ~/document.passphrase
chmod 600 ~/document.passphrase
./scripts/install.sh --mode production --configuration ~/openplanner-configuration.json --passphrase-file ~/document.passphrase
```

Delete the passphrase file and the document when the script is done: together they are every
secret of the platform. Without the passphrase option such a document is refused before
anything is written.

A plan with a refusal (a value the portal would refuse, a right that is missing, an object in
use) stops the script after the start with the report on standard error; nothing was applied.
Web and worker are running by then; fix the document and run the script again, or apply it
later with the commands of the runbook, *Configuration document*. The same holds for a plan
that changed between the two steps (`configuration_plan_stale`) and for a wrong passphrase
(`configuration_passphrase_wrong`).

Without a terminal the paths come from `OPENPLANNER_CONFIGURATION` and, for sealed secrets,
`OPENPLANNER_PASSPHRASE_FILE`, next to the other answers:

```sh
TLS_DOMAIN=afspraak.example.nl ACME_EMAIL=ops@example.nl \
OPENPLANNER_CONFIGURATION=/home/operator/openplanner-configuration.json \
./scripts/install.sh --mode production --version 0.3.0 --non-interactive
```

A run again with the document plans it again and applies only what differs: running the same
document twice changes nothing the second time.

## Installing from a SQLite configuration package

A SQLite package (ADR 0089) is no longer written, but one exported before ADR 0099 is still
read for one more release: the application converts it to a document that replaces the
platform's configuration and applies it by the same rules (runbook, *Configuration package*).
It is every setting of another installation with its secrets sealed under a passphrase, and
the login. The command is the same, and the passphrase is always needed:

```sh
cd ~/openplanner
printf '%s\n' 'the passphrase the package was exported with' > ~/package.passphrase
chmod 600 ~/package.passphrase
./scripts/install.sh --mode production --configuration ~/openplanner-configuration-x.sqlite --passphrase-file ~/package.passphrase
```

Both options go together. The passphrase is read from the file's first line and never travels
on a command line or appears in the script's output; delete the file and the package when the
script is done, together they are every secret of the platform. The script then:

1. pins and verifies the release first (step 4), because the package is read by the
   application itself: a one-off container of the pinned image writes the package's login to
   `secrets/config.env` (mode 600). A wrong passphrase stops the script here, before anything
   is written.
2. asks the usual questions except the identity provider's: the login is in `config.env`.
   `.env` is written without `OIDC_*`. (A package exported from an installation without a
   login has none to give; the questions are then asked as usual.)
3. starts web and worker, waits until both are healthy, copies the package into the web
   container and imports it. The summary (organisations, groups, channels, secrets) is
   printed; the copy in the container is removed, whatever the outcome.

The import is refused, and the script stops after the start, when the package was exported
from another version of OpenPlanner (`configuration_package_version`: export it again from an
installation on this version), or when the document it converts to breaks a rule of the
portal (`configuration_object_in_use`, `not_permitted`, `discovery_refused`: the identity
provider's discovery address is fetched, so it must be reachable). Web and worker are running
by then; fix the cause and run the script again with the same two options, or import later as
the runbook says.

Without a terminal, the two paths come from `OPENPLANNER_CONFIGURATION` and
`OPENPLANNER_PASSPHRASE_FILE`. A later run without the options leaves `secrets/config.env`
alone; a run with them writes it again from the package and imports again. To change the login
by hand afterwards, set the `OIDC_*` values in `.env`: Compose reads `config.env` before
`.env`, so `.env` wins; or remove `config.env` and answer the questions on the next run.

## Options

| Option | Effect |
|---|---|
| `--mode production\|test\|lab` | required on the first run; remembered afterwards |
| `--version X.Y.Z` | pin this release (pulled, signature verified; with the HTTPS proxy its image too, at the same version); without it a pinned release stays, and a first run takes the latest release. `X.Y.Z-rc.N` pins a candidate on a `test` or `lab` host |
| `--repository owner/name` | another repository's releases (default `3Corners-Labs/OpenPlanner`, or `UPDATE_REPOSITORY` from `.env`) |
| `--non-interactive` | never ask; take answers from the environment (also when there is no terminal) |
| `--no-tls` | production, test: no HTTPS proxy; asks the public address and your proxy's address (`FORWARDED_ALLOW_IPS`) |
| `--tls-domain HOST` | lab: run the HTTPS proxy for this host name (also sets it for the other modes) |
| `--own-certificate` | `proxy.env` names `certs/fullchain.pem` and `certs/privkey.pem` instead of ACME (runbook, *Own certificate*); locks the portal's certificate source (`TLS_CERTIFICATE_SOURCE=own` in `.env`, ADR 0070) |
| `--internal-ca` | `proxy.env` gets `TLS_INTERNAL=true`: Caddy's own certificate authority, for a lab without a public name (runbook, *Internal certificate authority for a lab*, ADR 0112); locks the portal's certificate source; refused with `--own-certificate`, warned in production |
| `--idp bundled\|own` | the bundled Keycloak (default) or your own provider; remembered (*The identity provider*) |
| `--acme-staging` | with the HTTPS proxy: the first `proxy.env` points at Let's Encrypt's staging CA |
| `--upgrade-docker` | also upgrade Docker Engine (restarts every container on the host) |
| `--no-packages` | leave the host's packages alone; only check them |
| `--no-updater` | do not install the host updater (portal update requests then wait) |
| `--configuration FILE [--passphrase-file PATH]` | install from a configuration document (JSON, ADR 0099: planned, then applied with the plan's hash once web is healthy; the passphrase only when it holds sealed secrets) or from an old SQLite package (ADR 0089: the login into `secrets/config.env`, the package converted and imported once web is healthy; the passphrase always); see *Installing from a configuration document*; `OPENPLANNER_CONFIGURATION` and `OPENPLANNER_PASSPHRASE_FILE` without a terminal |

## What a second run repairs, and what it reports

| Found | The script |
|---|---|
| a key set twice in `.env` (Compose uses the last one) | keeps the last value of each, after a backup to `.env.bak.<time>` |
| a bootstrap key in `.env` (older installations, docker01) | moves it with its value to `secrets/secrets.env`, after a backup of `.env` |
| a bootstrap key in `.env` and in `secrets/secrets.env` with different values | stops: keep the one the database was encrypted with, remove the other |
| `SESSION_SECRET` missing; in production also `MANAGEMENT_TOKEN_KEY`, `SETTINGS_ENCRYPTION_KEY` | generates it into `secrets/secrets.env`, never overwriting a value there; a key read from a file (`KEY_FILE`) is left alone |
| `secrets/` or `secrets.env` readable by others | sets modes 700 and 600 |
| `.env` readable by others | sets mode 600 |
| a host package missing or outdated, cosign older than the latest release | installs or upgrades it |
| updater, units, drop-in or `updater.env` missing or changed | installs them again from the checkout |
| an HTTPS proxy running where `--no-tls` was chosen (restarts for ever without `proxy.env`) | removes it |
| the HTTPS proxy runs but its image is not pinned (installed before ADR 0064 step 2, when the host built it) | pulls and verifies the proxy image at the version of the pinned application image and pins it; the proxy restarts on the released image |
| the `edge` network has no IPv6 (made before it became dual-stack; Compose 2.29 kept it, 2.31 recreates it itself), or has IPv6 on a kernel without it | `docker compose down` (volumes stay), then the start makes it again; a few seconds offline (runbook, *Visitors over IPv6*) |
| a kernel booted with `ipv6.disable=1` | writes `OPENPLANNER_EDGE_IPV6=false` to `.env.update`, so `edge` stays IPv4 only; a later run on a kernel with IPv6 drops it again |
| `PUBLIC_BASE_URL` or the OIDC URL on `http` in production, a missing OIDC client secret, `ENVIRONMENT` not matching the mode | stops with the list; nothing is pulled or started. The login is checked as Compose sees it: `.env`, else `secrets/config.env`, so a `.env` without `OIDC_*` is in order when `config.env` holds them |
| a release that does not pass `cosign verify` (either image) | stops; the running version stays |

## Update channel (ADR 0059, 0065)

Every merge to `main` becomes a signed release candidate `vX.Y.Z-rc.N` (runbook, *Release
candidates*). Which channel an installation follows is the **superuser's** choice, on
`/admin/updates` or the settings page (`update_channel`, class C): `release` (the default)
offers releases only; `rc` offers the newest of releases and candidates. `update_auto_apply`
lets the worker request a newer version itself. Both are not usable with `ENVIRONMENT=prod`:
production follows releases on a superuser's click, whatever is stored. An operator who wants
either fixed sets `UPDATE_CHANNEL` or `UPDATE_AUTO_APPLY` in `.env`, which locks the portal
value (ADR 0042).

The host decides only whom to trust, once, through the installer **mode**: `production`
trusts images signed by `main.yml` for a release tag only; `test` and `lab` also those signed
on `main`, where candidates are built. Outside production, `--version X.Y.Z-rc.N` pins a
candidate directly.

## Upgrading

- **The application and proxy images**: "Update now" in the admin portal (`/admin/updates`);
  the host updater pulls and verifies both, backs up and switches, with automatic rollback
  (runbook, *Updates from the admin portal*). The proxy restarts with the new version.
- **Compose file and scripts** change with the release too. After a portal update, bring the
  checkout to the same tag and run the installer, which also reinstalls the updater:

  ```sh
  cd ~/openplanner
  git fetch --tags && git checkout v0.3.0
  ./scripts/install.sh --version 0.3.0
  ```

## An existing installation without git

A host where the files were copied by hand (docker01, for one) becomes a checkout
in place; `.env`, `proxy.env`, `.env.update` and an override stay, as git ignores them:

```sh
cd ~/openplanner
git init -q && git remote add origin https://github.com/3Corners-Labs/OpenPlanner.git
git fetch --depth 1 origin tag v0.3.0 && git checkout -f v0.3.0
./scripts/install.sh --mode lab
```

## When the script stops

| Message | Fix |
|---|---|
| `run as the operator account, not as root` | run it without `sudo`; it calls `sudo` itself |
| `cannot reach Docker as <user>` / `now in the docker group` | log out and in again, run it again |
| `Docker Compose ... is too old` | run it again without `--no-packages`, or upgrade the Compose plugin |
| warning: `Docker Engine ... forwards visitors over IPv6 without NAT` | the engine is older than 27: run it again with `--upgrade-docker` at a quiet moment. Until then every visitor over IPv6 shares one address in the rate limits (runbook, *Visitors over IPv6*) |
| warning: `daemon.json switches ip6tables off` | remove `"ip6tables": false` from `/etc/docker/daemon.json` and restart Docker (`sudo systemctl restart docker`, which restarts every container on this host) |
| `cannot find the latest release` | pass `--version`, or put a token in `UPDATE_CHECK_TOKEN` (`.env`) for a private repository |
| `cannot pull ghcr.io/...` | `docker login ghcr.io -u <github user>` with a `read:packages` token, run it again (the updater gets a copy of this login) |
| the portal reports *signature invalid* for a release that CI signed (`cosign verify` on the host says `no signatures found`) | the host's cosign is older than the one CI signs with (cosign 3 writes OCI bundles that cosign 2 cannot read; this hit docker01 at v0.3.0). Run the installer first, which upgrades cosign: `git fetch --tags && git checkout vX.Y.Z && ./scripts/install.sh`, then "Update now" again |
| `is not signed by a release of ...` | the image is not a signed release of this repository: check `--repository` and `--version`; never work around it |
| `Docker's release key has fingerprint ...` | the download was not Docker's key; check the network path (proxy, DNS) before trying again |
| `checksum of cosign_... does not match` | the download is damaged or not the release; run it again later |
| `.env needs changes:` | fix the listed settings in `.env`, run it again |
| `--configuration and --passphrase-file go together` | a SQLite package needs both options, and a passphrase file needs a configuration (or set `OPENPLANNER_CONFIGURATION` and `OPENPLANNER_PASSPHRASE_FILE`) |
| `the configuration document holds sealed secrets: give --passphrase-file` | it was exported with secrets; pass the file whose first line is the passphrase; nothing was written |
| `is neither a configuration document (JSON) nor a SQLite configuration package` | the file is something else; export a document with `python -m app.cli config export` |
| `the configuration document was refused; nothing was applied` | the report below it names each refusal (`REFUSED path code field`); fix the document and run again; web and worker are running without it |
| `cannot read the configuration package:` ... `configuration_passphrase_wrong` | the passphrase file's first line is not the passphrase the package was exported with; nothing was written |
| `the configuration package was refused by the rules of the configuration document it converts to` | the report names the code (`configuration_object_in_use`, `not_permitted`, `discovery_refused`, `integration_not_ready`); fix the cause and run again; web and worker are running without it |
| `the configuration package was refused: ... another version` | export the package again from an installation on this version, run it again with the two options |
| `not healthy within 300s` | `docker compose --env-file .env.update logs web worker`; fix, run it again |

The updater's own log: `sudo journalctl -u openplanner-updater`.

## Removing an installation

```sh
sudo systemctl disable --now openplanner-updater.timer
sudo rm -rf /usr/local/sbin/openplanner-update /etc/openplanner /var/lib/openplanner-updater \
  /etc/systemd/system/openplanner-updater.service /etc/systemd/system/openplanner-updater.timer \
  /etc/systemd/system/openplanner-updater.service.d
sudo systemctl daemon-reload
cd ~/openplanner && docker compose --env-file .env.update down
```

`down` keeps the volumes. `docker compose --env-file .env.update down --volumes` also deletes
the database and the certificate; the database holds personal data, so only do that when the
retention rules allow it.
