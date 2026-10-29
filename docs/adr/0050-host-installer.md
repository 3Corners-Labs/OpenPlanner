# 0050. One host installer that installs, repairs and re-checks

Status: proposed (2026-10-29), requested by the product owner. Extends ADR 0029 (host updater)
and ADR 0030 (HTTPS proxy).

## Context

Installing a host took manual steps spread over the README, the runbook and the updater's
example file, and they did not agree: the runbook pinned the image in `/opt/openplanner`, the
updater's example pointed there too, while hosts keep the compose project in the operator's
home. On docker01 (2026-10-28) this cost an evening of interactive fixing:

- the host updater was never installed, so a portal update waited for 45 minutes without a
  message on the host;
- installed as documented, the updater could not have reached `/home` (the unit has
  `ProtectHome=yes`), nor pulled the private image as root, nor kept cosign's cache;
- an HTTPS proxy had been started on a lab host without `proxy.env` and restarted for ever;
- `.env` held three keys twice; Compose silently took the last value;
- the example `updater.env` lost its backslashes when sourced, so `\.` in the signing identity
  matched any character;
- cosign was a major version behind, and a containerd security update was waiting.

## Decision

1. `scripts/install.sh` is the one way to install and to repair a host. It runs as the
   operator account from the git checkout, which is the installation directory, by convention
   `/home/<user>/openplanner`. Nothing is placed in `/opt`. It uses `sudo` only for packages and
   the host updater, and refuses to run as root.
2. Every run is safe to repeat. It repairs what needs no person (keys set twice: the last
   value stays, after a backup; missing generated keys; file modes; the updater's files and
   units) and stops with a list for what does (an `http` address in production, a missing
   client secret, a mode that does not match `ENVIRONMENT`). It never overwrites a value a
   person chose.
3. Host packages are brought to their newest version on every run on Ubuntu and Debian: the
   tools from apt, the Compose plugin, and cosign from its latest release, checked against the
   release's checksums. A missing Docker Engine comes from Docker's apt repository, its key
   checked against a pinned fingerprint. An existing engine is upgraded only with
   `--upgrade-docker`, because that restarts every container on the host, other applications
   included; without the flag the waiting updates are reported.
4. The release image is pulled, verified with `cosign verify` against this repository's release
   workflow and pinned by digest before anything starts. Without `--version` an existing pin
   stays, so a rerun never upgrades or downgrades the application by accident.
5. `.env.update` carries, besides the pinned image, `COMPOSE_PROFILES` (`tls` in production,
   `dev` in a lab) and `OPENPLANNER_MODE`. The host updater replaces only the image line. So
   `docker compose --env-file .env.update <command>` is the single form for every Compose
   command, and starts exactly the installation's services.
6. The updater's unit keeps `ProtectHome=yes`; the installer adds a drop-in that opens only the
   checkout (`ProtectHome=read-only`, `ReadWritePaths=<checkout>`) and gives the service its own
   `HOME` in `/var/lib/openplanner-updater`, with a copy of the operator's registry login.
7. The bootstrap keys (`SETTINGS_ENCRYPTION_KEY`, `MANAGEMENT_TOKEN_KEY`, `SESSION_SECRET`) live
   in `secrets/secrets.env` (mode 600, directory 700), the file `python -m app.cli init` writes
   and Compose reads after `.env` (ADR 0042 §9); not in `.env`, which operators open, copy and
   share far more often. The installer writes that file itself, so it also works with a release
   that predates `init`; a test keeps its key list equal to `init`'s. Keys an older
   installation kept in `.env` move there with their values. Nothing in the file is overwritten,
   and a key with two different values stops the run (a new `SETTINGS_ENCRYPTION_KEY` makes
   every stored secret unreadable). Decided by the product owner on 2026-10-29.
8. Values in written env files are single-quoted; secrets are read without echo and never put
   on a command line.
9. `docs/install.md` documents every path, who writes it and its mode, the modes, the
   prerequisites, the options and every message the script stops with.

## Settings

None of the application. The installer's options (`--mode`, `--version`, `--repository`,
`--acme-staging`, `--upgrade-docker`, `--no-packages`, `--no-updater`, `--non-interactive`) are
host operations, not settings; they choose what the installer does in one run. `HEALTH_TIMEOUT`
(seconds, default 300) is the existing updater setting of ADR 0029 (class B, host), now also
read by the installer.

**Fixed on purpose:** Docker's release key fingerprint (a trust anchor, changed only by a
reviewed PR), the minimum Compose version 2.24.0 (needed for `env_file` with `required`), the
installation layout of decision 1, and the signing identity pattern (release tags of the
`main` workflow).

## Consequences

- One script to read for operators; `docs/install.md`, the runbook and the README now agree on
  one path.
- The installer is tested like the updater, against fake host tools
  (`tests/scripts/test_install.py`), and shellchecked in CI.
- A host's checkout must follow its release: after a portal update, `git checkout` the same tag
  and run the installer again (documented in `docs/install.md`).
- New host package sources (Docker's apt repository, cosign releases) are recorded in
  `docs/dependencies.md`; the threat model's update flow gains the supply-chain, privilege and
  secrets rows.
- Hosts that use Ubuntu's `docker.io` keep it; the script does not switch package families,
  because that would replace the engine under running containers.
