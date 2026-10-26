"""Documentation of the machine API, served only while the superuser publishes it (ADR 0023).

    GET /api/agent/v1/openapi.json   the OpenAPI spec of this API only
    GET /api/agent/v1/docs           the same spec as a server-rendered page (no scripts)

Both answer 404 while the documentation switch is off, as if they did not exist. The page is
built from the spec, so the two cannot disagree; Swagger UI is not used because it loads
scripts from a CDN.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, JSONResponse

from app.api.agent.auth import api_access
from app.web.locale import resolve_locale
from app.web.templates import create_templates, render

router = APIRouter(include_in_schema=False)
templates = create_templates([Path(__file__).parent / "templates"])

_METHODS = ("get", "post", "put", "patch", "delete")


@dataclass(frozen=True)
class Parameter:
    """One query or path parameter of an operation."""

    name: str
    where: str
    required: bool
    description: str


@dataclass(frozen=True)
class Operation:
    """One endpoint as the documentation page shows it."""

    method: str
    path: str
    summary: str
    description: str
    parameters: list[Parameter]
    responses: list[tuple[str, str]]
    secured: bool


def operations(spec: dict[str, Any]) -> list[Operation]:
    """Return the operations of an OpenAPI spec in path order."""
    found: list[Operation] = []
    for path, item in spec.get("paths", {}).items():
        for method in _METHODS:
            operation = item.get(method)
            if operation is None:
                continue
            found.append(
                Operation(
                    method=method.upper(),
                    path=path,
                    summary=operation.get("summary", ""),
                    description=operation.get("description", ""),
                    parameters=[
                        Parameter(
                            p["name"],
                            p.get("in", ""),
                            bool(p.get("required")),
                            p.get("description", ""),
                        )
                        for p in operation.get("parameters", [])
                    ],
                    responses=[
                        (status, response.get("description", ""))
                        for status, response in operation.get("responses", {}).items()
                    ],
                    secured=bool(operation.get("security")),
                )
            )
    return found


async def _ensure_published(request: Request) -> None:
    """Answer 404 unless the superuser publishes the documentation."""
    if not (await api_access(request).settings()).docs_public:
        raise HTTPException(404)


@router.get("/openapi.json")
async def spec(request: Request) -> JSONResponse:
    """Serve the OpenAPI spec of the machine API."""
    await _ensure_published(request)
    return JSONResponse(request.app.openapi(), headers={"cache-control": "no-store"})


@router.get("/docs", response_class=HTMLResponse)
async def page(request: Request) -> HTMLResponse:
    """Serve the documentation page."""
    await _ensure_published(request)
    locale = resolve_locale(request)
    openapi = request.app.openapi()
    flows = openapi["components"]["securitySchemes"]["clientCredentials"]["flows"]
    return render(
        templates,
        request,
        "agent_docs.html",
        locale,
        {
            "title": openapi["info"]["title"],
            "version": openapi["info"]["version"],
            "token_url": flows["clientCredentials"]["tokenUrl"],
            "scopes": sorted(flows["clientCredentials"]["scopes"].items()),
            "operations": operations(openapi),
        },
    )
