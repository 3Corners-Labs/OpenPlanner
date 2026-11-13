"""The contact-center connection form's inputs and the refusals linked to them (ADR 0032 point 9).

A refusal names a domain field (``contact_center_connection.<name>``); the form's input is
``cc_<name>``. ``forms`` reads these two maps, so a refusal is linked to its input through
``aria-describedby`` with a message that says what to change.
"""

from collections.abc import Mapping

from app.domain.contact_center import CONNECTION_FIELD

_INPUTS = ("api_base_url", "client_id", "client_secret", "refresh_token", "allowed_networks")

CONNECTION_TARGETS: Mapping[str, str] = {f"{CONNECTION_FIELD}{n}": f"cc_{n}" for n in _INPUTS}
"""Refused domain field → the input it is shown at."""
CONNECTION_MESSAGES: Mapping[str, str] = {
    f"{CONNECTION_FIELD}api_base_url": "admin.form.error.cc_api_base_url",
    f"{CONNECTION_FIELD}client_id": "admin.form.error.cc_client_id",
    f"{CONNECTION_FIELD}client_secret": "admin.form.error.cc_secret",
    f"{CONNECTION_FIELD}refresh_token": "admin.form.error.cc_secret",
    f"{CONNECTION_FIELD}allowed_networks": "admin.form.error.cc_allowed_networks",
}
"""Refused domain field → the message shown at its input."""
