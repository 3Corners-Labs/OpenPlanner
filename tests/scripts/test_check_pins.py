import pytest

from scripts.check_pins import ROOT, check_lines, files

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def _reasons(*lines: str) -> list[str]:
    return [violation.reason for violation in check_lines("ci.yml", lines)]


def test_action_pinned_to_sha_with_version_comment_passes() -> None:
    assert _reasons(f"      - uses: actions/checkout@{SHA} # v7.0.1") == []


@pytest.mark.parametrize(
    "line",
    [
        "      - uses: actions/checkout@v4",
        f"      - uses: actions/checkout@{SHA}",
        "        uses: anchore/sbom-action@main # v0.24.3",
    ],
)
def test_action_on_a_tag_or_without_version_comment_fails(line: str) -> None:
    ref = line.split("uses: ")[1].split(" ")[0]
    assert _reasons(line) == [f"action {ref} is not pinned as <sha> # vX.Y.Z"]


def test_local_actions_need_no_pin() -> None:
    assert _reasons("      - uses: ./.github/actions/setup") == []


def test_annotated_version_passes() -> None:
    assert (
        _reasons(
            "  # renovate: datasource=github-releases depName=astral-sh/uv",
            '  UV_VERSION: "0.13.6"',
        )
        == []
    )


@pytest.mark.parametrize(
    "line",
    [
        '  UV_VERSION: "0.13.6"',
        "JSCPD_VERSION := 5.4.2",
        "          docker run --rm ghcr.io/gitleaks/gitleaks:v8.30.1 git /repo",
        "\tnpx --yes jscpd@4.0.5",
    ],
)
def test_unannotated_version_fails(line: str) -> None:
    assert _reasons(line) == ["version without a `# renovate:` line above it"]


def test_annotation_covers_only_the_next_line() -> None:
    assert _reasons(
        "# renovate: datasource=npm depName=jscpd",
        "JSCPD_VERSION := 5.4.2",
        "OTHER_VERSION := 1.2.3",
    ) == ["version without a `# renovate:` line above it"]


@pytest.mark.parametrize(
    "line",
    [
        "    container: semgrep/semgrep:1.182.1",
        "    runs-on: ubuntu-24.04",
        "          schema: http://127.0.0.1:8000/openapi.json",
        "      # ADR 0029: 1.4.0 on a v1.4.0 tag",
        "          build-args: APP_VERSION=${{ steps.meta.outputs.version }}  # ADR 0029: 1.4.0",
        "    timeout-minutes: 45",
        "      - name: keyboard flows + axe (WCAG 2.2 AA, zero violations)",
        'ALLOWED="MIT;Apache-2.0 OR BSD-3-Clause;PSF-2.0"',
    ],
)
def test_native_managers_addresses_and_prose_are_not_pins(line: str) -> None:
    assert _reasons(line) == []


def test_a_minimum_with_a_reason_is_not_a_pin() -> None:
    assert _reasons("# not a pin: Compose 2.24 adds env_file.required", "COMPOSE_MIN=2.24.0") == []


def test_not_a_pin_needs_a_reason() -> None:
    assert _reasons("# not a pin:", "COMPOSE_MIN=2.24.0") == [
        "version without a `# renovate:` line above it"
    ]


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ("COMPOSE_MIN=2.24.0", True),
        ('pebble="$(docker create ghcr.io/letsencrypt/pebble:2.8.0)"', True),
        ("uv run --with python-docx==1.2.0 python", True),
        ('  [[ -z $VERSION ]] || die "--version must look like 1.2.3"', False),
        ("READY_URL=http://127.0.0.1:8000/readyz", False),
    ],
)
def test_shell_scripts_count_only_real_pins(line: str, flagged: bool) -> None:
    assert bool(list(check_lines("scripts/install.sh", [line]))) is flagged


def test_shell_scripts_and_hooks_are_checked() -> None:
    checked = {path.relative_to(ROOT).as_posix() for path in files(ROOT)}
    assert {"scripts/check-proxy.sh", "scripts/updater/openplanner-update.sh"} <= checked


def test_repository_has_no_untracked_pins() -> None:
    violations = [
        str(violation)
        for path in files(ROOT)
        for violation in check_lines(str(path), path.read_text(encoding="utf-8").splitlines())
    ]
    assert violations == []
