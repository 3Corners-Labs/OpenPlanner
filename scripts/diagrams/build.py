"""Regenerate everything in ``docs/diagrams/`` from ``scripts/diagrams/content.py``.

Usage: ``make diagrams`` (runs ``python -m scripts.diagrams.build`` with python-docx added for
this run only). Needs two desktop tools, found on their macOS paths or through the environment:

    DRAWIO   draw.io desktop CLI     exports the plates to PNG and the plate PDF
    CHROME   Chrome or Chromium      prints the complete document PDF

Outputs: ``openplanner-architectuur.drawio`` (editable source), ``plaat-{1,2,3}.png``,
``openplanner-architectuur-platen.pdf`` (vector plates), ``openplanner-architectuur.pdf``
(plates and register, A4 landscape) and ``openplanner-architectuur.docx``.
"""

import os
import subprocess  # noqa: S404 - runs two local desktop tools with fixed arguments
import sys
import tempfile
from pathlib import Path

from scripts.diagrams import documents, drawio

OUT = Path("docs/diagrams")
NAME = "openplanner-architectuur"
DRAWIO = os.environ.get("DRAWIO", "/Applications/draw.io.app/Contents/MacOS/draw.io")
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
EXPORT_SECONDS = 120
"""How long one draw.io export may run: seconds normally, so this much means draw.io hangs."""
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
"""The IEND chunk that closes every PNG."""


def run(*args: str) -> None:
    """Run a tool and stop the build when it fails."""
    subprocess.run(args, check=True, capture_output=True)  # noqa: S603 - fixed tool, own paths


def complete(path: Path) -> bool:
    """Return whether ``path`` is a whole PNG (up to IEND) or a whole PDF (up to ``%%EOF``)."""
    if not path.is_file():
        return False
    data = path.read_bytes()
    if path.suffix == ".png":
        return data.endswith(PNG_END)
    return data.rstrip().endswith(b"%%EOF")


def export(output: Path, source: Path, *options: str) -> None:
    """Export ``source`` with draw.io to ``output``; stop the build unless the file is whole.

    draw.io 29 on macOS can hit a fatal error while it shuts down, after the file is written,
    and then spin instead of exiting. After ``EXPORT_SECONDS`` the process is stopped: a whole
    file is kept with a note on stderr, anything else stops the build.
    """
    output.unlink(missing_ok=True)
    args = (DRAWIO, *options, "-o", str(output), str(source))
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=EXPORT_SECONDS)  # noqa: S603
    except subprocess.TimeoutExpired:
        if not complete(output):
            raise
        print(f"draw.io did not exit after writing {output}; stopped it", file=sys.stderr)
    if not complete(output):
        raise RuntimeError(f"draw.io wrote no complete {output}")


def main() -> int:
    """Write the draw.io source, export it and build both documents; return the exit code."""
    for tool in (DRAWIO, CHROME):
        if not Path(tool).exists():
            print(f"missing tool: {tool} (set DRAWIO or CHROME)", file=sys.stderr)
            return 1
    OUT.mkdir(parents=True, exist_ok=True)
    source = OUT / f"{NAME}.drawio"
    source.write_text(drawio.build(), encoding="utf-8")

    images = [OUT / f"plaat-{i}.png" for i in (1, 2, 3)]
    for i, image in enumerate(images, start=1):
        export(image, source, *"-x -f png -s 2 -b 20 -p".split(), str(i))
    plates_pdf = OUT / f"{NAME}-platen.pdf"
    export(plates_pdf, source, *"-x -f pdf -a --crop -b 20".split())

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "print.html"
        page.write_text(documents.print_page([i.resolve() for i in images]), encoding="utf-8")
        pdf = (OUT / f"{NAME}.pdf").resolve()
        flags = "--headless=new --disable-gpu --allow-file-access-from-files --no-pdf-header-footer"
        run(CHROME, *flags.split(), f"--print-to-pdf={pdf}", page.as_uri())

    documents.write_docx(images, OUT / f"{NAME}.docx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
