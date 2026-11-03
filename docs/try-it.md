# Trying OpenPlanner on your own computer

For anyone who wants to see OpenPlanner run: a laptop, no server, no account anywhere, about
five minutes the first time. For a server with a public name and HTTPS, use
`docs/install.md` instead; this page is the evaluation path.

What you get: the booking page, the admin portal with a login (a development Keycloak with one
account per role), a mailbox that catches every email the system "sends" (Mailpit), and a demo
group with free times. Everything listens on `127.0.0.1` only; nothing is reachable from the
network, and no real email, SMS or video call is made (Webex is not connected).

## What you need

| | |
|---|---|
| A computer | macOS, Windows 10/11 or Linux, 4 GB free memory, 5 GB free disk |
| Docker | [Docker Desktop](https://docs.docker.com/get-docker/) (macOS, Windows) or Docker Engine with the Compose plugin (Linux); Compose 2.24 or newer |
| The code | `git clone`, or *Code → Download ZIP* on GitHub (or a release's source archive) and unpack it |
| Free ports | 8000 (OpenPlanner), 8080 (Keycloak), 8025 (Mailpit) |

### Which Docker

What matters is a **Docker Engine (dockerd) with Compose 2.24 or newer**; the project uses no
Desktop-only feature. The images are built on your machine, so an Intel or Apple Silicon
(arm64) computer both work.

| Platform | Status |
|---|---|
| Docker Desktop (macOS, Windows with WSL 2 backend) | supported, tested (Docker Desktop on Apple Silicon) |
| Docker Engine + Compose plugin (Linux) | supported; the same engine runs the servers of `docs/install.md` |
| Colima (macOS, Linux), Rancher Desktop in *dockerd (moby)* mode, OrbStack | expected to work (same engine, same Compose); not tested by us |
| Podman, Rancher Desktop in *containerd* mode, Docker-compatible shims | **not supported**: their compose does not read `env_file` with `required: false` and the fixed network of `docker-compose.yml`; the script warns when it sees Podman |

Docker Desktop's licence needs a paid plan for larger companies; Docker Engine on Linux, Colima
and Rancher Desktop are free software and a fine choice there.

Python, Node and uv are **not** needed: the images are built inside Docker.

## Start

macOS, Linux, Windows with WSL or Git Bash, from the folder you cloned or unpacked:

```sh
./scripts/try.sh
```

Windows without WSL (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\try.ps1
```

The script checks Docker, the Compose version and the three ports, writes `.env` from
`.env.example`, builds and starts the containers, waits until the application is ready, loads the
demo group, prints the addresses and opens the booking page. The first run takes a few minutes
(it downloads base images and builds); later runs take seconds. Running it again changes nothing
that is in order.

| Command | Does |
|---|---|
| `try.sh` or `try.sh up` | start (add `--no-open` to keep the browser closed) |
| `try.sh status` | containers and addresses |
| `try.sh logs` | follow the logs (Ctrl-C leaves the containers running) |
| `try.sh down` | stop, keep the data |
| `try.sh reset` | stop and delete the data: a clean start next time |

On Windows the same words follow `try.ps1` (`try.ps1 down`, `-NoOpen`).

## What to try

1. **As a participant**: open <http://127.0.0.1:8000/g/demo>, pick a day and time, fill in the form,
   then open the mailbox at <http://127.0.0.1:8025> for the confirmation email and click its link
   to confirm. Move or cancel from the management link in the same email.
2. **As staff**: open <http://127.0.0.1:8000/admin/> and log in as `super` (password
   `dev-only-password`). Look at the agenda, the group's settings, branding, texts, the audit
   log and *System status*. Log in as `orgadmin`, `groupadmin`, `auditor` or `agent` in a private
   window to see what each role may do; `norole` shows the "no access" page.
3. **Embed it**: `docs/widget.md` describes the Web Component for your own website.
4. **Connect Webex** (optional): the integrations pages of the portal take the credentials for
   Webex Meetings and Webex Contact Center; `docs/runbook.md` says how to check them.

The accounts exist only in this development setup and the application refuses them in
production (`dev/keycloak/README.md`).

## Problems

| You see | Do |
|---|---|
| *Docker is not installed* / *not running* | install or start Docker Desktop; on Linux `sudo systemctl start docker` and add yourself to the `docker` group (log out and in) |
| *port(s) 8000 … are in use* | stop the other program, or a previous OpenPlanner (`try.sh down` in its folder) |
| *Compose … is too old* | update Docker Desktop, or install the `docker-compose-plugin` package |
| The first start never gets ready | `try.sh logs`; on Docker Desktop give Docker at least 4 GB of memory in its settings |
| The portal login fails after a `reset` | clear this site's cookies for `127.0.0.1` and try again |
| Anything odd | `try.sh reset`, then `try.sh` |

Report what you find at <https://github.com/3Corners-Labs/OpenPlanner/issues>; no personal data
in the report.

## Remove it

```sh
./scripts/try.sh reset
```

then delete the folder; `docker image prune` frees the built images.

## Next: a real installation

`docs/install.md` installs a signed release on an Ubuntu or Debian server with HTTPS and the
host updater, and `docs/runbook.md` runs it. The development accounts and the mail catcher of
this page are not part of that shape.
