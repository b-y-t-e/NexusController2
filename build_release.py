#!/usr/bin/env python
"""Build everything a release ships: the Windows executable and the Android APK.

    .venv\\Scripts\\python build_release.py

Runs the same steps as ``.github/workflows/release.yml``, in the same order, and
collects the results into ``release/`` next to a ``SHA256SUMS.txt`` covering all
of them — so a local build produces the exact set of files a tag would.

Two APKs are produced: ``NexusController.apk`` for Android 9 and newer, and
``NexusController-legacy.apk``, which installs back to Android 5.

    --skip-tests     build without running the test suites and Android lint
    --exe-only       Windows executable only, skip Android
    --apk-only       Android APK only, skip the executable
    --no-driver      do not bundle the ViGEmBus installer into the executable
    --debug-apk      debug instead of release builds (see below)

The APK is a signed *release* build. Signing needs the key — from
``android/keystore.properties`` or from ``NEXUS_KEYSTORE`` and friends in the
environment — and without it Gradle emits ``app-*-release-unsigned.apk``, which
no phone will install; that is an error here, not a warning.

``--debug-apk`` builds the debug variant instead, for trying the pipeline without
the key. Do not ship one: debug APKs are signed with the throwaway
``~/.android/debug.keystore``, so the phone treats each build as a different app
and refuses to update in place.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"          # PyInstaller's own output, an intermediate here
RELEASE = ROOT / "release"    # the finished set of files, ready to upload
ANDROID = ROOT / "android"

EXE_NAME = "NexusController.exe" if sys.platform == "win32" else "NexusController"
# Absolute, deliberately: on Windows a bare "gradlew.bat" is resolved against the
# *parent's* directory, not the cwd we hand to subprocess, so it is not found.
GRADLEW = ANDROID / ("gradlew.bat" if sys.platform == "win32" else "gradlew")
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
#: Prefix that marks the probe's own line in :func:`check_modules`, so its answer
#: can be told apart from anything else the interpreter decides to print.
PROBE_MARKER = "nexus-modules:"
#: What the build genuinely cannot proceed without, and how to get it.
REQUIRED_MODULES = {
    "pytest": "requirements-dev.txt",
    "pytest_timeout": "requirements-dev.txt",   # pyproject passes --timeout
    "PyInstaller": "pyinstaller",
}


def log(message: str) -> None:
    print(f"[release] {message}", flush=True)


def interpreter() -> str:
    """The Python to build with — the project venv, whoever launched us.

    Running ``python build_release.py`` instead of ``.venv\\Scripts\\python
    build_release.py`` is an easy mistake and the failure is baffling: the system
    Python usually has *some* pytest, so the suite starts and then dies on an
    option (``--timeout``) belonging to a plugin only the venv has. Rather than
    explain that, use the venv when it is there.
    """
    if VENV_PYTHON.is_file() and VENV_PYTHON.resolve() != Path(sys.executable).resolve():
        log(f"using the project venv: {VENV_PYTHON}")
        return str(VENV_PYTHON)
    return sys.executable


def check_modules(python: str, needed: dict[str, str]) -> None:
    """Fail early, and by name, when the build environment is incomplete."""
    # Marked, because stdout is not ours alone: a sitecustomize, a conda banner or
    # a warning printed by the interpreter itself all land here, and an unmarked
    # answer would be indistinguishable from that noise. Anything else is ignored.
    probe = (
        "import importlib.util,sys;"
        f"missing=[m for m in {sorted(needed)!r} if importlib.util.find_spec(m) is None];"
        f"print('{PROBE_MARKER}' + ','.join(missing))"
    )
    try:
        result = subprocess.run(
            [python, "-c", probe], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise StepFailed(f"cannot run {python}: {exc}") from exc
    if result.returncode != 0:
        # An empty stdout from a probe that crashed reads exactly like "nothing
        # is missing", so the build would sail past the check and fail later,
        # somewhere far less informative. Say what actually happened.
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise StepFailed(
            f"{python} could not be asked what it has installed "
            f"(exit {result.returncode}): {detail[-1] if detail else 'no output'}"
        )
    answers = [
        line[len(PROBE_MARKER):]
        for line in result.stdout.splitlines()
        if line.startswith(PROBE_MARKER)
    ]
    if not answers:
        raise StepFailed(
            f"{python} ran but did not answer what it has installed — "
            f"expected a line starting with {PROBE_MARKER!r}"
        )
    # needed.get, not needed[]: the names come from another process's stdout, and
    # a KeyError here would be a traceback instead of the sentence this function
    # exists to print. Anything unrecognised is reported as itself.
    missing = [name for name in answers[-1].split(",") if name]
    if missing:
        sources = sorted({needed.get(name, "the build environment") for name in missing})
        raise StepFailed(
            f"{python} is missing {', '.join(missing)} — "
            f"install {' and '.join(sources)} into it, or run this script with "
            f"{VENV_PYTHON}"
        )


def run(command: list[str], *, cwd: Path, what: str) -> None:
    """Run a build step, raising :class:`StepFailed` on a non-zero exit."""
    log(f"{what}: {' '.join(command)}")
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=cwd, check=False)
    except OSError as exc:
        raise StepFailed(f"{what} could not start: {exc}") from exc
    if result.returncode != 0:
        raise StepFailed(f"{what} failed (exit {result.returncode})")
    log(f"{what} ok — {time.monotonic() - started:.1f}s")


class StepFailed(RuntimeError):
    """A build step exited non-zero; the message is already user-facing."""


# --- steps ------------------------------------------------------------------

def test_server(python: str) -> None:
    run([python, "-m", "pytest", "-q"], cwd=ROOT, what="server tests")


def test_android() -> None:
    # One flavour is enough for the unit tests: they are the same code, built
    # against the same SDK. Lint is the opposite case — see below.
    run(
        [str(GRADLEW), "--no-daemon", "testModernDebugUnitTest"],
        cwd=ANDROID,
        what="Kotlin tests",
    )


def lint_android() -> None:
    """Both flavours, because this is the only check that tells them apart.

    ``modern`` starts at API 28 and ``legacy`` at 21, and a call to an API newer
    than the minimum compiles cleanly, passes every unit test and works on the
    phone in your hand. On an older one it throws ``NoClassDefFoundError`` —
    an ``Error``, so the surrounding ``catch (e: Exception)`` never sees it and
    the app simply dies. Lint is what knows the API level of every call.
    """
    run(
        [str(GRADLEW), "--no-daemon", "lintModernDebug", "lintLegacyDebug"],
        cwd=ANDROID,
        what="Android lint (both flavours)",
    )


def build_exe(python: str, no_driver: bool) -> Path:
    command = [python, str(ROOT / "tools" / "build_exe.py")]
    if no_driver:
        command.append("--no-driver")
    run(command, cwd=ROOT, what="Windows executable")

    produced = DIST / EXE_NAME
    if not produced.is_file():
        raise StepFailed(f"expected {produced} but it is not there")
    return produced


#: The certificate every released APK must carry, as apksigner prints it.
#:
#: The public half of the release key. Not a secret — a certificate is what the
#: phone already has — and pinning it is what turns "signed by something" into
#: "signed by the key every installed copy expects". The workflow reads this same
#: constant rather than keeping its own copy, because two pins that can disagree
#: are worse than one.
RELEASE_CERT_SHA256 = "13288e7aad983ddcb4e06a48862e1bbf3efcc7c3c76a613f2cec7c5d68c72feb"


def _sdk_roots() -> list[Path]:
    """Where an Android SDK is likely to be, most explicit first."""
    roots = []
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Android" / "Sdk")
    roots.append(Path.home() / "AppData" / "Local" / "Android" / "Sdk")
    roots.append(Path.home() / "Android" / "Sdk")
    return roots


def _version_key(directory: Path) -> tuple:
    """Sort build-tools by version, not alphabetically.

    Plain text order puts "9.0.0" above "35.0.0", which is how a check ends up
    running the oldest tool on the machine — or, in a container that only has the
    new one, not running at all.
    """
    return tuple(int(part) if part.isdigit() else -1 for part in directory.name.split("."))


def apksigner() -> Path:
    """The newest ``apksigner`` in the SDK, or a message saying where to get one."""
    names = ("apksigner.bat", "apksigner") if sys.platform == "win32" else ("apksigner",)
    for root in _sdk_roots():
        build_tools = root / "build-tools"
        if not build_tools.is_dir():
            continue
        for directory in sorted(build_tools.iterdir(), key=_version_key, reverse=True):
            for name in names:
                candidate = directory / name
                if candidate.is_file():
                    return candidate
    raise StepFailed(
        "cannot find apksigner in any Android SDK (looked under ANDROID_HOME, "
        "ANDROID_SDK_ROOT and %LOCALAPPDATA%\\Android\\Sdk) — it is what proves "
        "an APK carries the release key, so a release cannot be built without it"
    )


def certificate_of(apk: Path, tool: Path | None = None) -> str:
    """The SHA-256 digest of the certificate that signed *apk*."""
    tool = tool or apksigner()
    try:
        result = subprocess.run(
            [str(tool), "verify", "--print-certs", str(apk)],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise StepFailed(f"cannot run {tool}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise StepFailed(
            f"{apk.name} did not verify (exit {result.returncode}): "
            f"{detail[-1] if detail else 'no output'}"
        )
    marker = "certificate SHA-256 digest: "
    for line in result.stdout.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip().lower()
    raise StepFailed(f"apksigner said nothing about {apk.name}'s certificate")


def verify_signatures(artefacts: list[tuple[Path, str]]) -> None:
    """Refuse to ship an APK signed by anything but the release key.

    Until this existed the only pin was in the workflow, which runs *after* a tag
    is pushed — so the local ``release/`` directory, the one a person is most
    likely to hand round, was certified by its own SHA256SUMS.txt and by nothing
    else. It really did fill up with debug-signed APKs under release names.
    """
    apks = [(source, name) for source, name in artefacts if source.suffix == ".apk"]
    if not apks:
        return
    tool = apksigner()
    for source, name in apks:
        digest = certificate_of(source, tool)
        if digest != RELEASE_CERT_SHA256:
            raise StepFailed(
                f"{name} is signed by {digest}, not by the release key "
                f"({RELEASE_CERT_SHA256}). An APK signed by a different key cannot "
                "update an installed copy — Android refuses it — so this one must "
                "not be shipped. Check which keystore the build used"
            )
    log(f"signature ok — {len(apks)} APK(s) carry the release certificate")


#: The two APKs a release ships, newest-phone first.
#: ``modern`` is the one to install; ``legacy`` reaches back to Android 5 for
#: phones that cannot run it — which, for a room of four Buzz buzzers, is exactly
#: the drawer the spare phones come from.
APK_FLAVOURS = (("modern", "NexusController.apk"), ("legacy", "NexusController-legacy.apk"))


def build_apks(debug_build: bool = False) -> list[tuple[Path, str]]:
    """Build every flavour in one Gradle run and return ``(file, release name)``."""
    variant = "Debug" if debug_build else "Release"
    tasks = [f"assemble{flavour.capitalize()}{variant}" for flavour, _ in APK_FLAVOURS]
    # Clean, and with the build cache off. An incremental build once produced an
    # APK whose dex was missing a class that had not changed — Gradle reported
    # BUILD SUCCESSFUL and the app died on launch with ClassNotFoundException.
    # A release is not the place to find out that the cache was lying.
    run(
        [str(GRADLEW), "--no-daemon", "--no-build-cache", "clean", *tasks],
        cwd=ANDROID,
        what=f"Android APKs ({', '.join(f for f, _ in APK_FLAVOURS)})",
    )
    # A debug build is named apart, always. Under the release name it is
    # indistinguishable from the real thing in `release/` — same file name, same
    # SHA256SUMS.txt line — and the only way anyone finds out is a phone refusing
    # to update, months later.
    return [
        (_pick_apk(flavour, variant.lower()), _debug_name(name) if debug_build else name)
        for flavour, name in APK_FLAVOURS
    ]


def _debug_name(name: str) -> str:
    stem, _, suffix = name.rpartition(".")
    return f"{stem}-debug.{suffix}"


def _pick_apk(flavour: str, variant: str) -> Path:
    outputs = ANDROID / "app" / "build" / "outputs" / "apk" / flavour / variant
    candidates = sorted(outputs.glob("*.apk"))
    if not candidates:
        raise StepFailed(f"no APK in {outputs}")

    # "-" sorts before ".", so plain alphabetical order would hand back
    # app-<flavour>-release-unsigned.apk over app-<flavour>-release.apk. Only one
    # of the two can be here now that every build starts with `clean`, but which
    # one is exactly the question being asked, and answering it by sort order was
    # how an unsigned APK got picked in the first place.
    signed = [c for c in candidates if "unsigned" not in c.name]
    if signed:
        return signed[0]

    # Nothing signed. For a release that is the end of it: an unsigned APK cannot
    # be installed, and shipping one means the release is discovered to be broken
    # by whoever downloads it. The cause is always the same — Gradle could not
    # find the key — so name the two places it looks.
    if variant == "release":
        raise StepFailed(
            f"the {flavour} release APK is unsigned — Gradle found no signing key. "
            "Point android/keystore.properties at it, or set NEXUS_KEYSTORE, "
            "NEXUS_KEYSTORE_PASSWORD, NEXUS_KEY_ALIAS and NEXUS_KEY_PASSWORD"
        )
    return candidates[0]


# --- collection -------------------------------------------------------------

def remove(path: Path) -> None:
    """Delete a previous artefact, tolerating a Windows lock that lets go.

    A freshly built executable is often still held for a moment — by the virus
    scanner reading it, or by a copy of the app the user left running. The first
    is over in a second; the second never is, so say which one to go and close.
    """
    for attempt in range(4):
        try:
            path.unlink()
            return
        except PermissionError:
            if attempt == 3:
                raise StepFailed(
                    f"{path} is in use and cannot be replaced — close anything "
                    "running it (a previous NexusController.exe) and try again"
                ) from None
            time.sleep(1.0)
        except FileNotFoundError:
            return


def collect(artefacts: list[tuple[Path, str]]) -> None:
    """Copy the built files under their release names and checksum them.

    Only the files this run actually produced are replaced. Wiping the directory
    would mean ``--exe-only`` silently deleted the APK from the previous run,
    leaving a half-release behind — and the checksum file has to describe
    whatever ends up next to it, not just this run's half.
    """
    RELEASE.mkdir(parents=True, exist_ok=True)

    # One artefact that cannot be replaced must not discard the others. The exe
    # is routinely locked by a copy of the app the user is running, and giving up
    # there made a perfectly good APK look like it had never been built.
    failures: list[str] = []
    for source, name in artefacts:
        target = RELEASE / name
        try:
            remove(target)
            shutil.copy2(source, target)
        except (StepFailed, OSError) as exc:
            failures.append(f"{name}: {exc}")
            log(f"{name} BUILT but not copied — {exc}")
            log(f"      it is waiting in {source}")
            continue
        log(f"{name} — {target.stat().st_size / 1_048_576:.1f} MiB")

    checksums = RELEASE / "SHA256SUMS.txt"
    built = {name for _, name in artefacts}
    present = [f for f in sorted(RELEASE.iterdir()) if f.is_file() and f != checksums]

    # A checksum file says "these are the release". Anything left from an earlier
    # run may have been built from a different commit, and nothing in the file
    # would reveal that — so say it out loud rather than certify it silently.
    stale = [f.name for f in present if f.name not in built]
    if stale:
        log(f"NOTE: kept from an earlier build, not rebuilt now: {', '.join(stale)}")
        log("      run without --exe-only/--apk-only for a release built in one go")
        _warn_about_foreign_apks(f for f in present if f.name in stale)

    if failures:
        # No checksum file at all. It says "these are the release", and this
        # directory is not one: an artefact of this run sits next to whatever was
        # there before. Better no answer than a confident wrong one.
        remove(checksums)
        log("SHA256SUMS.txt not written — the release is incomplete")
    else:
        lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in present]
        # newline="\n" deliberately: with Windows CRLF, "sha256sum -c" reads the
        # \r as part of the file name and fails to open anything.
        checksums.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    if failures:
        raise StepFailed(
            "everything was built, but could not be put in place — "
            + "; ".join(failures)
        )


def _warn_about_foreign_apks(files) -> None:
    """Say so when an APK kept from an earlier run is not the release key's.

    The one that is about to be listed in SHA256SUMS.txt beside freshly signed
    files, under a release name, with nothing to distinguish it. Only a warning:
    these are not this run's artefacts and deleting somebody's files is not this
    script's business — but certifying them silently is exactly how the directory
    ended up full of debug builds.
    """
    for path in files:
        if path.suffix != ".apk":
            continue
        try:
            digest = certificate_of(path)
        except StepFailed as exc:
            log(f"      cannot check {path.name}: {exc}")
            continue
        if digest != RELEASE_CERT_SHA256:
            log(f"      WARNING: {path.name} is signed by {digest[:16]}…, not the release key")
            log("      it cannot update an installed copy — rebuild or delete it")


def check_tooling(want_apk: bool) -> None:
    if want_apk and not GRADLEW.is_file():
        raise StepFailed(f"{GRADLEW} is missing")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--skip-tests", action="store_true",
                        help="do not run the test suites or Android lint")
    parser.add_argument("--no-driver", action="store_true", help="do not bundle ViGEmBus")
    parser.add_argument("--debug-apk", action="store_true",
                        help="assembleDebug instead of assembleRelease — never ship one")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--exe-only", action="store_true", help="skip the Android build")
    target.add_argument("--apk-only", action="store_true", help="skip the Windows build")
    args = parser.parse_args(argv)

    want_exe = not args.apk_only
    want_apk = not args.exe_only
    started = time.monotonic()

    try:
        check_tooling(want_apk)
        python = interpreter()
        if want_exe:
            needed = dict(REQUIRED_MODULES)
            if args.skip_tests:
                needed = {"PyInstaller": REQUIRED_MODULES["PyInstaller"]}
            check_modules(python, needed)

        if not args.skip_tests:
            if want_exe:
                test_server(python)
            if want_apk:
                test_android()
                lint_android()

        artefacts: list[tuple[Path, str]] = []
        if want_exe:
            artefacts.append((build_exe(python, args.no_driver), EXE_NAME))
        if want_apk:
            built_apks = build_apks(args.debug_apk)
            # Before anything is copied under a release name, and only for a real
            # release: a debug build cannot carry this certificate and is not
            # pretending to.
            if not args.debug_apk:
                verify_signatures(built_apks)
            artefacts.extend(built_apks)

        collect(artefacts)
    except StepFailed as exc:
        log(f"ABORTED — {exc}")
        return 1
    except KeyboardInterrupt:
        log("interrupted")
        return 130

    log(f"done in {time.monotonic() - started:.0f}s — files in {RELEASE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
