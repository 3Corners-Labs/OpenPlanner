"""Jinja2 setup and the one function that renders an HTML page.

Every HTML response goes through ``render``, so locale headers, the language switcher,
the HTML Content-Security-Policy and caching rules are applied in one place.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from app.web.security_policy import CALL_CSP as CALL_CSP  # noqa: PLC0414 - re-exported
from app.web.security_policy import CALL_POLICY, POLICIES, PageKind, Policy
from app.web.security_policy import HTML_CSP as HTML_CSP  # noqa: PLC0414 - re-exported
from app.web.security_policy import SCRIPT_CSP as SCRIPT_CSP  # noqa: PLC0414 - re-exported
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
    kind: PageKind = PageKind.DOCUMENT,
) -> HTMLResponse:
    """Render template ``name`` in the request's locale as a page of ``kind``.

    The template receives the page's ``context``; then ``frame``, what a wrapper gives every
    page of its kind (the admin portal's menu and profile menu, ``app.admin.pages.page``);
    then ``_`` (bound gettext), ``locale``, ``languages``, ``environment`` and
    ``product_footer``. ``kind`` alone decides the page's ``Content-Security-Policy`` and
    ``Permissions-Policy`` (``PageKind.SCRIPTED`` allows the page's own scripts).

    Raises:
        ValueError: a name of ``context`` or ``frame`` that a later layer sets as well (a
            programming error). It would be overwritten without a word: System status once
            showed the environment bar's ``EnvironmentNotice`` as the environment, and a
            booking's "back to the agenda" link pointed at the booking itself.
    """
    return _render(templates, request, name, locale, context, frame, POLICIES[kind])


def render_call_page(
    templates: Jinja2Templates,
    request: Request,
    name: str,
    locale: RequestLocale,
    context: Mapping[str, object] | None = None,
) -> HTMLResponse:
    """Render template ``name`` as the call page, with the call page's policy (ADR 0123).

    The only function that applies ``CALL_POLICY``; ``tests/web/test_page_kinds.py`` lists by
    syntax tree the modules that may name it. Otherwise as ``render``.
    """
    return _render(templates, request, name, locale, context, None, CALL_POLICY)


def _render(
    templates: Jinja2Templates,
    request: Request,
    name: str,
    locale: RequestLocale,
    context: Mapping[str, object] | None,
    frame: Mapping[str, object] | None,
    policy: Policy,
) -> HTMLResponse:
    """Render ``name`` and set the headers of ``policy``: the body of both public functions."""
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
    response.headers["content-security-policy"] = policy.csp
    response.headers["permissions-policy"] = policy.permissions
    response.headers["cache-control"] = "no-store"
    locale.apply_to(request, response)
    return response
