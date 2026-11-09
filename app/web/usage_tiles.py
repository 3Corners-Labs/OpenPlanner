"""The figure tiles at the top of the *Volume* and *Capacity* screens (ADR 0117 step 6b).

A tile is one headline figure with a short sentence under it, for a reader who wants the answer
before the tables. The numbers come from the domain reports, whose small cells are already
hidden: a figure made from a hidden part is ``None`` here and the tile says so, it never shows a
number the tables do not. Nothing here adds up, divides or rounds a value that can be hidden.
"""

from dataclasses import dataclass

from app.domain.usage_report import VolumeReport
from app.domain.usage_report_capacity import CapacityReport
from app.i18n import BoundGettext
from app.web.usage_labels import label


@dataclass(frozen=True)
class Tile:
    """One tile.

    Attributes:
        label: What the figure is, in words.
        value: The figure, or ``None`` when it is hidden or cannot be made.
        suffix: ``%`` for a percentage, else empty.
        detail: The metric id and the sentence under the figure.
        count: Whether the figure is a count (a hidden one reads ``< n``) or a derived figure
            (a hidden one reads *not available*).
    """

    label: str
    value: int | None
    suffix: str
    detail: str
    count: bool = False


def volume_tiles(report: VolumeReport, _: BoundGettext) -> list[Tile]:
    """Return the four tiles of the *Volume* screen: U1, U3, U4 and U9."""
    t = report.tiles
    no_show = "U3" + (
        " · " + _("reports.tile.no_show.of", n=t.no_show_of) if t.no_show_of is not None else ""
    )
    cancel = "U4" + (
        " · " + _("reports.tile.cancel.late", percent=t.cancel_late_percent)
        if t.cancel_late_percent is not None
        else ""
    )
    confirmed = "U9" + (
        " · "
        + _(
            "reports.tile.confirmed.median", range=label(_, "buckets", str(t.confirm_median_bucket))
        )
        if t.confirm_median_bucket is not None
        else ""
    )
    return [
        Tile(
            _("reports.tile.created"),
            report.created_total,
            "",
            "U1 · " + _("reports.tile.created.detail"),
            True,
        ),
        Tile(_("reports.tile.no_show"), t.no_show_percent, "%", no_show),
        Tile(_("reports.tile.cancel"), t.cancel_percent, "%", cancel),
        Tile(_("reports.tile.confirmed"), t.confirmed_percent, "%", confirmed),
    ]


def capacity_tiles(report: CapacityReport, _: BoundGettext) -> list[Tile]:
    """Return the four tiles of the *Capacity* screen: C1, C3, C2 and C5."""
    fill = {r.code: r.count for r in report.fill.rows}
    full, blocks = fill.get("full"), report.fill.total
    full_percent = round(100 * full / blocks) if full is not None and blocks else None
    short = {r.code: r.count for r in report.shortfall.rows}
    used = (
        "C1 · " + _("reports.tile.utilisation.of", booked=report.booked, capacity=report.capacity)
        if report.booked is not None and report.capacity is not None
        else "C1"
    )
    blocks_detail = (
        "C2 · " + _("reports.tile.full.of", n=full, m=blocks) if full_percent is not None else "C2"
    )
    accepted = short.get("accepted")
    shortfall_detail = (
        "C5 · "
        + _("reports.tile.shortfall.detail")
        + (" · " + _("reports.tile.shortfall.accepted", n=accepted) if accepted is not None else "")
    )
    return [
        Tile(_("reports.tile.utilisation"), report.utilisation_percent, "%", used),
        Tile(
            _("reports.tile.idle"), report.idle, "", "C3 · " + _("reports.tile.idle.detail"), True
        ),
        Tile(_("reports.tile.full"), full_percent, "%", blocks_detail),
        Tile(_("reports.tile.shortfall"), report.shortfall.total, "", shortfall_detail, True),
    ]
