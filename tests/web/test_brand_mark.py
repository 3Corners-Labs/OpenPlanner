"""The product mark inline and its optional motion (ADR 0060 §7, build step 2)."""

import re
from pathlib import Path

from tests.admin.conftest import SUPER, Site

STATIC = Path(__file__).resolve().parents[2] / "app" / "web" / "static"


def test_the_motion_never_plays_under_reduced_motion_and_stops_on_its_own() -> None:
    """Every animation sits inside ``no-preference``; the loop stops after two rounds."""
    css = (STATIC / "brand-mark.css").read_text(encoding="utf-8")
    guarded = re.search(
        r"@media \(prefers-reduced-motion: no-preference\) \{(.*?)\n\}", css, re.DOTALL
    )

    assert guarded is not None
    outside = css.replace(guarded.group(0), "")
    assert "animation:" not in outside
    assert "brand-mark-cross 2.4s steps(1, end) 2;" in guarded.group(1)  # 4.8 s (WCAG 2.2.2)
    assert "infinite" not in css
    assert "forced-colors: active" in css


def test_both_surfaces_link_the_one_brand_sheet() -> None:
    root = STATIC.parents[1]
    shell = (root / "admin" / "templates" / "admin" / "_shell.html").read_text(encoding="utf-8")
    base = (root / "web" / "templates" / "base.html").read_text(encoding="utf-8")

    assert "static_url('brand-mark.css')" in shell
    assert "static_url('brand-mark.css')" in base


def test_the_island_only_adds_the_settle_class_once_per_sign_in() -> None:
    script = (STATIC / "brand-motion.js").read_text(encoding="utf-8")

    assert "customElements.define('op-brand-mark'" in script
    assert "prefers-reduced-motion: reduce" in script
    assert "sessionStorage" in script
    assert "innerHTML" not in script


async def test_the_overview_mark_names_the_sign_in_and_loads_the_island(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/")).text
    other = (await site.client.get("/admin/audit")).text

    assert re.search(r'<op-brand-mark data-sign-in="\d+"><svg class="brand-mark', page)
    assert 'aria-hidden="true"' in page.split("<op-brand-mark", 1)[1].split("</op-brand-mark>")[0]
    assert "brand-motion.js" in page
    assert "brand-motion.js" not in other  # elsewhere the mark stays static


def test_the_login_hand_off_loops_without_script() -> None:
    """The hand-off page has no script: the loop is CSS only."""
    root = STATIC.parents[1]
    hand_off = (root / "admin" / "templates" / "admin" / "login_redirect.html").read_text()

    assert 'brand_mark("logging-in__mark", 48, "loading")' in hand_off
