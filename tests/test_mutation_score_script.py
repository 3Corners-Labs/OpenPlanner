import pytest

from scripts.check_mutation_score import mutation_score


def _stats(**overrides: int) -> dict[str, int]:
    return {"killed": 0, "survived": 0, "total": 0, "skipped": 0, "timeout": 0, **overrides}


def test_counts_killed_and_timeouts_against_judged_mutants() -> None:
    assert mutation_score(_stats(killed=7, timeout=1, survived=2, total=10)) == 80.0


def test_skipped_mutants_are_not_judged() -> None:
    assert mutation_score(_stats(killed=4, skipped=5, total=9)) == 100.0


@pytest.mark.parametrize("stats", [_stats(), _stats(total=3, skipped=3)])
def test_no_judged_mutants_returns_none(stats: dict[str, int]) -> None:
    assert mutation_score(stats) is None
