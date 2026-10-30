"""Every production problem has a text (ADR 0042 section 11).

The settings page lists what production would refuse; a code without a text broke the page with
a KeyError the day a setting could first trigger it (the update channel, ADR 0067).
"""

import re
from pathlib import Path

from app.web.settings_labels import PROBLEM_LABELS

CONFIG = Path(__file__).parents[2] / "app" / "config.py"
CODE = re.compile(r'^\s+"([a-z][a-z0-9_]*)",\n\s+"([A-Z][A-Z0-9_]*)",\n\s+"', re.MULTILINE)


def test_every_code_of_production_problems_has_a_label() -> None:
    codes = {code for code, _ in CODE.findall(CONFIG.read_text())}

    # The scan finds the checks written out in Settings.production_problems; the placeholder
    # check builds its variable name in a loop and is covered by the labels alone.
    assert len(codes) >= 8
    assert codes - set(PROBLEM_LABELS) == set(), "a production problem has no text"
