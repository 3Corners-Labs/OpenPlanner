"""Check the role manuals against the software, so screens and guidance can be updated by machine.

Usage:
    python3 scripts/check.py --repo <checkout> [--sinds <commit>] [--herstel]

Writes ``bewijs/controle.md`` and exits with 1 when a blocking finding is left:

1. Task markers (``<!-- task: screen=...; permission=...; shots=... -->``): the screen is a help
   topic of the portal or a participant page, the permission exists, and a role of the manual
   holds it in the authorisation matrix.
2. Screenshots: every image and shot id is in the shot list; which files still have to follow.
3. Labels: every bold label is a text of the software (``app/i18n/locales/nl.po``).
4. Terms the organisation does not want named (``niet_noemen`` in ``variabelen.json``) and the
   product name of the software.
5. Since a commit: texts the software changed or removed and where the manuals use them, and
   topics that changed. With ``--herstel`` a renamed label is replaced in the sources.
6. Screenshots that differ from the previous capture (``img/vorige/``), with a picture of the
   difference in ``bewijs/verschil/``.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops

WORKSPACE = Path(__file__).resolve().parent.parent
PARTICIPANT_SCREENS = {
    "participant:group-page",
    "participant:booking-form",
    "participant:confirm",
    "participant:manage",
    "participant:move",
    "participant:cancel",
    "participant:join",
    "participant:trial",
    "participant:email",
}
"""Participant pages have no help topic; these keys name them."""

DEFAULT_ROLES = {
    "beheerder": ["Superuser", "Organisation admin", "Compliance and privacy (read-only)"],
    "planner": ["Group admin"],
    "medewerker": ["Agent"],
}
"""Matrix columns per manual, matched on a word in the manual's ``rol``; others are participants."""

STRUCTURAL = re.compile(
    r"^(wanneer|resultaat|let op|tip|belangrijk|waarom|stap \d+|voorbeeld|doel|wie|let wel)\b",
    re.IGNORECASE,
)
"""Bold words that structure the text and are no label of the software."""

TASK = re.compile(r"<!--\s*task:\s*(.*?)-->")
BOLD = re.compile(r"\*\*(.+?)\*\*")
IMAGE = re.compile(r"!\[[^\]]*\]\(img/([^)\s]+)\.png")
SHOT_ROW = re.compile(r"^\|\s*([a-z]{2}-\d{2}-[a-z0-9-]+)\s*\|")
CODE = re.compile(r"`([a-z_]+(?:\.[a-z_]+)+)`")


@dataclass
class Report:
    """Findings per section; ``blocking`` ones make the exit code 1."""

    sections: dict[str, list[str]] = field(default_factory=dict)
    blocking: int = 0

    def add(self, section: str, line: str, *, blocking: bool = False) -> None:
        """Add one finding to a section."""
        self.sections.setdefault(section, []).append(
            ("**Blokkerend:** " if blocking else "") + line
        )
        self.blocking += int(blocking)

    def markdown(self) -> str:
        """Return the report as Markdown."""
        out = ["# Controle van de handleidingen", ""]
        out.append(f"Blokkerende bevindingen: {self.blocking}.")
        for name, lines in self.sections.items():
            out += ["", f"## {name}", "", *[f"- {line}" for line in lines]]
        return "\n".join(out) + "\n"


def git(repo: Path, *args: str) -> str:
    """Run git in the checkout and return its output."""
    exe = shutil.which("git") or "git"
    return subprocess.run(  # noqa: S603
        [exe, "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def parse_po(text: str) -> dict[str, str]:
    """Return msgid → msgstr of a gettext catalogue (multi-line strings joined)."""
    entries: dict[str, str] = {}
    key: str | None = None
    current: list[str] = []
    msgid = ""

    def unquote(s: str) -> str:
        return s.strip()[1:-1].replace('\\"', '"').replace("\\n", "\n")

    def flush() -> None:
        nonlocal msgid
        if key == "msgstr" and msgid:
            entries[msgid] = "".join(current)
        elif key == "msgid":
            msgid = "".join(current)

    for line in text.splitlines():
        if line.startswith(("msgid ", "msgstr ")):
            flush()
            key, rest = line.split(" ", 1)
            current = [unquote(rest)]
        elif line.startswith('"') and key:
            current.append(unquote(line))
        else:
            flush()
            key = None
    flush()
    return entries


def paragraphs(text: str) -> list[tuple[int, str]]:
    """Return (first line number, text on one line) for each paragraph, so bold spans may wrap."""
    out: list[tuple[int, str]] = []
    start, lines = 0, []
    for number, line in enumerate([*text.splitlines(), ""], 1):
        if line.strip() and not line.lstrip().startswith(("|", "<!--")):
            if not lines:
                start = number
            lines.append(line.strip())
        else:
            if lines:
                out.append((start, " ".join(lines)))
                lines = []
            if line.lstrip().startswith("|"):
                out.append((number, line))
    return out


def labels_in(line: str) -> list[str]:
    """Return the bold labels of one line; a path (A → B → C) gives its parts."""
    found = []
    for bold in BOLD.findall(line):
        for part in re.split(r"\s*→\s*", bold):
            part = part.strip().rstrip(".")
            if not part or part[0] in "[0123456789" or "{{" in part or part.endswith(":"):
                continue
            if STRUCTURAL.match(part):
                continue
            found.append(part)
    return found


class Catalogue:
    """The texts of the software, for exact and placeholder matches."""

    def __init__(self, entries: dict[str, str], extra: list[str] | None = None) -> None:
        """Index the msgstr values of a catalogue, without their closing punctuation."""
        self.values = {normalise(v) for v in entries.values() if v} | set(extra or [])
        self.patterns = [
            re.compile("^" + re.sub(r"\\\{\w+\\\}", ".+?", re.escape(v)) + "$")
            for v in self.values
            if "{" in v and len(v) < 300 and len(re.sub(r"\{\w+\}|\W", "", v)) >= 6
        ]

    def match(self, label: str) -> str:
        """Return ``exact``, ``placeholder``, ``in tekst`` or ``onbekend`` for a label."""
        label = normalise(label)
        if label in self.values:
            return "exact"
        if any(p.match(label) for p in self.patterns):
            return "placeholder"
        if "…" in label:
            wildcard = re.compile("^" + re.escape(label).replace("…", ".+?") + "$")
            if any(wildcard.match(v) for v in self.values):
                return "placeholder"
        if any(label in v for v in self.values):
            return "in tekst"
        return "onbekend"


def normalise(text: str) -> str:
    """Collapse white space and drop closing punctuation, as a label is quoted without it."""
    return re.sub(r"\s+", " ", text).strip().rstrip(".!?:").strip()


def matrix(repo: Path) -> tuple[set[str], dict[str, set[str]], set[str]]:
    """Return the permission codes, the codes per role column and the participant actions."""
    text = (repo / "docs" / "authorization.md").read_text()
    codes = set(CODE.findall(text))
    per_role: dict[str, set[str]] = {}
    header: list[str] = []
    participant: set[str] = set()
    for line in text.splitlines():
        cells = (
            [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        )
        if cells and cells[0] == "Permission":
            header = cells
            continue
        if not cells or not header or set(cells[0]) <= {"-"}:
            if not line.startswith("|"):
                header = []
            continue
        code = CODE.search(cells[0])
        if not code:
            continue
        for role, value in zip(header[3:], cells[3:], strict=False):
            if value == "yes":
                per_role.setdefault(role, set()).add(code.group(1))
    participant = {c for c in codes if c.startswith("participant.")}
    return codes, per_role, participant


def manual_roles(rol: str, config: dict[str, list[str]]) -> list[str] | None:
    """Return the matrix columns of a manual's role, or None for the participants' manual."""
    for word, columns in config.items():
        if word.lower() in rol.lower():
            return columns
    return None


def front_matter(text: str) -> dict[str, str]:
    """Return the flat front matter of a source file."""
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    meta = {}
    for line in (match.group(1) if match else "").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"')
    return meta


def visual_changes(report: Report) -> None:
    """Compare each screenshot with its previous capture and draw the differences."""
    previous = WORKSPACE / "img" / "vorige"
    out = WORKSPACE / "bewijs" / "verschil"
    if not previous.is_dir():
        report.add(
            "Schermafdrukken gewijzigd", "Geen vorige opname in img/vorige/; niets vergeleken."
        )
        return
    out.mkdir(parents=True, exist_ok=True)
    changed = 0
    for old_file in sorted(previous.glob("*.png")):
        new_file = WORKSPACE / "img" / old_file.name
        if not new_file.exists():
            report.add("Schermafdrukken gewijzigd", f"`{old_file.stem}`: niet meer opgenomen.")
            continue
        old, new = Image.open(old_file).convert("RGB"), Image.open(new_file).convert("RGB")
        if old.size != new.size:
            report.add(
                "Schermafdrukken gewijzigd",
                f"`{old_file.stem}`: andere afmeting {old.size} → {new.size}.",
            )
            changed += 1
            continue
        mask = ImageChops.difference(old, new).convert("L").point(lambda p: 255 if p > 24 else 0)
        share = mask.histogram()[255] / (new.size[0] * new.size[1])
        if share > 0.002:
            changed += 1
            overlay = Image.new("RGB", new.size, (220, 0, 0))
            Image.composite(overlay, new, mask).save(out / old_file.name)
            report.add(
                "Schermafdrukken gewijzigd",
                f"`{old_file.stem}`: {share:.1%} van het beeld anders; "
                f"zie `bewijs/verschil/{old_file.name}`.",
            )
    if not changed:
        report.add("Schermafdrukken gewijzigd", "Geen verschillen boven 0,2 %.")


def since(repo: Path, commit: str, sources: dict[Path, str], fix: bool, report: Report) -> None:
    """Report what the software changed since ``commit`` and where the manuals use it."""
    po_path = "app/i18n/locales/nl.po"
    old = parse_po(git(repo, "show", f"{commit}:{po_path}"))
    new = parse_po((repo / po_path).read_text())
    new_values = {v for v in new.values() if v}
    renamed = {
        old[k]: new[k]
        for k in old
        if k in new and old[k] != new[k] and old[k] and old[k] not in new_values
    }
    removed = {old[k] for k in old if k not in new and old[k] and old[k] not in new_values}
    section = f"Gewijzigd in de software sinds {commit[:9]}"
    for path, text in sources.items():
        changed_text = text
        for number, line in enumerate(text.splitlines(), 1):
            for label in labels_in(line):
                if label in renamed:
                    action = "vervangen" if fix else "gebruik --herstel om te vervangen"
                    report.add(
                        section,
                        f"{path.name}:{number}: **{label}** heet nu "
                        f"**{renamed[label]}** ({action}).",
                    )
                    changed_text = changed_text.replace(f"**{label}**", f"**{renamed[label]}**")
                    changed_text = changed_text.replace(f"→ {label}", f"→ {renamed[label]}")
                if label in removed:
                    report.add(
                        section,
                        f"{path.name}:{number}: **{label}** bestaat niet meer in de software.",
                        blocking=True,
                    )
        if fix and changed_text != text:
            path.write_text(changed_text)
            sources[path] = changed_text
    topics = git(repo, "diff", "--name-only", f"{commit}..HEAD", "--", "docs/manual/nl").split()
    for topic in topics:
        slug = Path(topic).stem
        hits = [
            f"{p.name} › {m.group(0)[:60]}"
            for p, t in sources.items()
            for m in TASK.finditer(t)
            if f"screen={slug};" in m.group(1).replace(" ", "") + ";"
        ]
        report.add(
            section,
            f"Hulp bij scherm `{slug}` gewijzigd; "
            f"lees na: {', '.join(hits) or 'geen taak gekoppeld'}.",
        )
    templates = git(
        repo,
        "diff",
        "--name-only",
        f"{commit}..HEAD",
        "--",
        "app/admin/templates",
        "app/web/templates",
    )
    for name in templates.split():
        report.add(
            section,
            f"Sjabloon gewijzigd: `{name}`; neem de schermafdrukken van dat scherm opnieuw op.",
        )
    if not (renamed or removed or topics or templates.strip()):
        report.add(section, "Geen wijzigingen in teksten, hulp of sjablonen.")


def main() -> int:
    """Run every check and write the report."""
    if ".claude/skills" in Path(__file__).resolve().as_posix():
        sys.exit(
            f"{Path(__file__).name}: copy the scripts to a workspace outside the repository first "
            "(see the role-manuals SKILL.md): manuals hold an organisation's own material."
        )
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True, help="checkout of the software")
    parser.add_argument(
        "--sinds", help="commit of the previous check (default: variabelen.json commit)"
    )
    parser.add_argument(
        "--herstel", action="store_true", help="replace renamed labels in the sources"
    )
    args = parser.parse_args()
    repo: Path = args.repo.resolve()
    config = json.loads((WORKSPACE / "variabelen.json").read_text())
    report = Report()
    sources = {p: p.read_text() for p in sorted((WORKSPACE / "bron").glob("*.md"))}
    shot_ids = {
        m.group(1)
        for line in (WORKSPACE / "opnamelijst.md").read_text().splitlines()
        if (m := SHOT_ROW.match(line))
    }
    topics = {p.stem for p in (repo / "docs" / "manual" / "nl").glob("*.md")}
    codes, per_role, participant = matrix(repo)
    catalogue = Catalogue(
        parse_po((repo / "app" / "i18n" / "locales" / "nl.po").read_text()),
        config.get("schermteksten_extra", []),
    )
    roles_config = {**DEFAULT_ROLES, **config.get("rollen", {})}
    forbidden = [t for t in config.get("niet_noemen", []) if t]
    used: set[str] = set()
    verdicts: dict[str, int] = {}
    tasks = 0
    for path, text in sources.items():
        meta = front_matter(text)
        columns = manual_roles(meta.get("rol", ""), roles_config)
        for number, line in enumerate(text.splitlines(), 1):
            where = f"{path.name}:{number}"
            for marker in TASK.finditer(line):
                tasks += 1
                attrs = dict(
                    (k.strip(), v.strip())
                    for k, _, v in (p.partition("=") for p in marker.group(1).split(";"))
                    if k.strip()
                )
                screen, permission = attrs.get("screen", ""), attrs.get("permission", "")
                if screen not in topics and screen not in PARTICIPANT_SCREENS:
                    report.add(
                        "Koppelingen", f"{where}: scherm `{screen}` bestaat niet.", blocking=True
                    )
                if permission not in codes:
                    report.add(
                        "Koppelingen", f"{where}: recht `{permission}` bestaat niet.", blocking=True
                    )
                elif columns is None and permission not in participant:
                    report.add(
                        "Koppelingen",
                        f"{where}: `{permission}` is geen recht van deelnemers.",
                        blocking=True,
                    )
                elif columns and not any(permission in per_role.get(c, set()) for c in columns):
                    report.add(
                        "Koppelingen",
                        f"{where}: de rol {meta.get('rol')} heeft `{permission}` niet.",
                        blocking=True,
                    )
                for shot in filter(None, (s.strip() for s in attrs.get("shots", "").split(","))):
                    used.add(shot)
                    if shot not in shot_ids:
                        report.add(
                            "Koppelingen",
                            f"{where}: schermafdruk `{shot}` staat niet in de opnamelijst.",
                            blocking=True,
                        )
            for shot in IMAGE.findall(line):
                used.add(shot)
                if shot not in shot_ids:
                    report.add(
                        "Schermafdrukken",
                        f"{where}: `{shot}` staat niet in de opnamelijst.",
                        blocking=True,
                    )
            low = line.lower()
            for term in [*forbidden, "openplanner"]:
                if term.lower() in low and not line.lstrip().startswith("<!--"):
                    report.add("Niet te noemen", f"{where}: `{term}`.", blocking=True)
        if not path.name.startswith("00-"):
            for number, paragraph in paragraphs(text):
                for label in labels_in(paragraph):
                    verdict = catalogue.match(label)
                    verdicts[verdict] = verdicts.get(verdict, 0) + 1
                    where = f"{path.name}:{number}"
                    if verdict == "onbekend":
                        report.add(
                            "Schermteksten", f"{where}: **{label}** komt niet voor in de software."
                        )
                    elif verdict == "in tekst":
                        report.add(
                            "Schermteksten (deel van een langere tekst)", f"{where}: **{label}**"
                        )
        if not TASK.search(text) and not path.name.startswith("00-"):
            report.add(
                "Koppelingen",
                f"{path.name}: geen taakkoppelingen (nodig voor contextgedreven hulp).",
            )
    missing = sorted(
        s for s in used if s in shot_ids and not (WORKSPACE / "img" / f"{s}.png").exists()
    )
    report.add(
        "Schermafdrukken",
        f"{len(used)} gebruikt, {len(missing)} volgen nog: {', '.join(missing) or '–'}.",
    )
    unused = sorted(shot_ids - used)
    if unused:
        report.add("Schermafdrukken", f"In de opnamelijst maar niet gebruikt: {', '.join(unused)}.")
    report.add("Koppelingen", f"{tasks} taken gekoppeld aan een scherm en een recht.")
    report.add(
        "Schermteksten",
        "Gecontroleerd: " + ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items())) + ".",
    )
    commit = args.sinds or config.get("commit")
    if commit:
        since(repo, commit, sources, args.herstel, report)
    visual_changes(report)
    out = WORKSPACE / "bewijs" / "controle.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report.markdown())
    print(f"controle: {report.blocking} blokkerend; rapport in {out.relative_to(WORKSPACE)}")
    return 1 if report.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
