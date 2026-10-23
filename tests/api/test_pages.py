import re

import httpx
import pytest


def _html_lang(response: httpx.Response) -> str:
    match = re.search(r'<html lang="([^"]+)"', response.text)
    assert match
    return match.group(1)


async def test_home_defaults_to_dutch(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert _html_lang(response) == "nl"
    assert response.headers["content-language"] == "nl"
    assert "Een videogesprek plannen" in response.text


async def test_browser_language_is_detected(client: httpx.AsyncClient) -> None:
    response = await client.get("/", headers={"accept-language": "en-GB,en;q=0.9"})

    assert _html_lang(response) == "en"
    assert "Schedule a video call" in response.text
    assert "set-cookie" not in response.headers


async def test_explicit_choice_sets_a_preference_cookie(client: httpx.AsyncClient) -> None:
    response = await client.get("/?lang=en", headers={"accept-language": "nl"})

    assert _html_lang(response) == "en"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("lang=en;")
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert "max-age=31536000" in cookie.lower()


async def test_cookie_over_https_is_secure(client: httpx.AsyncClient) -> None:
    response = await client.get("https://testserver/?lang=en")

    assert "secure" in response.headers["set-cookie"].lower()


async def test_cookie_beats_browser_language(client: httpx.AsyncClient) -> None:
    response = await client.get("/", headers={"accept-language": "nl", "cookie": "lang=en"})

    assert _html_lang(response) == "en"


@pytest.mark.parametrize("value", ["de", "<script>", "../../etc/passwd", ""])
async def test_unsupported_choice_is_ignored(client: httpx.AsyncClient, value: str) -> None:
    response = await client.get("/", params={"lang": value})

    assert response.status_code == 200
    assert _html_lang(response) == "nl"
    assert "set-cookie" not in response.headers
    assert "<script>" not in response.text


async def test_language_switcher_offers_every_locale_in_its_own_language(
    client: httpx.AsyncClient,
) -> None:
    text = (await client.get("/")).text

    assert re.search(r'<a [^>]*href="\?lang=en"[^>]*lang="en"[^>]*>English</a>', text)
    assert re.search(r'<a [^>]*href="\?lang=nl"[^>]*aria-current="true"[^>]*>Nederlands</a>', text)
    assert '<nav aria-label="Kies een taal"' in text


async def test_switcher_keeps_other_query_parameters(client: httpx.AsyncClient) -> None:
    text = (await client.get("/?from=2026-01-05")).text

    assert 'href="?from=2026-01-05&amp;lang=en"' in text


async def test_switcher_links_never_contain_the_request_path(client: httpx.AsyncClient) -> None:
    text = (await client.get("/", params={"next": "//evil.example"})).text

    switcher = [href for href in re.findall(r'href="([^"]*)"', text) if "lang=" in href]
    assert switcher
    assert all(href.startswith("?") for href in switcher)


async def test_html_pages_get_a_csp_allowing_own_stylesheets(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "style-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["vary"] == "Accept-Language, Cookie"
    assert response.headers["cache-control"] == "no-store"


async def test_stylesheet_is_served(client: httpx.AsyncClient) -> None:
    response = await client.get("/static/site.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


async def test_pages_are_not_in_the_api_spec(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/" not in paths
