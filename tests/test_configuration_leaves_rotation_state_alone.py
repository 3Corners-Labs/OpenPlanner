"""The configuration document and package never carry a rotated webhook secret (ADR 0124 point 8).

An organisation's previous webhook secret lives in its sealed contact-center record, and the
first sightings of the installation's own secrets in ``webhook_secret_sightings``. Neither is
configuration: they belong to this host and this moment. The readers and writers of the
configuration never name the record or the field, and the package empties the sightings table.
"""

from pathlib import Path

from app.domain.configuration_package import STATE_TABLES

ROOT = Path(__file__).resolve().parents[1] / "app"
CONFIGURATION = [
    *sorted((ROOT / "adapters" / "db").glob("config_*.py")),
    *sorted((ROOT / "adapters" / "db").glob("configuration*.py")),
    *sorted((ROOT / "domain").glob("configuration*.py")),
]
FORBIDDEN = (
    "previous_webhook_secret",
    "webhook_secret_sighting",
    "contact_center_credential_name",
    "contact-center:",
)


def test_no_reader_or_writer_of_the_configuration_names_the_rotation_state() -> None:
    assert CONFIGURATION
    named = []
    for path in CONFIGURATION:
        for word in FORBIDDEN:
            # The one place that may name the table is the list of tables the package empties.
            listed = path.name == "configuration_package.py" and word == "webhook_secret_sighting"
            if word in path.read_text() and not listed:
                named.append(f"{path.relative_to(ROOT)}: {word}")

    assert named == []


def test_the_package_empties_the_table_of_first_sightings() -> None:
    assert "webhook_secret_sightings" in STATE_TABLES
