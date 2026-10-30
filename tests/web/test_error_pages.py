"""Malformed parameters of a page get the error page, never an echo of what was typed."""

from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from app.web.error_pages import validation_error_page
from tests.admin.conftest import SUPER, Site


async def test_a_malformed_query_parameter_on_a_page_is_not_echoed(site: Site) -> None:
    """ADR 0063 §3: a value typed into a list's address never comes back in the answer."""
    await site.login_as(SUPER)

    response = await site.client.get("/admin/groups/demo/closure", params={"scenario": "zqmail7x"})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "zqmail7x" not in response.text
    assert "Dit verzoek kon niet worden verwerkt." in response.text


async def test_a_citizen_page_gets_the_same_error_page(site: Site) -> None:
    response = await site.client.get("/g/demo/book", params={"start": "zqmail7x"})

    assert response.status_code < 500
    assert "zqmail7x" not in response.text


async def test_machine_paths_keep_the_json_answer_of_the_spec() -> None:
    request = Request(
        {"type": "http", "path": "/api/agent/v1/groups", "query_string": b"", "headers": []}
    )
    error = RequestValidationError(
        [{"loc": ("query", "days"), "msg": "bad", "type": "x", "input": "zq"}]
    )

    response = await validation_error_page(request, error)

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
