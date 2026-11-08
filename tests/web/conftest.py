"""The admin site's fixtures, for the pages shared by the portal and the participant pages, and
a renderer for one template written in the test."""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.requests import Request

from app.i18n import Translator, load_catalogues
from app.web.locale import RequestLocale
from app.web.templates import create_templates, render
from tests.admin.conftest import admin_db, site  # noqa: F401 - re-exported fixtures

__all__ = ["admin_db", "render_source", "site"]

RenderSource = Callable[[str, Mapping[str, object]], HTMLResponse]


@pytest.fixture
def render_source(tmp_path: Path) -> RenderSource:
    """Return a function that renders template ``source`` with ``context`` through ``render``."""
    translator = Translator(load_catalogues())
    locale = RequestLocale(translator.bind("nl"), remember=False, translator=translator)
    request = Request(
        {"type": "http", "app": FastAPI(), "headers": [], "query_string": b"", "path": "/x"}
    )

    def rendered(source: str, context: Mapping[str, object]) -> HTMLResponse:
        (tmp_path / "page.html").write_text(source)
        return render(create_templates([tmp_path]), request, "page.html", locale, context)

    return rendered
