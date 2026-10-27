"""Draw the three architecture plates as one editable draw.io file.

Pure string building, no third-party packages: every shape is an ``mxCell`` with a fixed
position, so the generated file opens in draw.io (desktop or diagrams.net) and can be edited
by hand. Layout constants are in draw.io units (pixels at 100 %).
"""

import html

from scripts.diagrams.content import (
    ACTORS,
    AS_OF,
    BLOCKS,
    EXTERNALS,
    FAMILY_NAMES,
    I18N_NOTE,
    LANES,
    LAYERS,
    LOGGING_BOX,
    PLATE1_FOOTNOTE,
    PLATE3_FOOTNOTE,
    PLATES,
    SHARED_PARTS,
    Block,
    Family,
    Layer,
)

COLOURS = {
    Family.CITIZEN: ("#0f7a6e", "#e3f3f0"),
    Family.CORE: ("#154273", "#e4ecf5"),
    Family.ADMIN: ("#9a5b00", "#fbefdc"),
    Family.PLATFORM: ("#5b4b8a", "#ece8f6"),
    Family.EXTERNAL: ("#4a5566", "#eef0f3"),
    Family.NONE: ("#5a6577", "#ffffff"),
}
ACCENT = "#154273"
MUTED = "#5a6577"
INK = "#142033"
FONT = "fontFamily=Helvetica;"
MONO = "fontFamily=Courier New;"


class Page:
    """One draw.io page: collects cells and serialises them as a ``<diagram>``."""

    def __init__(self, index: int, name: str, width: int, height: int) -> None:
        """Start an empty page; ``index`` keeps cell ids unique across pages."""
        self.prefix = f"p{index}"
        self.name, self.width, self.height = name, width, height
        self.cells: list[str] = []

    def _next_id(self) -> str:
        return f"{self.prefix}-{len(self.cells) + 1}"

    def box(self, x: float, y: float, w: float, h: float, label: str, style: str) -> str:
        """Add a vertex and return its id."""
        cid = self._next_id()
        self.cells.append(
            f'<mxCell id="{cid}" value="{html.escape(label, quote=True)}" style="{style}" '
            f'vertex="1" parent="1"><mxGeometry x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" '
            f'height="{h:.0f}" as="geometry"/></mxCell>'
        )
        return cid

    def edge(self, source: str, target: str, label: str = "", style: str = "") -> str:
        """Add an orthogonal arrow from ``source`` to ``target`` and return its id."""
        cid = self._next_id()
        base = (
            "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;"
            f"strokeColor={ACCENT};fontColor={MUTED};fontSize=11;labelBackgroundColor=#ffffff;"
        )
        self.cells.append(
            f'<mxCell id="{cid}" value="{html.escape(label, quote=True)}" '
            f'style="{base}{FONT}{style}" edge="1" parent="1" source="{source}" '
            f'target="{target}"><mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        return cid

    def text(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        label: str,
        size: int = 12,
        colour: str = INK,
        bold: bool = False,
    ) -> str:
        """Add a borderless text block."""
        style = (
            f"text;html=1;whiteSpace=wrap;align=left;verticalAlign=top;fontSize={size};"
            f"fontColor={colour};{FONT}" + ("fontStyle=1;" if bold else "")
        )
        return self.box(x, y, w, h, label, style)

    def xml(self) -> str:
        """Return the page as a ``<diagram>`` element."""
        return (
            f'<diagram id="{self.prefix}" name="{html.escape(self.name)}">'
            f'<mxGraphModel grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
            f'fold="1" page="1" pageScale="1" pageWidth="{self.width}" '
            f'pageHeight="{self.height}" background="#ffffff" math="0" shadow="0"><root>'
            f'<mxCell id="0"/><mxCell id="1" parent="0"/>{"".join(self.cells)}</root>'
            "</mxGraphModel></diagram>"
        )


def block_style(family: Family, dashed: bool = False, size: int = 12, align: str = "left") -> str:
    """Return the style of a rounded box in a family's colours."""
    stroke, fill = COLOURS[family]
    return (
        f"rounded=1;arcSize=8;whiteSpace=wrap;html=1;align={align};verticalAlign=middle;"
        f"spacingLeft=8;spacingRight=6;strokeColor={stroke};"
        f"fillColor={'#ffffff' if dashed else fill};strokeWidth=1.5;fontColor={INK};"
        f"fontSize={size};{FONT}" + ("dashed=1;" if dashed else "")
    )


def port_style(dimmed: bool = False) -> str:
    """Return the style of a port box (monospace, accent outline)."""
    colour = MUTED if dimmed else ACCENT
    return (
        f"rounded=1;whiteSpace=wrap;html=1;strokeColor={colour};fillColor=#ffffff;fontSize=11;"
        f"fontColor={colour};{MONO}" + ("dashed=1;" if dimmed else "")
    )


def label(family: Family, tag: str, name: str, sub: str = "") -> str:
    """Return a box label: coloured tag line, bold name, muted sub line."""
    stroke = COLOURS[family][0]
    head = f'<font color="{stroke}" style="font-size:10px"><b>{tag}</b></font><br>' if tag else ""
    tail = f'<br><font color="{MUTED}" style="font-size:10px">{sub}</font>' if sub else ""
    return f"{head}<b>{name}</b>{tail}"


def header(page: Page, number: int) -> None:
    """Draw the plate title, its description and the colour legend."""
    plate = PLATES[number - 1]
    page.text(40, 20, 1400, 34, f"OpenPlanner · {plate.title}", size=24, colour=ACCENT, bold=True)
    page.text(40, 56, 1520, 40, f"{plate.description} Stand {AS_OF}.", size=13, colour=MUTED)
    for i, (family, name) in enumerate(FAMILY_NAMES.items()):
        page.box(40 + i * 130, 100, 16, 16, "", block_style(family))
        page.text(62 + i * 130, 99, 100, 20, name, size=11, colour=MUTED)
    page.box(690, 100, 16, 16, "", block_style(Family.NONE, dashed=True))
    page.text(712, 99, 140, 20, "Nog niet gebouwd", size=11, colour=MUTED)


def _block(page: Page, x: float, y: float, w: float, h: float, block: Block) -> str:
    name = f"{block.name} (gepland)" if block.planned else block.name
    return page.box(
        x,
        y,
        w,
        h,
        label(block.family, block.id, name, block.summary),
        block_style(block.family, dashed=block.planned),
    )


def plate_overview() -> Page:
    """Plate 1: building blocks inside OpenPlanner, actors left, external systems right."""
    page = Page(1, "1 Hoofdlijn", 1600, 1000)
    header(page, 1)
    system = page.box(
        280,
        140,
        1030,
        750,
        f'<b style="font-size:16px">OpenPlanner</b>  <font color="{MUTED}">één VM · containers '
        "proxy · web · worker · SQLite-volume</font>",
        "rounded=1;arcSize=2;whiteSpace=wrap;html=1;align=left;verticalAlign=top;spacingLeft=14;"
        f"spacingTop=8;strokeColor={ACCENT};strokeWidth=2.5;fillColor=#f6f8fb;fontColor={ACCENT};"
        f"fontSize=14;{FONT}",
    )
    zones = {
        Family.CITIZEN: (300, 190, 300, 440),
        Family.CORE: (620, 190, 400, 440),
        Family.ADMIN: (1040, 190, 250, 440),
        Family.PLATFORM: (300, 650, 990, 170),
    }
    for family, (x, y, w, h) in zones.items():
        page.box(
            x,
            y,
            w,
            h,
            f"<b>{FAMILY_NAMES[family].upper()}</b>",
            "rounded=1;arcSize=3;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
            f"spacingLeft=10;spacingTop=4;strokeColor=#d5dbe4;fillColor=#ffffff;"
            f"fontColor={COLOURS[family][0]};fontSize=11;{FONT}",
        )

    by_family: dict[Family, list[Block]] = {}
    for block in BLOCKS:
        by_family.setdefault(block.family, []).append(block)
    for i, block in enumerate(by_family[Family.CITIZEN]):
        _block(page, 315, 225 + i * 100, 270, 85, block)
    core = by_family[Family.CORE]
    for i, block in enumerate(core[:-1]):
        row, col = divmod(i, 2)
        _block(page, 635 + col * 190, 225 + row * 100, 180, 85, block)
    _block(page, 635, 425, 370, 85, core[-1])
    for i, block in enumerate(by_family[Family.ADMIN]):
        _block(page, 1055, 225 + i * 100, 220, 85, block)
    for i, block in enumerate(by_family[Family.PLATFORM]):
        _block(page, 315 + i * 162, 690, 152, 110, block)
    page.box(
        315 + 5 * 162,
        690,
        152,
        110,
        label(Family.PLATFORM, "", *LOGGING_BOX),
        block_style(Family.PLATFORM),
    )
    page.text(300, 845, 990, 40, f'<font color="{MUTED}">{PLATE1_FOOTNOTE}</font>')

    for i, (name, sub, uses) in enumerate(ACTORS):
        actor = page.box(
            40,
            210 + i * 130,
            190,
            74,
            label(Family.EXTERNAL, "", name, sub),
            block_style(Family.EXTERNAL, align="center") + "arcSize=50;",
        )
        if uses:
            page.edge(actor, system, style="exitX=1;exitY=0.5;entryX=0;")
    for i, (name, sub) in enumerate(EXTERNALS):
        ext = page.box(
            1370,
            160 + i * 110,
            200,
            74,
            label(Family.EXTERNAL, "", name, sub),
            block_style(Family.EXTERNAL),
        )
        page.edge(system, ext, style="exitX=1;entryX=0;entryY=0.5;startArrow=block;startFill=1;")
    return page


def _layer(page: Page, y: float, layer: Layer) -> tuple[str, float]:
    x0, width = 200, 1080
    rows = -(-len(layer.components) // layer.per_row)
    height = 32 + rows * layer.box_height + (rows - 1) * 10
    fill, stroke = ("#e4ecf5", ACCENT) if layer.is_ports else ("#f6f8fb", "#d5dbe4")
    band = page.box(
        x0,
        y,
        width,
        height,
        "",
        f"rounded=1;arcSize=3;html=1;strokeColor={stroke};fillColor={fill};",
    )
    page.text(
        40,
        y + 8,
        150,
        60,
        f'<b>{layer.label.upper()}</b><br><font color="{ACCENT}" style="font-size:11px">'
        f"{layer.package}</font>",
        size=11,
        colour=MUTED,
    )
    box_w = (width - 32 - (layer.per_row - 1) * 10) / layer.per_row
    for i, comp in enumerate(layer.components):
        row, col = divmod(i, layer.per_row)
        x = x0 + 16 + col * (box_w + 10)
        yy = y + 16 + row * (layer.box_height + 10)
        if comp.family is None:
            page.box(x, yy, box_w, layer.box_height, f"<b>{comp.name}</b>", port_style())
        else:
            page.box(
                x,
                yy,
                box_w,
                layer.box_height,
                label(comp.family, comp.tags, comp.name, comp.modules),
                block_style(comp.family, size=11),
            )
    if layer.note:
        page.box(1300, y, 260, 70, layer.note, block_style(Family.NONE, dashed=True, size=11))
    return band, height


def plate_components() -> Page:
    """Plate 2: the packages under ``app/`` as layered bands in import direction."""
    page = Page(2, "2 Softwarecomponenten", 1600, 1360)
    header(page, 2)
    root = page.box(
        490,
        140,
        500,
        46,
        f'<b>app.main</b> · create_app()  <font color="{MUTED}">compositiewortel: koppelt '
        "adapters aan poorten</font>",
        f"rounded=1;whiteSpace=wrap;html=1;strokeColor={INK};fillColor=#ffffff;fontSize=12;"
        f"fontColor={INK};{FONT}",
    )
    y: float = 226
    bands: list[str] = []
    for layer in LAYERS:
        band, height = _layer(page, y, layer)
        previous = bands[-1] if bands else root
        if layer.label == "Domeinservices":
            page.edge(band, previous, layer.arrow_in)
        elif layer.is_ports:
            page.edge(previous, band, layer.arrow_in, "dashed=1;endFill=0;")
        else:
            page.edge(previous, band, layer.arrow_in)
        if layer.label == "Web":
            i18n = page.box(
                1300,
                y,
                260,
                80,
                label(Family.PLATFORM, "B14", "app.i18n (onafhankelijk)", I18N_NOTE),
                block_style(Family.PLATFORM, size=11),
            )
            page.edge(band, i18n, style="dashed=1;exitX=1;exitY=0.5;entryX=0;entryY=0.5;")
        if layer.label == "Adapters":
            ext = page.box(
                1300,
                y,
                260,
                80,
                label(
                    Family.EXTERNAL,
                    "",
                    "Naar buiten",
                    "SMTP · SMS-gateway · Webex Meetings · WxCC · IdP · GitHub (zie plaat 3)",
                ),
                block_style(Family.EXTERNAL, size=11),
            )
            page.edge(
                band,
                ext,
                style="exitX=1;exitY=0.5;entryX=0;entryY=0.5;startArrow=block;startFill=1;",
            )
        bands.append(band)
        y += height + 40
    page.edge(bands[0], bands[4], "roept aan", "exitX=0;exitY=0.85;entryX=0;entryY=0.5;dashed=1;")
    return page


def plate_integrations() -> Page:
    """Plate 3: one lane per external system, from port via adapter to the system."""
    page = Page(3, "3 Integraties en adapters", 1600, 1340)
    header(page, 3)
    for x, title in (
        (40, "POORT (DOMEIN)"),
        (240, "ADAPTER"),
        (520, "VERBINDING"),
        (790, "EXTERN SYSTEEM"),
        (1040, "HEEN (→) EN TERUG (←)"),
    ):
        page.text(x, 140, 260, 20, f"<b>{title}</b>", size=11, colour=MUTED)

    y = 170
    for lane in LANES:
        has_port = bool(lane.ports)
        port = page.box(
            40,
            y,
            170,
            80,
            f"<b>{'<br>'.join(lane.ports) or lane.port_note}</b>",
            port_style(dimmed=not has_port),
        )
        adapter = page.box(
            240,
            y,
            250,
            80,
            label(lane.family, lane.tags, lane.adapter, lane.modules),
            block_style(lane.family, size=11),
        )
        system = page.box(
            790,
            y,
            220,
            80,
            label(Family.EXTERNAL, "", lane.system, lane.system_note),
            block_style(Family.EXTERNAL, size=11),
        )
        if has_port:
            page.edge(adapter, port, style="dashed=1;endFill=0;")
        if lane.incoming:
            page.edge(system, adapter, lane.wire, "exitX=0;exitY=0.5;entryX=1;entryY=0.5;")
            facts = f"← {lane.inbound}<br>→ {lane.outbound}"
        else:
            page.edge(adapter, system, lane.wire, "exitX=1;exitY=0.5;entryX=0;entryY=0.5;")
            facts = f"→ {lane.outbound}<br>← {lane.inbound}"
        page.text(1040, y + 8, 530, 72, facts, size=11, colour=MUTED)
        y += 95

    y += 10
    page.text(40, y, 600, 20, "<b>GEDEELDE INTEGRATIE-ONDERDELEN</b>", size=11, colour=MUTED)
    y += 28
    for i, part in enumerate(SHARED_PARTS):
        row, col = divmod(i, 3)
        page.box(
            40 + col * 515,
            y + row * 92,
            500,
            80,
            f'<b>{part.name}</b>  <font color="{COLOURS[part.family][0]}" '
            f'style="font-size:10px">{part.modules}</font><br><font color="{MUTED}" '
            f'style="font-size:10px">{part.note}</font>',
            block_style(part.family, size=11),
        )
    page.text(40, y + 190, 1500, 20, f'<font color="{MUTED}">{PLATE3_FOOTNOTE}</font>', size=11)
    return page


def build() -> str:
    """Return the complete ``.drawio`` document with the three plates."""
    pages = (plate_overview(), plate_components(), plate_integrations())
    return (
        '<mxfile host="drawio" agent="OpenPlanner scripts/diagrams">'
        + "".join(page.xml() for page in pages)
        + "</mxfile>"
    )
