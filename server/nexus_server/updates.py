"""Finding a newer release on GitHub, and putting it in place of this build.

Pure where it can be. Everything that *decides* — is that version newer, which
asset belongs to this build, does the download match its checksum — takes data
and returns data, so the whole decision path is tested without a socket. The four
functions that touch the network or the disk take their opener as an argument for
the same reason.

The replacement itself is the one genuinely Windows-shaped part. A running
executable cannot be overwritten or deleted, but it *can* be renamed, so the swap
is: write the new build beside the old one, rename the old one out of the way,
move the new one into its place, start it, quit. The leftover is deleted on the
next start, when nothing holds it any more. Settings are in ``%APPDATA%`` and
none of this touches them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from . import __version__

log = logging.getLogger(__name__)

REPO = "b-y-t-e/NexusController2"
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
#: Every asset of a genuine release of ours is served from under this prefix.
#: Checked before anything is fetched: the URL comes out of a JSON document, and
#: nothing else in it decides where the bytes we are about to *run* come from.
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"

#: The Windows build, and the file listing what every asset should hash to.
ASSET_NAME = "NexusController.exe"
CHECKSUMS_NAME = "SHA256SUMS.txt"

USER_AGENT = f"NexusController/{__version__}"
#: The API answer is small; the executable is tens of megabytes. Both are capped
#: so a wrong or hostile URL cannot stream into memory until the machine dies.
MAX_JSON_BYTES = 1 << 20
MAX_ASSET_BYTES = 300 << 20

Opener = Callable[..., Any]


class UpdateError(RuntimeError):
    """Something about the release or the download is wrong; message is for the UI."""


# --- pure: what the release says --------------------------------------------

def parse_version(text: str) -> tuple[int, int, int] | None:
    """``"v2.1.0"``, ``"2.1"`` and ``"2.1.0-legacy"`` all read as a version."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip().lstrip("vV")
    # A suffix is not part of the number: the legacy flavour appends "-legacy" to
    # its versionName, and a release candidate would append something too.
    cleaned = cleaned.split("-", 1)[0].split("+", 1)[0]
    parts = cleaned.split(".")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    numbers = [int(p) for p in parts] + [0, 0]
    return (numbers[0], numbers[1], numbers[2])


def is_newer(current: str, candidate: str) -> bool:
    """Whether ``candidate`` is a version worth offering over ``current``.

    Numeric, never lexicographic: "2.10.0" sorts before "2.9.0" as text, which
    would offer users a downgrade and then offer it again forever.
    """
    here, there = parse_version(current), parse_version(candidate)
    if here is None or there is None:
        return False
    return there > here


@dataclass(frozen=True)
class Release:
    tag: str
    #: ``{asset name: download URL}``, only for URLs that passed the prefix check.
    assets: Mapping[str, str]
    notes: str = ""

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV")

    def url(self, name: str) -> str | None:
        return self.assets.get(name)


def parse_release(document: Any) -> Release:
    """Turn the GitHub API's answer into a :class:`Release`, or refuse it."""
    if not isinstance(document, dict):
        raise UpdateError("the release API answered with something that is not a release")
    tag = document.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None:
        raise UpdateError(f"release has no usable version tag: {tag!r}")

    assets: dict[str, str] = {}
    for asset in document.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name, url = asset.get("name"), asset.get("browser_download_url")
        # Anything served from somewhere else is dropped here rather than at
        # download time, so no later step has to remember to look.
        if isinstance(name, str) and isinstance(url, str) and url.startswith(DOWNLOAD_PREFIX):
            assets[name] = url
    notes = document.get("body") if isinstance(document.get("body"), str) else ""
    return Release(tag=tag, assets=assets, notes=notes or "")


def parse_checksums(text: str) -> dict[str, str]:
    """Read ``SHA256SUMS.txt`` — ``<hex>  <name>`` per line — into a mapping."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and len(parts[0]) == 64:
            # "*name" is how sha256sum marks binary mode; the name is what matters.
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def verify(payload: bytes, name: str, checksums: str) -> None:
    """Check a download against the release's own list, or refuse to use it.

    This is not a defence against a compromised release — the list ships beside
    the file it describes — but it is the one that catches what actually happens:
    a truncated download, a proxy serving an error page, a half-written file. The
    payload is about to replace the running program, so "probably fine" is not a
    standard it can be held to.
    """
    expected = parse_checksums(checksums).get(name)
    if expected is None:
        raise UpdateError(f"{CHECKSUMS_NAME} in that release does not mention {name}")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise UpdateError(
            f"{name} does not match its checksum — the download is corrupt or tampered with"
        )


# --- I/O: fetching ----------------------------------------------------------

def _read(url: str, *, opener: Opener, timeout: float, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener(request, timeout=timeout) as response:  # noqa: S310 - URL checked below
        # read(limit + 1), so a body exactly at the limit is still detectable as
        # oversized rather than silently truncated into something that then fails
        # its checksum for no visible reason.
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise UpdateError(f"{url} is larger than expected — refusing it")
    return payload


def fetch_latest(
    *, opener: Opener = urllib.request.urlopen, timeout: float = 10.0
) -> Release | None:
    """The latest published release, or ``None`` when there is none to have.

    ``None`` is not an error and is the answer in two ordinary situations: a
    repository with no releases yet answers 404, and a machine with no network
    answers not at all. Neither is worth a dialog — this runs unprompted at
    start-up, in a tool whose whole point is working on a LAN with the cable to
    the world unplugged.
    """
    try:
        payload = _read(RELEASE_API, opener=opener, timeout=timeout, limit=MAX_JSON_BYTES)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise UpdateError(f"the release API answered {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"could not reach GitHub: {exc}") from exc

    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"the release API answered with unreadable JSON: {exc}") from exc
    return parse_release(document)


def download(
    release: Release,
    name: str = ASSET_NAME,
    *,
    opener: Opener = urllib.request.urlopen,
    timeout: float = 120.0,
) -> bytes:
    """Fetch one asset and return it only once it matches the release's checksum."""
    url = release.url(name)
    if url is None:
        raise UpdateError(f"release {release.tag} has no {name}")
    sums_url = release.url(CHECKSUMS_NAME)
    if sums_url is None:
        raise UpdateError(f"release {release.tag} ships no {CHECKSUMS_NAME} to check against")

    try:
        checksums = _read(sums_url, opener=opener, timeout=timeout, limit=MAX_JSON_BYTES)
        payload = _read(url, opener=opener, timeout=timeout, limit=MAX_ASSET_BYTES)
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"download failed: {exc}") from exc

    verify(payload, name, checksums.decode("utf-8", "replace"))
    return payload


# --- I/O: putting it in place -----------------------------------------------

def running_executable() -> Path | None:
    """This build's ``.exe``, or ``None`` when running from source.

    Updating a source checkout would be replacing a file git is responsible for,
    so the whole feature is simply absent there — the dashboard says as much
    rather than offering a button that must not work.
    """
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def backup_for(exe: Path) -> Path:
    """Where the outgoing build is parked until the next start can delete it."""
    return exe.with_name(exe.stem + ".old" + exe.suffix)


def writable(exe: Path) -> bool:
    """Whether the swap can even be attempted, asked by doing rather than guessing.

    ``os.access`` reports the DACL and lies about the two cases that matter on
    Windows — a directory under ``Program Files`` protected by UAC virtualisation,
    and one where the ACL allows what the token does not. Creating a file is the
    only honest answer.
    """
    probe = exe.with_name(exe.name + ".writetest")
    try:
        probe.touch()
        probe.unlink()
    except OSError:
        return False
    return True


def clear_backup(exe: Path | None = None) -> bool:
    """Delete the previous build, if the last update left one. Never raises.

    Called at start-up. The file is only unlinkable once nothing runs it, which
    is exactly now — but an antivirus may still be reading it, and failing to
    remove yesterday's build is not a reason to fail to start today's.
    """
    exe = exe or running_executable()
    if exe is None:
        return False
    backup = backup_for(exe)
    try:
        backup.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.info("could not remove %s yet: %s", backup.name, exc)
        return False
    log.info("removed the previous build: %s", backup.name)
    return True


def install(payload: bytes, exe: Path) -> Path:
    """Put ``payload`` where ``exe`` is, and return where the old build went.

    Three steps, each undone if the next one fails, because the failure mode this
    guards against is the one that leaves no working executable at all: the user
    is left with an app that will not start and no way to be told why.
    """
    staged = exe.with_name(exe.name + ".new")
    backup = backup_for(exe)
    try:
        staged.write_bytes(payload)
    except OSError as exc:
        raise UpdateError(f"could not write the new build next to the old one: {exc}") from exc

    # A leftover from an update that failed halfway would make this rename fail on
    # Windows, where os.replace onto an existing file is fine but the *source*
    # being locked is not. Clear it first; it is ours either way.
    try:
        backup.unlink(missing_ok=True)
    except OSError:
        pass

    try:
        os.replace(exe, backup)          # renaming a running .exe is allowed
    except OSError as exc:
        staged.unlink(missing_ok=True)
        raise UpdateError(f"could not move the running build aside: {exc}") from exc

    try:
        os.replace(staged, exe)
    except OSError as exc:
        # Put the old build back, or there is nothing left to run.
        try:
            os.replace(backup, exe)
        except OSError:  # pragma: no cover - the disk is in real trouble
            log.exception("could not restore %s after a failed update", exe)
        staged.unlink(missing_ok=True)
        raise UpdateError(f"could not put the new build in place: {exc}") from exc

    log.info("updated %s, previous build kept as %s", exe.name, backup.name)
    return backup


def relaunch(exe: Path, *, spawn: Callable[..., Any] = subprocess.Popen) -> None:
    """Start the freshly installed build. The caller then quits."""
    try:
        # No shell, no cwd inherited from wherever the old process was started:
        # this is the one place that launches an executable by path, and it stays
        # boring on purpose.
        spawn([str(exe)], cwd=str(exe.parent), close_fds=True)
    except OSError as exc:
        raise UpdateError(f"the update is installed but could not be started: {exc}") from exc
