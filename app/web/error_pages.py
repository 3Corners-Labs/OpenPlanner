"""HTML error pages for domain errors, refused requests and rate limits.

Error pages never repeat submitted input or internal details; they show a translated message
and a way back. Rate-limit hits, refused attempts and unknown management links are also
security events (ADR 0053).
"""

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from slowapi.errors import RateLimitExceeded
from starlette.responses import Response

from app.domain.errors import DomainError, ErrorCode
from app.domain.security_log import (
    RATE_LIMITED,
    REQUEST_REJECTED,
    TOKEN_REJECTED,
    TOO_MANY_REQUESTS,
    UNKNOWN_MANAGEMENT_LINK,
)
from app.web.errors import ERROR_KEYS, status_for
from app.web.locale import resolve_locale
from app.web.protection import RejectedRequest
from app.web.security_events import report
from app.web.templates import create_templates, render

templates = create_templates([])
MANAGEMENT_LINK_PREFIX = "/b/"
MACHINE_PREFIXES = ("/api/", "/webhooks/")
"""Paths whose callers are programs: they keep FastAPI's JSON validation answer, which the
OpenAPI spec documents (``HTTPValidationError``)."""
"""Path prefix of the participant's management link; a 404 under it matches no link."""

_NOT_FOUND = {
    ErrorCode.GROUP_NOT_FOUND,
    ErrorCode.BOOKING_NOT_FOUND,
    ErrorCode.SCHEDULE_ITEM_NOT_FOUND,
    ErrorCode.ORGANISATION_NOT_FOUND,
    ErrorCode.API_CLIENT_NOT_FOUND,
    ErrorCode.UPDATE_REQUEST_NOT_FOUND,
    ErrorCode.QUEUE_NOT_ENABLED,
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
    if exc.code is ErrorCode.BOOKING_NOT_FOUND and request.url.path.startswith(
        MANAGEMENT_LINK_PREFIX
    ):
        await report(request, TOKEN_REJECTED, UNKNOWN_MANAGEMENT_LINK)
    if exc.code in _NOT_FOUND:
        return _page(request, 404, "error.not_found.title", "error.not_found.text")
    return _page(request, status_for(exc), "error.title", ERROR_KEYS[exc.code])


async def rejected_request_page(request: Request, exc: Exception) -> Response:
    """Render a refused request (cross-site POST, honeypot, malformed input)."""
    status = exc.status if isinstance(exc, RejectedRequest) else 400
    if isinstance(exc, RejectedRequest) and exc.reason:
        await report(request, REQUEST_REJECTED, exc.reason)
    return _page(request, status, "error.title", "error.invalid_request")


async def validation_error_page(request: Request, exc: Exception) -> Response:
    """Render a page's malformed query or path parameter as the generic error page.

    FastAPI's default answer repeats the submitted value in JSON; a page never echoes what
    was typed into its address (ADR 0063 §3), so admin and participant pages get the error page
    with a catalogue text only. Machine paths keep the documented JSON answer.
    """
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - registered for it only
        raise TypeError("expected a RequestValidationError")
    if request.url.path.startswith(MACHINE_PREFIXES):
        return await request_validation_exception_handler(request, exc)
    return _page(request, 422, "error.title", "error.invalid_request")


async def rate_limited_page(request: Request, exc: Exception) -> Response:
    """Render the rate-limit page with a ``Retry-After`` hint."""
    await report(request, RATE_LIMITED, TOO_MANY_REQUESTS, detail="surface:web")
    response = _page(request, 429, "error.title", "error.rate_limited")
    response.headers["retry-after"] = "60"
    return response


def register_error_pages(app: FastAPI) -> None:
    """Install the handlers on ``app``."""
    app.add_exception_handler(DomainError, domain_error_page)
    app.add_exception_handler(RejectedRequest, rejected_request_page)
    app.add_exception_handler(RateLimitExceeded, rate_limited_page)
    app.add_exception_handler(RequestValidationError, validation_error_page)
