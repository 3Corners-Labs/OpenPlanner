"""A stored log destination for the log destination and log shipping tests (ADR 0085)."""

from app.domain.log_destinations import DestinationKind, LogDestination
from app.domain.log_events import LogCategory
from tests.support.domain import NOW


def destination(**changes: object) -> LogDestination:
    """Return destination 1: Splunk HEC for audit and security events from warning, changed."""
    values: dict[str, object] = {
        "id": 1,
        "name": "Splunk",
        "kind": DestinationKind.SPLUNK_HEC,
        "address": "https://splunk.example.org:8088/services/collector/event",
        "index": "openplanner",
        "source": "",
        "min_level": "warning",
        "categories": frozenset({LogCategory.AUDIT, LogCategory.SECURITY}),
        "include_diagnostic": False,
        "include_trace": False,
        "enabled": True,
        "secret_set": True,
        "ca_pem": "",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return LogDestination(**values)  # type: ignore[arg-type]
