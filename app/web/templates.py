"""Jinja2 setup and the one function that renders an HTML page.

Every HTML response goes through ``render``, so locale headers, the language switcher,
the HTML Content-Security-Policy and caching rules are applied in one place.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from starlette.responses import HTMLResponse

from app.logging import tracer
from app.web.locale import LANG_PARAM, RequestLocale
from app.web.protection import is_action
from app.web.static_assets import ASSETS
from app.web.widgets import OVERVIEW_PATH

KEPT_QUERY_PARAMS = ("start", "month", "day", "days", "from", "until")
"""Query parameters the language switcher carries over; anything else is dropped so that
arbitrary input is never reflected into links."""

_KEPT_VALUE = re.compile(r"[0-9][0-9A-Z:+.-]{0,39}")
"""The shape of a kept value: a date, time or number. Anything else (text typed into the
address bar, a personal-data value, ADR 0063 §3) is dropped instead of echoed into a link."""

SHARED_TEMPLATE_DIR = Path(__file__).parent / "templates"
"""Layout and partials shared by all entry points."""


class FragmentResponse(HTMLResponse):
    """An HTML fragment without the page shell: a widget of the overview (ADR 0061).

    Routes that answer with a fragment declare this class, so the tests that sweep the
    portal's *pages* (navigation homes, manual topics) leave them out: a fragment is part of
    the page it belongs to, never a screen of its own.
    """


HTML_CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)
"""No scripts, no inline styles, no framing. Relaxed per page only when a feature needs it.
Fonts only from our own origin: an organisation's uploaded fonts (ADR 0041)."""

SCRIPT_CSP = (
    "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; "
    "img-src 'self'; font-src 'self'; form-action 'self'; base-uri 'none'; "
    "frame-ancestors 'none'"
)
"""Participant pages (ADR 0020): own scripts only, which may fetch the same pages; still no
inline scripts or styles, so injected markup cannot run."""


CALL_CSP = (
    "default-src 'none'; script-src 'self'; "
    "connect-src 'self' https://*.wbx2.com wss://*.wbx2.com https://*.webex.com "
    "wss://*.webex.com https://*.ciscospark.com; "
    "style-src 'self'; img-src 'self'; font-src 'self'; media-src 'self'; worker-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
"""The call page's policy (ADR 0123 point 4): ``SCRIPT_CSP`` plus the five hosts the call talks
to in ``connect-src``, and ``media-src`` and ``worker-src`` named. No ``unsafe-`` token, no
other script, style, font or image origin. The hosts are fixed on purpose, never a setting."""

DEFAULT_PERMISSIONS: dict[str, str] = {
    "camera": "()",
    "microphone": "()",
    "geolocation": "()",
    "payment": "()",
    "usb": "()",
    "display-capture": "()",
    "interest-cohort": "()",
}
"""The ``Permissions-Policy`` of every response: no powerful browser feature (served by
``app.api.security_headers``)."""

CALL_PERMISSIONS = {
    **DEFAULT_PERMISSIONS,
    "camera": "(self)",
    "microphone": "(self)",
    "display-capture": "(self)",
}
"""The call page's features (ADR 0123 point 4): camera, microphone and sharing a screen for our
own origin; everything else as the default."""


def permissions_policy(features: Mapping[str, str]) -> str:
    """Return the ``Permissions-Policy`` header value for ``features``."""
    return ", ".join(f"{name}={value}" for name, value in features.items())


class PageKind(StrEnum):
    """The closed set of kinds of HTML page; each fixes the page's response policy.

    A caller names a kind and never passes a header string, so no route can widen its own
    policy. ADR 0118 keeps ``DOCUMENT`` and ``SCRIPTED`` as they were; ``CALL`` is the third
    kind (ADR 0123 point 2) and is used by the one route of the call page only.
    """

    DOCUMENT = "document"
    SCRIPTED = "scripted"
    CALL = "call"


POLICIES: dict[PageKind, str] = {
    PageKind.DOCUMENT: HTML_CSP,
    PageKind.SCRIPTED: SCRIPT_CSP,
    PageKind.CALL: CALL_CSP,
}
"""The ``Content-Security-Policy`` of each kind of page."""


@dataclass(frozen=True)
class LanguageOption:
    """One entry of the language switcher.

    Attributes:
        code: Locale code, used for the ``lang`` and ``hreflang`` attributes.
        autonym: The language's name in that language (``"English"``).
        href: A query-only relative URL (``?lang=en``) that keeps the current path and the
            other query parameters; on a page at a ``GET`` action's address, the overview
            with ``?lang=`` (``switch_href``). It never contains the request path, so a crafted
            path cannot turn it into a link to another host.
        current: Whether this is the page's language.
    """

    code: str
    autonym: str
    href: str
    current: bool


def create_environment(template_dirs: Sequence[Path]) -> Environment:
    """Return the one Jinja2 configuration used for pages and emails.

    HTML templates are autoescaped; ``.txt`` templates (plain-text email parts) are not.
    Undefined variables raise instead of rendering empty. ``static_url('admin.css')`` is how
    a template links a file under ``/static``: the URL carries the file's version.
    """
    environment = Environment(
        loader=FileSystemLoader(list(template_dirs)),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.globals["static_url"] = ASSETS.url
    return environment


def create_templates(page_dirs: Sequence[Path]) -> Jinja2Templates:
    """Return a template renderer for an entry point.

    Args:
        page_dirs: The entry point's own template directories; they take precedence over
            the shared layout in ``SHARED_TEMPLATE_DIR``.
    """
    return Jinja2Templates(env=create_environment([*page_dirs, SHARED_TEMPLATE_DIR]))


def kept_query(request: Request) -> list[tuple[str, str]]:
    """Return the query parameters of ``request`` that a link may carry over.

    Only ``KEPT_QUERY_PARAMS`` with a date-like value, so arbitrary input (a filter text, a
    personal-data value, ADR 0063 §3) is never reflected into a link or a form.
    """
    return [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key in KEPT_QUERY_PARAMS and _KEPT_VALUE.fullmatch(value)
    ]


def switch_href(request: Request, param: str, value: str) -> str:
    """Return a query-only link (``?lang=en``) that sets ``param`` on the current page.

    Keeps only ``kept_query``, and never contains the request path, so a crafted path cannot
    turn it into a link to another host. A page served at a ``GET`` action's address (the
    refusal of an audited download) is not a page to come back to: a link to it would ask for
    the action again (ASVS V3.5.3), so there the link sets ``param`` on the overview instead.
    """
    # The route first: only a page at an action's address needs the method.
    if is_action(request) and request.method == "GET":
        return f"{OVERVIEW_PATH}?{urlencode([(param, value)])}"
    return f"?{urlencode([*kept_query(request), (param, value)])}"


def language_options(request: Request, locale: RequestLocale) -> list[LanguageOption]:
    """Build the language switcher for ``request``."""
    return [
        LanguageOption(
            code=code,
            autonym=locale.translator.gettext(code, "language.autonym"),
            href=switch_href(request, LANG_PARAM, code),
            current=code == locale.code,
        )
        for code in locale.translator.locales
    ]


@dataclass(frozen=True)
class EnvironmentNotice:
    """What the admin portal's environment bar says outside production."""

    label_key: str
    simulator: bool


ENVIRONMENT_LABELS = {
    "dev": "admin.environment.dev",
    "test": "admin.environment.test",
    "prod": "admin.environment.prod",
}
"""The name of each ``ENVIRONMENT`` in the portal: the bar outside production, System status
and the overview's status widget. Production shows no bar."""


def product_footer_of(request: Request) -> bool:
    """Whether the page shows "Appointments via OpenPlanner" (ADR 0060 §6, class D).

    The organisation the request follows decides (``app.web.organisation_settings``); a
    request that follows none shows the platform's value.
    """
    current = getattr(request.state, "organisation_settings", None) or getattr(
        request.app.state, "settings", None
    )
    return bool(getattr(current, "product_footer", True))


def environment_of(request: Request) -> EnvironmentNotice | None:
    """Return the environment bar for this installation, or ``None`` in production."""
    settings = getattr(request.app.state, "settings", None)
    environment = getattr(settings, "environment", "prod")
    if environment == "prod" or environment not in ENVIRONMENT_LABELS:
        return None
    return EnvironmentNotice(
        ENVIRONMENT_LABELS[environment], bool(getattr(settings, "simulator_enabled", False))
    )


def render(
    templates: Jinja2Templates,
    request: Request,
    name: str,
    locale: RequestLocale,
    context: Mapping[str, object] | None = None,
    *,
    frame: Mapping[str, object] | None = None,
    scripts: bool = False,
    kind: PageKind | None = None,
) -> HTMLResponse:
    """Render template ``name`` in the request's locale.

    The template receives the page's ``context``; then ``frame``, what a wrapper gives every
    page of its kind (the admin portal's menu and profile menu, ``app.admin.pages.page``);
    then ``_`` (bound gettext), ``locale``, ``languages``, ``environment`` and
    ``product_footer``. ``scripts=True`` allows the page's own scripts (``SCRIPT_CSP``) and is
    shorthand for ``kind=PageKind.SCRIPTED``; ``kind`` names the kind of page, which alone
    decides the ``Content-Security-Policy`` and, for the call page, the ``Permissions-Policy``.

    Raises:
        ValueError: ``scripts`` and ``kind`` both given, or a name of ``context`` or ``frame``
            that a later layer sets as well (a programming error). It would be overwritten
            without a word: System status once showed the environment bar's
            ``EnvironmentNotice`` as the environment, and a booking's "back to the agenda" link
            pointed at the booking itself.
    """
    if kind is not None and scripts:
        raise ValueError(f"{name}: give the kind of page or scripts=True, not both")
    kind = kind or (PageKind.SCRIPTED if scripts else PageKind.DOCUMENT)
    own: dict[str, object] = {
        "_": locale.gettext,
        "locale": locale.code,
        "languages": language_options(request, locale),
        "environment": environment_of(request),
        "product_footer": product_footer_of(request),
    }
    page, shared = dict(context or {}), dict(frame or {})
    clash = (page.keys() & (shared.keys() | own.keys())) | (shared.keys() & own.keys())
    if clash:
        raise ValueError(f"{name}: {sorted(clash)} set twice; give the page's value its own name")
    started = perf_counter()
    response = templates.TemplateResponse(request, name, {**page, **shared, **own})
    tracer.trace("template.render", template=name, ms=int((perf_counter() - started) * 1000))
    response.headers["content-security-policy"] = POLICIES[kind]
    if kind is PageKind.CALL:
        response.headers["permissions-policy"] = permissions_policy(CALL_PERMISSIONS)
    response.headers["cache-control"] = "no-store"
    locale.apply_to(request, response)
    return response
