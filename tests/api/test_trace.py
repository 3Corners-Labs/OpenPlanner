import io
import json
import logging

import httpx
import structlog
from fastapi import APIRouter, Depends, FastAPI

from app.api.trace import TraceMiddleware, bind_group_from_path
from app.domain.event_context import TRACE_ID, current, current_trace_id
from app.logging import configure_logging


def _app() -> FastAPI:
    app = FastAPI()
    router = APIRouter(dependencies=[Depends(bind_group_from_path)])

    @router.get("/g/{slug}")
    async def group(slug: str) -> dict[str, str | None]:
        context = current()
        return {"trace": context.trace_id, "group": context.group}

    app.include_router(router)
    app.add_middleware(TraceMiddleware)
    return app


async def _get(client: httpx.AsyncClient, path: str) -> dict[str, str | None]:
    response = await client.get(path, headers={"traceparent": f"00-{'c' * 32}-{'d' * 16}-01"})
    return dict(response.json())


async def test_every_request_gets_its_own_trace_and_its_group() -> None:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await _get(client, "/g/burgerzaken")
        second = await _get(client, "/g/burgerzaken")

    assert TRACE_ID.fullmatch(str(first["trace"]))
    assert first["trace"] != second["trace"]
    assert first["trace"] != "c" * 32  # an incoming traceparent is not trusted
    assert first["group"] == "burgerzaken"
    assert current_trace_id() == ""


async def test_a_malformed_slug_is_not_bound() -> None:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = await _get(client, "/g/Not%20A%20Slug")

    assert body["group"] is None


# ADR 0085 point 9: the ``http.request`` trace point carries the route template, never the path.


async def test_a_request_leaves_one_trace_line_with_its_route_template() -> None:
    out = io.StringIO()
    configure_logging("TRACE", stream=out)
    try:
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/g/burgerzaken?token=secret-token")
            await client.get("/nowhere")
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    traced = [line for line in lines if line["message"] == "http.request"]
    assert [(t["method"], t["route"], t["status"]) for t in traced] == [
        ("GET", "/g/{slug}", 200),
        ("GET", "", 404),
    ]
    assert all(isinstance(t["ms"], int) and TRACE_ID.fullmatch(t["trace.id"]) for t in traced)
    assert all("burgerzaken" not in str(t["route"]) for t in traced)  # the group tag may
    assert "secret-token" not in out.getvalue()
