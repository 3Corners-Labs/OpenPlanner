"""The ``template.render`` trace point: template name and duration (ADR 0085 point 9)."""

from pathlib import Path

from fastapi import FastAPI
from starlette.requests import Request

from app.i18n import Translator, load_catalogues
from app.web.locale import RequestLocale
from app.web.templates import create_templates, render
from tests.support.tracing import captured_trace


def test_rendering_a_page_leaves_its_template_name_and_duration(tmp_path: Path) -> None:
    (tmp_path / "hello.html").write_text("<p>{{ greeting }}</p>")
    translator = Translator(load_catalogues())
    locale = RequestLocale(translator.bind("nl"), remember=False, translator=translator)
    request = Request(
        {"type": "http", "app": FastAPI(), "headers": [], "query_string": b"", "path": "/x"}
    )

    with captured_trace() as lines:
        response = render(
            create_templates([tmp_path]),
            request,
            "hello.html",
            locale,
            {"greeting": "ada@example.org"},
        )

    assert response.status_code == 200
    assert [line.point for line in lines] == ["template.render"]
    assert lines[0].fields["template"] == "hello.html"
    assert isinstance(lines[0].fields["ms"], int)
    assert "ada@example.org" not in repr(lines)
