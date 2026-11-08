"""``docs/reporting.md``, generated from the metric catalogue (ADR 0117 point 6).

``python -m app.cli reporting-doc > docs/reporting.md`` writes it; a test fails when the committed
file differs, so the published definitions are the ones the code uses, as for
``docs/settings.md``.
"""

from app.domain.call_facts import FAILURE_CODES, LEAVE_REASONS
from app.domain.message_usage import PURPOSES
from app.domain.privacy import USAGE_FIELDS
from app.domain.slot_close import OUTCOME_RULE
from app.domain.usage import BUCKET_LABELS, FACT_HOUR, METRIC_VERSION, METRICS
from app.domain.usage_rollup import FILL_CLASSES, feed_rows
from app.domain.usage_suppress import MIN_CELL_SIZE_FLOOR

_INTRO = f"""# Reporting on use

Generated from `app/domain/usage.py` and `app/domain/privacy.py` by
`python -m app.cli reporting-doc > docs/reporting.md`; do not edit by hand. CI fails when this
file differs from the code. The decision is
[ADR 0117](adr/0117-reporting-on-use-from-a-store-that-holds-no-personal-data.md).

The statistics hold no personal data. They are counts and sums per hour and per cut, never one
line per booking, and every read hides a cell below the threshold (never below
{MIN_CELL_SIZE_FLOOR}). Metric definitions are version {METRIC_VERSION}; a change of a definition
or a bucket bound needs a new version.
"""


def render_markdown() -> str:
    """Return ``docs/reporting.md``: metrics, facts and their hour, buckets, the allow-list."""
    parts = [_INTRO, "\n## Metrics\n\n"]
    parts.append("| ID | Metric | Definition | Facts | Adds up as |\n|---|---|---|---|---|\n")
    for m in METRICS:
        facts = ", ".join(f"`{kind.value}`" for kind in m.facts)
        team = " (team view, off by default)" if m.staff else ""
        parts.append(
            f"| {m.id} | {m.name}{team} | {m.definition} | {facts} | {m.aggregation.value} |\n"
        )
    parts.append("\n## Facts and the hour they are keyed on\n\n")
    parts.append("| Fact | Hour from |\n|---|---|\n")
    parts.extend(f"| `{kind.value}` | {source.value} |\n" for kind, source in FACT_HOUR.items())
    parts.append("\n## How a fact becomes a cell\n\n")
    parts.append(
        "One row per metric a fact kind feeds. The count of a cell is always the number of facts; "
        "the figure is what the metric adds up. A word in `reason_code` is the metric's own "
        "state, not the fact's reason.\n\n"
    )
    parts.append("| Fact | Metric | Figure | Cut by | Only when |\n|---|---|---|---|---|\n")
    parts.extend(
        f"| `{r.kind.value}` | {r.metric} | {r.figure} | {r.cuts} | {r.condition} |\n"
        for r in feed_rows()
    )
    parts.append("\n## Closed lists\n\n")
    parts.append(
        "A `reason_code` of a fact is always one of these; a free text never becomes one.\n\n"
    )
    parts.append(
        f"- Failure codes (`call.failed` is `task.`, `meeting.` or `integration.` plus one of): "
        f"{', '.join(f'`{c}`' for c in sorted(FAILURE_CODES))}\n"
    )
    left = ", ".join(f"`{c}`" for c in sorted(LEAVE_REASONS))
    parts.append(f"- Why a participant left a direct call: {left}\n")
    purposes = ", ".join(f"`{c}`" for c in sorted(set(PURPOSES.values())))
    parts.append(f"- Purpose of a message: {purposes}\n")
    parts.append("- Shortfall of a slot: `open`, `accepted`, `resolved`\n")
    parts.append(f"- Fill of a closed slot: {', '.join(f'`{c}`' for c in FILL_CLASSES)}\n")
    parts.append("\n## How an outcome is derived\n\n")
    parts.append(OUTCOME_RULE.strip() + "\n")
    parts.append("\n## Duration buckets\n\n")
    parts.append(
        "The same ten buckets for every duration; a signed duration (a host who was early) "
        "uses the mirrored set.\n\n"
    )
    parts.extend(f"{code}. {label}\n" for code, label in enumerate(BUCKET_LABELS))
    parts.append("\n## What a fact may hold\n\n")
    parts.append(
        "Exactly these fields and no others; `staff_id` only on the separate team-member fact.\n\n"
    )
    parts.extend(f"- `{field}`\n" for field in USAGE_FIELDS)
    return "".join(parts)
