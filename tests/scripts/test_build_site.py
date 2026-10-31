"""The public website builds from site/ and keeps the rules of ADR 0087."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scripts.build_site import (
    LOCALES,
    ROOT,
    SITE,
    Releases,
    build,
    llms_text,
    load_content,
    parse_releases,
    parse_roadmap,
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


def test_the_latest_stable_release_is_found_behind_a_hundred_candidates() -> None:
    url = "https://github.com/3Corners-Labs/OpenPlanner/releases/tag/"
    candidates = [
        {
            "tag_name": f"v0.4.0-rc.{n}",
            "published_at": f"2026-10-31T{n % 24:02d}:00:00Z",
            "html_url": url + f"v0.4.0-rc.{n}",
            "prerelease": True,
        }
        for n in range(100)
    ]
    latest = {
        "tag_name": "v0.3.0",
        "published_at": "2026-10-30T11:19:10Z",
        "html_url": url + "v0.3.0",
        "prerelease": False,
    }
    # The workflow appends releases/latest; a duplicate of a listed release counts once.
    releases = parse_releases([*candidates, latest, latest])
    assert releases.latest is not None
    assert releases.latest.tag == "v0.3.0"
    assert sum(r.tag == "v0.3.0" for r in releases.recent) <= 1


def test_roadmap_groups_open_adr_steps_and_skips_the_rest(tmp_path: Path) -> None:
    (tmp_path / "0085-log-destinations.md").write_text(
        "# 0085. Log destinations in the portal\n\nbody"
    )
    issue = "https://github.com/3Corners-Labs/OpenPlanner/issues/"
    raw = [
        {"number": 1, "html_url": issue + "1", "labels": [{"name": "adr:0085"}], "state": "open"},
        {"number": 2, "html_url": issue + "2", "labels": [{"name": "adr:0085"}], "state": "open"},
        {"number": 3, "html_url": issue + "3", "labels": [{"name": "adr:0090"}], "state": "open"},
        {"number": 4, "html_url": issue + "4", "labels": [{"name": "adr:drift"}], "state": "open"},
        {"number": 5, "html_url": issue + "5", "labels": [{"name": "bug"}], "state": "open"},
        {
            "number": 6,
            "html_url": issue + "6",
            "labels": [{"name": "adr:0085"}],
            "pull_request": {},
        },
    ]
    roadmap = parse_roadmap(raw, tmp_path)
    assert [(item.number, item.open_steps) for item in roadmap] == [("0090", 1), ("0085", 2)]
    assert roadmap[1].title == "Log destinations in the portal"
    assert roadmap[1].adr_url.endswith("/docs/adr/0085-log-destinations.md")
    assert roadmap[0].title == "ADR 0090"
    assert "label%3Aadr%3A0085" in roadmap[1].issues_url


def test_roadmap_is_on_the_page_and_in_llms_txt(tmp_path: Path) -> None:
    (tmp_path / "adr").mkdir()
    (tmp_path / "adr" / "0085-x.md").write_text("# 0085. Log destinations\n")
    roadmap = parse_roadmap([{"labels": [{"name": "adr:0085"}], "state": "open"}], tmp_path / "adr")
    out = tmp_path / "out"
    build(out, None, "", tour_video=False, roadmap=roadmap)
    assert "Log destinations" in (out / "index.html").read_text(encoding="utf-8")
    assert "ADR 0085 Log destinations: 1 open steps" in (out / "llms.txt").read_text()


@pytest.mark.parametrize("locale", LOCALES)
def test_every_repository_link_on_the_site_exists(locale: str) -> None:
    text = json.dumps(load_content(locale))
    paths = set(re.findall(r"OpenPlanner/(?:blob|tree)/main/([^\"#?]+)", text))
    missing = sorted(path for path in paths if not (ROOT / path).exists())
    assert missing == []


def test_every_standard_has_a_known_status() -> None:
    for locale in LOCALES:
        standards = load_content(locale)["standards"]
        assert {item["status"] for item in standards["items"]} <= set(standards["status"])


def test_the_site_shows_the_applications_own_mark(dist: Path) -> None:
    mark = (ROOT / "app" / "web" / "static" / "brand" / "mark.svg").read_text(encoding="utf-8")
    slot = re.search(r'<path d="([^"]+)"', mark)
    assert slot is not None
    html = (dist / "index.html").read_text(encoding="utf-8")
    # Header, hero, integration hub and footer: the same drawing as the admin portal.
    assert html.count(slot.group(1)) == 4


def test_the_web_root_gets_its_server_configuration(dist: Path) -> None:
    htaccess = (dist / ".htaccess").read_text()
    assert "Content-Security-Policy" in htaccess
    assert "Strict-Transport-Security" not in htaccess, (
        "HSTS only once HTTPS works (ADR 0087 step 4)"
    )
