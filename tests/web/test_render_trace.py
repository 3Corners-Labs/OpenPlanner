"""The ``template.render`` trace point: template name and duration (ADR 0085 point 9)."""

from tests.support.tracing import captured_trace
from tests.web.conftest import RenderSource


def test_rendering_a_page_leaves_its_template_name_and_duration(
    render_source: RenderSource,
) -> None:
    with captured_trace() as lines:
        response = render_source("<p>{{ greeting }}</p>", {"greeting": "ada@example.org"})

    assert response.status_code == 200
    assert [line.point for line in lines] == ["template.render"]
    assert lines[0].fields["template"] == "page.html"
    assert isinstance(lines[0].fields["ms"], int)
    assert "ada@example.org" not in repr(lines)
