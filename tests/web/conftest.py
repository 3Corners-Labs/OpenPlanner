"""A renderer for one template written in the test.

The admin site (``site``) that the pages shared by the portal and the participant pages use is
in ``tests/conftest.py``.
"""

from pathlib import Path

import pytest

from tests.support.template_source import RenderSource, source_renderer


@pytest.fixture
def render_source(tmp_path: Path) -> RenderSource:
    """Return a function that renders template ``source`` with ``context`` (and ``frame``)."""
    return source_renderer(tmp_path)
