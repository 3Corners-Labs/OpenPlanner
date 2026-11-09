"""The release a capture of the role manuals shows, and a frozen version of them per release.

Two commands, run from the checkout with its own Python (``update.sh`` and ``freeze.sh`` call
them this way)::

    uv run python .claude/skills/role-manuals/scripts/release.py record <workspace>
    uv run python .claude/skills/role-manuals/scripts/release.py freeze <workspace>

Both read the capture's own record, ``evidence/state.json`` of ``capture.mjs``: the commit its
stack was built from and the fingerprint of the files the image was built from. The capture's
release is the nearest release tag of that commit (a release before a candidate of it on the
same commit) when ``stack.sh fingerprint`` gives that tag the same fingerprint; otherwise the
capture is a trial. ``pyproject.toml`` is no source: it names the previous release until the
next one is cut.

``record`` writes the label for the title pages to ``software_version``. ``freeze`` copies a
settled version into ``versies/`` of the workspace: one folder per version, read-only, with
``MANIFEST.sha256`` over its files, a ``LEESMIJ.md`` and a zip with its own checksum. It refuses
a trial, title pages that name another release, a version that exists, sources that do not
state one version number, a missing record of the settlement, a screenshot this capture did not
take, and a check that blocked or read other files than there are now (``evidence/check.json``
of ``check.py``). Both commands refuse a workspace inside a checkout. It builds the copy
beside ``versies/`` and moves it into place only when it is whole. Then it sets ``commit``, the
baseline of the next ``check.py --since``, to the release: every check reports what changed since
the last frozen version, and nothing else moves the baseline.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from workspace import Workspace  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
STACK = Path(".claude/skills/verify-openplanner/scripts/stack.sh")
"""The script that fingerprints an image's files, relative to the checkout."""

TAGS = "v[0-9]*"
RELEASE = re.compile(r"v\d+\.\d+\.\d+(-rc\.\d+)?")
"""A release tag (``v0.6.0``) or a candidate of one (``v0.6.0-rc.35``); no other tag names one."""

COMMIT = re.compile(r"[0-9a-f]{7,40}")
FINGERPRINT = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"\d+(\.\d+)*")
"""The number a version of the manuals has: digits and dots, never "concept"."""

RECORD = Path("evidence/afhechting.md")
"""What a person reread and what stays open, written before a version is frozen."""

READ_BY_FREEZE = ("variables.json", "build/manuals.json")
"""The workspace files freeze reads itself, so the check it relies on must have read them too."""

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

    A nearer tag of another shape is passed over. A release can share its commit with its last
    candidate; the release names it then.
    """
    passed_over: list[str] = []
    while True:
        skip = [f"--exclude={tag}" for tag in passed_over]
        try:
            nearest = git(
                repo, "describe", "--tags", "--abbrev=0", f"--match={TAGS}", *skip, commit
            )
        except subprocess.CalledProcessError:
            return ""
        if RELEASE.fullmatch(nearest):
            break
        passed_over.append(nearest)
    peers = git(repo, "tag", "--points-at", f"refs/tags/{nearest}^{{commit}}", "--list", TAGS)
    releases = sorted(tag for tag in peers.split() if RELEASE.fullmatch(tag) and "-" not in tag)
    return releases[0] if releases else nearest


def software_label(tag: str, commit: str, same: bool) -> str:
    """Return the software version for the title pages, in the manuals' language.

    A release tag gives its number and a candidate (``-rc.N``) says which release it tests. An
    image built from other files than the tag's says so and names its commit; without a tag the
    commit is all there is.
    """
    if not tag:
        return f"gebouwd uit commit {commit[:7]}"
    number = tag.removeprefix("v")
    release, dash, _ = number.partition("-")
    label = f"{number}, testversie van {release}" if dash else number
    return label if same else f"{label}, met latere wijzigingen (commit {commit[:7]})"


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


def capture(ws: Workspace) -> dict:
    """Return the record of the last capture, ``evidence/state.json``."""
    path = ws.evidence / "state.json"
    if not path.exists():
        sys.exit(f"release.py: {path} is missing; capture first (update.sh)")
    return _read(path)


def capture_release(state: dict, repo: Path) -> tuple[str, str, bool]:
    """Return a capture's commit, its release tag, and whether its image is that tag's.

    The stack client records the commit and the image fingerprint of its run in the state; a
    state without them (a capture by an older skill) is refused, so an empty fingerprint never
    matches an empty one.
    """
    build = state.get("stack", {}).get("build", {})
    commit, built = str(build.get("commit", "")), str(build.get("fingerprint", ""))
    if not COMMIT.fullmatch(commit) or not FINGERPRINT.fullmatch(built):
        sys.exit(
            "release.py: evidence/state.json records no commit and image fingerprint of its "
            "run; capture again with this version of the skill"
        )
    tag = release_tag(repo, commit)
    return commit, tag, bool(tag) and image_of(repo, tag) == built


def record(workspace: Path, repo: Path = REPO) -> str:
    """Write the label of the release the capture shows into variables.json; return it."""
    ws = Workspace(workspace)
    commit, tag, same = capture_release(capture(ws), repo)
    label = software_label(tag, commit, same)
    data = _read(ws.variables)
    data["software_version"] = label
    _write(ws.variables, data)
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_passed(ws: Workspace) -> None:
    """Refuse unless the last check passed on the files as they are now.

    ``check.py`` writes its verdict to ``evidence/check.json``: the number of blocking findings
    and the sha256 of every file it read, by its path in the workspace. Each of them must be
    unchanged. The verdict must also name every source there is now (one added since cannot be
    listed) and the files freeze reads itself (``READ_BY_FREEZE``).
    """
    path = ws.evidence / "check.json"
    if not path.exists():
        sys.exit("release.py: evidence/check.json is missing; freeze.sh checks first")
    verdict = _read(path)
    if verdict.get("blocking") != 0:
        sys.exit(f"release.py: the last check found {verdict.get('blocking')} blocking finding(s)")
    listed: dict[str, str] = verdict.get("inputs", {})

    def unchanged(name: str) -> bool:
        file = ws.root / name
        inside = not Path(name).is_absolute() and ".." not in Path(name).parts
        return inside and file.is_file() and _sha256(file) == listed[name]

    expected = {f"sources/{p.name}" for p in ws.sources.glob("*.md")} | set(READ_BY_FREEZE)
    differ = sorted((expected - set(listed)) | {name for name in listed if not unchanged(name)})
    if differ:
        sys.exit(f"release.py: not what the last check read: {', '.join(differ)}; run freeze.sh")


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


def earlier_images(ws: Workspace, state: dict) -> list[str]:
    """Return the screenshots in ``img/`` that this capture did not take.

    ``update.sh`` starts every capture with a new state and keeps the images of the one before,
    so an image whose shot this capture skipped or never reached comes from an earlier capture.
    """
    records = state.get("records", {})
    return sorted(
        image.stem
        for image in ws.img.glob("*.png")
        if records.get(image.stem, {}).get("status") != "captured"
    )


def freeze(workspace: Path, repo: Path = REPO, today: dt.date | None = None) -> Path:
    """Freeze the settled version of the manuals into ``versies/``; return its folder."""
    ws = Workspace(workspace)
    state = capture(ws)
    captured_from, tag, same = capture_release(state, repo)
    if not same:
        sys.exit(
            "release.py: the capture is a trial: its image is no release tag's; "
            "run update.sh from a checkout of the release tag"
        )
    variables = _read(ws.variables)
    label = software_label(tag, captured_from, same)
    if variables.get("software_version") != label:
        sys.exit(
            f"release.py: the title pages say {variables.get('software_version')!r}, the "
            f"capture shows {label!r}; freeze.sh records it first"
        )
    commit = git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    version, version_text = manual_version(_read(ws.build / "manuals.json")["manuals"])
    if not (ws.root / RECORD).exists():
        sys.exit(f"release.py: write {RECORD} first: what was reread and what stays open")
    earlier = earlier_images(ws, state)
    if earlier:
        sys.exit(
            f"release.py: not taken by this capture, so from an earlier one: {', '.join(earlier)}; "
            "capture them again, or remove them from img/ so the manuals say they follow"
        )
    check_passed(ws)
    started = str(state.get("started", ""))
    if not re.match(r"\d{4}-\d{2}-\d{2}", started):
        sys.exit("release.py: evidence/state.json holds no start of the capture")
    captured = dt.date.fromisoformat(started[:10])
    name = f"{captured.isoformat()}_software-{tag.removeprefix('v')}_handleiding-{version}"
    versions = ws.root / "versies"
    target, archive = versions / name, versions / f"{name}.zip"
    if target.exists() or archive.exists():
        sys.exit(f"release.py: {name} exists already; a frozen version never changes")
    versions.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=versions))
    partial = versions / f".{name}.zip.part"
    try:
        for part in PARTS:
            shutil.copytree(ws.root / part, work / part, ignore=SKIP)
        shutil.copy2(ws.variables, work)
        for doc in sorted(ws.root.glob("*.md")):
            if doc.name != "README.md":
                shutil.copy2(doc, work)
        values = {
            "version": version,
            "version_text": version_text,
            "software_version": label,
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
    _write(ws.variables, variables)
    return target


def main(argv: list[str]) -> int:
    """Run ``record`` or ``freeze`` and say what it did."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("record", "write the release the capture shows for the title pages"),
        ("freeze", "freeze the settled version into versies/"),
    ):
        commands.add_parser(name, help=text).add_argument("workspace", type=Path)
    args = parser.parse_args(argv)
    if args.command == "record":
        print(f"Softwareversie: {record(args.workspace)}")
    else:
        target = freeze(args.workspace)
        print(f"Vastgelegd: {target} (controle: shasum -a 256 -c MANIFEST.sha256 in die map)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
