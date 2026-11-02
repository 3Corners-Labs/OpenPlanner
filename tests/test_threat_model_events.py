"""Every abuse path of ``docs/threat-model.md`` is tied to the security event it leaves.

ASVS V16.3.3 (security events are logged) and V16.3.4 (unexpected errors and failed checks are
logged). The threat model is grouped in flows; each flow with abuse cases must say which event
class an attack on it produces, or why it produces none. The test fails when

- a flow with abuse cases is not in ``FLOWS`` (a new flow without its event, or a renamed one),
- a flow in ``FLOWS`` no longer exists,
- an event class names an action the application never writes, or a proof test that is gone,
- a flow cites an event of a class that it does not list, or says "security event" yet lists no
  security class,
- "no event" is chosen without a reason.

The classes are the ones of ``docs/audit-events.md``; each lists the test that shows the event
being written with a code and without personal data.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.domain.authorization import Permission

ROOT = Path(__file__).resolve().parents[1]
THREAT_MODEL = ROOT / "docs" / "threat-model.md"


@dataclass(frozen=True)
class EventClass:
    """A kind of event, the actions it uses and the tests that prove it is written.

    Attributes:
        category: ``security``, ``audit`` or ``system`` (``event.category`` of the schema).
        actions: Event keys written by the application; ``<permission>`` stands for the
            value of any ``Permission``.
        proofs: ``path::test_name`` of tests that show the event, with its code, in the log.
    """

    category: str
    actions: tuple[str, ...]
    proofs: tuple[str, ...]


CLASSES: dict[str, EventClass] = {
    "public_refusal": EventClass(
        "security",
        ("request.rate_limited", "request.rejected", "token.rejected"),
        (
            "tests/api/test_security_events.py::test_a_filled_in_honeypot_is_a_security_event_without_the_address",
            "tests/api/test_security_events.py::test_a_cross_site_post_is_a_security_event",
            "tests/api/test_security_events.py::test_an_unknown_management_link_is_a_security_event",
            "tests/api/test_security_events.py::test_a_rate_limit_flood_is_one_event_per_caller",
            "tests/api/test_security_events.py::test_an_unknown_bearer_token_on_the_machine_api_is_a_security_event",
            "tests/api/test_fault_injection_booking.py::test_a_failing_security_log_does_not_turn_a_refusal_into_a_server_error",
        ),
    ),
    "webhook": EventClass(
        "security",
        ("webhook.rejected",),
        (
            "tests/api/test_webhooks.py::test_forged_or_stale_webhooks_are_refused",
            "tests/api/test_webhooks.py::test_a_flood_of_forged_webhooks_is_one_security_event_per_source",
        ),
    ),
    "provider_token": EventClass(
        "security",
        ("webex.token_refresh",),
        (
            "tests/adapters/test_webex_auth.py::test_a_failing_refresh_is_a_security_event",
            "tests/adapters/test_fault_injection_outbound.py::test_a_provider_timeout_on_the_token_refresh_is_a_security_event_with_a_code",
        ),
    ),
    "login": EventClass(
        "security",
        ("admin.login", "admin.login.mfa_missing", "auth.entitlement_refused"),
        (
            "tests/admin/test_admin_flow.py::test_identity_errors_are_audited",
            "tests/admin/test_admin_flow.py::test_a_login_without_a_second_factor_is_refused_with_a_clear_text",
            "tests/admin/test_fault_injection_login.py::test_an_identity_provider_that_fails_grants_no_session_and_leaves_an_event",
        ),
    ),
    "denied": EventClass(
        "audit",
        ("<permission>",),
        ("tests/domain/test_admin_service.py::test_denial_is_audited",),
    ),
    "change": EventClass(
        "audit",
        ("<permission>",),
        (
            "tests/domain/test_settings_service.py::test_a_change_is_stored_bumps_the_version_and_is_audited",
        ),
    ),
    "job": EventClass(
        "system",
        ("integration.request",),
        (
            "tests/jobs/test_fault_injection_jobs.py::test_a_timeout_is_retried_with_a_code_and_the_booking_is_untouched",
            "tests/jobs/test_fault_injection_jobs.py::test_the_last_timeout_fails_the_job_visibly_and_still_creates_no_meeting",
            "tests/jobs/test_call_jobs.py::test_webexs_reason_goes_with_the_failure_to_the_timeline_and_the_system_event",
        ),
    ),
}
"""The event classes (``docs/audit-events.md``)."""

PUBLIC, WEBHOOK, TOKEN, LOGIN = "public_refusal", "webhook", "provider_token", "login"
DENIED, CHANGE, JOB = "denied", "change", "job"
ADMIN = (DENIED, CHANGE)


@dataclass(frozen=True)
class Flow:
    """What an attack on one flow of the threat model leaves behind.

    Attributes:
        classes: Event classes, from ``CLASSES``; empty when the flow has no event.
        no_event: Why it has none; required exactly when ``classes`` is empty.
    """

    classes: tuple[str, ...] = ()
    no_event: str = ""


PLATFORM_SETTINGS = (
    "## Flow: platform settings, System status and Advanced settings (ADR 0042 steps 3 and 4)"
)
FLOWS: dict[str, Flow] = {
    "## Flow: health checks": Flow(
        no_event="public read-only probes without a refusal path; a flood is limited at the "
        "reverse proxy (open decision O7)"
    ),
    "## Flow: TLS termination (ADR 0030)": Flow((CHANGE, JOB)),
    "## Flow: public pages and language choice": Flow((PUBLIC,)),
    "### `GET` routes with an effect (V3.5.3)": Flow((PUBLIC,)),
    "### Redirects (V3.7.2)": Flow(
        no_event="an unsafe target is replaced by the safe default; nothing is refused, so "
        "there is no caller to attribute"
    ),
    "### Header policy per response type (V3.2.1, V3.4.3)": Flow(
        no_event="static response headers; no request is refused"
    ),
    "## Shared trust boundaries (TEN-06, BP-4)": Flow(ADMIN),
    "## Cross-cutting": Flow((PUBLIC, WEBHOOK, TOKEN, CHANGE)),
    "## Flow: booking, cancelling, rescheduling (domain and persistence, WP2)": Flow((PUBLIC,)),
    "## Flow: public booking pages (WP3)": Flow((PUBLIC,)),
    "## Flow: organisation branding (WP3b, ADR 0041)": Flow(ADMIN),
    "## Flow: organisation management": Flow(ADMIN),
    "## Flow: groups list (ADR 0047 step 5)": Flow(ADMIN),
    "## Flow: outgoing email (WP3, worker)": Flow((JOB,)),
    "## Flow: message channels, SMS reminders and message texts (ADR 0022, 0028)": Flow(
        (DENIED, CHANGE, JOB)
    ),
    "## Flow: admin portal login and authorisation (WP7a)": Flow((LOGIN, DENIED, CHANGE)),
    "### Entitlements: role and scope in one claim value (ADR 0049)": Flow((LOGIN, CHANGE)),
    "### Identity providers per organisation (ADR 0083)": Flow((LOGIN, CHANGE)),
    "## Flow: groups and planning in the admin portal (WP7b)": Flow(ADMIN),
    "## Flow: manual and context-sensitive help (ADR 0033)": Flow(
        no_event="static manual text for signed-in admins; nothing is changed or refused"
    ),
    "## Flow: holiday calendars (ADR 0025)": Flow(ADMIN),
    "## Flow: emergency closure (ADR 0024)": Flow(ADMIN),
    "## Flow: alternative contact options (ADR 0037 §2)": Flow(ADMIN),
    "## Flow: custom booking form fields (ADR 0055)": Flow((PUBLIC, DENIED, CHANGE)),
    "## Flow: embedded booking (ADR 0035, EM-2)": Flow((PUBLIC,)),
    "## Flow: widget script on host websites (ADR 0035, EM-3)": Flow(
        no_event="a static script file; the booking it posts is refused by the booking flow"
    ),
    "## Flow: allowed origins for embedding (ADR 0035, EM-1)": Flow(ADMIN),
    "## Flow: video meetings, work items and direct calls (WP5/WP6)": Flow(
        (PUBLIC, WEBHOOK, JOB, DENIED, CHANGE)
    ),
    "## Flow: own queue (ADR 0043)": Flow(ADMIN),
    "## Flow: verify button and simulator (admin)": Flow(ADMIN),
    "## Flow: troubleshooting per booking (ADR 0068)": Flow(ADMIN),
    "## Flow: machine API for contact-center bots (ADR 0023)": Flow((PUBLIC, CHANGE)),
    "## Flow: updates from the admin portal (ADR 0029)": Flow((CHANGE,)),
    "## Flow: maintenance mode (ADR 0038)": Flow((CHANGE,)),
    "## Flow: build supply chain and dependency updates (ADR 0051)": Flow(
        no_event="CI and Renovate, outside the running application; a failure fails the build"
    ),
    PLATFORM_SETTINGS: Flow(ADMIN),
    "## Flow: configuration package (ADR 0089)": Flow(ADMIN),
    "## Flow: personal data in the admin portal (ADR 0063)": Flow(ADMIN),
    "## Flow: organisation settings (ADR 0042 step 5)": Flow(ADMIN),
    "## Flow: organisation texts (ADR 0074)": Flow(ADMIN),
    "## Flow: secrets of class C and taking values over (ADR 0042 step 6, §12)": Flow(ADMIN),
    "## Flow: login mapping with a dry run (ADR 0042 step 7, §8)": Flow(ADMIN),
}
"""Per flow heading of the threat model: the event classes an attack on it leaves."""

_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")
_CITED = re.compile(r"`([a-z_]+(?:\.[a-z_]+)+)`")


def flows_with_abuse_cases(text: str) -> dict[str, str]:
    """Return the text of every heading that has at least one table row besides the header."""
    sections: dict[str, list[str]] = {}
    heading = ""
    previous = ""
    for line in text.splitlines():
        if line.startswith("#"):
            heading, previous = line.strip(), ""
            sections[heading] = []
        elif heading and line.startswith("|"):
            if _SEPARATOR.match(line.strip()):
                if sections[heading] and sections[heading][-1] == previous:
                    sections[heading].pop()  # the row before the separator was the header
            else:
                sections[heading].append(line)
                previous = line
        elif heading and line.strip():
            previous = ""
    return {h: "\n".join(rows) for h, rows in sections.items() if rows}


def problems(found: dict[str, str], flows: dict[str, Flow]) -> list[str]:
    """Return what is wrong between the threat model's flows and ``flows``."""
    wrong = [f"{h}: has abuse cases but no entry in FLOWS" for h in found if h not in flows]
    wrong += [
        f"{h}: in FLOWS but not (or no longer) in the threat model" for h in flows if h not in found
    ]
    for heading, flow in flows.items():
        text = found.get(heading, "")
        if bool(flow.classes) == bool(flow.no_event):
            wrong.append(f"{heading}: give either event classes or a reason for none")
        if "security event" in text and not any(
            CLASSES[c].category == "security" for c in flow.classes
        ):
            wrong.append(
                f"{heading}: the text names a security event, the entry lists no security class"
            )
        for name, kind in CLASSES.items():
            cited = {a for a in kind.actions if a != "<permission>" and f"`{a}`" in text}
            if cited and name not in flow.classes:
                wrong.append(f"{heading}: cites {sorted(cited)} but lacks the class {name}")
    return wrong


def test_every_abuse_path_of_the_threat_model_names_its_event() -> None:
    found = flows_with_abuse_cases(THREAT_MODEL.read_text(encoding="utf-8"))

    assert found, "no abuse cases parsed from the threat model"
    assert problems(found, FLOWS) == []


def test_a_flow_without_an_event_is_found() -> None:
    found = flows_with_abuse_cases(
        "## Flow: new\n\n| STRIDE | Threat | Mitigation | Test |\n|---|---|---|---|\n"
        "| Spoofing | x | y | z |\n"
    )

    assert problems(found, {}) == ["## Flow: new: has abuse cases but no entry in FLOWS"]
    assert problems(found, {"## Flow: new": Flow()}) == [
        "## Flow: new: give either event classes or a reason for none"
    ]
    assert problems({}, {"## Flow: new": Flow((PUBLIC,))}) == [
        "## Flow: new: in FLOWS but not (or no longer) in the threat model"
    ]


def test_a_cited_event_of_another_class_is_found() -> None:
    found = flows_with_abuse_cases(
        "## Flow: new\n\n| STRIDE | Threat | Mitigation | Test |\n|---|---|---|---|\n"
        "| Spoofing | x | one `webhook.rejected` security event | z |\n"
    )

    assert problems(found, {"## Flow: new": Flow((CHANGE,))}) == [
        "## Flow: new: the text names a security event, the entry lists no security class",
        "## Flow: new: cites ['webhook.rejected'] but lacks the class webhook",
    ]


def _app_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app").rglob("*.py"))


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_the_actions_of_an_event_class_are_written_by_the_application(name: str) -> None:
    permissions = {p.value for p in Permission}
    source = _app_text()

    for action in CLASSES[name].actions:
        assert action == "<permission>" and permissions or f'"{action}"' in source, action


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_the_proofs_of_an_event_class_exist(name: str) -> None:
    for proof in CLASSES[name].proofs:
        path, _, test = proof.partition("::")
        source = (ROOT / path).read_text(encoding="utf-8")
        assert re.search(rf"^(async )?def {test}\b", source, re.MULTILINE), proof


def test_every_event_class_has_a_proof() -> None:
    assert all(
        kind.proofs and kind.category in {"security", "audit", "system"}
        for kind in CLASSES.values()
    )
