import re
import subprocess
import xml.etree.ElementTree as ET  # noqa: S405 - parses our own generated file
from pathlib import Path

import pytest

from scripts.diagrams import build, content, documents, drawio

PORTS_FILE = Path("app/domain/ports.py")
WHOLE_PNG = b"\x89PNG\r\n\x1a\n" + build.PNG_END


def test_drawio_is_well_formed_with_three_pages() -> None:
    root = ET.fromstring(drawio.build())  # noqa: S314 - our own output

    pages = root.findall("diagram")

    assert [p.get("name") for p in pages] == [
        "1 Hoofdlijn",
        "2 Softwarecomponenten",
        "3 Integraties en adapters",
    ]


def test_cell_ids_are_unique_per_page() -> None:
    for page in ET.fromstring(drawio.build()).findall("diagram"):  # noqa: S314 - our own output
        ids = [cell.get("id") for cell in page.iter("mxCell")]

        assert len(ids) == len(set(ids))


def test_every_building_block_is_on_plate_one_and_in_the_register() -> None:
    ids = [b.id for b in content.BLOCKS]
    overview = ET.tostring(ET.fromstring(drawio.build()).findall("diagram")[0], "unicode")  # noqa: S314

    assert sorted(ids, key=lambda i: int(i[1:])) == [f"B{n}" for n in range(1, 18)]
    assert all(f"<b>{i}</b>" in overview.replace("&lt;", "<").replace("&gt;", ">") for i in ids)
    assert [row[1][0] for row in documents.register_rows()] == ids


def test_component_tags_name_existing_blocks() -> None:
    known = {b.id for b in content.BLOCKS}
    tags = {t for layer in content.LAYERS for c in layer.components for t in c.tags.split()}
    tags |= {t for lane in content.LANES for t in lane.tags.split()}

    assert tags <= known


def _declared_ports() -> set[str]:
    return set(re.findall(r"^class (\w+)\(Protocol\)", PORTS_FILE.read_text(), re.M))


def _ports_on_plate_two() -> list[str]:
    ports_layer = next(layer for layer in content.LAYERS if layer.is_ports)
    return [n.strip() for c in ports_layer.components for n in c.name.split("·")]


def test_ports_on_the_plates_exist_in_ports_py() -> None:
    drawn = set(_ports_on_plate_two()) | {p for lane in content.LANES for p in lane.ports}

    assert drawn <= _declared_ports()


def test_every_port_in_ports_py_is_on_plate_two_once() -> None:
    drawn = _ports_on_plate_two()

    missing = _declared_ports() - set(drawn)
    assert not missing, f"add {sorted(missing)} to the Poorten layer in scripts/diagrams/content.py"
    assert len(drawn) == len(set(drawn))


def test_plain_strips_markup_and_entities() -> None:
    assert documents.plain("<b>Planning &amp; beschikbaarheid</b><br>x") == (
        "Planning & beschikbaarheid x"
    )


def test_png_size_reads_the_header(tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (640).to_bytes(4) + (480).to_bytes(4)
    )

    assert documents.png_size(png) == (640, 480)


def test_complete_needs_the_closing_chunk(tmp_path: Path) -> None:
    png, pdf = tmp_path / "a.png", tmp_path / "a.pdf"
    png.write_bytes(WHOLE_PNG)
    pdf.write_bytes(b"%PDF-1.7\n1 0 obj\n%%EOF\n")

    assert build.complete(png)
    assert build.complete(pdf)
    assert not build.complete(tmp_path / "missing.png")
    png.write_bytes(WHOLE_PNG[:-4])
    assert not build.complete(png)


def _fake_drawio(monkeypatch: pytest.MonkeyPatch, *, write: bool, hang: bool) -> list[tuple]:
    """Stand in for draw.io: write a whole PNG to the ``-o`` path if asked, then hang if asked.

    A hang is the ``TimeoutExpired`` that ``subprocess.run`` raises once it has stopped the
    process; no real process runs, so the tests do not depend on the machine's speed.
    """
    calls: list[tuple] = []

    def run(args: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kwargs["timeout"]))
        if write:
            Path(args[args.index("-o") + 1]).write_bytes(WHOLE_PNG)
        if hang:
            raise subprocess.TimeoutExpired(args, build.EXPORT_SECONDS)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(build.subprocess, "run", run)
    return calls


def test_export_keeps_a_whole_file_when_drawio_hangs_after_writing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_drawio(monkeypatch, write=True, hang=True)
    image, source = tmp_path / "plaat-3.png", tmp_path / "source.drawio"

    build.export(image, source, "-x", "-p", "3")

    assert image.read_bytes() == WHOLE_PNG
    assert calls == [
        ((build.DRAWIO, "-x", "-p", "3", "-o", str(image), str(source)), build.EXPORT_SECONDS)
    ]


def test_export_stops_the_build_when_drawio_hangs_without_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_drawio(monkeypatch, write=False, hang=True)

    with pytest.raises(subprocess.TimeoutExpired):
        build.export(tmp_path / "plaat-1.png", tmp_path / "source.drawio", "-x")


def test_export_never_takes_an_old_file_for_a_new_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_drawio(monkeypatch, write=False, hang=False)
    image = tmp_path / "plaat-1.png"
    image.write_bytes(WHOLE_PNG)

    with pytest.raises(RuntimeError, match="no complete"):
        build.export(image, tmp_path / "source.drawio", "-x")
    assert not image.exists()
