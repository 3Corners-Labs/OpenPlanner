"""The one reader of the proxy's ``/idp`` route and error headers for the tests (ADR 0126).

``deploy/proxy/idp.caddy`` holds the route of the bundled identity provider and
``deploy/proxy/Caddyfile`` the headers of every answer the proxy makes itself. The tests of the
route (``tests/scripts/test_proxy_idp_route.py``) and of the proxy check
(``tests/scripts/test_check_proxy.py``) read both through this module, so a change in how the
files are written fails one reader, never two that disagree. Each reader fails when it finds
nothing, so a check over its result can never pass empty.
"""

import re
from pathlib import Path

REPO = Path(__file__).parents[2]
ROUTE = (REPO / "deploy/proxy/idp.caddy").read_text()
CADDYFILE = (REPO / "deploy/proxy/Caddyfile").read_text()


def _found(what: str, found: list[str] | dict[str, str]) -> None:
    """Fail when a reader found nothing: the file's form changed under it."""
    assert found, f"no {what} found in deploy/proxy"


def dropped_headers() -> list[str]:
    """Return the response headers the route drops (``header_down -Name``)."""
    found = re.findall(r"^\s*header_down -(\S+)$", ROUTE, re.MULTILINE)
    _found("dropped header", found)
    return found


def set_headers() -> dict[str, str]:
    """Return the response headers the route sets or adds, with their values.

    ``header_down Name value`` replaces the provider's value, ``header_down +Name value`` adds one
    next to it (the Content-Security-Policy).
    """
    found = {
        name: quoted or bare
        for name, quoted, bare in re.findall(
            r'^\s*header_down \+?([A-Za-z-]+) (?:"([^"]*)"|([^\s"`]+))$', ROUTE, re.MULTILINE
        )
    }
    _found("set header", found)
    return found


def replacements(direction: str, field: str) -> list[tuple[str, str]]:
    """Return the regex replacements the route makes on ``field``, in order, for Python's ``re``.

    Args:
        direction: ``header_up`` (the request) or ``header_down`` (the answer).
        field: The header, ``Cookie`` or ``Set-Cookie``.

    Returns:
        (pattern, replacement) pairs; Go's ``$1`` becomes ``\\g<1>``. Both engines share the
        syntax the route uses.
    """
    found = re.findall(rf"^\s*{direction} {field} `([^`]+)` \"([^\"]*)\"$", ROUTE, re.MULTILINE)
    _found(f"{direction} {field} replacement", found)
    return [(pattern, re.sub(r"\$(\d)", r"\\g<\1>", replace)) for pattern, replace in found]


def detour_pattern() -> str:
    """Return the regular expression of the route's refused detours, unescaped from CEL."""
    found = re.findall(r'@detour expression `\{http\.request\.uri\}\.matches\("(.+)"\)`', ROUTE)
    _found("detour expression", found)
    return found[0].replace("\\\\", "\\")


def error_headers() -> dict[str, str]:
    """Return the headers of every answer the proxy makes itself (``handle_errors``)."""
    block = re.search(r"handle_errors \{\s*header \{(.*?)\n\t\t\}", CADDYFILE, re.DOTALL)
    assert block, "no handle_errors header block in deploy/proxy/Caddyfile"
    found = {
        name: quoted or bare
        for name, quoted, bare in re.findall(
            r'^\s*([A-Za-z-]+) (?:"([^"]*)"|(\S+))$', block.group(1), re.MULTILINE
        )
    }
    _found("error header", found)
    return found
