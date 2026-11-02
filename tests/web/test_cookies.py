"""The shared rule for the application's own cookies (admin session, participant link)."""

from starlette.responses import Response

from app.admin.session import session_cookie_for
from app.web.cookies import CookieSpec, cookie_for, set_cookie


def test_https_gives_the_host_prefix_the_root_path_and_secure() -> None:
    assert cookie_for("https://booking.example", "op_link", "/b") == CookieSpec(
        "__Host-op_link", "/", True
    )
    assert cookie_for("HTTPS://booking.example", "op_link", "/b").secure


def test_plain_http_keeps_the_plain_name_and_the_narrow_path() -> None:
    assert cookie_for("http://127.0.0.1:8000", "op_link", "/b") == CookieSpec(
        "op_link", "/b", False
    )


def test_the_admin_and_participant_cookies_are_different_cookies() -> None:
    for url in ("https://booking.example", "http://127.0.0.1:8000"):
        admin = session_cookie_for(url)
        participant = cookie_for(url, "op_link", "/b")
        assert admin.name != participant.name

    assert session_cookie_for("https://booking.example") == CookieSpec("__Host-op_admin", "/", True)
    assert session_cookie_for("http://127.0.0.1:8000") == CookieSpec("op_admin", "/admin", False)


def test_the_cookie_is_http_only_same_site_lax_and_has_no_domain() -> None:
    response = Response()

    set_cookie(response, CookieSpec("__Host-op_link", "/", True), "value", 3600)

    header = response.headers["set-cookie"]
    assert header.startswith("__Host-op_link=value")
    for attribute in ("HttpOnly", "SameSite=lax", "Secure", "Path=/", "Max-Age=3600"):
        assert attribute in header
    assert "Domain" not in header
