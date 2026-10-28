"""Liveness and readiness endpoints for container orchestration and monitoring.

Readiness answers ``degraded`` (still 200) when the data volume runs low on space or the
database outgrows its limit (ADR 0040): a reason to alert, not to stop serving or to roll back
an update. The body never says why; the worker's log and system event do.
"""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app.adapters.db.engine import is_reachable
from app.adapters.db.maintenance import measure_storage, storage_limits
from app.config import Settings
from app.domain.storage import StorageGrade, assess

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    """Body of both health endpoints. Deliberately carries no diagnostic detail."""

    status: Literal["ok", "degraded", "unavailable"]


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
    """Report whether the process can serve traffic (the database answers) and is short of space."""
    if not await is_reachable(request.app.state.engine):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthStatus(status="unavailable")
    settings: Settings = request.app.state.settings
    usage = measure_storage(settings.database_url)
    if usage is not None and assess(usage, storage_limits(settings))[0] is not StorageGrade.OK:
        return HealthStatus(status="degraded")
    return HealthStatus(status="ok")
