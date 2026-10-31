"""The public website builds from site/ and keeps the rules of ADR 0087."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scripts.build_site import (
    LOCALES,
    SITE,
    Releases,
    build,
    llms_text,
    load_content,
    parse_releases,
)

RAW_RELEASES = [
    {
        "tag_name": "v0.4.0-rc.3",
        "published_at": "2026-10-31T21:00:00Z",
        "html_url": "https://github.com/3Corners-Labs/OpenPlanner/releases/tag/v0.4.0-rc.3",
        "prerelease": True,
    },
    {
        "tag_name": "v0.3.0",
        "published_at": "2026-10-20T10:00:00Z",
        "html_url": "https://github.com/3Corners-Labs/OpenPlanner/releases/tag/v0.3.0",
        "prerelease": False,
    },
    {
        "tag_name": "v0.4.0-rc.2",
        "published_at": "2026-10-30T21:00:00Z",
        "html_url": "https://github.com/3Corners-Labs/OpenPlanner/releases/tag/v0.4.0-rc.2",
        "prerelease": True,
    },
    {
        "tag_name": "v0.2.0",
        "published_at": "2026-10-01T10:00:00Z",
        "html_url": "https://github.com/3Corners-Labs/OpenPlanner/releases/tag/v0.2.0",
        "prerelease": False,
    },
    {
        "tag_name": "v9.9.9",
        "published_at": None,
        "html_url": "https://x/draft",
        "prerelease": False,
        "draft": True,
    },
]


class _Page(HTMLParser):
    """Collects what the checks below need: tags with attributes, headings, scripts."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def find(self, tag: str) -> list[dict[str, str | None]]:
        return [a for t, a in self.tags if t == tag]


@pytest.fixture(scope="module")
def dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("site")
    build(out, parse_releases(RAW_RELEASES), "0123456789abcdef", tour_video=False)
    return out


def _page(dist: Path, locale: str) -> tuple[str, _Page]:
    path = dist / ("index.html" if locale == "nl" else f"{locale}/index.html")
    html = path.read_text(encoding="utf-8")
    parser = _Page()
    parser.feed(html)
    return html, parser


def test_parse_releases_picks_latest_candidate_and_recent_and_skips_drafts_and_foreign_urls() -> (
    None
):
    releases = parse_releases(RAW_RELEASES)
    assert releases.latest is not None
    assert releases.latest.tag == "v0.3.0"
    assert releases.candidate is not None
    assert releases.candidate.tag == "v0.4.0-rc.3"
    assert [r.tag for r in releases.recent] == ["v0.4.0-rc.3", "v0.4.0-rc.2", "v0.3.0", "v0.2.0"]
    assert releases.latest.date == "2026-10-20"


def test_both_locales_have_the_same_keys() -> None:
    def keys(value: object, prefix: str = "") -> set[str]:
        if isinstance(value, dict):
            return {
                k
                for key, inner in value.items()
                for k in ({prefix + key} | keys(inner, prefix + key + "."))
            }
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return keys(value[0], prefix + "[].")
        return set()

    assert keys(load_content("nl")) == keys(load_content("en"))


@pytest.mark.parametrize("locale", LOCALES)
def test_page_is_complete_for_its_locale(dist: Path, locale: str) -> None:
    html, page = _page(dist, locale)
    assert page.find("html")[0]["lang"] == locale
    assert html.count("<h1") == 1
    assert page.find("title")
    assert page.find("main")
    hreflangs = {a["hreflang"] for a in page.find("link") if a.get("rel") == "alternate"}
    assert hreflangs == {"nl", "en", "x-default"}
    canonical = [a for a in page.find("link") if a.get("rel") == "canonical"][0]["href"]
    assert canonical == (
        "https://openplanner.nl/" if locale == "nl" else "https://openplanner.nl/en/"
    )
    # Every image has a text alternative (empty for the decorative mark).
    assert all("alt" in a for a in page.find("img"))
    # The release data is baked in: the latest release, the candidate and the commit.
    assert "v0.3.0" in html
    assert "v0.4.0-rc.3" in html
    assert "0123456789ab" in html
    # The structured data for search engines and AI assistants.
    assert '"@type": "SoftwareApplication"' in html
    assert '"@type": "FAQPage"' in html


@pytest.mark.parametrize("locale", LOCALES)
def test_page_loads_nothing_from_another_origin(dist: Path, locale: str) -> None:
    html, page = _page(dist, locale)
    csp = [a for a in page.find("meta") if a.get("http-equiv") == "Content-Security-Policy"][0][
        "content"
    ]
    assert csp is not None
    assert csp.startswith("default-src 'self'")
    for tag, attr in (
        ("script", "src"),
        ("link", "href"),
        ("img", "src"),
        ("source", "src"),
        ("video", "poster"),
    ):
        for attrs in page.find(tag):
            value = attrs.get(attr)
            if (
                value
                and tag != "link"
                or (
                    tag == "link" and attrs.get("rel") in ("stylesheet", "icon", "apple-touch-icon")
                )
            ):
                assert value is not None, (tag, value)
                assert not re.match(r"^(https?:)?//", value), (tag, value)
    assert "<style" not in html, "styles live in site.css, inline styles would need a CSP exception"


def test_script_sets_no_cookie_and_uses_no_storage() -> None:
    script = (SITE / "static" / "site.js").read_text(encoding="utf-8")
    for forbidden in (
        "document.cookie",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "fetch(",
        "XMLHttpRequest",
        "navigator.sendBeacon",
    ):
        assert forbidden not in script, forbidden


def test_build_writes_the_files_for_crawlers(dist: Path) -> None:
    assert (dist / "robots.txt").read_text().startswith("# openplanner.nl")
    sitemap = (dist / "sitemap.xml").read_text()
    assert "https://openplanner.nl/</loc>" in sitemap
    assert "https://openplanner.nl/en/</loc>" in sitemap
    llms = (dist / "llms.txt").read_text()
    assert llms.startswith("# OpenPlanner\n\n> ")
    assert "## Integrations" in llms
    assert "v0.3.0" in llms
    assert (dist / "static" / "brand" / "mark.svg").exists(), (
        "the brand mark comes from the application"
    )
    assert (dist / "static" / "og-nl.png").exists()
    assert (dist / "static" / "og-en.png").exists()


def test_build_without_release_data_links_to_github(tmp_path: Path) -> None:
    build(tmp_path, None, "", tour_video=False)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "github.com/3Corners-Labs/OpenPlanner/releases" in html
    assert "gebouwd uit commit" not in html
    assert "## Releases" not in llms_text(
        {"nl": load_content("nl"), "en": load_content("en")}, Releases(None, None)
    )


def test_content_files_are_valid_json_with_trailing_newline() -> None:
    for locale in LOCALES:
        text = (SITE / "content" / f"{locale}.json").read_text(encoding="utf-8")
        json.loads(text)
        assert text.endswith("}\n")
