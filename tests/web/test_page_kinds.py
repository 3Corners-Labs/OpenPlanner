"""The kinds of page (ADR 0123 point 2): each fixes its policy, and only the call page's route
may be the call kind (point 4)."""

import re
from pathlib import Path

import pytest

from app.api.security_headers import SECURITY_HEADERS
from app.web.templates import (
    CALL_CSP,
    CALL_PERMISSIONS,
    DEFAULT_PERMISSIONS,
    HTML_CSP,
    POLICIES,
    SCRIPT_CSP,
    PageKind,
    permissions_policy,
)
from tests.web.conftest import RenderSource

APP = Path("app")
HOSTS = (
    "https://*.wbx2.com",
    "wss://*.wbx2.com",
    "https://*.webex.com",
    "wss://*.webex.com",
    "https://*.ciscospark.com",
)


def _directives(policy: str) -> dict[str, list[str]]:
    parts = [part.strip().split() for part in policy.split(";") if part.strip()]
    return {part[0]: part[1:] for part in parts}


def test_the_policy_of_each_kind_is_closed_and_complete() -> None:
    assert set(POLICIES) == set(PageKind)
    assert POLICIES[PageKind.DOCUMENT] == HTML_CSP
    assert POLICIES[PageKind.SCRIPTED] == SCRIPT_CSP
    assert POLICIES[PageKind.CALL] == CALL_CSP


def test_the_call_policy_is_the_script_policy_plus_the_call_hosts() -> None:
    call, script = _directives(CALL_CSP), _directives(SCRIPT_CSP)

    assert call["connect-src"] == [*script["connect-src"], *HOSTS]
    assert {
        key: value
        for key, value in call.items()
        if key not in ("connect-src", "media-src", "worker-src")
    } == {key: value for key, value in script.items() if key != "connect-src"}
    assert call["media-src"] == ["'self'"]
    assert call["worker-src"] == ["'self'"]
    assert call["form-action"] == ["'self'"]
    assert call["base-uri"] == ["'none'"]
    assert call["frame-ancestors"] == ["'none'"]


def test_the_call_policy_holds_no_unsafe_token_and_no_other_origin() -> None:
    assert "unsafe-" not in CALL_CSP
    assert "blob:" not in CALL_CSP
    assert "data:" not in CALL_CSP
    assert "webexcontent" not in CALL_CSP
    for directive in ("script-src", "style-src", "img-src", "font-src", "default-src"):
        assert _directives(CALL_CSP)[directive] in (["'self'"], ["'none'"])


def test_the_call_permissions_are_the_default_with_three_features_for_our_origin() -> None:
    changed = {k for k in DEFAULT_PERMISSIONS if CALL_PERMISSIONS[k] != DEFAULT_PERMISSIONS[k]}

    assert changed == {"camera", "microphone", "display-capture"}
    assert all(CALL_PERMISSIONS[key] == "(self)" for key in changed)
    assert CALL_PERMISSIONS.keys() == DEFAULT_PERMISSIONS.keys()
    assert (
        permissions_policy(DEFAULT_PERMISSIONS).encode() == SECURITY_HEADERS[b"permissions-policy"]
    )
    assert (
        permissions_policy(DEFAULT_PERMISSIONS)
        == "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "display-capture=(), interest-cohort=()"
    )


@pytest.mark.parametrize(
    ("kwargs", "csp", "permissions"),
    [
        ({}, HTML_CSP, None),
        ({"scripts": True}, SCRIPT_CSP, None),
        ({"kind": PageKind.DOCUMENT}, HTML_CSP, None),
        ({"kind": PageKind.SCRIPTED}, SCRIPT_CSP, None),
        ({"kind": PageKind.CALL}, CALL_CSP, permissions_policy(CALL_PERMISSIONS)),
    ],
)
def test_render_sets_the_policy_of_the_kind(
    render_source: RenderSource, kwargs: dict[str, object], csp: str, permissions: str | None
) -> None:
    response = render_source("<p>x</p>", {}, **kwargs)

    assert response.headers["content-security-policy"] == csp
    assert response.headers.get("permissions-policy") == permissions


def test_a_page_names_its_kind_or_scripts_never_both(render_source: RenderSource) -> None:
    with pytest.raises(ValueError, match="not both"):
        render_source("<p>x</p>", {}, scripts=True, kind=PageKind.CALL)


def _sources_naming(pattern: str) -> set[str]:
    return {
        path.as_posix()
        for path in [*APP.rglob("*.py"), *APP.rglob("*.html")]
        if re.search(pattern, path.read_text())
    }


def test_only_the_call_route_can_produce_the_call_page_policy() -> None:
    assert _sources_naming(r"PageKind\.CALL") == {
        "app/api/call_pages.py",
        "app/web/templates.py",
    }
    assert _sources_naming(r"CALL_CSP|CALL_PERMISSIONS") == {"app/web/templates.py"}
    assert _sources_naming(r"\bkind\s*=\s*PageKind\.CALL\b") == {"app/api/call_pages.py"}


def test_no_route_passes_a_header_string_to_render() -> None:
    # The kinds are closed: nothing under app/ sets a content security policy except render
    # and the middleware's default.
    assert _sources_naming(r"""["']content-security-policy["']\]\s*=""") == {"app/web/templates.py"}
