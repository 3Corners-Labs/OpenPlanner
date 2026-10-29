"""The ``<gov-booking>`` script for host websites (ADR 0035 §5).

    GET /widget/v1/gov-booking.js    the bundle, 404 when this installation has none

Host pages load it with ``<script type="module" … crossorigin>``, a CORS request: the answer
allows every origin (``*``, never with credentials) and overrides the default
``Cross-Origin-Resource-Policy: same-origin``. The script itself only talks to the widget API,
which checks the organisation's allowed origins.
"""

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.web.protection import asset_limit, limiter
from app.web.widget_bundle import SCRIPT_URL_PATH, load_bundle

router = APIRouter(include_in_schema=False)

CACHE = "public, max-age=300"
"""Short: the path keeps its major version across releases, the content changes with them."""


@router.get(SCRIPT_URL_PATH)
@limiter.limit(asset_limit)
async def script(request: Request) -> Response:
    """Serve the widget script, or 404 without a build."""
    bundle = load_bundle()
    if bundle is None:
        return Response(status_code=404)
    return Response(
        bundle.data,
        media_type="text/javascript; charset=utf-8",
        headers={
            "cache-control": CACHE,
            "access-control-allow-origin": "*",
            "cross-origin-resource-policy": "cross-origin",
        },
    )
