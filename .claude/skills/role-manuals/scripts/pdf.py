"""Print the HTML manuals in build/ to tagged PDF with Chrome (Playwright).

Usage: python3 scripts/pdf.py [build/<name>.html ...]   (default: every HTML file in build/)
The PDF gets A4 pages, a footer with title and page number, bookmarks from the headings and a
tagged structure for screen readers.
"""

import html
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FOOTER = (
    '<div style="font-family:Arial,sans-serif;font-size:8px;color:#666;width:100%;'
    'padding:0 18mm;display:flex;justify-content:space-between">'
    '<span>{title}</span><span>Pagina <span class="pageNumber"></span> van '
    '<span class="totalPages"></span></span></div>'
)


def main(paths: list[str]) -> None:
    """Print each HTML file to a PDF next to it."""
    files = [Path(p).resolve() for p in paths] or sorted((ROOT / "build").glob("*.html"))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page()
        for file in files:
            page.goto(file.as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            title = html.escape(page.title())
            target = file.with_suffix(".pdf")
            page.pdf(
                path=str(target),
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=FOOTER.format(title=title),
                margin={"top": "18mm", "bottom": "20mm", "left": "18mm", "right": "18mm"},
                tagged=True,
                outline=True,
            )
            print(f"pdf: {target.name}")
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:])
