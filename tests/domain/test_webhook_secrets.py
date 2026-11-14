"""The overlap rule of a rotated webhook secret (ADR 0124 point 8): pure, decided by the clock."""

from datetime import timedelta

import pytest

from app.domain.inbound_gate import Refusal
from app.domain.webhook_overlap import Match, verify_with_overlap
from app.domain.webhook_secrets import (
    OVERLAP_MAX,
    SecretFinding,
    Sighting,
    WebhookSecrets,
    fingerprint,
    installation_secrets,
    lapsed,
    previous_finding,
    previous_until,
    rotated,
    settled,
    sighted,
)
from tests.support.domain import NOW

OVERLAP = timedelta(minutes=60)
KEY = b"k" * 32
OTHER_KEY = b"o" * 32


def _only(*good: str, stale: tuple[str, ...] = ()):
    """A check that verifies the secrets in ``good``, says stale for ``stale``, else invalid."""

    def check(secret: str) -> Refusal | None:
        if secret in good:
            return None
        return Refusal.STALE if secret in stale else Refusal.SIGNATURE

    return check


# --- rotation ---------------------------------------------------------------------------------


def test_a_rotation_makes_the_current_secret_the_previous_one() -> None:
    first = rotated(None, "one", NOW)
    second = rotated(first, "two", NOW + timedelta(minutes=5))

    assert first == WebhookSecrets("one", None, NOW)
    assert second == WebhookSecrets("two", "one", NOW + timedelta(minutes=5))


def test_a_second_rotation_inside_the_overlap_drops_the_secret_from_before() -> None:
    first = rotated(None, "one", NOW)
    second = rotated(first, "two", NOW + timedelta(minutes=10))
    third = rotated(second, "three", NOW + timedelta(minutes=20))

    assert third.previous == "two"
    now = NOW + timedelta(minutes=21)
    assert verify_with_overlap(third, now, OVERLAP, _only("one")).refusal is Refusal.SIGNATURE
    assert verify_with_overlap(third, now, OVERLAP, _only("two")).by is Match.PREVIOUS


# --- the clock decides ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("minutes", "counts"), [(0, True), (59, True), (60, False), (61, False), (-1, True)]
)
def test_the_previous_secret_counts_until_the_overlap_has_passed(
    minutes: int, counts: bool
) -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    assert secrets.previous_counts(NOW + timedelta(minutes=minutes), OVERLAP) is counts


def test_an_overlap_of_nothing_means_at_once() -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    assert secrets.previous_counts(NOW, timedelta(0)) is False
    verdict = verify_with_overlap(secrets, NOW, timedelta(0), _only("old"))
    assert verdict.refusal is Refusal.SIGNATURE


def test_a_shorter_overlap_in_the_portal_applies_to_the_next_check() -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)
    later = NOW + timedelta(minutes=30)

    assert verify_with_overlap(secrets, later, OVERLAP, _only("old")).by is Match.PREVIOUS
    assert verify_with_overlap(secrets, later, timedelta(minutes=10), _only("old")).refusal


def test_a_previous_secret_without_a_known_moment_never_counts() -> None:
    secrets = WebhookSecrets("new", "old", None)

    assert secrets.previous_ends(OVERLAP) is None
    assert secrets.previous_counts(NOW, OVERLAP) is False


def test_nothing_ends_without_a_previous_secret() -> None:
    assert WebhookSecrets("new", None, NOW).previous_ends(OVERLAP) is None


# --- the verdict ------------------------------------------------------------------------------


def test_the_current_secret_verifies() -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    verdict = verify_with_overlap(secrets, NOW, OVERLAP, _only("new"))

    assert (verdict.refusal, verdict.by) == (None, Match.CURRENT)


def test_the_previous_secret_verifies_inside_its_overlap_and_is_told_apart_only_by_match() -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    verdict = verify_with_overlap(secrets, NOW + timedelta(minutes=1), OVERLAP, _only("old"))

    assert (verdict.refusal, verdict.by) == (None, Match.PREVIOUS)


def test_the_previous_secret_is_refused_after_its_overlap() -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    verdict = verify_with_overlap(secrets, NOW + OVERLAP, OVERLAP, _only("old"))

    assert (verdict.refusal, verdict.by) == (Refusal.SIGNATURE, None)


def test_a_stranger_is_an_invalid_signature() -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    assert verify_with_overlap(secrets, NOW, OVERLAP, _only()).refusal is Refusal.SIGNATURE


def test_stale_is_told_for_a_secret_that_counts() -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    on_current = verify_with_overlap(secrets, NOW, OVERLAP, _only(stale=("new",)))
    on_previous = verify_with_overlap(secrets, NOW, OVERLAP, _only(stale=("old",)))

    assert on_current.refusal is Refusal.STALE
    assert on_previous.refusal is Refusal.STALE


def test_a_stale_delivery_of_an_ended_secret_is_not_told_as_stale() -> None:
    # It would show that the secret was once ours and that the signature was authentic.
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    verdict = verify_with_overlap(secrets, NOW + OVERLAP, OVERLAP, _only(stale=("old",)))

    assert verdict.refusal is Refusal.SIGNATURE


def test_both_secrets_are_always_compared_whatever_the_first_says() -> None:
    seen: list[str] = []

    def check(secret: str) -> Refusal | None:
        seen.append(secret)
        return None

    secrets = rotated(rotated(None, "old", NOW), "new", NOW)
    verify_with_overlap(secrets, NOW, OVERLAP, check)
    verify_with_overlap(secrets, NOW + OVERLAP, OVERLAP, check)
    verify_with_overlap(WebhookSecrets("only"), NOW, OVERLAP, check)

    # Two comparisons every time: the current secret matching, an ended previous secret, and no
    # previous secret at all (compared against a stand-in) cost the same.
    assert seen == ["new", "old", "new", "old", "only", "only"]


def test_no_secret_shows_in_a_repr() -> None:
    assert "new" not in repr(WebhookSecrets("new-secret-value", "old-secret-value", NOW))


# --- removal is housekeeping, not the rule ----------------------------------------------------


def test_the_longest_overlap_is_a_day() -> None:
    assert OVERLAP_MAX == timedelta(hours=24)


def test_a_write_removes_a_previous_secret_only_when_no_setting_could_keep_it() -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    assert lapsed(secrets, NOW + OVERLAP_MAX - timedelta(seconds=1)) is False
    assert lapsed(secrets, NOW + OVERLAP_MAX) is True
    assert lapsed(WebhookSecrets("new", None, NOW), NOW + OVERLAP_MAX) is False
    assert lapsed(WebhookSecrets("new", "old", None), NOW) is True


def test_the_sweep_removes_a_previous_secret_once_the_live_overlap_has_ended() -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    assert settled(secrets, NOW + timedelta(minutes=30), OVERLAP) is secrets
    assert settled(secrets, NOW + OVERLAP, OVERLAP) == WebhookSecrets("new", None, NOW)
    assert settled(WebhookSecrets("new", None, NOW), NOW + OVERLAP, OVERLAP).previous is None


# --- the installation's own secrets -----------------------------------------------------------


def test_a_fingerprint_is_a_keyed_digest_that_is_not_the_secret() -> None:
    made = fingerprint(KEY, "hunter2")

    assert made == fingerprint(KEY, "hunter2")
    assert made != fingerprint(OTHER_KEY, "hunter2")
    assert made != fingerprint(KEY, "hunter3")
    assert "hunter2" not in made
    assert len(made) == 64


def test_a_secret_never_seen_is_first_seen_now() -> None:
    assert sighted(None, "secret", [KEY], NOW) == Sighting(fingerprint(KEY, "secret"), NOW)


def test_the_same_secret_keeps_its_first_sighting() -> None:
    stored = Sighting(fingerprint(KEY, "secret"), NOW)

    assert sighted(stored, "secret", [KEY], NOW + timedelta(days=3)) == stored


def test_a_new_secret_starts_a_new_sighting() -> None:
    stored = Sighting(fingerprint(KEY, "secret"), NOW)
    later = NOW + timedelta(days=3)

    assert sighted(stored, "another", [KEY], later) == Sighting(fingerprint(KEY, "another"), later)


def test_a_rotation_of_the_key_keeps_the_time_and_remakes_the_fingerprint() -> None:
    stored = Sighting(fingerprint(OTHER_KEY, "secret"), NOW)

    kept = sighted(stored, "secret", [KEY, OTHER_KEY], NOW + timedelta(days=3))

    assert kept == Sighting(fingerprint(KEY, "secret"), NOW)


def test_an_installation_previous_secret_counts_from_the_first_sighting() -> None:
    secrets = installation_secrets("new", "old", NOW)

    assert secrets.previous_counts(NOW + timedelta(minutes=59), OVERLAP) is True
    assert secrets.previous_counts(NOW + OVERLAP, OVERLAP) is False


def test_a_previous_secret_equal_to_the_current_one_is_dropped() -> None:
    assert installation_secrets("same", "same", NOW).previous is None
    assert installation_secrets("new", None, NOW).previous is None


def test_the_findings_on_a_previous_value_in_the_environment() -> None:
    inside, after = NOW + timedelta(minutes=10), NOW + OVERLAP

    assert previous_finding("new", None, NOW, after, OVERLAP) is None
    assert previous_finding("new", "old", NOW, inside, OVERLAP) is None
    assert previous_finding("new", "old", NOW, after, OVERLAP) is SecretFinding.ENDED
    assert previous_finding("same", "same", NOW, inside, OVERLAP) is SecretFinding.SAME


# --- what the card says -----------------------------------------------------------------------


def test_the_card_names_the_moment_the_previous_secret_stops() -> None:
    inside = NOW + timedelta(minutes=30)

    assert previous_until(True, NOW, inside, OVERLAP) == NOW + OVERLAP
    assert previous_until(True, NOW, NOW + OVERLAP, OVERLAP) is None
    assert previous_until(False, NOW, inside, OVERLAP) is None
    assert previous_until(True, None, inside, OVERLAP) is None
    assert previous_until(True, NOW, NOW, timedelta(0)) is None


def test_the_card_and_the_gate_agree_on_when_the_previous_secret_counts() -> None:
    secrets = rotated(rotated(None, "old", NOW), "new", NOW)

    for minutes in (0, 30, 59, 60, 90):
        at = NOW + timedelta(minutes=minutes)
        assert (previous_until(True, NOW, at, OVERLAP) is not None) is secrets.previous_counts(
            at, OVERLAP
        )
