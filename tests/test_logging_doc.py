"""``docs/logging.md`` names every trace point and its fields (ADR 0085 point 9)."""

from pathlib import Path

from app.logging import TRACE_POINTS

DOC = Path(__file__).resolve().parents[1] / "docs" / "logging.md"


def test_the_logging_doc_lists_every_trace_point_with_its_fields() -> None:
    text = DOC.read_text()
    for point, fields in TRACE_POINTS.items():
        row = next((line for line in text.splitlines() if line.startswith(f"| `{point}` |")), None)
        assert row is not None, f"{point} is not in docs/logging.md"
        for name in fields:
            assert f"`{name}`" in row, f"field {name} of {point} is not in docs/logging.md"
