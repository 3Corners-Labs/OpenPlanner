"""The instance endpoint of the public address's round trip (ADR 0086 point 3).

    GET /api/v1/instance[?probe={nonce}]   public, no permission, limited as availability reads

Every caller gets the installation's ``instance_id`` (a random UUID, no secret): it tells the
worker that the public name reaches this installation and not another. Only a caller that
carries the pending nonce, which lives in the database for a few minutes and is used once,
also gets the scheme the app saw and whether the forwarded headers were honoured. No address
is ever in the answer, no cookie is set and nothing is cached.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.domain.address_check import InstanceService
from app.domain.public_address import forwarded_state
from app.web.protection import limiter, read_limit

router = APIRouter(prefix="/api/v1", tags=["instance"])

MAX_NONCE_CHARS = 64
"""Longest probe value read; the worker's nonce is 22 characters."""


class InstanceBody(BaseModel):
    """Body of ``/api/v1/instance``; the last two fields only for the holder of the nonce."""

    instance_id: str
    scheme: str | None = None
    forwarded: str | None = None


@router.get(
    "/instance",
    response_model=InstanceBody,
    summary="Which installation answers",
    responses={
        429: {
            "description": "Over the availability-read limit; the error page, `Retry-After` set.",
            "content": {"text/html": {}},
        }
    },
)
@limiter.limit(read_limit)
async def instance(
    request: Request, probe: Annotated[str | None, Query(max_length=MAX_NONCE_CHARS)] = None
) -> JSONResponse:
    """Return the instance id, and the proxy detail to the caller that holds the pending nonce."""
    service: InstanceService = request.app.state.instance_service
    client = request.client.host if request.client else None
    answer = await service.answer(
        probe,
        scheme=request.url.scheme,
        forwarded=forwarded_state(
            forwarded_for=request.headers.get("x-forwarded-for"),
            forwarded_proto=request.headers.get("x-forwarded-proto"),
            client=client,
            scheme=request.url.scheme,
        ),
    )
    body = InstanceBody(
        instance_id=answer.instance_id,
        scheme=answer.scheme,
        forwarded=answer.forwarded.value if answer.forwarded else None,
    )
    return JSONResponse(body.model_dump(), headers={"cache-control": "no-store"})
