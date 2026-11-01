"""After a refused form the page opens at the first error, never at the top of a long page."""

import re
from pathlib import Path

from jinja2 import DictLoader, Environment

APP = Path(__file__).resolve().parents[2] / "app"
MACRO = APP / "web" / "templates" / "_error_message.html"


def test_every_error_message_comes_from_the_shared_macro() -> None:
    """A hand-written error lacks the focus, so the browser would start at the top again."""
    raw = [
        str(path.relative_to(APP))
        for path in APP.rglob("*.html")
        if path != MACRO and re.search(r'class="error-message', path.read_text())
    ]
    assert raw == []


def test_the_macro_takes_focus_so_the_browser_scrolls_to_it() -> None:
    env = Environment(loader=DictLoader({"m.html": MACRO.read_text()}), autoescape=True)
    macro = env.get_template("m.html").module.error_message  # type: ignore[attr-defined]

    html = str(macro("Te kort <b>", "error-package"))

    assert 'id="error-package"' in html
    assert 'tabindex="-1"' in html
    assert "autofocus" in html
    assert "Te kort &lt;b&gt;" in html
    assert str(macro("")) == ""
