"""Shared plumbing of the admin pages that refresh themselves (ADR 0072, ADR 0010).

A page marks a region ``data-live`` and holds one ``data-live-fragment`` that names its poll
URL; ``static/live-region.js`` asks that URL every ``data-poll-seconds`` and swaps the answer
in. The server compares the ``seen`` key the page sends with the key of what it would render
and answers ``204`` while they are the same, so a quiet page costs one empty response.
"""

import hashlib
from typing import Annotated

from fastapi import Query, Request
from starlette.responses import Response

from app.admin.authz import AdminContext, can_function
from app.admin.pages import templates
from app.web.locale import RequestLocale
from app.web.templates import render

Seen = Annotated[str, Query(max_length=64)]
"""The key of the fragment the page shows; the server answers 204 while it is current."""


def key(*parts: object) -> str:
    """Return a short digest of what the fragment shows, so the poller can ask "still this?"."""
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


def fragment(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    name: str,
    seen: str,
    current: str,
    extra: dict[str, object],
) -> Response:
    """Answer the poller: 204 while ``seen`` is ``current``, otherwise the fragment.

    The fragment gets the group and ``can()`` like the page, for its links and actions.
    """
    if current == seen:
        return Response(status_code=204, headers={"cache-control": "no-store"})
    shared = {"summary": context.group, "can": can_function(context.principal)}
    return render(templates, request, name, locale, {**shared, **extra})
