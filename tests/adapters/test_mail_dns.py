"""The TXT lookup on dnspython (ADR 0073): joined strings, empty answers, failures as None."""

import io
import json
from typing import Any

import dns.resolver
import pytest
import structlog

from app.adapters.mail_dns import DnsTxtResolver
from app.logging import configure_logging


class FakeAnswer:
    def __init__(self, *records: tuple[bytes, ...]) -> None:
        self.records = [type("Rdata", (), {"strings": strings})() for strings in records]

    def __iter__(self) -> Any:
        return iter(self.records)


class FakeResolver:
    """``dns.asyncresolver.Resolver`` fake: answers per name, or raises what the test put in."""

    def __init__(self, answers: dict[str, FakeAnswer | Exception]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, float, bool]] = []

    async def resolve(self, name: str, rdtype: str, lifetime: float, search: bool) -> FakeAnswer:
        self.calls.append((name, rdtype, lifetime, search))
        answer = self.answers[name]
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_txt_strings_of_one_record_are_joined() -> None:
    fake = FakeResolver(
        {
            "demo.nl": FakeAnswer(
                (b"v=spf1 include:_spf.provider.example ", b"-all"),
                (b"google-site-verification=abc",),
            )
        }
    )

    records = await DnsTxtResolver(lifetime=2.5, resolver=fake).txt_records("demo.nl")

    assert records == ("v=spf1 include:_spf.provider.example -all", "google-site-verification=abc")
    assert fake.calls == [("demo.nl", "TXT", 2.5, False)]


@pytest.mark.parametrize(
    "failure", [dns.resolver.NXDOMAIN(), dns.resolver.NoAnswer()], ids=["nxdomain", "no_answer"]
)
async def test_a_name_without_records_is_an_empty_answer(failure: Exception) -> None:
    fake = FakeResolver({"_dmarc.demo.nl": failure})

    assert await DnsTxtResolver(resolver=fake).txt_records("_dmarc.demo.nl") == ()


@pytest.mark.parametrize(
    "failure",
    [
        dns.resolver.LifetimeTimeout(),
        dns.resolver.NoNameservers(),
        dns.resolver.NoResolverConfiguration(),
    ],
    ids=["timeout", "no_nameservers", "no_configuration"],
)
async def test_a_failed_lookup_is_none_and_logged_without_the_answer(failure: Exception) -> None:
    stream = io.StringIO()
    configure_logging("WARNING", stream=stream)
    fake = FakeResolver({"demo.nl": failure})

    assert await DnsTxtResolver(resolver=fake).txt_records("demo.nl") is None

    structlog.reset_defaults()
    event = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert (event["message"], event["domain"], event["error"]) == (
        "dns.lookup_failed",
        "demo.nl",
        type(failure).__name__,
    )
