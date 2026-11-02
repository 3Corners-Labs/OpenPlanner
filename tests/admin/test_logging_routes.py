"""The *Log destinations* screen: create, change, test, switch, delete (ADR 0085 step 4).

The write-only credential (nothing secret on a page or in the audit log), the verdict of a
test line, the closed list of reasons the page may show, and what each role sees.
"""

import re
from collections.abc import AsyncIterator

import pytest

from app.config import Settings
from app.domain.audit import Outcome
from app.domain.log_destinations import Reason
from tests.admin.conftest import AUDITOR, ORG_ADMIN, SAME_ORIGIN, SUPER, Site, _running_site
from tests.admin.test_admin_flow import audit_events

LOGGING = "/admin/logging"
TOKEN = "hec-token-0123456789"
NEW = {
    "name": "Splunk production",
    "kind": "splunk_hec",
    "address": "https://splunk.example.org:8088",
    "secret": TOKEN,
    "index": "openplanner",
    "source": "",
    "min_level": "warning",
    "category_audit": "on",
    "category_security": "on",
}


async def create(site: Site, **overrides: str) -> str:
    """Create a destination through the form; return the page of the destination."""
    data = {**NEW, **overrides}
    response = await site.client.post(LOGGING, data=data, headers=SAME_ORIGIN)
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert re.fullmatch(r"/admin/logging/\d+\?done=logging_created", location)
    return location.split("?")[0]


@pytest.fixture
async def superuser(site: Site) -> Site:
    await site.login_as(SUPER)
    return site


async def test_the_entry_is_in_the_platform_section_for_those_who_may_see_it(site: Site) -> None:
    for admin, shown in ((SUPER, True), (AUDITOR, True), (ORG_ADMIN, False)):
        await site.login_as(admin)
        page = await site.client.get("/admin/")
        assert (f'href="{LOGGING}"' in page.text) is shown, admin.subject


async def test_with_no_destination_the_list_says_the_log_stays_on_standard_output(
    superuser: Site,
) -> None:
    page = await superuser.client.get(LOGGING)

    assert page.status_code == 200
    assert "standaarduitvoer" in page.text
    assert f'href="{LOGGING}/new"' in page.text


async def test_the_superuser_creates_a_destination_switched_off_and_untested(
    superuser: Site,
) -> None:
    url = await create(superuser)

    page = await superuser.client.get(url)
    assert page.status_code == 200
    assert "Splunk production" in page.text
    assert "Niet getest" in page.text
    assert ">Uit<" in page.text
    assert TOKEN not in page.text  # write-only
    assert "Opgeslagen (niet getoond)" in page.text
    listing = await superuser.client.get(LOGGING)
    assert "Splunk production" in listing.text
    assert "https://splunk.example.org:8088" in listing.text
    events = await audit_events(superuser)
    assert ("sub-super", "logging.destination", Outcome.SUCCESS, "") in events


async def test_nothing_secret_reaches_any_page_or_the_audit_log(superuser: Site) -> None:
    url = await create(superuser)
    await superuser.client.post(
        url, data={**NEW, "secret": "rotated-token-9876", "min_level": "error"}, headers=SAME_ORIGIN
    )

    for path in (LOGGING, url, f"{LOGGING}/new"):
        text = (await superuser.client.get(path)).text
        assert TOKEN not in text
        assert "rotated-token-9876" not in text
    app = superuser.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    from app.adapters.db.uow import SqlUnitOfWork  # noqa: PLC0415
    from app.domain.authorization import Scope  # noqa: PLC0415

    async with SqlUnitOfWork(app.state.engine).read() as tx:
        stored = repr(await tx.list_audit_events(Scope(everything=True), limit=50))
    assert TOKEN not in stored
    assert "rotated-token-9876" not in stored
    assert "splunk.example.org" not in stored


async def test_a_refused_form_shows_the_error_at_its_field_and_keeps_what_was_typed(
    superuser: Site,
) -> None:
    response = await superuser.client.post(
        LOGGING, data={**NEW, "address": "ftp://nowhere.example.org"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert 'id="address-error"' in response.text
    assert 'value="Splunk production"' in response.text
    assert TOKEN not in response.text  # the credential is never echoed back


async def test_an_unknown_kind_is_refused_not_a_crash(superuser: Site) -> None:
    response = await superuser.client.post(
        LOGGING, data={**NEW, "kind": "carrier-pigeon"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422


async def test_a_test_line_that_arrives_marks_the_destination_tested(superuser: Site) -> None:
    url = await create(superuser)

    response = await superuser.client.post(f"{url}/test", headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert response.headers["location"] == f"{url}?tested=ok"
    page = await superuser.client.get(response.headers["location"])
    assert "De testregel is aangekomen" in page.text
    assert "Niet getest" not in page.text
    assert superuser.log_senders.built  # delivered through the destination's own sender
    assert b"logging.test" in str(superuser.log_senders.last.lines).encode()


@pytest.mark.parametrize(
    ("reason", "code", "text"),
    [
        (Reason.REFUSED, 403, "controleer het token"),
        (Reason.TLS, None, "certificaat werd niet vertrouwd"),
        (Reason.UNREACHABLE, None, "niet te bereiken"),
    ],
)
async def test_a_failed_test_line_shows_the_reason_and_the_code_only(
    superuser: Site, reason: Reason, code: int | None, text: str
) -> None:
    url = await create(superuser)
    original = superuser.log_senders.__class__.__call__

    def failing(self, target, timeout):  # type: ignore[no-untyped-def]
        sender = original(self, target, timeout)
        sender.failures = [reason]
        sender.code = code
        return sender

    superuser.log_senders.__class__.__call__ = failing  # type: ignore[method-assign,assignment]
    try:
        response = await superuser.client.post(f"{url}/test", headers=SAME_ORIGIN)
    finally:
        superuser.log_senders.__class__.__call__ = original  # type: ignore[method-assign]

    page = await superuser.client.get(response.headers["location"])
    assert "De testregel is niet aangekomen" in page.text
    assert text in page.text
    events = await audit_events(superuser)
    assert ("sub-super", "logging.destination", Outcome.FAILURE, reason.value) in events


async def test_the_query_cannot_put_text_on_the_page(superuser: Site) -> None:
    url = await create(superuser)

    page = await superuser.client.get(
        f"{url}?test_failed=<script>alert(1)</script>&code=abc&tested=yes"
    )

    assert "<script>alert(1)" not in page.text
    assert "De testregel is aangekomen" not in page.text
    assert "De testregel is niet aangekomen" not in page.text


async def test_switching_on_and_off_and_deleting(superuser: Site) -> None:
    url = await create(superuser)

    on = await superuser.client.post(f"{url}/enable", data={"enabled": "on"}, headers=SAME_ORIGIN)
    assert on.headers["location"].endswith("done=logging_enabled")
    assert ">Aan<" in (await superuser.client.get(url)).text
    again = await superuser.client.post(
        f"{url}/enable", data={"enabled": "on"}, headers=SAME_ORIGIN
    )
    assert again.headers["location"].endswith("done=unchanged")
    off = await superuser.client.post(f"{url}/enable", headers=SAME_ORIGIN)
    assert off.headers["location"].endswith("done=logging_disabled")

    gone = await superuser.client.post(f"{url}/delete", headers=SAME_ORIGIN)
    assert gone.headers["location"] == f"{LOGGING}?done=removed"
    assert (await superuser.client.get(url)).status_code == 404


async def test_the_auditor_reads_but_has_no_forms_or_buttons(site: Site) -> None:
    await site.login_as(SUPER)
    url = await create(site)
    await site.login_as(AUDITOR)

    page = await site.client.get(url)
    listing = await site.client.get(LOGGING)

    assert page.status_code == listing.status_code == 200
    assert "Splunk production" in listing.text
    assert f"{url}/test" not in page.text
    assert 'name="secret"' not in page.text
    assert f'href="{LOGGING}/new"' not in listing.text
    for method, path in (("post", url), ("post", f"{url}/test"), ("get", f"{LOGGING}/new")):
        denied = await getattr(site.client, method)(path, headers=SAME_ORIGIN)
        assert denied.status_code == 403, path


async def test_the_status_of_each_process_is_shown_without_any_platform_text(
    superuser: Site,
) -> None:
    url = await create(superuser)
    destination_id = int(url.rsplit("/", 1)[1])
    app = superuser.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    from app.adapters.db.uow import SqlUnitOfWork  # noqa: PLC0415
    from app.domain.log_destinations import DestinationStatus, State  # noqa: PLC0415

    async with SqlUnitOfWork(app.state.engine).locked() as tx:
        await tx.save_log_destination_status(
            DestinationStatus(
                destination_id, "worker", State.FAILING, failed=3, dropped=7, last_error="tls"
            ),
            superuser.clock.now(),
        )
        await tx.save_log_destination_status(
            DestinationStatus(destination_id, "web", State.OK, sent=120, queued=2),
            superuser.clock.now(),
        )

    page = await superuser.client.get(url)

    assert "worker" in page.text
    assert "120" in page.text
    assert "certificaat werd niet vertrouwd" in page.text
    assert ">Mislukt<" in page.text
    superuser.log_senders.built.clear()


async def test_system_status_summarises_the_destinations_for_those_who_may_see_them(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    empty = await site.client.get("/admin/settings")
    assert "Geen logbestemming" in empty.text
    url = await create(site)
    await site.client.post(f"{url}/enable", data={"enabled": "on"}, headers=SAME_ORIGIN)

    full = await site.client.get("/admin/settings")
    assert "1 ingesteld, 1 aan." in full.text
    assert 'href="/admin/logging"' in full.text

    await site.login_as(AUDITOR)
    assert "1 ingesteld, 1 aan." in (await site.client.get("/admin/settings")).text


# ADR 0085 point 7: the host's kill switch.


@pytest.fixture
async def switched_off(settings: Settings, admin_db: str) -> AsyncIterator[Site]:
    async with _running_site(
        settings.model_copy(update={"log_shipping_enabled": False}), admin_db
    ) as s:
        yield s


async def test_the_kill_switch_stops_test_lines_and_the_screen_says_so(switched_off: Site) -> None:
    await switched_off.login_as(SUPER)
    url = await create(switched_off)

    listing = await switched_off.client.get(LOGGING)
    page = await switched_off.client.get(url)
    test = await switched_off.client.post(f"{url}/test", headers=SAME_ORIGIN)

    assert "staat uit op de host" in listing.text
    assert "staat uit op de host" in page.text
    assert test.status_code == 409
    assert "staat uit op de host" in test.text
    assert not switched_off.log_senders.built  # nothing was built, nothing was sent
    # The configuration stays: the destination can still be changed and switched on.
    assert (await switched_off.client.get(url)).status_code == 200


async def test_with_the_switch_on_the_screen_has_no_such_notice(superuser: Site) -> None:
    await create(superuser)

    assert "staat uit op de host" not in (await superuser.client.get(LOGGING)).text
