"""``docs/settings.md``, generated from the settings registry (ADR 0042 §3).

``python -m app.cli settings-doc > docs/settings.md`` writes it; a test fails when the committed
file differs, so the published list always matches the code, as for ``docs/authorization.md``.
"""

from app.domain.settings_registry import REGISTRY, SettingClass, SettingSpec, bounds_text

_HEADINGS = {
    SettingClass.BOOTSTRAP: "A. Bootstrap (environment only)",
    SettingClass.DEPLOYMENT: "B. Deployment (environment only)",
    SettingClass.PLATFORM: "C. Platform (portal, *Advanced settings*; lockable by env)",
    SettingClass.ORGANISATION: "D. Organisation (portal, organisation settings; lockable by env)",
}

_INTRO = """# Settings

Generated from `app/domain/settings_registry.py` by
`python -m app.cli settings-doc > docs/settings.md`; do not edit by hand. CI fails when this
file differs from the code. The classes and their rules are in
[ADR 0042](adr/0042-settings-layers-env-lock-and-advanced-settings.md).

- **A and B** are read from the environment only; their values never appear in the portal.
  A bootstrap secret `X` may instead come from the file named by `X_FILE` (§9);
  `python -m app.cli init` generates the three keys into `secrets/secrets.env`.
- **C and D** will be set in the portal (build step 3 onwards). An environment variable
  *locks* them: it wins, and the portal shows the setting read-only. A locked value outside
  the portal bounds is accepted but flagged by `python -m app.cli settings list`.
- Secrets are write-only: the portal and the CLI only say whether they are set.

On the host, `python -m app.cli settings list` shows every setting with its source;
`python -m app.cli settings show KEY` shows one.
"""


def render_markdown() -> str:
    """Return ``docs/settings.md``: one table per class, in registry order."""
    parts = [_INTRO]
    for class_ in SettingClass:
        specs = [spec for spec in REGISTRY if spec.class_ is class_]
        parts.append(f"\n## {_HEADINGS[class_]}\n\n")
        if class_ in (SettingClass.PLATFORM, SettingClass.ORGANISATION):
            parts.append("| Variable | Kind | Default | Portal bounds | Permission | Note |\n")
            parts.append("|---|---|---|---|---|---|\n")
            parts.extend(_portal_row(spec) for spec in specs)
        else:
            parts.append("| Variable | Kind | Default | Note |\n|---|---|---|---|\n")
            parts.extend(_env_row(spec) for spec in specs)
    return "".join(parts)


def _default(spec: SettingSpec) -> str:
    """Write a default for the table."""
    if spec.secret or spec.default is None:
        return "—"
    if isinstance(spec.default, bool):
        return f"`{str(spec.default).lower()}`"
    return f"`{spec.default}`" if spec.default != "" else '`""`'


def _note(spec: SettingSpec) -> str:
    """Return the description, with the accepted aliases."""
    aliases = ", ".join(f"`{a.upper()}`" for a in spec.aliases)
    return spec.description + (f" Also accepted: {aliases}." if aliases else "")


def _env_row(spec: SettingSpec) -> str:
    """One table row for a class A or B setting."""
    return f"| `{spec.env}` | {spec.kind.value} | {_default(spec)} | {_note(spec)} |\n"


def _portal_row(spec: SettingSpec) -> str:
    """One table row for a class C or D setting."""
    return (
        f"| `{spec.env}` | {spec.kind.value} | {_default(spec)} | {bounds_text(spec) or '—'} "
        f"| `{spec.permission}` | {_note(spec)} |\n"
    )
