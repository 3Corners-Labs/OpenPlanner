"""Resolve the locale of an HTTP request and remember an explicit choice in a cookie."""

from dataclasses import dataclass

from fastapi import Request
from starlette.responses import Response

from app.i18n import BoundGettext, Translator, match_locale, negotiate_locale

LANG_PARAM = "lang"
"""Query parameter set by the language switcher, and the name of the preference cookie."""

LANG_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
"""One year. A functional preference cookie: no personal data, no consent needed."""


@dataclass(frozen=True)
class RequestLocale:
    """The negotiated locale of one request.

    Attributes:
        gettext: ``_()`` bound to the locale; ``gettext.locale`` is the code.
        remember: Whether the visitor chose this locale explicitly and it should be stored.
        translator: The application translator, for rendering the language switcher.
    """

    gettext: BoundGettext
    remember: bool
    translator: Translator

    @property
    def code(self) -> str:
        """The locale code, e.g. ``"nl"``."""
        return self.gettext.locale

    def for_organisation(self, organisation_id: int) -> "RequestLocale":
        """Return the same locale with the organisation's own texts applied (ADR 0004 §4)."""
        return RequestLocale(
            self.translator.bind(self.code, organisation_id), self.remember, self.translator
        )

    def apply_to(self, request: Request, response: Response) -> None:
        """Set the language headers, and the preference cookie after an explicit choice."""
        response.headers["content-language"] = self.code
        response.headers["vary"] = "Accept-Language, Cookie"
        if self.remember:
            response.set_cookie(
                LANG_PARAM,
                self.code,
                max_age=LANG_COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
            )


def resolve_locale(request: Request) -> RequestLocale:
    """FastAPI dependency: negotiate the locale for ``request`` (ADR 0004, section 5).

    The organisation default joins the negotiation once organisations exist (WP2).
    """
    translator: Translator = request.app.state.translator
    explicit = request.query_params.get(LANG_PARAM)
    locale = negotiate_locale(
        enabled=translator.locales,
        explicit=explicit,
        cookie=request.cookies.get(LANG_PARAM),
        accept_language=request.headers.get("accept-language"),
    )
    remember = match_locale(explicit, translator.locales) == locale
    return RequestLocale(translator.bind(locale), remember, translator)


async def request_locale(request: Request) -> RequestLocale:
    """FastAPI dependency: ``resolve_locale`` after bringing the organisation texts up to date.

    Every page and form goes through this, so a text an admin just saved shows within
    ``REFRESH_SECONDS`` on every process (ADR 0004 §9).
    """
    translator: Translator = request.app.state.translator
    await translator.refresh()
    return resolve_locale(request)
