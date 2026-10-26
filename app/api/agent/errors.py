"""JSON errors of the machine API (ADR 0023).

Every refusal is ``{"error": "<code>"}`` with the matching status, in the OAuth vocabulary
where one exists (RFC 6749 §5.2, RFC 6750 §3.1). Errors never echo input: FastAPI's default
validation response repeats the submitted values, which could be personal data, so it is
replaced here.
"""

from collections.abc import Callable

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from app.domain.api_clients import ApiAuthError
from app.domain.errors import DomainError, ErrorCode

log = structlog.get_logger()

REALM = 'realm="openplanner"'

_TOKEN_STATUS: dict[str, int] = {
    "invalid_request": 400,
    "unsupported_grant_type": 400,
    "invalid_scope": 400,
    "invalid_client": 401,
    "invalid_token": 401,  # nosec B105 - an OAuth error code, not a secret
    "insufficient_scope": 403,
    "temporarily_unavailable": 503,
}

_DOMAIN_STATUS: dict[ErrorCode, int] = {
    ErrorCode.GROUP_NOT_FOUND: 404,
    ErrorCode.NOT_PERMITTED: 403,
    ErrorCode.INVALID_RANGE: 422,
    ErrorCode.DATABASE_BUSY: 503,
}


class ApiProblem(Exception):  # noqa: N818 - an answer, like HTTPException
    """Refuse a machine request with an OAuth-style error code.

    Args:
        code: The error code (``invalid_request``, ``invalid_client``, ``invalid_token``, …).
        scope: For ``insufficient_scope``: the scope the call needs.
    """

    def __init__(self, code: str, scope: str | None = None) -> None:
        """Store the code and, if any, the missing scope."""
        super().__init__(code)
        self.code = code
        self.scope = scope


def problem(code: str, status: int, headers: dict[str, str] | None = None) -> JSONResponse:
    """Return ``{"error": code}`` with ``status``; never cached."""
    return JSONResponse(
        {"error": code},
        status_code=status,
        headers={"cache-control": "no-store", **(headers or {})},
    )


def _challenge(code: str, scope: str | None) -> dict[str, str]:
    """Return the ``WWW-Authenticate`` header for an authentication or scope refusal."""
    if code == "invalid_client":
        return {"www-authenticate": f"Basic {REALM}"}
    if code in ("invalid_token", "insufficient_scope"):
        extra = f', scope="{scope}"' if scope else ""
        return {"www-authenticate": f'Bearer {REALM}, error="{code}"{extra}'}
    if code == "temporarily_unavailable":
        return {"retry-after": "300"}
    return {}


def answer(code: str, scope: str | None = None) -> JSONResponse:
    """Return the response for an OAuth-style error code."""
    return problem(code, _TOKEN_STATUS.get(code, 400), _challenge(code, scope))


def register_errors(app: FastAPI) -> None:
    """Install the JSON error handlers on the machine API's application."""

    async def api_problem(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, ApiProblem):  # pragma: no cover - registered for this type
            raise TypeError("expected an ApiProblem")
        return answer(exc.code, exc.scope)

    async def auth_error(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, ApiAuthError):  # pragma: no cover - registered for this type
            raise TypeError("expected an ApiAuthError")
        return answer(exc.code)

    async def domain_error(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, DomainError):  # pragma: no cover - registered for this type
            raise TypeError("expected a DomainError")
        if exc.code is ErrorCode.NOT_PERMITTED:
            return answer("insufficient_scope")
        return problem(exc.code.value, _DOMAIN_STATUS.get(exc.code, 422))

    async def invalid_input(request: Request, exc: Exception) -> Response:
        # Only the names of the offending parameters, never their values.
        errors = exc.errors() if isinstance(exc, RequestValidationError) else []
        fields = sorted({str(e["loc"][-1]) for e in errors if e.get("loc")})
        log.info("agent_api.invalid_request", fields=",".join(fields)[:200])
        return problem("invalid_request", 422)

    async def http_error(request: Request, exc: Exception) -> Response:
        status = exc.status_code if isinstance(exc, HTTPException) else 500
        code = {404: "not_found", 405: "method_not_allowed"}.get(status, "invalid_request")
        return problem(code, status)

    async def rate_limited(request: Request, exc: Exception) -> Response:
        return problem("rate_limited", 429, {"retry-after": "60"})

    handlers: dict[type[Exception], Callable[[Request, Exception], object]] = {
        ApiProblem: api_problem,
        ApiAuthError: auth_error,
        DomainError: domain_error,
        RequestValidationError: invalid_input,
        HTTPException: http_error,
        RateLimitExceeded: rate_limited,
    }
    for exc_type, handler in handlers.items():
        app.add_exception_handler(exc_type, handler)  # type: ignore[arg-type]
