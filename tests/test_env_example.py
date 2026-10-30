"""``.env.example`` and ``proxy.env.example`` list every environment-only variable (ADR 0042 §3).

Class A and B variables live only in the environment, so the example files are where an
operator finds them; a variable written there must exist in the settings registry.
"""

import re
from pathlib import Path

from app.config import FILE_SECRETS
from app.domain.settings_registry import REGISTRY, SettingClass, lookup

ROOT = Path(__file__).resolve().parents[1]
PROXY = {"tls_domain", "acme_email", "acme_ca", "tls_cert_file", "tls_key_file"}
"""Read by the proxy container only, from ``proxy.env``."""

_ASSIGNMENT = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)


def _names(path: Path) -> set[str]:
    return set(_ASSIGNMENT.findall(path.read_text()))


def _env_only(proxy: bool) -> set[str]:
    return {
        spec.env
        for spec in REGISTRY
        if spec.class_ in (SettingClass.BOOTSTRAP, SettingClass.DEPLOYMENT)
        and (spec.key in PROXY) is proxy
    }


def test_every_environment_only_variable_is_in_env_example() -> None:
    text = (ROOT / ".env.example").read_text()

    missing = sorted(n for n in _env_only(proxy=False) if not re.search(rf"\b{n}\b", text))

    assert missing == [], f"add these to .env.example: {missing}"


def test_every_proxy_variable_is_in_proxy_env_example() -> None:
    assert _env_only(proxy=True) <= _names(ROOT / "proxy.env.example")


def test_every_variable_in_env_example_is_registered() -> None:
    files = {f"{name.upper()}_FILE" for name in FILE_SECRETS}

    unknown = sorted(n for n in _names(ROOT / ".env.example") if n not in files and not lookup(n))

    assert unknown == []
