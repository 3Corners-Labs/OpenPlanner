"""Build the public website, openplanner.nl, from ``site/`` into plain files (ADR 0087).

The site is one page per locale (``/`` Dutch, ``/en/`` English) rendered from
``site/templates/index.html`` with the texts of ``site/content/<locale>.json``, plus the
static files, the brand mark from the application (one source for the logo, ADR 0060) and
what search engines and AI crawlers read: ``sitemap.xml``, ``robots.txt``, ``llms.txt`` and the
JSON-LD inside the page. Release data is baked in from a GitHub releases JSON handed over by
the workflow; the visitor's browser never calls GitHub (ADR 0087 point 4).

Run ``make site`` (output in ``site/dist/``), or::

    python -m scripts.build_site --out site/dist --releases releases.json --commit <sha>
"""

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
BRAND = ROOT / "app" / "web" / "static" / "brand"
ADR_DIR = ROOT / "docs" / "adr"
LOCALES = ("nl", "en")
ORIGIN = "https://openplanner.nl"
REPOSITORY = "https://github.com/3Corners-Labs/OpenPlanner"
RECENT = 5


@dataclass(frozen=True)
class Release:
    """One GitHub release as the page shows it."""

    tag: str
    date: str
    url: str
    prerelease: bool


@dataclass(frozen=True)
class Releases:
    """The release data baked into the page: the latest of each kind and the recent list."""

    latest: Release | None
    candidate: Release | None
    recent: list[Release] = field(default_factory=list)


def parse_releases(raw: list[dict[str, Any]]) -> Releases:
    """Pick the latest release, the latest candidate and the recent list from GitHub's JSON.

    Args:
        raw: The array ``GET /repos/{owner}/{repo}/releases`` returns, newest first. Drafts
            are left out; a release without ``published_at`` is treated as a draft.

    Returns:
        The latest stable release, the latest pre-release and the first five entries.
    """
    releases = [
        Release(
            tag=str(item["tag_name"]),
            date=str(item["published_at"])[:10],
            url=str(item["html_url"]),
            prerelease=bool(item.get("prerelease")),
        )
        for item in raw
        if not item.get("draft")
        and item.get("published_at")
        and str(item["html_url"]).startswith(REPOSITORY + "/releases/")
    ]
    releases = list({r.tag: r for r in releases}.values())
    releases.sort(key=lambda r: (r.date, r.tag), reverse=True)
    latest = next((r for r in releases if not r.prerelease), None)
    candidate = next((r for r in releases if r.prerelease), None)
    return Releases(latest=latest, candidate=candidate, recent=releases[:RECENT])


@dataclass(frozen=True)
class Recording:
    """The recorded walkthrough on the page (ADR 0087 step 3), from ``static/media/tour.json``."""

    version: str
    recorded: str
    seconds: int


def recording(media: Path) -> Recording | None:
    """Return the recording in ``media`` when video, poster, captions and metadata are all there.

    The ``site`` job downloads them from the newest release that carries them (tour.yml); a
    local build has none, and the page then says the recording is still to come.
    """
    needed = ("tour.webm", "tour.jpg", "tour.nl.vtt", "tour.en.vtt", "tour.json")
    if not all((media / name).is_file() for name in needed):
        return None
    meta = json.loads((media / "tour.json").read_text(encoding="utf-8"))
    return Recording(
        version=str(meta.get("version", "")),
        recorded=str(meta.get("recorded", "")),
        seconds=int(meta.get("seconds", 0)),
    )


@dataclass(frozen=True)
class RoadmapItem:
    """One decision with open build steps, from the issues labelled ``adr:NNNN`` (ADR 0078)."""

    number: str
    title: str
    adr_url: str
    issues_url: str
    open_steps: int


_ADR_LABEL = re.compile(r"^adr:(\d{4})$")


def adr_titles(adr_dir: Path) -> dict[str, tuple[str, str]]:
    """Map each ADR number to its title and file name, read from ``docs/adr/NNNN-*.md``."""
    titles: dict[str, tuple[str, str]] = {}
    for path in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
        first = path.read_text(encoding="utf-8").split("\n", 1)[0]
        title = re.sub(r"^#\s*\d{4}\.\s*", "", first).strip()
        titles[path.name[:4]] = (title, path.name)
    return titles


def parse_roadmap(raw: list[dict[str, Any]], adr_dir: Path) -> list[RoadmapItem]:
    """Group the open ADR step issues by decision, newest decision first.

    Args:
        raw: The array ``GET /repos/{owner}/{repo}/issues?state=open`` returns. Pull requests
            and issues without an ``adr:NNNN`` label (such as ``adr:drift``) are left out.
        adr_dir: Where the ADR files are, for the titles.

    Returns:
        One item per decision with at least one open step.
    """
    counts: dict[str, int] = {}
    for issue in raw:
        if "pull_request" in issue or issue.get("state", "open") != "open":
            continue
        for label in issue.get("labels", []):
            match = _ADR_LABEL.match(str(label.get("name", "")))
            if match:
                counts[match.group(1)] = counts.get(match.group(1), 0) + 1
                break
    titles = adr_titles(adr_dir)
    items = []
    for number in sorted(counts, reverse=True):
        title, file = titles.get(number, (f"ADR {number}", ""))
        items.append(
            RoadmapItem(
                number=number,
                title=title,
                adr_url=f"{REPOSITORY}/blob/main/docs/adr/{file}"
                if file
                else f"{REPOSITORY}/tree/main/docs/adr",
                issues_url=f"{REPOSITORY}/issues?q=is%3Aissue+is%3Aopen+label%3Aadr%3A{number}",
                open_steps=counts[number],
            )
        )
    return items


def brand_mark() -> Markup:
    """The application's mark (ADR 0060), inline so CSS can colour and animate its parts.

    The file is the product's own and has no script or external reference (ADR 0060 §5);
    the fixed fills are left in place and the site's stylesheet overrides them by token.
    """
    svg = (BRAND / "mark.svg").read_text(encoding="utf-8").strip()
    if "<script" in svg or "href=" in svg:
        msg = "mark.svg must stay a plain drawing (ADR 0060 §5)"
        raise ValueError(msg)
    tagged = svg.replace("<svg ", '<svg class="mark" aria-hidden="true" focusable="false" ', 1)
    return Markup(tagged)  # noqa: S704 - the product's own file, checked above


def load_content(locale: str) -> dict[str, Any]:
    """Read ``site/content/<locale>.json``."""
    with (SITE / "content" / f"{locale}.json").open(encoding="utf-8") as handle:
        content: dict[str, Any] = json.load(handle)
    return content


def structured_data(content: dict[str, Any], releases: Releases | None) -> list[dict[str, Any]]:
    """The JSON-LD blocks of a page: the software, its publisher and the questions it answers.

    Search engines and AI assistants read these before the prose. Every value comes from the
    locale's content so the two languages never drift from the page.
    """
    software: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "OpenPlanner",
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Linux (Docker)",
        "url": ORIGIN + content["path"],
        "inLanguage": content["lang"],
        "description": content["meta"]["description"],
        "license": "https://joinup.ec.europa.eu/collection/eupl/eupl-text-eupl-12",
        "isAccessibleForFree": True,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"},
        "codeRepository": REPOSITORY,
        "featureList": [item["title"] for item in content["features"]["items"]],
        "publisher": {"@type": "Organization", "name": "3Corners", "url": "https://3corners.nl"},
    }
    if releases and releases.latest:
        software["softwareVersion"] = releases.latest.tag.lstrip("v")
        software["releaseNotes"] = releases.latest.url
    faq = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": item["name"],
                "acceptedAnswer": {"@type": "Answer", "text": item["text"]},
            }
            for item in content["integrations"]["items"]
        ],
    }
    return [software, faq]


def llms_text(
    contents: dict[str, dict[str, Any]],
    releases: Releases | None,
    roadmap: list[RoadmapItem] | None = None,
) -> str:
    """``llms.txt``: the site in Markdown for AI tools, with the links that carry the facts.

    The format (llmstxt.org) is a title, a blockquote summary, sections of links. English
    first, Dutch after it, so a model that reads only the top gets the full picture.
    """
    en, nl = contents["en"], contents["nl"]
    lines = [
        "# OpenPlanner",
        "",
        f"> {en['meta']['description']}",
        "",
        "OpenPlanner is open source (EUPL-1.2), written in Python (FastAPI, SQLite) with a",
        "server-rendered admin portal and a vanilla-JavaScript booking widget. Every design",
        "decision is an Architecture Decision Record in the repository with its build status.",
        "",
        "## Key facts",
        "",
    ]
    lines += [f"- {item['title']}: {item['text']}" for item in en["features"]["items"]]
    lines += ["", "## Integrations", ""]
    lines += [
        f"- {item['name']} ({item['tag']}): {item['text']} [{item['link']}]({item['href']})"
        for item in en["integrations"]["items"]
    ]
    lines += ["", "## Enterprise", ""]
    lines += [f"- {item['title']}: {item['text']}" for item in en["enterprise"]["items"]]
    status = en["standards"]["status"]
    lines += ["", "## Standards", ""]
    lines += [
        f"- {item['name']} ({status[item['status']]}): {item['what']} "
        f"[{item['link']}]({item['href']})"
        for item in en["standards"]["items"]
    ]
    lines += ["", "## Automatic updates", ""]
    lines += [f"- {line}" for line in en["standards"]["update"]]
    if roadmap:
        lines += ["", "## Roadmap (decided, not built yet)", ""]
        lines += [
            f"- ADR {item.number} {item.title}: {item.open_steps} open steps ({item.issues_url})"
            for item in roadmap
        ]
    if releases and releases.latest:
        lines += [
            "",
            "## Releases",
            "",
            f"- Latest release: {releases.latest.tag} ({releases.latest.date}): "
            f"{releases.latest.url}",
        ]
    lines += [
        "",
        "## Documentation",
        "",
        f"- [Repository]({REPOSITORY})",
        f"- [README]({REPOSITORY}#readme)",
        f"- [Installation guide]({REPOSITORY}/blob/main/docs/install.md)",
        f"- [Architecture]({REPOSITORY}/blob/main/docs/architecture.md)",
        f"- [Architecture Decision Records]({REPOSITORY}/tree/main/docs/adr)",
        f"- [Embedding the widget]({REPOSITORY}/blob/main/docs/widget.md)",
        f"- [Machine API for bots]({REPOSITORY}/blob/main/docs/agent-api.md)",
        f"- [Security policy]({REPOSITORY}/blob/main/SECURITY.md)",
        f"- [Releases]({REPOSITORY}/releases)",
        "",
        "## Nederlands",
        "",
        f"> {nl['meta']['description']}",
        "",
        f"- [Nederlandse pagina]({ORIGIN}/)",
        f"- [English page]({ORIGIN}/en/)",
        "",
    ]
    return "\n".join(lines)


def sitemap(built: datetime) -> str:
    """``sitemap.xml`` with both locales as alternates of each other."""
    day = built.strftime("%Y-%m-%d")
    alternates = "".join(
        f'<xhtml:link rel="alternate" hreflang="{loc}" href="{ORIGIN}{path}"/>'
        for loc, path in (("nl", "/"), ("en", "/en/"), ("x-default", "/"))
    )
    urls = "".join(
        f"<url><loc>{ORIGIN}{path}</loc><lastmod>{day}</lastmod>{alternates}</url>"
        for path in ("/", "/en/")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">'
        f"{urls}</urlset>\n"
    )


ROBOTS = f"""# openplanner.nl: a public, static site. Everyone may read it, including AI crawlers.
User-agent: *
Allow: /

Sitemap: {ORIGIN}/sitemap.xml
"""


def build(
    out: Path,
    releases: Releases | None,
    commit: str,
    base: str = "/",
    roadmap: list[RoadmapItem] | None = None,
) -> list[Path]:
    """Render every locale into ``out`` and copy the static files. Returns the files written.

    ``base`` is the root the assets are linked from: ``/`` on the web server; a relative
    root for a preview served from a sub-path. ``releases`` and ``roadmap`` are ``None`` in a
    local build without GitHub data; the page then links to GitHub instead.
    """
    env = Environment(
        loader=FileSystemLoader(SITE / "templates"),
        autoescape=select_autoescape(default=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("index.html")
    built = datetime.now(tz=UTC)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SITE / "static", out / "static")
    shutil.copytree(BRAND, out / "static" / "brand")
    # Files for the web root itself (.htaccess): the host's configuration, not page content.
    shutil.copytree(SITE / "root", out, dirs_exist_ok=True)
    written: list[Path] = []
    contents = {locale: load_content(locale) for locale in LOCALES}
    for content in contents.values():
        page = out / content["path"].strip("/") / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        # A relative asset root is one level deeper for the English page.
        page_base = (
            base if page.parent == out or not base.startswith(".") else "../" + base.lstrip("./")
        )
        page.write_text(
            template.render(
                c=content,
                releases=releases,
                roadmap=roadmap,
                mark=brand_mark(),
                commit=commit,
                base=page_base,
                tour=recording(SITE / "static" / "media"),
                structured_data=structured_data(content, releases),
                built=built.strftime("%Y-%m-%d"),
            ),
            encoding="utf-8",
        )
        written.append(page)
    for name, text in (
        ("sitemap.xml", sitemap(built)),
        ("robots.txt", ROBOTS),
        ("llms.txt", llms_text(contents, releases, roadmap)),
    ):
        (out / name).write_text(text, encoding="utf-8")
        written.append(out / name)
    return written


def main(argv: list[str] | None = None) -> int:
    """Command line: output folder, GitHub releases and issues JSON, commit and asset root."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=SITE / "dist")
    parser.add_argument(
        "--releases", type=Path, help="JSON from GET /repos/{owner}/{repo}/releases"
    )
    parser.add_argument(
        "--issues", type=Path, help="JSON from GET /repos/{owner}/{repo}/issues?state=open"
    )
    parser.add_argument("--commit", default="", help="the commit the site is built from")
    parser.add_argument(
        "--base", default="/", help="asset root: '/' on the server, './' for a preview"
    )
    args = parser.parse_args(argv)
    releases = None
    if args.releases:
        with args.releases.open(encoding="utf-8") as handle:
            releases = parse_releases(json.load(handle))
    roadmap = None
    if args.issues:
        with args.issues.open(encoding="utf-8") as handle:
            roadmap = parse_roadmap(json.load(handle), ADR_DIR)
    written = build(args.out, releases, args.commit, args.base, roadmap)
    sys.stdout.write(f"built {len(written)} files into {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
