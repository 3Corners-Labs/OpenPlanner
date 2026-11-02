"""The public website builds from site/ and keeps the rules of ADR 0087."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

import scripts.build_site as build_site
from scripts.build_site import (
    LOCALES,
    ROOT,
    SITE,
    Recording,
    Releases,
    Screen,
    build,
    llms_text,
    load_content,
    parse_releases,
    recording,
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
    build(out, parse_releases(RAW_RELEASES), "0123456789abcdef")
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
    build(tmp_path, None, "")
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


def _built_adrs() -> set[str]:
    rows = (ROOT / "docs" / "adr-status.md").read_text(encoding="utf-8").splitlines()
    return {
        line[2:6]
        for line in rows
        if re.match(r"^\| \d{4} \|", line) and line.split("|")[3].strip().startswith("BUILT")
    }


@pytest.mark.parametrize("locale", LOCALES)
def test_roadmap_items_are_not_built_and_the_page_shows_no_adr_number(
    locale: str, tmp_path: Path
) -> None:
    items = load_content(locale)["roadmap"]["items"]
    assert items
    assert {item["adr"] for item in items}.isdisjoint(_built_adrs())
    out = tmp_path / "out"
    build(out, None, "")
    html = (out / ("index.html" if locale == "nl" else "en/index.html")).read_text(encoding="utf-8")
    body = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", body)
    assert not re.search(r"\bADR'?s?\b", text), "the main page lists no ADRs"
    for item in items:
        assert item["title"] in html


def test_roadmap_is_in_llms_txt(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build(out, None, "")
    first = load_content("en")["roadmap"]["items"][0]["title"]
    assert f"- {first}:" in (out / "llms.txt").read_text()


@pytest.mark.parametrize("locale", LOCALES)
def test_the_footer_carries_a_disclaimer(locale: str, dist: Path) -> None:
    html, _ = _page(dist, locale)
    assert "EUPL-1.2" in load_content(locale)["footer"]["disclaimer"]
    assert 'class="foot__disclaimer"' in html


def test_the_release_section_is_refreshed_live_from_github_only(dist: Path) -> None:
    html, _ = _page(dist, "en")
    script = (SITE / "static" / "site.js").read_text(encoding="utf-8")
    assert "connect-src https://api.github.com;" in html
    assert (SITE / "root" / ".htaccess").read_text().count(
        "connect-src https://api.github.com;"
    ) == 1
    assert "data-releases" in html
    assert script.count("fetch(") == 1
    assert 'credentials: "omit"' in script
    assert 'referrerPolicy: "no-referrer"' in script
    assert "innerHTML" not in script.split("The live release list")[1].split("The interactive")[0]
    assert set(re.findall(r"https://[\w.-]+", script.split("The live release list")[1])) <= {
        "https://api.github.com",
        "https://github.com",
    }


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


def test_a_recording_counts_only_when_all_its_files_are_there(tmp_path: Path) -> None:
    assert recording(tmp_path) is None
    for name in ("tour.webm", "tour.jpg", "tour.nl.vtt", "tour.en.vtt"):
        (tmp_path / name).write_text("x")
    assert recording(tmp_path) is None, "no metadata yet"
    (tmp_path / "tour.json").write_text(
        '{"version": "v0.4.0", "recorded": "2026-11-02", "seconds": 47}'
    )
    found = recording(tmp_path)
    assert found is not None
    assert (found.version, found.recorded, found.seconds) == ("v0.4.0", "2026-11-02", 47)


@pytest.mark.parametrize("locale", LOCALES)
def test_without_a_recording_the_page_says_it_is_coming(dist: Path, locale: str) -> None:
    html, page = _page(dist, locale)
    assert not page.find("video")
    assert load_content(locale)["tour"]["video_soon"] in html


def test_the_stills_of_a_recording_follow_the_story_and_skip_what_is_missing(
    tmp_path: Path,
) -> None:
    for name in ("tour.webm", "tour.jpg", "tour.nl.vtt", "tour.en.vtt"):
        (tmp_path / name).write_text("x")
    for key in ("book", "agenda"):
        (tmp_path / f"tour-{key}.jpg").write_text("x")
    (tmp_path / "tour.json").write_text(
        json.dumps({"version": "v0.4.0", "screens": ["book", "join", "agenda", "nonsense"]})
    )
    found = recording(tmp_path)
    assert found is not None
    assert [s.key for s in found.screens] == ["book", "agenda"], (
        "join has no file, nonsense no chapter"
    )
    assert found.screens[0].caption["en"].startswith("The participant picks a day")


def test_an_older_recording_without_stills_shows_the_video_alone(tmp_path: Path) -> None:
    for name in ("tour.webm", "tour.jpg", "tour.nl.vtt", "tour.en.vtt"):
        (tmp_path / name).write_text("x")
    (tmp_path / "tour.json").write_text('{"version": "v0.4.0"}')
    found = recording(tmp_path)
    assert found is not None
    assert found.screens == ()


@pytest.mark.parametrize("locale", LOCALES)
def test_the_real_screens_are_on_the_page_with_their_captions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locale: str
) -> None:
    screens = (Screen("book", {"nl": "Kies een tijd.", "en": "Pick a time."}),)
    monkeypatch.setattr(
        build_site, "recording", lambda _media: Recording("v0.4.0", "2026-11-01", 60, screens)
    )
    build(tmp_path, None, "0123456789abcdef")
    html, page = _page(tmp_path, locale)
    images = [a for a in page.find("img") if (a.get("src") or "").endswith("tour-book.jpg")]
    assert len(images) == 1
    assert images[0].get("alt"), "every still has a text alternative"
    assert {"nl": "Kies een tijd.", "en": "Pick a time."}[locale] in html
    assert load_content(locale)["tour"]["screens_title"] in html


@pytest.mark.parametrize("locale", LOCALES)
def test_the_architecture_plate_names_every_part_and_flow(dist: Path, locale: str) -> None:
    html, _ = _page(dist, locale)
    plate = load_content(locale)["enterprise"]["plate"]
    for node in plate["people"] + plate["host"] + plate["systems"]:
        assert node["name"].replace("'", "&#39;") in html
    for flow in plate["flows"]:
        assert flow.replace("'", "&#39;") in html
    planned = [n for n in plate["systems"] if n.get("planned")]
    assert html.count(plate["planned"]) == len(planned), "what is not built says so (point 8)"
