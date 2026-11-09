"""The release a capture of the role manuals shows, and a frozen version of them per release.

Two commands, run from the checkout (``update.sh`` and ``freeze.sh`` call them)::

    python3 release.py record <workspace> <stack.env of the run>
    python3 release.py freeze <workspace>

``record`` reads which commit the run's image was built from, and its fingerprint, from the
run's ``stack.env`` (``verify-openplanner``). It finds the nearest release tag, a release before
a candidate of it on the same commit, and asks ``stack.sh fingerprint`` whether the image was
built from that tag's files. The label goes to ``software_version`` for the title pages; the tag
goes to ``release`` when the image is the tag's, else ``release`` is empty and the capture is a
trial. ``pyproject.toml`` is no source: it names the previous release until the next one is cut.

``freeze`` copies a settled version into ``versies/`` of the workspace: one folder per version,
read-only, with ``MANIFEST.sha256`` over its files, a ``LEESMIJ.md`` and a zip with its own
checksum. It refuses a trial, a version that exists, sources that do not state one version
number, a missing record of the settlement and a check that is not the last word. It builds the
copy beside ``versies/`` and moves it into place only when it is whole. Then it sets ``commit``,
the baseline of the next ``check.py --since``, to the release: every check reports what changed
since the last frozen version, and nothing else moves the baseline.
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
STACK = Path(".claude/skills/verify-openplanner/scripts/stack.sh")
"""The script that fingerprints an image's files, relative to the checkout."""

TAGS = "v[0-9]*"
COMMIT = re.compile(r"[0-9a-f]{7,40}")
FINGERPRINT = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"\d+(\.\d+)*")
"""The number a version of the manuals has: digits and dots, never "concept"."""

RECORD = Path("evidence/afhechting.md")
"""What a person reread and what stays open, written before a version is frozen."""

PARTS = ("build", "sources", "img", "evidence")
"""The folders a version consists of; besides them variables.json and the workspace's own
Markdown files (the style guide, the shot list), never README.md, which lists the versions."""

SKIP = shutil.ignore_patterns("previous", ".DS_Store")
"""The previous capture (``img/previous/``, ``evidence/previous/``) is no part of a version."""

MONTHS = (
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
)


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return its output without the final newline."""
    exe = shutil.which("git") or "git"
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [exe, "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def release_tag(repo: Path, commit: str) -> str:
    """Return the nearest release tag of ``commit``, or "" when none is reachable.

    A release can share its commit with its last candidate; the release names it then.
    """
    try:
        nearest = git(repo, "describe", "--tags", "--abbrev=0", f"--match={TAGS}", commit)
    except subprocess.CalledProcessError:
        return ""
    peers = git(repo, "tag", "--points-at", f"{nearest}^{{commit}}", "--list", TAGS).split()
    releases = sorted(tag for tag in peers if "-" not in tag)
    return releases[0] if releases else nearest


def software_label(tag: str, commit: str, same: bool) -> str:
    """Return the software version for the title pages, in the manuals' language.

    A release tag gives its number and a pre-release (``-rc.N``) says which release it tests. An
    image built from other files than the tag's says so and names its commit; without a tag the
    commit is all there is.
    """
    if not tag:
        return f"gebouwd uit commit {commit[:7]}"
    number = tag.removeprefix("v")
    release, dash, _ = number.partition("-")
    label = f"{number}, testversie van {release}" if dash else number
    return label if same else f"{label}, met latere wijzigingen (commit {commit[:7]})"


def run_facts(stack_env: Path) -> tuple[str, str]:
    """Return the commit and the fingerprint the run's image was built from.

    Both come from the ``stack.env`` that ``stack.sh up`` wrote; a record without them is refused,
    so an empty fingerprint never matches an empty one.
    """
    facts = dict(line.split("=", 1) for line in stack_env.read_text().splitlines() if "=" in line)
    commit = facts.get("BUILT_FROM_COMMIT", "")
    built = facts.get("BUILT_FROM_FINGERPRINT", "")
    if not COMMIT.fullmatch(commit) or not FINGERPRINT.fullmatch(built):
        sys.exit(f"release.py: {stack_env} records no commit and image fingerprint of its build")
    return commit, built


def image_of(repo: Path, tag: str) -> str:
    """Return the fingerprint of the files the image of ``tag`` is built from."""
    bash = shutil.which("bash") or "/bin/bash"
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [bash, str(repo / STACK), "fingerprint", tag], capture_output=True, text=True, check=False
    )
    found = done.stdout.strip()
    if done.returncode or not FINGERPRINT.fullmatch(found):
        sys.exit(f"release.py: stack.sh fingerprint {tag} failed: {done.stderr.strip()}")
    return found


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def record(workspace: Path, stack_env: Path, repo: Path = REPO) -> str:
    """Write the release the run shows into the workspace's variables.json; return its label."""
    commit, built = run_facts(stack_env)
    tag = release_tag(repo, commit)
    same = bool(tag) and image_of(repo, tag) == built
    label = software_label(tag, commit, same)
    path = workspace / "variables.json"
    data = _read(path)
    data["software_version"] = label
    data["release"] = tag if same else ""
    _write(path, data)
    return label


def dutch_date(day: dt.date) -> str:
    """Return a date as the manuals write it: ``8 november 2026``."""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def manual_version(manuals: list[dict]) -> tuple[str, str]:
    """Return the version number every source states first, and the first source's full text."""
    found = {m["file"]: str(m.get("version", "")) for m in manuals}
    numbers = {text.split(",")[0].strip() for text in found.values()}
    if len(numbers) != 1 or not VERSION.fullmatch(next(iter(numbers))):
        listed = ", ".join(f"{name}: {text or '–'}" for name, text in sorted(found.items()))
        sys.exit(f"release.py: the sources state no one version number first ({listed})")
    return numbers.pop(), found[min(found)]


def _fresh_check(workspace: Path) -> None:
    """Refuse unless ``evidence/check.md`` passed and nothing it checked changed after it.

    ``freeze.sh`` checks right before; this guards a direct call. The report's first line counts
    the blocking findings (``check.py``, ``Report.markdown``).
    """
    report = workspace / "evidence" / "check.md"
    if not report.exists() or "Blokkerende bevindingen: 0." not in report.read_text():
        sys.exit("release.py: evidence/check.md is missing or blocks; freeze.sh checks first")
    inputs = [workspace / "variables.json", workspace / "build" / "manuals.json"]
    inputs += list((workspace / "sources").glob("*.md"))
    newer = [p.name for p in inputs if p.stat().st_mtime > report.stat().st_mtime]
    if newer:
        sys.exit(f"release.py: changed after the last check: {', '.join(newer)}; run freeze.sh")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(folder: Path) -> list[Path]:
    """Every file below ``folder``, in the order of their paths as text."""
    found = [p for p in folder.rglob("*") if p.is_file()]
    return sorted(found, key=lambda p: p.relative_to(folder).as_posix())


def _readme(template: Path, values: dict[str, str]) -> str:
    text = template.read_text()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _read_only(folder: Path) -> None:
    for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    folder.chmod(0o555)


def _writable(folder: Path) -> None:
    for path in [folder, *folder.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)


def freeze(workspace: Path, repo: Path = REPO, today: dt.date | None = None) -> Path:
    """Freeze the settled version of the manuals into ``versies/``; return its folder."""
    variables = _read(workspace / "variables.json")
    tag = variables.get("release", "")
    if not tag:
        sys.exit(
            "release.py: the last capture is a trial: its image is no release tag's; "
            "run update.sh from a checkout of the release tag"
        )
    try:
        commit = git(repo, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}")
    except subprocess.CalledProcessError:
        sys.exit(f"release.py: the tag {tag} is not in this checkout; fetch the tags")
    version, version_text = manual_version(_read(workspace / "build" / "manuals.json")["manuals"])
    if not (workspace / RECORD).exists():
        sys.exit(f"release.py: write {RECORD} first: what was reread and what stays open")
    _fresh_check(workspace)
    started = str(_read(workspace / "evidence" / "state.json").get("started", ""))
    if not re.match(r"\d{4}-\d{2}-\d{2}", started):
        sys.exit("release.py: evidence/state.json holds no start of the capture")
    captured = dt.date.fromisoformat(started[:10])
    name = f"{captured.isoformat()}_software-{tag.removeprefix('v')}_handleiding-{version}"
    versions = workspace / "versies"
    target, archive = versions / name, versions / f"{name}.zip"
    if target.exists() or archive.exists():
        sys.exit(f"release.py: {name} exists already; a frozen version never changes")
    versions.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=versions))
    partial = versions / f".{name}.zip.part"
    try:
        for part in PARTS:
            shutil.copytree(workspace / part, work / part, ignore=SKIP)
        shutil.copy2(workspace / "variables.json", work)
        for doc in sorted(workspace.glob("*.md")):
            if doc.name != "README.md":
                shutil.copy2(doc, work)
        values = {
            "version": version,
            "version_text": version_text,
            "software_version": str(variables.get("software_version", "")),
            "release": tag,
            "commit": commit[:12],
            "captured": dutch_date(captured),
            "frozen": dutch_date(today or dt.date.today()),
            "record": RECORD.as_posix(),
        }
        template = Path(__file__).resolve().parents[1] / "templates" / "leesmij.md"
        (work / "LEESMIJ.md").write_text(_readme(template, values))
        lines = [f"{_sha256(p)}  ./{p.relative_to(work).as_posix()}" for p in _files(work)]
        (work / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
        _read_only(work)
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in _files(work):
                bundle.write(path, f"{name}/{path.relative_to(work).as_posix()}")
        work.rename(target)
        partial.rename(archive)
    except BaseException:
        if work.exists():
            _writable(work)
            shutil.rmtree(work)
        partial.unlink(missing_ok=True)
        raise
    checksum = versions / f"{name}.zip.sha256"
    checksum.write_text(f"{_sha256(archive)}  {archive.name}\n")
    for path in (archive, checksum):
        path.chmod(0o444)
    variables["commit"] = commit[:12]
    _write(workspace / "variables.json", variables)
    return target


def main(argv: list[str]) -> int:
    """Run ``record`` or ``freeze`` and say what it did."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    recording = commands.add_parser("record", help="write the release a capture shows")
    recording.add_argument("workspace", type=Path)
    recording.add_argument("stack_env", type=Path)
    freezing = commands.add_parser("freeze", help="freeze the settled version into versies/")
    freezing.add_argument("workspace", type=Path)
    args = parser.parse_args(argv)
    if args.command == "record":
        print(f"Softwareversie: {record(args.workspace, args.stack_env)}")
    else:
        target = freeze(args.workspace)
        print(f"Vastgelegd: {target} (controle: shasum -a 256 -c MANIFEST.sha256 in die map)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
