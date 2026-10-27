import re
import xml.etree.ElementTree as ET  # noqa: S405 - parses our own generated file
from pathlib import Path

from scripts.diagrams import content, documents, drawio

PORTS_FILE = Path("app/domain/ports.py")


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


def test_ports_on_the_plates_exist_in_ports_py() -> None:
    declared = set(re.findall(r"^class (\w+)\(Protocol\)", PORTS_FILE.read_text(), re.M))
    ports_layer = next(layer for layer in content.LAYERS if layer.is_ports)
    drawn = {n.strip() for c in ports_layer.components for n in c.name.split("·")}
    drawn |= {p for lane in content.LANES for p in lane.ports}

    assert drawn <= declared


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
