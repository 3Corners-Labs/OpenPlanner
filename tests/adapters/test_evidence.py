"""The builders of the evidence report: what becomes a row, and what never does."""

from datetime import timedelta

from pydantic import SecretStr

from app.adapters.diagnostics import CheckResult
from app.adapters.evidence import (
    address_section,
    collector_failed,
    logs_section,
    oidc_section,
    retention_section,
    rotation_section,
    settings_section,
    tls_section,
    version_section,
)
from app.adapters.oidc_check import OidcInspection
from app.adapters.settings_report import describe
from app.config import Settings
from app.domain.audit import Category
from app.domain.evidence import Section, Status
from app.domain.key_rotation import RotationStatus
from app.domain.log_destinations import DestinationKind, DestinationStatus, LogDestination, State
from app.domain.public_address import AddressCheck, AddressFinding, AddressKind, Forwarded
from app.domain.retention import GroupRetention, RetentionOverview
from app.domain.scheduling import Group, GroupSummary
from app.domain.tls import Certificate, CertificateCheck, Finding, Trust
from tests.support.domain import NOW, make_group

DIGEST = "sha256:" + "ab" * 32


def _values(section: Section) -> dict[str, str]:
    return {row.name: row.value for row in section.rows}


def test_the_version_section_says_unknown_where_the_host_gave_nothing() -> None:
    bare = _values(version_section(Settings(environment="test")))
    known = _values(
        version_section(
            Settings(
                environment="test",
                app_version="1.2.3",
                app_commit="abc1234",
                app_image_digest=DIGEST,
            )
        )
    )

    assert bare["image digest"] == "unknown"
    assert bare["commit"] == "unknown"
    assert known["version"] == "1.2.3"
    assert known["image digest"] == DIGEST


def test_oidc_without_a_discovery_url_is_not_checked() -> None:
    section = oidc_section(None, Settings(environment="test"), None)

    assert section.status is Status.NOT_CHECKED


def test_oidc_lists_findings_the_pinned_issuer_and_what_the_provider_announced() -> None:
    settings = Settings(
        environment="test",
        oidc_discovery_url="https://idp.example.nl/.well-known/openid-configuration",
    )
    inspection = OidcInspection(
        [
            CheckResult("discovery", 200, True, "issuer https://idp.example.nl"),
            CheckResult("PKCE S256", None, False, "not advertised", required=False),
        ],
        {"response_types_supported": ("code",), "grant_types_supported": ()},
    )

    section = oidc_section(inspection, settings, "https://idp.example.nl")
    values = _values(section)

    assert section.status is Status.OK
    assert values["issuer pinned (compared exactly)"] == "https://idp.example.nl"
    assert values["discovery"] == "OK (HTTP 200): issuer https://idp.example.nl"
    assert values["PKCE S256"].startswith("WARN")
    assert values["response_types_supported"] == "code"
    assert values["grant_types_supported"] == "(none)"


def test_oidc_fails_on_a_required_finding_and_removes_credentials_from_addresses() -> None:
    settings = Settings(
        environment="test",
        oidc_discovery_url="https://idp.example.nl/.well-known/openid-configuration",
    )
    inspection = OidcInspection(
        [CheckResult("discovery", 200, False, "issuer https://u:zzpw@idp.example.nl differs")], {}
    )

    section = oidc_section(inspection, settings, "https://u:zzpw@idp.example.nl")

    assert section.status is Status.FAILED
    assert "zzpw" not in repr(section)


def test_tls_section_reports_the_certificate_and_fails_on_a_finding() -> None:
    certificate = Certificate("openplanner.nl", "R11", NOW, NOW + timedelta(days=20), ("a.nl",))
    ok = CertificateCheck(NOW, "openplanner.nl", Finding.OK, certificate, Trust.TRUSTED)
    bad = CertificateCheck(NOW, "openplanner.nl", Finding.UNREACHABLE, error="unreachable")

    assert _values(tls_section(ok, NOW))["days left"] == "20"
    assert tls_section(ok, NOW).status is Status.OK
    assert tls_section(bad, NOW).status is Status.FAILED
    assert _values(tls_section(bad, NOW))["error"] == "unreachable"


def test_address_section_names_each_address_by_host_only() -> None:
    checks = [
        AddressCheck(
            AddressKind.PUBLIC,
            NOW,
            "https://openplanner.nl",
            AddressFinding.OK,
            "https",
            Forwarded.HONOURED,
        ),
        AddressCheck(
            AddressKind.WEBHOOK,
            NOW,
            "https://hooks.example.nl",
            AddressFinding.UNREACHABLE,
            error="timeout",
        ),
    ]

    section = address_section(checks)

    assert section.status is Status.FAILED
    assert "public https://openplanner.nl" in _values(section)
    assert "error timeout" in _values(section)["webhook https://hooks.example.nl"]
    assert address_section(None).status is Status.NOT_CHECKED


def _destination() -> LogDestination:
    return LogDestination(
        1,
        "Splunk",
        DestinationKind.SPLUNK_HEC,
        "https://u:zzdestpass@splunk.example.org:8088/zzpath",
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


def test_log_destinations_show_host_state_and_counters_but_no_credential_or_error_text() -> None:
    failing = DestinationStatus(
        1, "worker", State.FAILING, sent=3, dropped=1, failed=2, last_error="zzerrortext"
    )

    section = logs_section(Settings(environment="test"), [_destination()], [failing])

    assert section.status is Status.WARNING
    text = repr(section)
    assert "https://splunk.example.org:8088" in text
    assert "credential set" in text
    assert "failing; sent 3, dropped 1, failed 2" in text
    for secret in ("zzdestpass", "zzpath", "zzerrortext"):
        assert secret not in text


def test_without_destinations_the_logs_stay_on_the_host() -> None:
    section = logs_section(Settings(environment="test", log_shipping_enabled=False), [], [])

    assert section.status is Status.INFO
    assert _values(section)["shipping kill switch (LOG_SHIPPING_ENABLED)"] == "off"
    assert "stdout" in _values(section)["destinations"]


def _group(overdue: int, failed: int) -> GroupRetention:
    group: Group = make_group()
    return GroupRetention(GroupSummary(group, "demo", "Demo"), 4, overdue, failed, NOW)


def test_retention_section_has_counts_and_fails_when_something_is_overdue() -> None:
    fine = RetentionOverview(NOW, (_group(0, 0),))
    late = RetentionOverview(NOW, (_group(2, 1),))

    assert retention_section(fine).status is Status.OK
    assert retention_section(late).status is Status.FAILED
    values = _values(retention_section(late))
    assert values["overdue (past the purge moment)"] == "2"
    assert values["failed purge jobs"] == "1"
    assert values["bookings holding personal data"] == "4"


def test_rotation_section_distinguishes_no_rotation_unfinished_and_done() -> None:
    assert rotation_section(RotationStatus(None, None)).status is Status.INFO
    assert rotation_section(RotationStatus(2, None)).status is Status.WARNING
    assert rotation_section(RotationStatus(0, 0)).status is Status.OK
    assert _values(rotation_section(RotationStatus(0, None)))[
        "open bookings still on the previous token key"
    ].startswith("-")


def test_settings_show_class_and_source_but_secrets_strings_and_paths_only_as_set() -> None:
    settings = Settings(
        environment="test",
        smtp_password=SecretStr("zzsecret"),
        installation_name="zzname",
        webex_api_base_url="https://u:zzurlpass@webexapis.example/v1/zzpath",
        rate_limit_bookings="7/hour",
    )

    section = settings_section(describe(settings, {"SMTP_PASSWORD": "x"}))
    values = _values(section)

    assert values["SMTP_PASSWORD"].endswith("; set")
    assert values["INSTALLATION_NAME"].endswith("; set")
    assert values["WEBEX_API_BASE_URL"].endswith("https://webexapis.example")
    assert "class " in values["SMTP_PASSWORD"]
    assert "source env" in values["SMTP_PASSWORD"]
    assert "zzsecret" not in repr(section)
    assert "zzname" not in repr(section)
    assert "zzurlpass" not in repr(section)


def test_a_failed_collector_keeps_only_the_exception_class() -> None:
    section = collector_failed("tls", "Certificate", ValueError("zzmessage with an address"))

    assert section.status is Status.FAILED
    assert _values(section) == {"collector failed": "ValueError"}
