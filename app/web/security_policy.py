"""The response policies of an HTML page: one home for what a page may load, reach and use.

A page's ``Content-Security-Policy`` and ``Permissions-Policy`` are built here from one base
mapping of directives, so each kind of page is the one before it with something added and never
a second written-out string. ``PageKind`` names the two kinds every page may choose (ADR 0118);
the call page's policy (ADR 0123 point 4) is not a kind: it has no name a caller can pass, and
only ``app.web.templates.render_call_page`` and its participant sibling apply it
(``tests/web/test_page_kinds.py`` checks by syntax tree which modules may even name it).

The hosts of the call policy and the browser features it allows are fixed on purpose: a setting
would let an installation widen what a page may reach.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

SELF = ("'self'",)
NONE = ("'none'",)

CALL_HOSTS = (
    "https://*.wbx2.com",
    "wss://*.wbx2.com",
    "https://*.webex.com",
    "wss://*.webex.com",
    "https://*.ciscospark.com",
)
"""The hosts the call talks to (ADR 0123 point 4), added to ``connect-src`` of the call page."""

_DOCUMENT: dict[str, tuple[str, ...]] = {
    "default-src": NONE,
    "style-src": SELF,
    "img-src": SELF,
    "font-src": SELF,
    "form-action": SELF,
    "base-uri": NONE,
    "frame-ancestors": NONE,
}
"""No scripts, no inline styles, no framing; fonts only from our own origin (ADR 0041)."""

_SCRIPTED: dict[str, tuple[str, ...]] = {
    "default-src": NONE,
    "script-src": SELF,
    "connect-src": SELF,
    **{name: value for name, value in _DOCUMENT.items() if name != "default-src"},
}
"""Own scripts only, which may fetch the same pages (ADR 0020, ADR 0118 point 5)."""

_CALL: dict[str, tuple[str, ...]] = {
    **_SCRIPTED,
    "connect-src": (*_SCRIPTED["connect-src"], *CALL_HOSTS),
    "media-src": SELF,
    "worker-src": SELF,
}
"""The scripted policy with ``connect-src`` extended and ``media-src`` and ``worker-src`` named."""


def content_security_policy(directives: Mapping[str, tuple[str, ...]]) -> str:
    """Return the header value for ``directives``, in their order."""
    return "; ".join(" ".join((name, *sources)) for name, sources in directives.items())


def permissions_policy(features: Mapping[str, str]) -> str:
    """Return the ``Permissions-Policy`` header value for ``features``."""
    return ", ".join(f"{name}={allowlist}" for name, allowlist in features.items())


HTML_CSP = content_security_policy(_DOCUMENT)
"""The policy of a page without scripts."""
SCRIPT_CSP = content_security_policy(_SCRIPTED)
"""The policy of a participant or admin page with its own scripts."""
CALL_CSP = content_security_policy(_CALL)
"""The call page's policy: ``SCRIPT_CSP`` plus the call's hosts, no ``unsafe-`` token."""

DEFAULT_PERMISSIONS: dict[str, str] = {
    "camera": "()",
    "microphone": "()",
    "geolocation": "()",
    "payment": "()",
    "usb": "()",
    "display-capture": "()",
    "interest-cohort": "()",
}
"""No powerful browser feature. ``app.api.security_headers`` puts it on every response."""

CALL_PERMISSIONS: dict[str, str] = {
    **DEFAULT_PERMISSIONS,
    "camera": "(self)",
    "microphone": "(self)",
    "display-capture": "(self)",
}
"""Camera, microphone and screen capture for our own origin; everything else as the default."""

DEFAULT_PERMISSIONS_POLICY = permissions_policy(DEFAULT_PERMISSIONS)


@dataclass(frozen=True)
class Policy:
    """What a page's response says about what it may load and use.

    Attributes:
        csp: The ``Content-Security-Policy`` header value.
        permissions: The ``Permissions-Policy`` header value.
    """

    csp: str
    permissions: str


class PageKind(StrEnum):
    """The kinds of HTML page a route may choose; each fixes the page's policy.

    A caller names a kind and never passes a header string. The call page is deliberately not
    here: it is rendered by its own function (see the module's docstring).
    """

    DOCUMENT = "document"
    SCRIPTED = "scripted"


POLICIES: dict[PageKind, Policy] = {
    PageKind.DOCUMENT: Policy(HTML_CSP, DEFAULT_PERMISSIONS_POLICY),
    PageKind.SCRIPTED: Policy(SCRIPT_CSP, DEFAULT_PERMISSIONS_POLICY),
}
"""The policy of each kind of page."""

CALL_POLICY = Policy(CALL_CSP, permissions_policy(CALL_PERMISSIONS))
"""The call page's policy (ADR 0123 point 4). Reached by the call page's functions only."""
