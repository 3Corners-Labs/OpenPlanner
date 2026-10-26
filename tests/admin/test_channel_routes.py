"""Channel screens (ADR 0022): SMTP and SMS per organisation, write-only secrets, audit."""

import httpx

from tests.admin.conftest import SAME_ORIGIN, SUPER, Site
from tests.admin.test_admin_flow import audit_events

PAGE = "/admin/organisations/demo/channels"
SMTP = {
    "smtp_host": "smtp.demo.nl",
    "smtp_port": "587",
    "smtp_security": "starttls",
    "smtp_username": "afspraak",
    "smtp_credential": "Geheim-wachtwoord-123",
    "sender_address": "afspraak@demo.nl",
    "sender_name": "Gemeente Demo",
}
SMS = {
    "sms_api_base_url": "https://api.us.webexconnect.io",
    "sms_service_key": "service-key-abc",
    "sms_sender_id": "GemDemo",
    "sms_country_code": "31",
}


async def _post(site: Site, path: str, data: dict[str, str]) -> httpx.Response:
    return await site.client.post(f"{PAGE}/{path}", data=data, headers=SAME_ORIGIN)


async def test_integrations_page_links_every_organisation(site: Site) -> None:
    await site.login_as(SUPER)
    page = (await site.client.get("/admin/integrations")).text

    assert f'href="{PAGE}"' in page
    assert 'href="/admin/organisations/andere/channels"' in page
    assert "Niet ingesteld" in page


async def test_smtp_is_saved_and_the_password_never_shown(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "email", SMTP)

    assert response.status_code == 303
    assert response.headers["location"] == f"{PAGE}?done=saved"
    page = (await site.client.get(PAGE)).text
    assert 'value="smtp.demo.nl"' in page
    assert "Geheim-wachtwoord-123" not in page
    assert "Er is een geheim opgeslagen" in page
    assert 'autocomplete="new-password"' in page
    assert ("sub-super", "integrations.manage", "success", "") in [
        (a, b, str(c), d) for a, b, c, d in await audit_events(site)
    ]
    listing = (await site.client.get("/admin/integrations")).text
    assert "Ingesteld" in listing

    # A blank password keeps the stored one; nothing else changed.
    again = await _post(site, "email", {**SMTP, "smtp_credential": ""})
    assert again.headers["location"] == f"{PAGE}?done=unchanged"


async def test_a_header_injection_in_the_sender_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "email", {**SMTP, "sender_name": "Demo\r\nBcc: x@evil.nl"})

    assert response.status_code == 422
    assert 'href="#sender_name"' in response.text
    assert "Geheim-wachtwoord-123" not in response.text  # never echoed, not even on error


async def test_a_login_without_password_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "email", {**SMTP, "smtp_credential": ""})

    assert response.status_code == 422
    assert 'href="#smtp_credential"' in response.text


async def test_sms_service_is_saved_and_removed(site: Site) -> None:
    await site.login_as(SUPER)

    saved = await _post(site, "sms", SMS)
    assert saved.headers["location"] == f"{PAGE}?done=saved"
    page = (await site.client.get(PAGE)).text
    assert 'value="GemDemo"' in page
    assert "service-key-abc" not in page

    removed = await site.client.post(f"{PAGE}/sms/remove", headers=SAME_ORIGIN)
    assert removed.headers["location"] == f"{PAGE}?done=removed"
    assert 'value="GemDemo"' not in (await site.client.get(PAGE)).text


async def test_an_http_gateway_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "sms", {**SMS, "sms_api_base_url": "http://gateway.example"})

    assert response.status_code == 422
    assert 'href="#sms_api_base_url"' in response.text
