#!/usr/bin/env python
"""Cut a release: bump the version, build it, tag it, push it.

    .venv\\Scripts\\python publish_release.py patch
    .venv\\Scripts\\python publish_release.py minor
    .venv\\Scripts\\python publish_release.py 2.1.0

The version lives in three places that must never disagree — ``pyproject.toml``,
``server/nexus_server/__init__.py`` and ``android/app/build.gradle.kts`` — and
the tag ``.github/workflows/release.yml`` reacts to is a fourth. This script is
the one place that moves all four together:

1. refuse to work on a repository whose state you have not looked at: a dirty
   tree is reported file by file and needs an explicit answer,
2. read the current version, check the three files agree, compute the new one,
3. ask for a final confirmation that names the version and every step,
4. rewrite the version files,
5. run ``build_release.py`` so the tag is only pushed for a tree that really
   builds — a failure here restores the files and leaves no trace,
6. commit, annotate a ``vX.Y.Z`` tag, push both.

Pushing the tag is what makes GitHub build and publish the release; the local
``release/`` directory is the same set of files, built here to prove it works.

    --no-build       do not build locally (let the workflow be the first build)
    --skip-tests     pass --skip-tests to build_release.py
    --no-push        commit and tag, but stop before pushing
    --dry-run        print what would happen and change nothing
    --yes            answer every prompt with yes (for a script; think first).
                     One question it does not answer: a dirty tree stops the run
                     rather than being committed by a script that was not shown
                     what is in it.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import build_release
from build_release import StepFailed

ROOT = Path(__file__).resolve().parent

PARTS = ("major", "minor", "patch")


def log(message: str) -> None:
    print(f"[publish] {message}", flush=True)


def read_source(path: Path, label: str) -> str:
    """Read a source file without touching its line endings.

    Bytes, not ``read_text``: that translates CRLF to LF on the way in, so
    writing the result back would rewrite every line of the file and turn a
    one-line version bump into a whole-file diff. Whether these files are LF or
    CRLF in a given checkout is up to git's autocrlf, not up to us.
    """
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise StepFailed(f"cannot read {label}: {exc}") from exc


def write_source(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


# --- the version ------------------------------------------------------------

@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def tag(self) -> str:
        return f"v{self}"

    @classmethod
    def parse(cls, text: str) -> "Version":
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())
        if not match:
            raise StepFailed(f"{text!r} is not a version of the form MAJOR.MINOR.PATCH")
        return cls(*(int(g) for g in match.groups()))

    def bumped(self, part: str) -> "Version":
        if part == "major":
            return Version(self.major + 1, 0, 0)
        if part == "minor":
            return Version(self.major, self.minor + 1, 0)
        if part == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        raise StepFailed(f"unknown part to bump: {part!r}")


def android_version_code(version: Version) -> int:
    """A single integer that only ever grows, derived from the version.

    Android compares ``versionCode`` numerically and an upgrade with a code that
    did not increase is refused by the phone, so it must not be maintained by
    hand next to a string that is. Two digits each for minor and patch leaves
    room for 99 of either before the encoding would fold, which is more than this
    project will ever need.
    """
    return version.major * 10_000 + version.minor * 100 + version.patch


@dataclass(frozen=True)
class VersionFile:
    """One place the version is written, and how to read and rewrite it."""

    relative: str
    #: Must capture a group named ``value``; exactly one match is required, so a
    #: second ``version = "…"`` appearing in the file stops the release instead of
    #: being silently skipped or, worse, rewritten by accident.
    pattern: re.Pattern[str]
    render: Callable[[Version], str]
    #: Whether the captured text is the full MAJOR.MINOR.PATCH — the consistency
    #: check compares only those; ``versionCode`` is derived, not declared.
    is_version: bool = True

    def path(self, root: Path) -> Path:
        return root / self.relative

    def read(self, root: Path) -> str:
        return self._match(read_source(self.path(root), self.relative)).group("value")

    def rewrite(self, root: Path, version: Version) -> None:
        path = self.path(root)
        text = read_source(path, self.relative)
        match = self._match(text)
        new_text = text[: match.start("value")] + self.render(version) + text[match.end("value"):]
        write_source(path, new_text)

    def _match(self, text: str) -> re.Match[str]:
        matches = list(self.pattern.finditer(text))
        if not matches:
            raise StepFailed(f"no version line found in {self.relative}")
        if len(matches) > 1:
            raise StepFailed(
                f"{len(matches)} version lines found in {self.relative} — "
                "refusing to guess which one is the release version"
            )
        return matches[0]


VERSION_FILES: tuple[VersionFile, ...] = (
    VersionFile(
        "pyproject.toml",
        # \r? everywhere below: with a CRLF checkout the carriage return sits
        # between the closing quote and the end of the line, and "$" alone would
        # match nothing at all — a release that stops on "no version line found".
        re.compile(r'^version = "(?P<value>\d+\.\d+\.\d+)"\r?$', re.MULTILINE),
        str,
    ),
    VersionFile(
        "server/nexus_server/__init__.py",
        re.compile(r'^__version__ = "(?P<value>\d+\.\d+\.\d+)"\r?$', re.MULTILINE),
        str,
    ),
    VersionFile(
        "android/app/build.gradle.kts",
        # The suffix is optional because the file ships "2.0" today; anything the
        # release writes from here on is the full three-part version.
        re.compile(r'^[ \t]*versionName = "(?P<value>\d+\.\d+(?:\.\d+)?)"\r?$', re.MULTILINE),
        str,
    ),
    VersionFile(
        "android/app/build.gradle.kts",
        re.compile(r"^[ \t]*versionCode = (?P<value>\d+)\r?$", re.MULTILINE),
        lambda v: str(android_version_code(v)),
        is_version=False,
    ),
)


def current_version(root: Path) -> Version:
    """The version the three declaring files agree on, or a refusal to guess."""
    found = {f.relative: f.read(root) for f in VERSION_FILES if f.is_version}
    # android/app/build.gradle.kts is allowed to carry "2.0" for "2.0.0" — that is
    # what it says today, and a two-part versionName is perfectly normal Android.
    normalised = {
        name: value if value.count(".") == 2 else f"{value}.0"
        for name, value in found.items()
    }
    distinct = set(normalised.values())
    if len(distinct) != 1:
        detail = ", ".join(f"{name}: {value}" for name, value in sorted(found.items()))
        raise StepFailed(f"the version files disagree — {detail}")
    return Version.parse(distinct.pop())


def check_version_code(root: Path, version: Version) -> None:
    """The Android upgrade path, checked before anything is written."""
    entry = next(f for f in VERSION_FILES if not f.is_version)
    old = int(entry.read(root))
    new = android_version_code(version)
    if new <= old:
        raise StepFailed(
            f"versionCode would go from {old} to {new} — Android refuses to "
            "install an upgrade whose code did not increase"
        )


# --- git --------------------------------------------------------------------

def git(*args: str, root: Path = ROOT) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise StepFailed(f"cannot run git: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise StepFailed(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{detail[-1] if detail else 'no output'}"
        )
    return result.stdout


def dirty_files(root: Path = ROOT) -> list[str]:
    """``git status --porcelain`` as lines, untracked files included."""
    return [line for line in git("status", "--porcelain", root=root).splitlines() if line.strip()]


def current_branch(root: Path = ROOT) -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD", root=root).strip()


def tag_exists(tag: str, root: Path = ROOT) -> bool:
    return bool(git("tag", "--list", tag, root=root).strip())


def remote_tag_exists(tag: str, root: Path = ROOT) -> bool:
    """Whether the tag is already on the remote — a warning, never a hard stop.

    This is the only step that needs the network, and being offline is not a
    reason to refuse to prepare a release: the push would fail loudly anyway.
    """
    try:
        return bool(git("ls-remote", "--tags", "origin", tag, root=root).strip())
    except StepFailed as exc:
        log(f"could not ask the remote about {tag} ({exc}) — continuing")
        return False


def release_url(root: Path = ROOT) -> str | None:
    """The releases page of ``origin``, for the line printed at the end."""
    try:
        url = git("remote", "get-url", "origin", root=root).strip()
    except StepFailed:
        return None
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$", url)
    if not match:
        return None
    return f"https://github.com/{match['owner']}/{match['repo']}/releases"


# --- prompts ----------------------------------------------------------------

def ask(question: str, *, assume_yes: bool = False) -> bool:
    if assume_yes:
        log(f"{question} — yes (--yes)")
        return True
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        # No one is there to answer. A release is not something to do by default.
        print()
        return False
    return answer in {"y", "yes", "t", "tak"}


def ask_for_version(version: Version, *, assume_yes: bool = False) -> bool:
    """The last gate, deliberately not a keystroke.

    Everything after this point is public: a pushed tag builds and publishes a
    release under this number, and taking one back means deleting a tag other
    people may already have fetched. Typing the version is cheap next to that.
    """
    if assume_yes:
        log(f"releasing {version} without asking (--yes)")
        return True
    try:
        answer = input(f'Type "{version}" to release it, anything else to stop: ').strip()
    except EOFError:
        print()
        return False
    return answer == str(version)


# --- the run ----------------------------------------------------------------

def plan(
    version: Version,
    args: argparse.Namespace,
    dirty: list[str],
    include_dirty: bool,
    undecided: bool = False,
) -> None:
    log(f"about to release {version}, tag {version.tag}")
    for entry in VERSION_FILES:
        log(f"  edit {entry.relative}: {entry.read(ROOT)} -> {entry.render(version)}")
    if args.no_build:
        log("  build locally: no (--no-build) — the workflow will be the first build")
    else:
        log("  build locally: tests, Windows executable and both APKs into release/")
    if dirty:
        # `undecided` is the dry run: nobody has been asked yet, and pretending
        # the answer is "no" would print a plan that a real run never follows.
        outcome = (
            "you will be asked whether to commit them too; saying no stops the release"
            if undecided
            else ("committed with the version bump" if include_dirty else "NOT included")
        )
        log(f"  {len(dirty)} uncommitted change(s): {outcome}")
    log(f"  commit and tag {version.tag}")
    log("  push the branch and the tag" if not args.no_push else "  push: no (--no-push)")
    if not args.no_push:
        log("  GitHub then builds and publishes the release for that tag")


def rewrite_all(version: Version) -> dict[Path, str]:
    """Write the new version everywhere, returning the originals for a rollback."""
    originals = {
        entry.path(ROOT): read_source(entry.path(ROOT), entry.relative)
        for entry in VERSION_FILES
    }
    for entry in VERSION_FILES:
        entry.rewrite(ROOT, version)
        log(f"{entry.relative} -> {entry.render(version)}")
    return originals


def restore(originals: dict[Path, str]) -> None:
    for path, text in originals.items():
        try:
            write_source(path, text)
        except OSError as exc:  # pragma: no cover - the disk went away mid-release
            log(f"could not restore {path}: {exc} — check `git diff`")


def build(args: argparse.Namespace) -> None:
    """Build exactly what the tag will build, before the tag exists."""
    build_args: list[str] = []
    if args.skip_tests:
        build_args.append("--skip-tests")
    log("building locally — this is the slow part")
    if build_release.main(build_args) != 0:
        raise StepFailed("the local build failed — nothing was committed or tagged")


def commit_and_tag(version: Version, include_dirty: bool) -> None:
    """Commit the bump and tag it, saying where it stopped if it cannot.

    Every failure from here on leaves the repository changed. A hook can reject
    the commit, a tag can already exist, the remote can refuse the branch — and
    the message has to say what is on disk now and how to either finish the job
    or put it back, because the state is halfway through something a person did
    not watch.
    """
    files = " ".join(sorted({f.relative for f in VERSION_FILES}))
    if include_dirty:
        git("add", "-A")
    else:
        for entry in {f.relative for f in VERSION_FILES}:
            git("add", "--", entry)

    # Polish commit messages, English code — the project's convention.
    try:
        git("commit", "-m", f"Wersja {version}")
    except StepFailed as exc:
        raise StepFailed(
            f"{exc}\n"
            f"        nothing was committed, and the version files now say {version} "
            f"and are staged.\n"
            f"        Undo with: git restore --staged --worktree {files}"
        ) from exc

    try:
        git("tag", "-a", version.tag, "-m", f"Nexus Controller {version}")
    except StepFailed as exc:
        raise StepFailed(
            f"{exc}\n"
            f"        the version bump IS committed (it is HEAD) but not tagged, so "
            f"nothing will be published.\n"
            f"        Finish with: git tag -a {version.tag} -m \"Nexus Controller "
            f"{version}\" && git push origin HEAD {version.tag}\n"
            f"        Undo with:   git reset --hard HEAD~1"
        ) from exc

    log(f"committed and tagged {version.tag}")


def push(version: Version) -> None:
    branch = current_branch()
    try:
        git("push", "origin", branch)
    except StepFailed as exc:
        raise StepFailed(
            f"{exc}\n"
            f"        nothing was published. The commit and the tag {version.tag} exist "
            f"here and nowhere else.\n"
            f"        Finish with: git push origin {branch} && git push origin {version.tag}\n"
            f"        Undo with:   git tag -d {version.tag} && git reset --hard HEAD~1"
        ) from exc

    # Separately, and last: a tag pushed alongside a branch that the remote
    # rejects would trigger a release build for a commit nobody can see.
    try:
        git("push", "origin", version.tag)
    except StepFailed as exc:
        raise StepFailed(
            f"{exc}\n"
            f"        the branch is pushed but the tag is not, so nothing is building "
            f"— the bump is public, the release is not.\n"
            f"        Finish with: git push origin {version.tag}\n"
            f"        Abandon with: git tag -d {version.tag}  (the commit stays pushed)"
        ) from exc

    log(f"pushed {branch} and {version.tag}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "bump",
        help="major, minor, patch, or an explicit version such as 2.1.0",
    )
    parser.add_argument("--no-build", action="store_true",
                        help="do not build locally before tagging")
    parser.add_argument("--skip-tests", action="store_true",
                        help="build without running the test suites and Android lint")
    parser.add_argument("--no-push", action="store_true",
                        help="commit and tag, but do not push (nothing is published)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and change nothing")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask anything (for scripts); a dirty tree still stops the run")
    args = parser.parse_args(argv)

    #: Set as soon as either becomes true, because the interrupt handler below
    #: has to know how far this got to say anything useful about it.
    originals: dict[Path, str] | None = None
    committed = False

    try:
        git("rev-parse", "--git-dir")

        old = current_version(ROOT)
        new = (
            old.bumped(args.bump) if args.bump in PARTS else Version.parse(args.bump)
        )
        if new <= old:
            raise StepFailed(f"{new} is not newer than the current {old}")
        check_version_code(ROOT, new)

        if tag_exists(new.tag):
            raise StepFailed(f"tag {new.tag} already exists here — pick another version")
        if remote_tag_exists(new.tag):
            raise StepFailed(f"tag {new.tag} is already on origin — it has been released")

        branch = current_branch()
        if branch != "main":
            # --yes answers this one, and not the dirty-tree question below, on
            # purpose. The branch is the caller's own choice, visible in the
            # command they ran and in this line, and the worst it produces is a
            # tag on a commit they picked — deletable. Saying yes to the other
            # question commits content nobody has looked at, and publishes it.
            log(f"NOTE: on branch {branch}, not main")
            if not ask("Release from this branch anyway?", assume_yes=args.yes):
                raise StepFailed("stopped — switch to main and run again")

        dirty = dirty_files()
        include_dirty = False
        if dirty:
            log(f"the working tree is not clean — {len(dirty)} entry/entries:")
            for line in dirty[:20]:
                log(f"  {line}")
            if len(dirty) > 20:
                log(f"  … and {len(dirty) - 20} more")
            # Nothing below happens in a dry run: neither the refusal nor the
            # question. Refusing before the plan is printed turned --dry-run on a
            # dirty tree — much the commonest way to run it — into the one case
            # that shows nothing at all.
            if args.yes and not args.dry_run:
                # --yes means "do not ask me", not "sweep whatever is lying
                # around into the release". Answering this particular question
                # with yes runs `git add -A`, so an unattended run would tag and
                # publish a half-finished edit, a stray scratch file or a config
                # someone was in the middle of — and the tag is public before
                # anyone looks. The only safe unattended answer is to stop.
                raise StepFailed(
                    "the working tree is not clean and --yes will not commit changes it was "
                    "not shown — commit or stash them first, or run without --yes to be asked"
                )
            if not args.dry_run:
                # Always asked: --yes has already been turned away above.
                include_dirty = ask("Commit ALL of these together with the version bump?")
                if not include_dirty:
                    # Leaving them behind would tag a tree that differs from the
                    # one just built and tested, which is the point of the check.
                    raise StepFailed(
                        "stopped — commit or stash your changes first, then run again"
                    )

        plan(new, args, dirty, include_dirty, undecided=args.dry_run and bool(dirty))
        if args.dry_run:
            log("dry run — nothing was changed")
            return 0
        if not ask_for_version(new, assume_yes=args.yes):
            log("stopped — nothing was changed")
            return 1

        originals = rewrite_all(new)
        try:
            if not args.no_build:
                build(args)
        except StepFailed:
            restore(originals)
            raise

        commit_and_tag(new, include_dirty)
        committed = True
        if args.no_push:
            log(f"not pushed (--no-push) — `git push origin HEAD {new.tag}` when ready")
            return 0
        push(new)
    except StepFailed as exc:
        log(f"ABORTED — {exc}")
        return 1
    except KeyboardInterrupt:
        # Ctrl-C during the build is the likeliest way this ever ends early, and
        # by then every version file says the new number. Leaving them like that
        # without a word is how a later, unrelated commit carries a version bump
        # nobody decided to make.
        log("interrupted")
        if committed:
            log(f"NOTE: {new.tag} is committed and tagged here but not pushed.")
            log(f"      Finish with: git push origin HEAD {new.tag}")
            log(f"      Undo with:   git tag -d {new.tag} && git reset --hard HEAD~1")
        elif originals is not None:
            restore(originals)
            log("the version files were put back")
        return 130

    url = release_url()
    log(f"released {new} — GitHub is building it now")
    if url:
        log(f"watch it at {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
