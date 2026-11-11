"""The reference rule that gave existing capacity pools a code (ADR 0121 step 2f).

Migration 0110 writes the rule out because a migration imports no application code; no
application code uses it, because a pool's code is stored and never derived. Tests use it to
give a pool the code its name would have had, and ``tests/test_migration_pool_code.py`` pins the
migration's rule to this one.
"""

import re

CODE_LENGTH = 40
"""The longest pool code: the grammar of a skill code."""

_NOT_CODE = re.compile(r"[^a-z0-9]+")


def derive_pool_code(name: str) -> str:
    """Return the code a pool name gives: lower-case, ``[a-z0-9]`` and single hyphens, trimmed.

    Accents are not transliterated: a character outside ``a-z0-9`` becomes a hyphen. The result
    is cut to ``CODE_LENGTH``, and ``pool`` when nothing is left. It is not made unique.
    """
    cleaned = _NOT_CODE.sub("-", name.lower()).strip("-")[:CODE_LENGTH].strip("-")
    return cleaned or "pool"
