"""``deploy/keycloak/kcadm-as-admin.sh``: kcadm.sh as realm master's admin in its container.

The installer, ``scripts/check-proxy.sh`` and the runbook run Keycloak's admin CLI through this
one script inside the bundled identity provider's container. These tests run it with a stand-in
``kcadm.sh`` that records its arguments and its stdin; ``scripts/check-proxy.sh`` pass 4 runs it in
the real container (ADR 0126).
"""

import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "deploy/keycloak/kcadm-as-admin.sh"
SH = shutil.which("sh") or "/bin/sh"
RECORDING = """#!/bin/sh
# Stand-in kcadm.sh: one line with its arguments, then whatever came on stdin, then a blank line.
printf '%s\\n' "$*" >> "$LOG"
cat >> "$LOG"
printf '\\n\\n' >> "$LOG"
"""
REFUSING = """#!/bin/sh
# Stand-in kcadm.sh whose login is refused: records the call and fails.
printf '%s\\n\\n' "$*" >> "$LOG"
exit 1
"""


def run(
    tmp_path: Path, *args: str, stdin: str = "", kcadm: str = RECORDING, path: str = "/idp"
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the script as the container would, with a stand-in kcadm.sh; return the calls it made."""
    stand_in = tmp_path / "kcadm.sh"
    stand_in.write_text(kcadm)
    stand_in.chmod(0o755)
    log = tmp_path / "calls"
    environment = {
        "PATH": "/usr/bin:/bin",
        "KCADM": str(stand_in),
        "LOG": str(log),
        "TMPDIR": str(tmp_path),
        "KC_BOOTSTRAP_ADMIN_PASSWORD": "pw-from-the-container",
    }
    if path:
        environment["KC_HTTP_RELATIVE_PATH"] = path
    done = subprocess.run(  # noqa: S603 - the repository's own script, a stand-in kcadm.sh
        [SH, str(SCRIPT), *args],
        input=stdin,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = [call.strip("\n") for call in log.read_text().split("\n\n") if call.strip()]
    return done, calls


def test_it_logs_in_on_keycloaks_own_address_then_runs_the_command(tmp_path: Path) -> None:
    done, calls = run(tmp_path, "update", "realms/master", "-s", "attributes.frontendUrl=http://x")

    assert done.returncode == 0, done.stderr
    login, command = calls
    assert login.startswith("config credentials --config ")
    assert "--server http://localhost:8080/idp --realm master --user admin" in login
    assert login.endswith("--password pw-from-the-container")
    config = login.split()[3]
    assert command == f"update realms/master -s attributes.frontendUrl=http://x --config {config}"
    assert not Path(config).exists(), "the login is removed when the command ends"


def test_a_body_comes_on_stdin_to_the_command_and_never_as_an_argument(tmp_path: Path) -> None:
    body = '{"smtpServer":{"password":"secret #1"}}'

    done, calls = run(tmp_path, "update", "realms/openplanner", "-f", "-", stdin=body)

    assert done.returncode == 0, done.stderr
    login, command = calls
    assert body not in login  # the login reads nothing from stdin
    arguments, given = command.split("\n", 1)
    assert arguments.startswith("update realms/openplanner -f - --config ")
    assert body not in arguments
    assert given == body


def test_a_refused_login_runs_nothing(tmp_path: Path) -> None:
    done, calls = run(tmp_path, "update", "realms/master", kcadm=REFUSING, path="")

    assert done.returncode != 0
    assert len(calls) == 1
    assert calls[0].startswith("config credentials")
    assert "--server http://localhost:8080 --realm" in calls[0]  # without the proxy, no path
