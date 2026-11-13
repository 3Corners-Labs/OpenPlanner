"""How an inbound event is remembered (ADR 0124 point 7): the key's derivation.

The service's behaviour with it (per organisation, by body, across the upgrade) is tested with
the service in ``test_integration.py``.
"""

from app.domain.webhook_replay import (
    INSTALLATION_SCOPE,
    body_identity,
    replay_key,
    scope_of,
)


def test_the_key_is_one_digest_of_platform_scope_and_identity() -> None:
    first = replay_key("wxcc", scope_of(1), "evt")
    others = {
        replay_key("webex", scope_of(1), "evt"),
        replay_key("wxcc", scope_of(2), "evt"),
        replay_key("wxcc", INSTALLATION_SCOPE, "evt"),
        replay_key("wxcc", scope_of(1), "evt2"),
    }

    assert len(first) == 64
    assert first == replay_key("wxcc", scope_of(1), "evt")
    assert first not in others
    assert len(others) == 4
    assert "evt" not in first


def test_a_body_identity_is_a_digest_that_cannot_meet_an_event_identifier() -> None:
    body = b'{"a":1}'

    assert body_identity(body) == body_identity(body)
    assert body_identity(body) != body_identity(b'{"a":2}')
    assert body_identity(body).startswith("body:")
    assert '{"a"' not in body_identity(body)


def test_every_address_has_its_own_scope() -> None:
    assert len({scope_of(None), scope_of(1), scope_of(2)}) == 3
    assert scope_of(None) == INSTALLATION_SCOPE
