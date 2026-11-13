"""The policies of a page (ADR 0123 point 4): built from one base, one function for the call page,
and a syntax-tree check of which modules may reach it."""

import ast
from pathlib import Path

import pytest

from app.api.security_headers import SECURITY_HEADERS
from app.web.security_policy import (
    CALL_CSP,
    CALL_HOSTS,
    CALL_PERMISSIONS,
    CALL_POLICY,
    DEFAULT_PERMISSIONS,
    DEFAULT_PERMISSIONS_POLICY,
    HTML_CSP,
    POLICIES,
    SCRIPT_CSP,
    PageKind,
    permissions_policy,
)
from tests.web.conftest import RenderSource

APP = Path("app")
CALL_NAMES = frozenset(
    {"render_call_page", "participant_call_page", "CALL_POLICY", "CALL_CSP", "CALL_PERMISSIONS"}
)
"""Every name that can produce the call page's response policy."""
CALL_MODULES = frozenset(
    {
        "app/web/security_policy.py",
        "app/web/templates.py",
        "app/api/participant.py",
        "app/api/call_pages.py",
    }
)
"""The closed list of modules that may name any of them: the policy, the renderers, the route."""
HEADERS = frozenset({"content-security-policy", "permissions-policy"})
HEADER_MODULES = frozenset({"app/web/security_policy.py", "app/web/templates.py"})
"""Where a policy header may be written; the middleware's default is a bytes key, listed below."""


def _directives(policy: str) -> dict[str, list[str]]:
    parts = [part.strip().split() for part in policy.split(";") if part.strip()]
    return {part[0]: part[1:] for part in parts}


def test_the_kinds_are_two_and_each_has_a_policy() -> None:
    assert {kind.value for kind in PageKind} == {"document", "scripted"}
    assert set(POLICIES) == set(PageKind)
    assert POLICIES[PageKind.DOCUMENT].csp == HTML_CSP
    assert POLICIES[PageKind.SCRIPTED].csp == SCRIPT_CSP
    with pytest.raises(ValueError, match="call"):
        PageKind("call")


def test_the_policies_keep_the_exact_bytes_pages_always_had() -> None:
    assert HTML_CSP == (
        "default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    assert SCRIPT_CSP == (
        "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; "
        "img-src 'self'; font-src 'self'; form-action 'self'; base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    assert DEFAULT_PERMISSIONS_POLICY == (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "display-capture=(), interest-cohort=()"
    )
    assert SECURITY_HEADERS[b"permissions-policy"].decode() == DEFAULT_PERMISSIONS_POLICY


def test_the_call_policy_is_the_script_policy_plus_the_call_hosts() -> None:
    call, script = _directives(CALL_CSP), _directives(SCRIPT_CSP)

    assert call["connect-src"] == [*script["connect-src"], *CALL_HOSTS]
    assert {
        k: v for k, v in call.items() if k not in ("connect-src", "media-src", "worker-src")
    } == {k: v for k, v in script.items() if k != "connect-src"}
    assert call["media-src"] == ["'self'"]
    assert call["worker-src"] == ["'self'"]
    assert call["form-action"] == ["'self'"]
    assert call["base-uri"] == ["'none'"]
    assert call["frame-ancestors"] == ["'none'"]
    assert CALL_POLICY.csp == CALL_CSP


def test_the_call_policy_holds_no_unsafe_token_and_no_other_origin() -> None:
    for token in ("unsafe-", "blob:", "data:", "webexcontent"):
        assert token not in CALL_CSP
    for directive in ("script-src", "style-src", "img-src", "font-src", "default-src"):
        assert _directives(CALL_CSP)[directive] in (["'self'"], ["'none'"])


def test_the_call_permissions_are_the_default_with_three_features_for_our_origin() -> None:
    changed = {k for k in DEFAULT_PERMISSIONS if CALL_PERMISSIONS[k] != DEFAULT_PERMISSIONS[k]}

    assert changed == {"camera", "microphone", "display-capture"}
    assert all(CALL_PERMISSIONS[key] == "(self)" for key in changed)
    assert CALL_PERMISSIONS.keys() == DEFAULT_PERMISSIONS.keys()
    assert CALL_POLICY.permissions == permissions_policy(CALL_PERMISSIONS)


@pytest.mark.parametrize(
    ("kwargs", "csp"),
    [
        ({}, HTML_CSP),
        ({"kind": PageKind.DOCUMENT}, HTML_CSP),
        ({"kind": PageKind.SCRIPTED}, SCRIPT_CSP),
    ],
)
def test_render_sets_the_policy_of_the_kind(
    render_source: RenderSource, kwargs: dict[str, object], csp: str
) -> None:
    response = render_source("<p>x</p>", {}, **kwargs)

    assert response.headers["content-security-policy"] == csp
    assert response.headers["permissions-policy"] == DEFAULT_PERMISSIONS_POLICY


def test_render_takes_no_scripts_switch_and_no_call_kind(render_source: RenderSource) -> None:
    with pytest.raises(TypeError):
        render_source("<p>x</p>", {}, scripts=True)


def _mentions(tree: ast.AST, names: frozenset[str]) -> set[str]:
    """The names of ``names`` a module imports (under any alias), uses or names in a string."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                if alias.name in names or alias.name == "*":
                    found.add(alias.name)
        elif isinstance(node, ast.Name | ast.Attribute):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in names:
                found.add(name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names:
            found.add(node.value)  # getattr(module, "CALL_POLICY")
    return found


def _modules() -> dict[str, ast.AST]:
    return {path.as_posix(): ast.parse(path.read_text()) for path in sorted(APP.rglob("*.py"))}


def test_only_the_call_modules_can_reach_the_call_page_policy() -> None:
    reaching = {
        path: found
        for path, tree in _modules().items()
        if path not in CALL_MODULES and (found := _mentions(tree, CALL_NAMES))
    }

    assert reaching == {}
    # The closed list is not stale: the route and the two renderers do use them.
    users = {path for path, tree in _modules().items() if _mentions(tree, CALL_NAMES)}
    assert users == CALL_MODULES


def test_a_star_import_cannot_hide_the_call_page_policy() -> None:
    star = {path for path, tree in _modules().items() if "*" in _mentions(tree, frozenset())}

    assert star == set()


def test_the_syntax_check_sees_an_alias_and_a_forwarded_name() -> None:
    aliased = ast.parse("from app.web.templates import render_call_page as r\nr()")
    forwarded = ast.parse("import app.web.security_policy as p\nx = p.CALL_POLICY")
    by_string = ast.parse('getattr(m, "CALL_CSP")')

    assert _mentions(aliased, CALL_NAMES) == {"render_call_page"}
    assert _mentions(forwarded, CALL_NAMES) == {"CALL_POLICY"}
    assert _mentions(by_string, CALL_NAMES) == {"CALL_CSP"}


def _header_writers(tree: ast.AST) -> bool:
    """Whether a module names a policy header in a string or bytes constant, in any case."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str | bytes):
            text = (
                node.value.decode(errors="replace") if isinstance(node.value, bytes) else node.value
            )
            if text.lower() in HEADERS:
                return True
    return False


def test_no_other_module_writes_a_policy_header() -> None:
    writers = {path for path, tree in _modules().items() if _header_writers(tree)}

    assert writers <= HEADER_MODULES | {"app/api/security_headers.py"}, writers
