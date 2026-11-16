"""The secret of the address for message deliveries, made on the connection card (ADR 0124 pt 15).

Through the card's button, the sealed record and the gate at the address: the secret is shown once
on a page the browser does not keep, is the address's own (the events' secret does not open it),
a new one overlaps the old for ``webhook_secret_overlap_minutes``, and it reaches no log, audit
event or later page.
"""

import json
from datetime import timedelta

import structlog.testing

from app.adapters.webhooks import sign_wxcc
from app.domain.contact_center import ContactCenterKind
from tests.support.admin_site import SAME_ORIGIN, SUPER, Site
from tests.support.contact_center_site import ADDRESS, PAGE, _connect, _connections

MAKE = f"{PAGE}/contact-center/message-secret"
MESSAGES = f"{ADDRESS}/messages"


def _body(stamp_ms: int) -> bytes:
    return json.dumps(
        {
            "id": "evt-m",
            "type": "task-message:appended",
            "comciscotimestamp": stamp_ms,
            "data": {"taskId": "unknown-task"},
        },
        separators=(",", ":"),
    ).encode()


async def _call(site: Site, secret: str) -> int:
    stamp = int(site.clock.now().timestamp() * 1000)
    body = _body(stamp)
    response = await site.client.post(
        MESSAGES, content=body, headers=sign_wxcc(body, secret, stamp)
    )
    return response.status_code


async def _make(site: Site) -> str:
    """Press the button and read the secret off the page that shows it once."""
    response = await site.client.post(MAKE, headers=SAME_ORIGIN)
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    marked = response.text.split('class="secret-value">', 1)[1]
    return marked.split("</code>", 1)[0]


async def test_the_address_does_not_exist_until_a_secret_is_made_and_then_only_that_opens_it(
    site: Site,
) -> None:
    await _connect(site)
    events_secret = await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)
    assert events_secret is not None

    before = await _call(site, events_secret)
    secret = await _make(site)
    own = await _call(site, secret)
    events_at_messages = await _call(site, events_secret)

    assert (before, own, events_at_messages) == (404, 204, 401)


async def test_the_secret_is_shown_once_and_the_card_never_shows_it(site: Site) -> None:
    await _connect(site)
    secret = await _make(site)

    card = (await site.client.get(PAGE)).text

    assert secret not in card
    assert f"{ADDRESS}/messages" in card  # the address to paste into the console
    assert 'name="message-secret"' not in card


async def test_a_new_secret_overlaps_the_old_one_and_then_the_old_one_stops(site: Site) -> None:
    await _connect(site)
    old = await _make(site)
    new = await _make(site)

    inside = (await _call(site, old), await _call(site, new))
    site.clock.advance(timedelta(minutes=61))
    after = (await _call(site, old), await _call(site, new))

    assert old != new
    assert inside == (204, 204)
    assert after == (401, 204)


async def test_without_a_connection_the_button_sends_the_administrator_back_with_a_refusal(
    site: Site,
) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(MAKE, headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert "done=contact_center_refused" in response.headers["location"]


async def test_no_secret_reaches_a_log_or_an_audit_event(site: Site) -> None:
    await _connect(site)
    with structlog.testing.capture_logs() as logs:
        secret = await _make(site)
        await _call(site, secret)
    audit = await site.app.state.admin_service.audit_log(SUPER)

    texts = repr(logs) + repr([(e.event.detail, e.event.reason) for e in audit])

    assert secret not in texts
    assert any(e.event.detail.endswith("make_message_secret") for e in audit)
