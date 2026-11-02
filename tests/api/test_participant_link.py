"""The management link is exchanged for a participant cookie (ADR 0093 steps 1 and 2).

``GET /b/{token}`` checks the token, sets ``op_link`` and answers 303 to ``/b/m/{public_ref}``,
which holds no credential. Without the cookie the page asks to open the email link again.
"""

import json
from datetime import timedelta

import httpx
import pytest

from app.config import Settings
from tests.api.test_booking_pages import ADA, FRIDAY_0900, SAME_ORIGIN
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.participant import confirm_by_link, emailed_token, open_link

COOKIE = "op_link"


async def _book(client: httpx.AsyncClient, start: str = FRIDAY_0900) -> str:
    """Book and return the emailed management token (the booking stays unconfirmed)."""
    response = await client.post("/g/demo/book", data={"start": start, **ADA}, headers=SAME_ORIGIN)
    assert response.status_code == 303
    return await emailed_token(client)


@pytest.fixture
async def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
async def site(demo_settings: Settings, clock: FixedClock) -> httpx.AsyncClient:  # type: ignore[misc]
    async with app_client(demo_settings, clock) as client:
        yield client


async def test_a_valid_token_is_exchanged_for_a_cookie_and_a_page_without_it(
    site: httpx.AsyncClient,
) -> None:
    token = await _book(site)

    response = await site.get(f"/b/{token}")

    assert response.status_code == 303
    target = response.headers["location"]
    assert target.startswith("/b/m/")
    assert token not in target
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    assert token not in cookie
    for attribute in ("HttpOnly", "SameSite=lax", "Path=/b", "Max-Age=86400"):
        assert attribute in cookie
    assert "Domain" not in cookie
    final = await site.get(target)
    assert final.status_code == 200
    assert token not in str(final.url)
    assert token not in final.text  # no credential in any link of the page either
    assert "Niet bevestigd" in final.text


async def test_the_exchange_and_the_page_keep_the_referrer_policy_and_no_store(
    site: httpx.AsyncClient,
) -> None:
    token = await _book(site)

    exchange = await site.get(f"/b/{token}")
    page = await site.get(exchange.headers["location"])

    for response in (exchange, page):
        assert response.headers["referrer-policy"] == "same-origin"
        assert "no-store" in response.headers["cache-control"]


async def test_the_language_of_the_link_survives_the_redirect(site: httpx.AsyncClient) -> None:
    token = await _book(site)

    response = await site.get(f"/b/{token}", params={"lang": "en"})
    junk = await site.get(f"/b/{token}", params={"lang": "<script>"})

    assert response.headers["location"].endswith("?lang=en")
    assert "?" not in junk.headers["location"]
    assert "Not confirmed" in (await site.get(response.headers["location"])).text


async def test_an_unknown_token_behaves_as_before_and_sets_no_cookie(
    site: httpx.AsyncClient,
) -> None:
    response = await site.get("/b/not-a-real-token")

    assert response.status_code == 404
    assert "set-cookie" not in response.headers
    assert "niet meer geldig" in response.text


async def test_without_the_cookie_the_page_asks_for_the_email_link(
    demo_settings: Settings, clock: FixedClock
) -> None:
    async with app_client(demo_settings, clock) as first:
        base = await open_link(first, await _book(first))
    async with app_client(demo_settings, clock) as other_device:
        response = await other_device.get(base)
        english = await other_device.get(base, params={"lang": "en"})

    assert response.status_code == 403
    assert "Open de link uit uw e-mail opnieuw" in response.text
    assert "Open the link from your email again" in english.text
    assert "Lovelace" not in response.text


async def test_the_link_from_the_email_works_again_on_a_second_device(
    demo_settings: Settings, clock: FixedClock
) -> None:
    async with app_client(demo_settings, clock) as first:
        token = await _book(first)
        base = await open_link(first, token)
    async with app_client(demo_settings, clock) as second:
        again = await open_link(second, token)
        page = await second.get(again)

    assert again == base  # the same booking, named by the same reference
    assert page.status_code == 200


async def test_an_unknown_reference_is_told_apart_from_nothing(site: httpx.AsyncClient) -> None:
    token = await _book(site)
    real = await open_link(site, token)

    unknown = await site.get("/b/m/" + "A" * 22)
    forged = await site.get(real.replace(real[-3:], "xyz"))

    for response in (unknown, forged):
        assert response.status_code == 403  # the same answer as a booking that exists
        assert "Open de link uit uw e-mail opnieuw" in response.text


async def test_a_cookie_for_one_booking_opens_no_other_booking(
    demo_settings: Settings, clock: FixedClock
) -> None:
    async with app_client(demo_settings, clock) as mine:
        mine_ref = await open_link(mine, await _book(mine))
        # A second, separate participant books another slot.
        async with app_client(demo_settings, clock) as theirs:
            their_ref = await open_link(theirs, await _book(theirs, "2026-01-02T08:30:00Z"))
        refused = await mine.get(their_ref)
        refused_post = await mine.post(f"{their_ref}/cancel", headers=SAME_ORIGIN)
        own = await mine.get(mine_ref)

    assert mine_ref != their_ref
    assert (refused.status_code, refused_post.status_code, own.status_code) == (403, 403, 200)


async def test_an_expired_session_needs_the_email_link_again(
    site: httpx.AsyncClient, clock: FixedClock
) -> None:
    token = await _book(site)
    base = await open_link(site, token)
    clock.advance(timedelta(hours=23))
    assert (await site.get(base)).status_code == 200  # a use extends the session
    clock.advance(timedelta(hours=23))
    assert (await site.get(base)).status_code == 200
    clock.advance(timedelta(hours=25))

    expired = await site.get(base)
    again = await open_link(site, token)

    assert expired.status_code == 403
    assert again == base
    assert (await site.get(again)).status_code == 200


async def test_the_session_lifetime_is_the_setting(
    demo_settings: Settings, clock: FixedClock
) -> None:
    short = demo_settings.model_copy(update={"participant_link_session_hours": 20})
    async with app_client(short, clock) as client:
        token = await _book(client)
        response = await client.get(f"/b/{token}")
        base = response.headers["location"]
        clock.advance(timedelta(hours=19, minutes=59))
        alive = await client.get(base)
        clock.advance(timedelta(hours=20, minutes=1))
        lapsed = await client.get(base)

    assert "Max-Age=72000" in response.headers["set-cookie"]
    assert (alive.status_code, lapsed.status_code) == (200, 403)


async def test_a_forged_or_cleared_cookie_value_opens_nothing(site: httpx.AsyncClient) -> None:
    token = await _book(site)
    base = await open_link(site, token)

    site.cookies.set(COOKIE, "A" * 43, domain="testserver.local", path="/b")
    site.cookies.delete(COOKIE, domain="testserver.local", path="/b")
    site.cookies.clear()
    cleared = await site.get(base)
    site.cookies.set(COOKIE, "A" * 43, domain="testserver.local", path="/b")
    forged = await site.get(base)

    assert (cleared.status_code, forged.status_code) == (403, 403)


async def test_guessing_references_draws_on_the_budget_for_unknown_links(
    demo_settings: Settings,
) -> None:
    limited = demo_settings.model_copy(
        update={"rate_limit_enabled": True, "rate_limit_unknown_links": "3/hour"}
    )
    async with app_client(limited, FixedClock(NOW)) as client:
        statuses = [(await client.get(f"/b/m/guess{n}")).status_code for n in range(5)]
        token_guess = await client.get("/b/some-token")

    assert statuses == [403, 403, 403, 429, 429]
    assert token_guess.status_code == 429  # one bucket for tokens and references


async def test_a_form_left_open_on_an_old_path_is_not_carried_out(
    site: httpx.AsyncClient,
) -> None:
    token = await _book(site)
    await confirm_by_link(site, token)

    response = await site.post(f"/b/{token}/cancel", headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/cancel")
    page = await open_link(site, token)
    assert "Bevestigd" in (await site.get(page)).text  # nothing was cancelled


@pytest.mark.parametrize("suffix", ["/confirm", "/cancel", "/reschedule"])
async def test_old_subpaths_of_mails_already_sent_lead_to_the_new_ones(
    site: httpx.AsyncClient, suffix: str
) -> None:
    token = await _book(site)

    response = await site.get(f"/b/{token}{suffix}")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/b/m/")
    assert response.headers["location"].endswith(suffix)
    assert token not in response.headers["location"]


async def test_one_cookie_holds_a_rescheduled_booking_too(site: httpx.AsyncClient) -> None:
    token = await _book(site)
    old = await open_link(site, token)
    await site.post(f"{old}/confirm", headers=SAME_ORIGIN)

    moved = await site.post(
        f"{old}/reschedule", data={"start": "2026-01-02T08:30:00Z"}, headers=SAME_ORIGIN
    )
    new = moved.headers["location"].split("?")[0]

    assert new != old
    assert (await site.get(new)).status_code == 200
    assert (await site.get(old)).status_code == 200


async def test_the_join_and_trial_links_of_the_booking_page_still_lead_there(
    site: httpx.AsyncClient,
) -> None:
    token = await _book(site)
    base = await open_link(site, token)

    join = await site.get(f"{base}/join")
    trial = await site.get(f"{base}/test")

    assert join.headers["location"] == f"/b/{token}/join"  # moves in ADR 0093 step 3
    assert trial.headers["location"] == f"/b/{token}/test"


async def test_the_bridge_to_the_join_page_needs_the_cookie(
    demo_settings: Settings, clock: FixedClock
) -> None:
    async with app_client(demo_settings, clock) as first:
        base = await open_link(first, await _book(first))
    async with app_client(demo_settings, clock) as other:
        response = await other.get(f"{base}/join")

    assert response.status_code == 403
    assert "location" not in response.headers


async def test_neither_the_token_nor_personal_data_reach_the_log(
    demo_settings: Settings, clock: FixedClock, capsys: pytest.CaptureFixture[str]
) -> None:
    loud = demo_settings.model_copy(update={"log_level": "DEBUG"})
    async with app_client(loud, clock) as client:
        token = await _book(client)
        base = await open_link(client, token)
        await client.get(base)
        await client.get(base.replace(base[-3:], "xyz"))  # refused
        await client.get("/b/not-a-real-token")

    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert lines
    text = json.dumps(lines)
    for secret in (token, "Lovelace", "ada@example.org", base.rsplit("/", 1)[-1]):
        assert secret not in text


async def test_the_cookie_is_host_prefixed_over_https(
    demo_settings: Settings, clock: FixedClock
) -> None:
    secure = demo_settings.model_copy(update={"public_base_url": "https://booking.example"})
    async with app_client(secure, clock) as client:
        token = await _book(client)
        response = await client.get(f"/b/{token}")

    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-op_link=")
    for attribute in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
        assert attribute in cookie
    assert "Path=/b" not in cookie
    assert "Domain" not in cookie
    assert "op_admin" not in cookie  # independent of the admin cookie
