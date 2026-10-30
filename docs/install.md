# Installing OpenPlanner on a host

One script installs, repairs and re-checks a host: `scripts/install.sh`. Run it after a fresh
clone, after a manual change, or whenever something looks wrong; a second run changes nothing
that is in order. This page explains what it does, where everything lives and what to do when it
stops. Operating the running service is in `docs/runbook.md`.

## Where everything lives

The installation directory is the git checkout itself: **`/home/<user>/openplanner`**, where
`<user>` is the operator account that runs Docker. There is no `/opt`; the script works from
wherever the checkout is, and writes that path into the updater's configuration.

| Path | Written by | Holds | Mode |
|---|---|---|---|
| `~/openplanner/` | `git clone`, at a release tag | `docker-compose.yml`, `deploy/proxy/`, `scripts/` | |
| `~/openplanner/.env` | installer, then you | the application's settings and integration secrets (`.env.example` lists every one) | 600 |
| `~/openplanner/secrets/secrets.env` | installer (or `app.cli init`) | the bootstrap keys `SETTINGS_ENCRYPTION_KEY`, `MANAGEMENT_TOKEN_KEY`, `SESSION_SECRET` (ADR 0042 §9); back it up apart from the database | 600, directory 700 |
| `~/openplanner/proxy.env` | installer (with the HTTPS proxy) | `TLS_DOMAIN`, `ACME_EMAIL` (or the own certificate's files) for the HTTPS proxy, no secrets | 600 |
| `~/openplanner/.env.update` | installer, host updater | `OPENPLANNER_IMAGE` and, with the HTTPS proxy, `OPENPLANNER_PROXY_IMAGE` (pinned digests), `COMPOSE_PROFILES`, `OPENPLANNER_TLS`, `OPENPLANNER_MODE` | 644 |
| `~/openplanner/.env.bak.<time>` | installer, when it repaired `.env` | the `.env` as it was before | 600 |
| `~/openplanner/docker-compose.override.yml` | you, optional | host-specific additions, such as a published port on a lab host | |
| `/usr/local/sbin/openplanner-update` | installer | the host updater (`scripts/updater/openplanner-update.sh`) | 755 |
| `/etc/systemd/system/openplanner-updater.{service,timer}` | installer | runs the updater every minute | 644 |
| `/etc/systemd/system/openplanner-updater.service.d/override.conf` | installer | lets the updater write only `~/openplanner` under `/home` | 644 |
| `/etc/openplanner/updater.env` | installer | `COMPOSE_DIR=/home/<user>/openplanner`, the two images, signing identity | 600 |
| `/var/lib/openplanner-updater/` | updater, installer | `previous.env` (rollback), lock, a copy of the ghcr.io login, cosign's cache | 700 |
| Docker volume `openplanner_data` | web, worker | the SQLite database and its update backups (personal data) | |
| Docker volume `openplanner_proxy-data` | proxy | certificate, private key, ACME account | |

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
| `production` | `prod` | web, worker, HTTPS proxy (Let's Encrypt) | real citizens; public DNS name, ports 80 and 443 |
| `test` | `test` | web, worker, HTTPS proxy (Let's Encrypt) | acceptance on its own public DNS name; `--no-tls` behind your own proxy |
| `lab` | `test` | web, worker, Keycloak, Mailpit; HTTPS proxy with `--tls-domain` | internal demo and integration tests (as on docker01) |

HTTPS is the standard shape (ADR 0064): `production` and `test` run the proxy unless `--no-tls`
says a load balancer of your own does TLS; a `lab` runs it when `--tls-domain <host>` names a
public DNS name. The script remembers the mode and that choice in `.env.update`, so later runs
need neither.

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
   `systemctl list-timers openplanner-updater.timer` shows the updater.

With the HTTPS proxy, a trial run against Let's Encrypt's staging CA first keeps you clear of its
rate limits: add `--acme-staging` on the first run, then follow *HTTPS certificate* in
`docs/runbook.md` to switch to the real CA; *System status* in the portal shows which
certificate is served and whether browsers trust it.

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

## Options

| Option | Effect |
|---|---|
| `--mode production\|test\|lab` | required on the first run; remembered afterwards |
| `--version X.Y.Z` | pin this release (pulled, signature verified; with the HTTPS proxy its image too, at the same version); without it a pinned release stays, and a first run takes the latest release. `X.Y.Z-rc.N` pins a candidate on a `test` or `lab` host |
| `--repository owner/name` | another repository's releases (default `3Corners-Labs/OpenPlanner`, or `UPDATE_REPOSITORY` from `.env`) |
| `--non-interactive` | never ask; take answers from the environment (also when there is no terminal) |
| `--no-tls` | production, test: no HTTPS proxy; asks the public address and your proxy's address (`FORWARDED_ALLOW_IPS`) |
| `--tls-domain HOST` | lab: run the HTTPS proxy for this host name (also sets it for the other modes) |
| `--own-certificate` | `proxy.env` names `certs/fullchain.pem` and `certs/privkey.pem` instead of ACME (runbook, *Own certificate*) |
| `--acme-staging` | with the HTTPS proxy: the first `proxy.env` points at Let's Encrypt's staging CA |
| `--upgrade-docker` | also upgrade Docker Engine (restarts every container on the host) |
| `--no-packages` | leave the host's packages alone; only check them |
| `--no-updater` | do not install the host updater (portal update requests then wait) |

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
| `PUBLIC_BASE_URL` or the OIDC URL on `http` in production, a missing OIDC client secret, `ENVIRONMENT` not matching the mode | stops with the list; nothing is pulled or started |
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
| `cannot find the latest release` | pass `--version`, or put a token in `UPDATE_CHECK_TOKEN` (`.env`) for a private repository |
| `cannot pull ghcr.io/...` | `docker login ghcr.io -u <github user>` with a `read:packages` token, run it again (the updater gets a copy of this login) |
| the portal reports *signature invalid* for a release that CI signed (`cosign verify` on the host says `no signatures found`) | the host's cosign is older than the one CI signs with (cosign 3 writes OCI bundles that cosign 2 cannot read; this hit docker01 at v0.3.0). Run the installer first, which upgrades cosign: `git fetch --tags && git checkout vX.Y.Z && ./scripts/install.sh`, then "Update now" again |
| `is not signed by a release of ...` | the image is not a signed release of this repository: check `--repository` and `--version`; never work around it |
| `Docker's release key has fingerprint ...` | the download was not Docker's key; check the network path (proxy, DNS) before trying again |
| `checksum of cosign_... does not match` | the download is damaged or not the release; run it again later |
| `.env needs changes:` | fix the listed settings in `.env`, run it again |
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
