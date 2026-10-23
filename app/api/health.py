"""Liveness and readiness endpoints for container orchestration and monitoring."""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app.adapters.db.engine import is_reachable

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    """Body of both health endpoints. Deliberately carries no diagnostic detail."""

    status: Literal["ok", "unavailable"]


@router.get("/healthz", response_model=HealthStatus)
async def liveness() -> HealthStatus:
    """Report that the process is up. Does not touch dependencies."""
    return HealthStatus(status="ok")


@router.get(
    "/readyz",
    response_model=HealthStatus,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthStatus}},
)
async def readiness(request: Request, response: Response) -> HealthStatus:
    """Report whether the process can serve traffic, i.e. the database answers."""
    if await is_reachable(request.app.state.engine):
        return HealthStatus(status="ok")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthStatus(status="unavailable")
