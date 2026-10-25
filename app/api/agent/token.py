"""The token endpoint of the machine API: OAuth 2.0 client credentials (RFC 6749 §4.4).

    POST /api/agent/v1/oauth/token
        Authorization: Basic base64(client_id:client_secret)   (or both as form fields)
        grant_type=client_credentials[&scope=availability:read]
    → 200 {"access_token", "token_type": "Bearer", "expires_in", "scope"}

Rate-limited per IP. Every credential problem answers ``invalid_client`` the same way; the
precise reason is only in the audit log.
"""

import base64
import binascii
from urllib.parse import parse_qsl, unquote

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from app.api.agent.auth import api_access, client_address
from app.api.agent.errors import ApiProblem
from app.api.agent.schemas import ErrorResponse, TokenResponse
from app.web.protection import agent_token_limit, limiter

router = APIRouter()

MAX_TOKEN_REQUEST_BYTES = 4096

_FORM_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "application/x-www-form-urlencoded": {
                "schema": {
                    "type": "object",
                    "required": ["grant_type"],
                    "properties": {
                        "grant_type": {"type": "string", "enum": ["client_credentials"]},
                        "scope": {"type": "string", "description": "Space-separated scopes."},
                        "client_id": {"type": "string", "description": "Without Basic auth."},
                        "client_secret": {"type": "string", "description": "Without Basic auth."},
                    },
                }
            }
        },
    }
}


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    """Return the client id and secret from ``Authorization: Basic``, if present.

    Raises:
        ApiProblem: ``invalid_client`` for a malformed header.
    """
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if not scheme:
        return None
    if scheme.lower() != "basic":
        raise ApiProblem("invalid_client")
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ApiProblem("invalid_client") from exc
    client_id, colon, secret = decoded.partition(":")
    if not colon:
        raise ApiProblem("invalid_client")
    # RFC 6749 §2.3.1: both parts are form-urlencoded before base64.
    return unquote(client_id), unquote(secret)


async def _form(request: Request) -> dict[str, str]:
    """Read the urlencoded body; a repeated parameter is a malformed request (RFC 6749 §3.1).

    Raises:
        ApiProblem: ``invalid_request`` for a wrong content type, a body that is too large
            or a repeated parameter.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise ApiProblem("invalid_request")
    raw = await request.body()
    if len(raw) > MAX_TOKEN_REQUEST_BYTES:
        raise ApiProblem("invalid_request")
    pairs = parse_qsl(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    form: dict[str, str] = {}
    for key, value in pairs:
        if key in form:
            raise ApiProblem("invalid_request")
        form[key] = value
    return form


@router.post(
    "/oauth/token",
    response_model=TokenResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Malformed request or unknown scope."},
        401: {"model": ErrorResponse, "description": "`invalid_client`."},
        429: {"model": ErrorResponse, "description": "Too many token requests."},
        503: {"model": ErrorResponse, "description": "The API is switched off."},
    },
    openapi_extra=_FORM_SCHEMA,
    summary="Get an access token (client credentials)",
    tags=["auth"],
)
@limiter.limit(agent_token_limit)
async def token(request: Request) -> JSONResponse:
    """Exchange the client id and secret for a bearer token valid for 10 minutes.

    Send the credentials with HTTP Basic (preferred) or as form fields, never both.
    """
    form = await _form(request)
    if form.get("grant_type") != "client_credentials":
        raise ApiProblem("unsupported_grant_type" if form.get("grant_type") else "invalid_request")
    basic = _basic_credentials(request)
    in_form = "client_id" in form or "client_secret" in form
    if basic is not None and in_form:
        raise ApiProblem("invalid_request")
    client_id, secret = basic or (form.get("client_id", ""), form.get("client_secret", ""))
    issued = await api_access(request).issue_token(
        client_id[:128], secret[:256], form.get("scope"), client_address(request)
    )
    body = TokenResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        scope=" ".join(sorted(issued.scopes)),
    )
    return JSONResponse(
        body.model_dump(), headers={"cache-control": "no-store", "pragma": "no-cache"}
    )
