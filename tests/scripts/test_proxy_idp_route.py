"""The bundled identity provider's route (ADR 0126): what ``deploy/proxy/idp.caddy`` lets through.

The real Caddy runs the file in ``scripts/check-proxy.sh`` (passes 3 and 4, with Docker). These
tests run without Docker: they read the file, tie its values to the application's own (the
cookies it sets, its HSTS and opener policy, the realm of the template) and apply its regular
expressions to the cookies and answers they are written for. The patterns use the syntax Go's
RE2 and Python's ``re`` share, and Go's ``$1`` replacements become ``\\g<1>`` here.
"""

import json
import re
from pathlib import Path

import pytest

from app.admin.display_routes import COOKIE_BASE as DISPLAY_COOKIE
from app.admin.session import session_cookie_for
from app.api.immediate_pages import COOKIE_BASE as REQUEST_COOKIE
from app.api.link_session import COOKIE_BASE as LINK_COOKIE
from app.api.security_headers import SECURITY_HEADERS
from app.web.cookies import cookie_for
from app.web.locale import LANG_PARAM

REPO = Path(__file__).parents[2]
ROUTE = (REPO / "deploy/proxy/idp.caddy").read_text()
REALM = json.loads((REPO / "deploy/keycloak/realm.template.json").read_text())["realm"]
HTTPS = "https://afspraak.example.nl"
KEYCLOAK_COOKIES = (
    "AUTH_SESSION_ID",
    "KC_AUTH_SESSION_HASH",
    "KC_RESTART",
    "KEYCLOAK_IDENTITY",
    "KEYCLOAK_SESSION",
    "KEYCLOAK_REMEMBER_ME",
    "KEYCLOAK_LOCALE",
)
"""The cookies Keycloak 26 sets for a realm; each must still reach it."""


def _replacements(direction: str, field: str) -> list[tuple[str, str]]:
    """Return the ``header_up``/``header_down`` regex replacements of ``field``, in order."""
    found = re.findall(rf"^\s*{direction} {field} `([^`]+)` \"([^\"]*)\"$", ROUTE, re.M)
    assert found, f"no {direction} {field} replacement in idp.caddy"
    return [(pattern, re.sub(r"\$(\d)", r"\\g<\1>", replace)) for pattern, replace in found]


def _apply(direction: str, field: str, value: str) -> str:
    """Return ``value`` after every replacement the route makes on ``field``."""
    for pattern, replace in _replacements(direction, field):
        value = re.sub(pattern, replace, value)
    return value


def _header_value(name: str) -> str:
    """Return the value the route sets for a response header (``header_down Name value``)."""
    found = re.search(rf"^\s*header_down \+?{name} (\"[^\"]*\"|\S+)$", ROUTE, re.M)
    assert found, f"idp.caddy sets no {name}"
    return found.group(1).strip('"')


def _portal_cookies() -> list[str]:
    """Every cookie the portal sets on an https address, by its name."""
    names = [session_cookie_for(HTTPS).name]
    names += [cookie_for(HTTPS, base, "/").name for base in (LINK_COOKIE, REQUEST_COOKIE)]
    names.append(cookie_for(HTTPS, DISPLAY_COOKIE, "/").name)
    return [*names, LANG_PARAM]


def test_only_the_login_of_the_templates_realm_and_its_assets_are_passed() -> None:
    """Point 1: Keycloak's own advice (/realms/, /resources/), narrowed to the one realm."""
    public = re.search(r"^\s*@login path (.+)$", ROUTE, re.M)
    assert public, "idp.caddy names no public paths"

    assert public.group(1).split() == [f"/idp/realms/{REALM}/*", "/idp/resources/*"]
    assert re.search(r"^@idp path /idp /idp/\*$", ROUTE, re.M)
    # Everything else under /idp gets the proxy's own 404, the last of the route's handlers.
    assert re.search(r"handle \{\s*respond 404\s*\}\s*\}\s*$", ROUTE)


def test_a_service_worker_script_is_refused_before_the_provider_sees_it() -> None:
    """Point 4: a browser sends ``Service-Worker: script`` when it fetches one."""
    assert re.search(r"@worker header Service-Worker \*\s*handle @worker \{\s*respond 403", ROUTE)
    assert ROUTE.index("handle @worker") < ROUTE.index("handle @login")


def test_every_cookie_of_the_portal_is_removed_and_the_providers_are_kept() -> None:
    """Point 3: the session and every other cookie of the portal stay on the portal's side."""
    portal = _portal_cookies()
    assert all(name.startswith("__Host-") for name in portal[:-1])
    for order in (
        [*portal, *KEYCLOAK_COOKIES],
        [*KEYCLOAK_COOKIES, *portal],
        [KEYCLOAK_COOKIES[0], *portal, *KEYCLOAK_COOKIES[1:]],
    ):
        header = "; ".join(f"{name}=v-{index}" for index, name in enumerate(order))

        sent = _apply("header_up", "Cookie", header)

        names = [pair.split("=", 1)[0] for pair in sent.split("; ") if pair]
        assert names == [name for name in order if name in KEYCLOAK_COOKIES], header


def test_only_the_portal_sets_cookies_outside_the_proxys_rule() -> None:
    """A new cookie of the portal must be a ``__Host-`` cookie or be named in idp.caddy.

    ``app/web/cookies.py`` gives every cookie of the portal the prefix on https; the language
    preference is the one cookie set directly. Any other direct ``set_cookie`` call fails here.
    """
    direct = sorted(
        str(path.relative_to(REPO))
        for path in (REPO / "app").rglob("*.py")
        if re.search(r"\.set_cookie\(", path.read_text())
    )

    assert direct == ["app/web/cookies.py", "app/web/locale.py"]
    assert re.search(rf"\|{LANG_PARAM}\)=", ROUTE)


@pytest.mark.parametrize(
    ("set_cookie", "expected"),
    [
        (
            "__Host-op_admin=x; Path=/; Secure; HttpOnly",
            "__Host-op_admin=x; Path=/idp/; Secure; HttpOnly",
        ),
        ("tossed=1; Path=/admin; Domain=afspraak.example.nl", "tossed=1; Path=/idp/"),
        ("tossed=1; path=/idpx/; HttpOnly", "tossed=1; path=/idp/; HttpOnly"),
        ("tossed=1; Path=/i", "tossed=1; Path=/idp/"),
        ("tossed=1; Path=/id; Secure", "tossed=1; Path=/idp/; Secure"),
        ("tossed=1; Path=/IDP/", "tossed=1; Path=/idp/"),
        ("AUTH_SESSION_ID=a;Version=1;Path=/idp/realms/openplanner/;Secure;HttpOnly", None),
        ("KC_RESTART=b; Path=/idp", None),
        (
            "KEYCLOAK_LOCALE=nl; Path=/idp/realms/openplanner/; domain=AFSPRAAK.example.nl",
            "KEYCLOAK_LOCALE=nl; Path=/idp/realms/openplanner/",
        ),
        # A browser uses the last Path: every one is confined, however many there are.
        (
            "__Host-op_admin=x; Path=/; Path=/; Secure",
            "__Host-op_admin=x; Path=/idp/; Path=/idp/; Secure",
        ),
        ("t=1; Path=/; path=/admin; PATH=/", "t=1; Path=/idp/; path=/idp/; PATH=/idp/"),
        # Browsers trim the white space around the name and the value.
        ("t=1; Path =/admin; Domain = afspraak.example.nl", "t=1; Path =/idp/"),
        ("t=1; Path= /; Secure", "t=1; Path= /idp/; Secure"),
    ],
)
def test_a_cookie_the_provider_sets_stays_under_idp_on_this_host(
    set_cookie: str, expected: str | None
) -> None:
    """Point 4: a ``__Host-`` name with a path other than / is refused by the browser."""
    assert _apply("header_down", "Set-Cookie", set_cookie) == (expected or set_cookie)


def _paths_a_browser_sees(set_cookie: str) -> list[str]:
    """Return the value of every Path attribute as RFC 6265 reads it (trimmed, case-blind)."""
    attributes = [part.split("=", 1) for part in set_cookie.split(";")[1:] if "=" in part]
    return [value.strip() for name, value in attributes if name.strip().lower() == "path"]


def test_no_mix_of_path_attributes_leaves_the_cookie_outside_idp() -> None:
    """Point 4, exhaustively over short mixes: whatever an answer sends, every Path is confined."""
    values = ["/", "/admin", "/i", "/id", "/idp", "/idpx/", "/idp/", "/idp/realms/openplanner/"]
    spellings = ["; Path={}", ";path={}", "; PATH = {}", ";  Path= {} "]
    for first in values:
        for second in values:
            for spelling in spellings:
                header = "__Host-op_admin=x" + spelling.format(first) + spelling.format(second)

                paths = _paths_a_browser_sees(_apply("header_down", "Set-Cookie", header))

                for path in paths:
                    assert path == "/idp" or path.startswith("/idp/"), (header, paths)


def test_the_headers_that_act_on_the_whole_origin_are_dropped() -> None:
    dropped = set(re.findall(r"^\s*header_down -(\S+)$", ROUTE, re.M))

    assert dropped == {
        "Service-Worker-Allowed",
        "Clear-Site-Data",
        "Alt-Svc",
        "NEL",
        "Report-To",
        "Reporting-Endpoints",
        "Accept-CH",
        "Critical-CH",
    }


def test_hsts_is_the_portals_own_and_the_opener_policy_never_is() -> None:
    """A provider's max-age=0 never ends HSTS; a portal window it opens is out of its reach."""
    assert _header_value("Strict-Transport-Security") == (
        SECURITY_HEADERS[b"strict-transport-security"].decode()
    )
    opener = _header_value("Cross-Origin-Opener-Policy")
    assert opener == "same-origin-allow-popups"
    assert opener != SECURITY_HEADERS[b"cross-origin-opener-policy"].decode()


def test_the_added_policy_keeps_fetches_posts_and_workers_under_idp() -> None:
    """Point 4: added next to the provider's own policy (``+``), so both are enforced."""
    policy = dict(
        directive.strip().split(" ", 1)
        for directive in _header_value("Content-Security-Policy").split(";")
        if directive.strip()
    )

    assert re.search(r"header_down \+Content-Security-Policy ", ROUTE)
    assert policy == {
        "connect-src": "https://{http.request.host}:*/idp/",
        "form-action": "https://{http.request.host}:*/idp/",
        "worker-src": "'none'",
    }
