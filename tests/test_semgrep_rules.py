"""The repo's semgrep rules name the same personal-data fields as the code (ADR 0063 §1)."""

import re
from pathlib import Path

from app.logging import PII_FIELDS

RULES = Path(__file__).resolve().parents[1] / ".semgrep" / "rules.yml"


def test_the_no_pii_in_log_call_rule_lists_every_pii_field() -> None:
    """The rule's regex is a copy of the list; this keeps it one home (``app.domain.privacy``)."""
    rule = RULES.read_text().split("id: no-pii-in-log-call", 1)[1]
    found = re.search(r"\$KEY\s*\n\s*regex: \^\(([a-z_|]+)\)\$", rule)

    assert found is not None, "the $KEY regex of no-pii-in-log-call moved"
    assert set(found.group(1).split("|")) == set(PII_FIELDS)
