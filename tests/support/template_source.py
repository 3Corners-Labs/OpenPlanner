"""A template written in the test, rendered through the application's own ``render``.

The ``render_source`` fixture of ``tests/web/conftest.py`` returns ``source_renderer`` on the
test's own directory.
"""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.requests import Request

from app.i18n import Translator, load_catalogues
from app.web.locale import RequestLocale
from app.web.templates import create_templates, render

RenderSource = Callable[..., HTMLResponse]
"""``(source, context, frame=None, **options)``: render a template written in the test; the
options (``kind``) go to ``render``."""


def source_renderer(directory: Path) -> RenderSource:
    """Return a function that renders template ``source`` with ``context`` (and ``frame``).

    Each call writes its source to ``page.html`` in ``directory`` and renders that, so one test
    may render several sources.
    """
    translator = Translator(load_catalogues())
    locale = RequestLocale(translator.bind("nl"), remember=False, translator=translator)
    request = Request(
        {"type": "http", "app": FastAPI(), "headers": [], "query_string": b"", "path": "/x"}
    )

    def rendered(
        source: str,
        context: Mapping[str, object],
        frame: Mapping[str, object] | None = None,
        **options: Any,  # noqa: ANN401 - ``kind`` of ``render``, passed through
    ) -> HTMLResponse:
        (directory / "page.html").write_text(source)
        templates = create_templates([directory])
        return render(templates, request, "page.html", locale, context, frame=frame, **options)

    return rendered
