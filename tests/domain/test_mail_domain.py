"""The sender domain check (ADR 0073): verdicts over SPF, DKIM and DMARC record texts."""

import pytest

from app.domain.mail_domain import (
    REASONS,
    DomainReport,
    Record,
    RecordCheck,
    Verdict,
    assess_dkim,
    assess_dmarc,
    assess_spf,
    check_domain,
    sender_domain,
)
from tests.fakes.dns import FakeTxtResolver

SPF_OK = "v=spf1 include:_spf.provider.example ip4:192.0.2.0/24 -all"
DKIM_OK = "v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC7"
DMARC_OK = "v=DMARC1; p=reject; rua=mailto:dmarc@demo.nl"


@pytest.mark.parametrize(
    ("records", "verdict", "reason"),
    [
        ((SPF_OK,), Verdict.OK, "ok"),
        (("v=spf1 include:_spf.google.com ~all",), Verdict.OK, "ok"),
        (("V=SPF1 -ALL",), Verdict.OK, "ok"),
        (("v=spf1 redirect=_spf.demo.nl",), Verdict.OK, "ok"),
        (("v=spf1 mx +all",), Verdict.WARNING, "spf_pass_all"),
        (("v=spf1 mx all",), Verdict.WARNING, "spf_pass_all"),
        (("v=spf1 mx ?all",), Verdict.WARNING, "spf_neutral_all"),
        (("v=spf1 mx",), Verdict.WARNING, "spf_neutral_all"),
        ((), Verdict.MISSING, "missing"),
        (("google-site-verification=abc",), Verdict.MISSING, "missing"),
        (("v=spf10 -all",), Verdict.MISSING, "missing"),
        ((SPF_OK, "v=spf1 -all"), Verdict.INVALID, "multiple"),
        (("v=spf1 includes:x -all",), Verdict.INVALID, "invalid"),
        (("v=spf1 ip4: -all",), Verdict.INVALID, "invalid"),
        (None, Verdict.UNKNOWN, "unknown"),
    ],
)
def test_spf_verdicts(records: tuple[str, ...] | None, verdict: Verdict, reason: str) -> None:
    assert assess_spf(records) == RecordCheck(Record.SPF, verdict, reason)


@pytest.mark.parametrize(
    ("records", "selector", "verdict", "reason"),
    [
        ((DKIM_OK,), "s1", Verdict.OK, "ok"),
        (("k=rsa; p=MIGfMA0G",), "s1", Verdict.OK, "ok"),  # v= is optional (RFC 6376)
        (("v=DKIM1; p=",), "s1", Verdict.INVALID, "dkim_revoked"),
        (("v=DKIM1; k=rsa",), "s1", Verdict.INVALID, "invalid"),
        (("v=DKIM2; p=abc",), "s1", Verdict.INVALID, "invalid"),
        ((DKIM_OK, DKIM_OK), "s1", Verdict.INVALID, "multiple"),
        ((), "s1", Verdict.MISSING, "missing"),
        (("some-other=record",), "s1", Verdict.MISSING, "missing"),
        (None, "s1", Verdict.UNKNOWN, "unknown"),
        ((DKIM_OK,), None, Verdict.NOT_CHECKED, "dkim_no_selector"),
    ],
)
def test_dkim_verdicts(
    records: tuple[str, ...] | None, selector: str | None, verdict: Verdict, reason: str
) -> None:
    assert assess_dkim(records, selector) == RecordCheck(Record.DKIM, verdict, reason)


@pytest.mark.parametrize(
    ("records", "verdict", "reason"),
    [
        ((DMARC_OK,), Verdict.OK, "ok"),
        (("v=DMARC1; p=quarantine; pct=100",), Verdict.OK, "ok"),
        (("v=dmarc1;p=reject",), Verdict.OK, "ok"),
        (("v=DMARC1; p=none; rua=mailto:x@demo.nl",), Verdict.WARNING, "dmarc_policy_none"),
        (("v=DMARC1; rua=mailto:x@demo.nl",), Verdict.INVALID, "invalid"),
        (("v=DMARC1; p=block",), Verdict.INVALID, "invalid"),
        ((DMARC_OK, DMARC_OK), Verdict.INVALID, "multiple"),
        ((), Verdict.MISSING, "missing"),
        (("v=spf1 -all",), Verdict.MISSING, "missing"),
        (None, Verdict.UNKNOWN, "unknown"),
    ],
)
def test_dmarc_verdicts(records: tuple[str, ...] | None, verdict: Verdict, reason: str) -> None:
    assert assess_dmarc(records) == RecordCheck(Record.DMARC, verdict, reason)


def test_the_finding_is_the_worst_verdict() -> None:
    def report(*verdicts: Verdict) -> DomainReport:
        checks = tuple(RecordCheck(r, v, "x") for r, v in zip(Record, verdicts, strict=True))
        return DomainReport("demo.nl", checks)

    assert report(Verdict.OK, Verdict.OK, Verdict.OK).finding is Verdict.OK
    assert report(Verdict.OK, Verdict.NOT_CHECKED, Verdict.OK).finding is Verdict.NOT_CHECKED
    assert report(Verdict.OK, Verdict.NOT_CHECKED, Verdict.WARNING).finding is Verdict.WARNING
    assert report(Verdict.UNKNOWN, Verdict.OK, Verdict.WARNING).finding is Verdict.UNKNOWN
    assert report(Verdict.MISSING, Verdict.UNKNOWN, Verdict.WARNING).finding is Verdict.MISSING
    assert report(Verdict.MISSING, Verdict.INVALID, Verdict.OK).finding is Verdict.INVALID


async def test_check_domain_asks_the_three_names_and_reports_each() -> None:
    resolver = FakeTxtResolver(
        {
            "demo.nl": (SPF_OK, "google-site-verification=abc"),
            "s1._domainkey.demo.nl": (DKIM_OK,),
            "_dmarc.demo.nl": ("v=DMARC1; p=none",),
        }
    )

    report = await check_domain(resolver, "demo.nl", "s1")

    assert resolver.asked == ["demo.nl", "s1._domainkey.demo.nl", "_dmarc.demo.nl"]
    assert report.domain == "demo.nl"
    assert [c.verdict for c in report.checks] == [Verdict.OK, Verdict.OK, Verdict.WARNING]
    assert report.finding is Verdict.WARNING


async def test_check_domain_without_selector_skips_the_dkim_lookup() -> None:
    resolver = FakeTxtResolver({"demo.nl": (SPF_OK,), "_dmarc.demo.nl": (DMARC_OK,)})

    report = await check_domain(resolver, "demo.nl", None)

    assert resolver.asked == ["demo.nl", "_dmarc.demo.nl"]
    assert report.checks[1] == RecordCheck(Record.DKIM, Verdict.NOT_CHECKED, "dkim_no_selector")
    assert report.finding is Verdict.NOT_CHECKED


async def test_a_failed_lookup_is_unknown_not_missing() -> None:
    resolver = FakeTxtResolver({"demo.nl": None, "_dmarc.demo.nl": (DMARC_OK,)})

    report = await check_domain(resolver, "demo.nl", None)

    assert report.checks[0] == RecordCheck(Record.SPF, Verdict.UNKNOWN, "unknown")
    assert report.checks[2].verdict is Verdict.OK


@pytest.mark.parametrize(
    ("address", "domain"),
    [
        ("afspraak@demo.nl", "demo.nl"),
        ("Gemeente Demo <afspraak@Demo.NL>", "demo.nl"),
        ("afspraak@demo.nl.", "demo.nl"),
    ],
)
def test_sender_domain(address: str, domain: str) -> None:
    assert sender_domain(address) == domain


@pytest.mark.parametrize(
    "check",
    [
        *(
            assess_spf(r)
            for r in [(SPF_OK,), (), ("v=spf1 +all",), ("v=spf1 ?all",), ("v=spf1 x",), None]
        ),
        *(
            assess_dkim(r, "s1")
            for r in [(DKIM_OK,), (), ("v=DKIM1; p=",), ("v=DKIM1; k=rsa",), None]
        ),
        assess_dkim(None, None),
        *(assess_dmarc(r) for r in [(DMARC_OK,), (), ("v=DMARC1; p=none",), ("v=DMARC1;",), None]),
    ],
    ids=lambda c: f"{c.record}-{c.reason}",
)
def test_every_reason_a_check_can_carry_is_listed(check: RecordCheck) -> None:
    assert check.reason in REASONS
