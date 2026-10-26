"""HTML error pages for domain errors, refused requests and rate limits.

Error pages never repeat submitted input or internal details; they show a translated message
and a way back.
"""

from fastapi import FastAPI, Request
from slowapi.errors import RateLimitExceeded
from starlette.responses import Response

from app.domain.errors import DomainError, ErrorCode
from app.web.errors import ERROR_KEYS, status_for
from app.web.locale import resolve_locale
from app.web.protection import RejectedRequest
from app.web.templates import create_templates, render

templates = create_templates([])

_NOT_FOUND = {
    ErrorCode.GROUP_NOT_FOUND,
    ErrorCode.BOOKING_NOT_FOUND,
    ErrorCode.SCHEDULE_ITEM_NOT_FOUND,
    ErrorCode.ORGANISATION_NOT_FOUND,
    ErrorCode.API_CLIENT_NOT_FOUND,
    ErrorCode.UPDATE_REQUEST_NOT_FOUND,
}


def _page(request: Request, status: int, title_key: str, message_key: str) -> Response:
    """Render the generic error page."""
    locale = resolve_locale(request)
    response = render(
        templates,
        request,
        "error.html",
        locale,
        {"title": locale.gettext(title_key), "message": locale.gettext(message_key)},
    )
    response.status_code = status
    return response


async def domain_error_page(request: Request, exc: Exception) -> Response:
    """Render a domain error that a route did not handle itself."""
    if not isinstance(exc, DomainError):  # pragma: no cover - registered for DomainError only
        raise TypeError("expected a DomainError")
    if exc.code in _NOT_FOUND:
        return _page(request, 404, "error.not_found.title", "error.not_found.text")
    return _page(request, status_for(exc), "error.title", ERROR_KEYS[exc.code])


async def rejected_request_page(request: Request, exc: Exception) -> Response:
    """Render a refused request (cross-site POST, honeypot, malformed input)."""
    status = exc.status if isinstance(exc, RejectedRequest) else 400
    return _page(request, status, "error.title", "error.invalid_request")


async def rate_limited_page(request: Request, exc: Exception) -> Response:
    """Render the rate-limit page with a ``Retry-After`` hint."""
    response = _page(request, 429, "error.title", "error.rate_limited")
    response.headers["retry-after"] = "60"
    return response


def register_error_pages(app: FastAPI) -> None:
    """Install the handlers on ``app``."""
    app.add_exception_handler(DomainError, domain_error_page)
    app.add_exception_handler(RejectedRequest, rejected_request_page)
    app.add_exception_handler(RateLimitExceeded, rate_limited_page)
