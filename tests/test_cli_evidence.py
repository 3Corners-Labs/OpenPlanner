"""``python -m app.cli evidence``: one report, and no secret or personal data in it (WP-C2).

The installation under test holds a marker in every place a secret or personal data could leak
from: bootstrap secrets and integration secrets in the settings, credentials inside addresses,
a log destination's credential, a booking's personal fields in the database and a sealed
organisation secret. None of the markers may appear in the Markdown or the JSON.
"""

import asyncio
import json
import stat
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import installation_box
from app.cli import _evidence, main
from app.config import Settings
from app.domain.audit import Category
from app.domain.evidence import Status, render_json, render_markdown
from app.domain.log_destinations import DestinationKind, LogDestination
from app.domain.public_address import InstanceAnswer, InstanceReply
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.instance import FakeInstanceProbe
from tests.fakes.tls import FakeCertificateProbe
from tests.support.database import migrated_database
from tests.support.domain import ADA, NOW, SLOT_0900, TEST_TOKEN_KEY, book
from tests.support.seed_rows import seed_group

SECRETS = {
    "smtp_password": "zzmark-smtp-password",
    "oidc_client_secret": "zzmark-oidc-client-secret",
    "session_secret": "zzmark-session-secret-" + "x" * 16,
    "webex_client_secret": "zzmark-webex-client-secret",
    "webex_webhook_secret": "zzmark-webex-webhook-secret",
    "wxcc_webhook_secret": "zzmark-wxcc-webhook-secret",
    "update_check_token": "zzmark-update-token",
    "keycloak_directory_client_secret": "zzmark-keycloak-secret",
}
URL_PARTS = ("zzmark-url-user", "zzmark-url-pass", "zzmark-url-path", "zzmark-url-query")
TEXT_MARKS = ("zzmark-installation", "zzmark-smtp-user", "zzmark-acme@example.org")
PII = "zzmark-pii"
DESTINATION_CREDENTIAL = "zzmark-destination-credential"
ALL_MARKERS = (
    *SECRETS.values(),
    *URL_PARTS,
    *TEXT_MARKS,
    PII,
    DESTINATION_CREDENTIAL,
    "zzmark-settings-key",
    "zzmark-token-key",
)
SETTINGS_KEY = "zzmark-settings-key-" + "k" * 24
TOKEN_KEY = "zzmark-token-key-" + "t" * 24
DISCOVERY = f"https://{URL_PARTS[0]}:{URL_PARTS[1]}@idp.example.nl/.well-known/openid-configuration"


async def _populate(url: str) -> None:
    """Put a booking with personal data and a log destination with a credential in the database."""
    engine = create_engine(url)
    try:
        await seed_group(engine)
        uow = SqlUnitOfWork(
            engine, installation_box(Settings(settings_encryption_key=SETTINGS_KEY))
        )
        contact = replace(ADA, first_name=PII, last_name=f"{PII}-b", email=f"{PII}@example.org")
        await book(
            BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0),
            SLOT_0900,
            "balie",
            contact,
        )
        destination = LogDestination(
            None,
            "Splunk",
            DestinationKind.SPLUNK_HEC,
            "https://splunk.example.org:8088/services/zzmark-dest-path",
            "",
            "",
            "info",
            frozenset({Category.AUDIT}),
            False,
            False,
            True,
            True,
            "",
            NOW,
            NOW,
        )
        async with uow.locked() as tx:
            await tx.add_log_destination(destination, DESTINATION_CREDENTIAL)
    finally:
        await engine.dispose()


@pytest.fixture
def installation(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with a marker in every secret, address and free-text value, over a used database."""
    url = migrated_database(tmp_path, monkeypatch, "evidence")
    for key, value in SECRETS.items():
        monkeypatch.setenv(key.upper(), value)
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", SETTINGS_KEY)
    monkeypatch.setenv("MANAGEMENT_TOKEN_KEY", TOKEN_KEY)
    monkeypatch.setenv("INSTALLATION_NAME", TEXT_MARKS[0])
    monkeypatch.setenv("OIDC_DISCOVERY_URL", DISCOVERY)
    asyncio.run(_populate(url))
    return settings.model_copy(
        update={
            "database_url": url,
            "settings_encryption_key": SecretStr(SETTINGS_KEY),
            "management_token_key": SecretStr(TOKEN_KEY),
            **{key: SecretStr(value) for key, value in SECRETS.items()},
            "installation_name": TEXT_MARKS[0],
            "smtp_username": TEXT_MARKS[1],
            "acme_email": TEXT_MARKS[2],
            "oidc_discovery_url": DISCOVERY,
            "webex_api_base_url": (
                f"https://{URL_PARTS[0]}:{URL_PARTS[1]}@webexapis.example/v1/{URL_PARTS[2]}"
                f"?{URL_PARTS[3]}=1"
            ),
            "app_version": "1.2.3",
            "app_commit": "abc1234",
            "app_image_digest": "sha256:" + "cd" * 32,
        }
    )


def _discovery() -> httpx.MockTransport:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": []})
        return httpx.Response(
            200,
            json={
                "issuer": "https://idp.example.nl",
                "authorization_endpoint": "https://idp.example.nl/authorize",
                "token_endpoint": "https://idp.example.nl/token",
                "jwks_uri": "https://idp.example.nl/jwks",
                "code_challenge_methods_supported": ["S256"],
                "response_types_supported": ["code"],
            },
        )

    return httpx.MockTransport(route)


async def test_no_marker_reaches_the_markdown_or_the_json(installation: Settings) -> None:
    report = await _evidence(
        installation,
        oidc_transport=_discovery(),
        tls_probe=FakeCertificateProbe(),
        address_probe=FakeInstanceProbe(InstanceReply(InstanceAnswer("x", "https", None))),
    )

    for text in (render_markdown(report), render_json(report), repr(report)):
        for marker in ALL_MARKERS:
            assert marker not in text, marker
    assert [s.key for s in report.sections] == [
        "version",
        "oidc",
        "address",
        "tls",
        "hosts",
        "logs",
        "retention",
        "rotation",
        "settings",
    ]


async def test_the_report_bundles_the_measurements_that_exist(installation: Settings) -> None:
    report = await _evidence(
        installation,
        oidc_transport=_discovery(),
        tls_probe=FakeCertificateProbe(),
        address_probe=FakeInstanceProbe(),
    )
    sections = {s.key: s for s in report.sections}
    rows = {key: {r.name: r.value for r in s.rows} for key, s in sections.items()}

    assert rows["version"]["version"] == "1.2.3"
    assert rows["version"]["image digest"] == "sha256:" + "cd" * 32
    assert rows["oidc"]["issuer pinned (compared exactly)"] == "https://idp.example.nl"
    assert rows["oidc"]["response_types_supported"] == "code"
    assert sections["tls"].status is Status.FAILED  # the test settings use http
    assert rows["hosts"]["video (WEBEX_API_BASE_URL)"] == "https://webexapis.example"
    assert "log destination Splunk (splunk_hec)" in rows["hosts"]
    assert rows["logs"]["Splunk (splunk_hec)"].startswith(
        "https://splunk.example.org:8088; enabled"
    )
    assert rows["retention"]["bookings holding personal data"] == "1"
    assert rows["rotation"]["rotation"].startswith("none under way")
    assert rows["settings"]["SMTP_PASSWORD"].endswith("; set")
    assert rows["settings"]["INSTALLATION_NAME"].endswith("; set")
    assert not report.ok


async def test_a_broken_collector_fails_its_section_and_leaves_the_others(
    installation: Settings, tmp_path: Path
) -> None:
    unmigrated = f"sqlite+aiosqlite:///{tmp_path / 'zzmark-empty.db'}"
    broken = installation.model_copy(update={"database_url": unmigrated})

    report = await _evidence(
        broken, tls_probe=FakeCertificateProbe(), address_probe=FakeInstanceProbe()
    )
    by_key = {s.key: s for s in report.sections}

    assert by_key["retention"].status is Status.FAILED
    assert by_key["version"].status is Status.INFO
    assert "zzmark-empty" not in render_markdown(report)


def test_the_command_prints_json_and_exits_1_while_a_section_failed(
    installation: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OIDC_DISCOVERY_URL")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("PUBLIC_ADDRESS_CHECK_ENABLED", "false")

    code = main(["evidence", "--format", "json"])

    document = json.loads(capsys.readouterr().out)
    assert code == 1  # the public address is http: the certificate check finds nothing
    assert document["ok"] is False
    assert {s["key"] for s in document["sections"]} >= {"oidc", "tls", "settings"}
    assert next(s for s in document["sections"] if s["key"] == "oidc")["status"] == "not_checked"
    for marker in ALL_MARKERS:
        assert marker not in json.dumps(document), marker


def test_out_writes_both_files_readable_by_the_owner_only(
    installation: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OIDC_DISCOVERY_URL")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("PUBLIC_ADDRESS_CHECK_ENABLED", "false")
    out = tmp_path / "evidence"

    main(["evidence", "--out", str(out)])

    markdown, document = out / "evidence.md", out / "evidence.json"
    assert markdown.read_text().startswith("# OpenPlanner evidence report")
    assert json.loads(document.read_text())["schema"] == 1
    assert stat.S_IMODE(markdown.stat().st_mode) == 0o600
    assert stat.S_IMODE(document.stat().st_mode) == 0o600
    assert "evidence.md" in capsys.readouterr().out
