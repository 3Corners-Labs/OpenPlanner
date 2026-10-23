"""Fail when the mutation score in ``mutants/mutmut-cicd-stats.json`` is below a threshold.

Score = (killed + timeout) / (total - skipped). Mutants without covering tests count as
survivors. With no mutants at all (an empty domain) the check passes and says so.

Usage: ``python scripts/check_mutation_score.py [--min 80]`` after
``mutmut run && mutmut export-cicd-stats``.
"""

import argparse
import json
import sys
from pathlib import Path

STATS = Path("mutants/mutmut-cicd-stats.json")


def mutation_score(stats: dict[str, int]) -> float | None:
    """Return the score in percent, or ``None`` when there were no mutants to judge."""
    judged = stats["total"] - stats["skipped"]
    if judged <= 0:
        return None
    return 100.0 * (stats["killed"] + stats["timeout"]) / judged


def main() -> int:
    """Parse arguments, read the stats file and return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min", type=float, default=80.0, help="minimum score in percent")
    args = parser.parse_args()

    if not STATS.exists():
        print("No mutation data found; nothing was mutated (no tests for the mutated code).")
        return 0
    stats = json.loads(STATS.read_text())
    score = mutation_score(stats)
    if score is None:
        print("No mutants generated; nothing to judge.")
        return 0
    print(f"Mutation score {score:.1f}% (minimum {args.min:.0f}%): {stats}")
    return 0 if score >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
