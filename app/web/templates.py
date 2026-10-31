"""Jinja2 setup and the one function that renders an HTML page.

Every HTML response goes through ``render``, so locale headers, the language switcher,
the HTML Content-Security-Policy and caching rules are applied in one place.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from starlette.responses import HTMLResponse

from app.web.locale import LANG_PARAM, RequestLocale
from app.web.static_assets import ASSETS

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


@dataclass(frozen=True)
class LanguageOption:
    """One entry of the language switcher.

    Attributes:
        code: Locale code, used for the ``lang`` and ``hreflang`` attributes.
        autonym: The language's name in that language (``"English"``).
        href: A query-only relative URL (``?lang=en``) that keeps the current path and the
            other query parameters. It never contains the request path, so a crafted path
            cannot turn it into a link to another host.
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
    turn it into a link to another host.
    """
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
}
"""Bar label per non-production ``ENVIRONMENT``; production shows no bar."""


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
    key = ENVIRONMENT_LABELS.get(getattr(settings, "environment", "prod"))
    if key is None:
        return None
    return EnvironmentNotice(key, bool(getattr(settings, "simulator_enabled", False)))


def render(
    templates: Jinja2Templates,
    request: Request,
    name: str,
    locale: RequestLocale,
    context: Mapping[str, object] | None = None,
    *,
    scripts: bool = False,
) -> HTMLResponse:
    """Render template ``name`` in the request's locale.

    The template receives ``_`` (bound gettext), ``locale``, ``languages``, ``environment``
    and ``product_footer`` in addition to ``context``. ``scripts=True`` allows the page's own
    scripts (``SCRIPT_CSP``).
    """
    response = templates.TemplateResponse(
        request,
        name,
        {
            **(context or {}),
            "_": locale.gettext,
            "locale": locale.code,
            "languages": language_options(request, locale),
            "environment": environment_of(request),
            "product_footer": product_footer_of(request),
        },
    )
    response.headers["content-security-policy"] = SCRIPT_CSP if scripts else HTML_CSP
    response.headers["cache-control"] = "no-store"
    locale.apply_to(request, response)
    return response
