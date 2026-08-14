"""Finding a newer release on GitHub, and putting it in place of this build.

Pure where it can be. Everything that *decides* — is that version newer, which
asset belongs to this build, does the download match its checksum — takes data
and returns data, so the whole decision path is tested without a socket. Every
function that touches the network takes its opener as an argument for the same
reason.

The replacement itself is the one genuinely Windows-shaped part. A running
executable cannot be overwritten or deleted, but it *can* be renamed, so the swap
is: write the new build beside the old one, rename the old one out of the way,
move the new one into its place, start it, quit. The leftover is deleted on the
next start, when nothing holds it any more. Settings are in ``%APPDATA%`` and
none of this touches them.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import logging
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
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

#: Digits allowed in one part of a version. Both sides agree on it, so that a
#: number too big for Kotlin's Int is refused here too rather than compared.
MAX_VERSION_DIGITS = 9

Opener = Callable[..., Any]


class UpdateError(RuntimeError):
    """Something about the release or the download is wrong; message is for the UI."""


# --- pure: the state the dashboard renders -----------------------------------

class UpdateState:
    """What the page is told about updating, and the rules about who may write it.

    No sockets, no files, no version of its own beyond the string it is handed —
    which is why it lives here rather than in ``app.py``. The rules are the whole
    substance of this feature's correctness and every one of them was learned
    from something going wrong, so they belong somewhere a test can drive them
    directly instead of through a dashboard, a thread and a fake GitHub.

    Two writers, and they do not trust each other. A check runs in the
    background and may only answer while *its own* check is still the current
    one; an install claims the state up front and holds it. The names are here
    too, because a typo in one of them fails open — the caller proceeds where it
    should have refused — and the page compares against the same strings.
    """

    IDLE = "idle"
    CHECKING = "checking"
    #: A newer release exists. ``error`` may be set as well: an install that
    #: failed leaves the offer standing and says why the last try did not work.
    AVAILABLE = "available"
    #: Asked and answered: nothing newer. Never carries a version.
    NONE = "none"
    ERROR = "error"
    INSTALLING = "installing"
    #: The swap has happened. Terminal for this process, which is still the old
    #: build and still reports the old version — so a check from here would find
    #: the release newer than itself and offer it again, and a second install
    #: would move the *new* build aside as if it were the old one, throwing away
    #: the only copy of what was there before. Reachable while the window is
    #: open exactly when the new build could not be started.
    INSTALLED = "installed"

    #: Nothing new may be started while the state is one of these.
    BUSY = frozenset({CHECKING, INSTALLING, INSTALLED})

    #: Everything a release puts in the state, so one place can clear all of it.
    _RELEASE_FIELDS = ("latest", "tag", "has_asset")

    def __init__(self, version: str) -> None:
        self._version = version
        #: Replaced wholesale, never mutated in place, so a reader never sees a
        #: half-written answer.
        self._fields: dict = {"state": self.IDLE, "current": version}
        self._lock = threading.Lock()
        #: Which check is the current one; see :meth:`finish_check`.
        self._generation = 0

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._fields)

    def clear_error(self) -> dict:
        """Forget the last failure, because the user has said they read it.

        A state of "error" with nothing to say is not a state: everything else
        leaves the page something to render, so this one goes back to where a
        check would find it. An offer keeps standing — only the sentence goes.
        """
        with self._lock:
            if self._fields.get("state") == self.ERROR:
                return self._write(state=self.IDLE, error=None)
            return self._write(error=None)

    # -- the check ----------------------------------------------------------

    def begin_check(self) -> int | None:
        """Claim the state for a new check, or ``None`` if something else holds it."""
        with self._lock:
            if self._fields.get("state") in self.BUSY:
                return None
            self._write(state=self.CHECKING, error=None)
            self._generation += 1
            return self._generation

    def check_found(self, generation: int, release: "Release") -> None:
        """A check found something newer."""
        self._finish_check(generation, **self._offer(release), error=None)

    def check_found_nothing(self, generation: int) -> None:
        self._finish_check(generation, **self._no_release())

    def check_failed(self, generation: int, message: str) -> None:
        self._finish_check(generation, state=self.ERROR, error=message)

    def nothing_newer(self) -> dict:
        with self._lock:
            return self._write(**self._no_release())

    def failed(self, message: str) -> dict:
        with self._lock:
            return self._write(state=self.ERROR, error=message)

    # -- the install --------------------------------------------------------

    def begin_install(self) -> str | None:
        """Claim the state for an install; a string is the refusal to show."""
        with self._lock:
            state = self._fields.get("state")
            if state == self.INSTALLING:
                # Two at once would both go through the rename in
                # install_staged(), where one moves the running .exe aside while
                # the other is looking for it.
                return "An update is already being installed"
            if state == self.INSTALLED:
                return (
                    "The update is already installed — close this window and "
                    "start Nexus Controller again"
                )
            self._write(state=self.INSTALLING, error=None)
            return None

    def install_failed(self, message: str, release: "Release | None") -> dict:
        """Record a failed install without taking the offer off the page.

        The attempt failed; the release did not go anywhere. Dropping to "error"
        left the page with nothing but a sentence — and once that was dismissed,
        nothing at all: no version, no button, and nothing that runs a check by
        itself. So the state goes back to the offer it was, and the message rides
        along in ``error``.
        """
        if release is not None and is_newer(self._version, release.version):
            with self._lock:
                return self._write(**self._offer(release), error=message)
        # Nothing was ever established about a newer build — the check itself is
        # what failed — so there is no offer to put back.
        return self.failed(message)

    def installed(self, error: str | None = None) -> dict:
        with self._lock:
            return self._write(state=self.INSTALLED, error=error)

    # -- internals ----------------------------------------------------------

    def _finish_check(self, generation: int, **fields) -> None:
        """Write the answer of check ``generation``, if it is still the current one.

        A check sets nothing it does not own. Between its start and its answer
        the user can press "Download and install", and the install owns the state
        from then on — an answer landing over ``installing`` re-enables the button
        under a download that is already running and lets a second one start on
        top of it.

        The number is why this is not simply "does the state still say checking".
        After an install has been and gone the state can be ``checking`` again for
        a *later* check, and an earlier one still in flight would otherwise answer
        in its name, with an answer from before the install ran.
        """
        with self._lock:
            if self._fields.get("state") != self.CHECKING or generation != self._generation:
                return
            # Tested and written under one acquisition, or an install could land
            # in the gap between them and be overwritten anyway.
            self._write(**fields)

    def _offer(self, release: "Release") -> dict:
        """The fields that describe an offer. One definition, every caller.

        ``has_asset`` is read off the release here and nowhere else. It used to
        arrive as an argument from the check and be computed inside the failed
        install, which is one question with two answers — and the page draws the
        install button from it.
        """
        return {
            "state": self.AVAILABLE,
            "latest": release.version,
            "tag": release.tag,
            "has_asset": release.url(ASSET_NAME) is not None,
        }

    def _no_release(self) -> dict:
        """"Nothing newer", with the release fields cleared rather than left to go
        stale — a version number from an earlier answer, attached to "none"."""
        return {"state": self.NONE, "error": None, **dict.fromkeys(self._RELEASE_FIELDS)}

    def _write(self, **fields) -> dict:
        """Replace the state. The caller holds the lock."""
        merged = dict(self._fields)
        merged.update(fields)
        merged["current"] = self._version
        self._fields = merged
        return dict(merged)


# --- pure: what the release says --------------------------------------------

def parse_version(text: str) -> tuple[int, int, int] | None:
    """``"v2.1.0"``, ``"2.1"`` and ``"2.1.0-legacy"`` all read as a version."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip().lstrip("vV")
    # A suffix is not part of the number: the legacy flavour appends "-legacy" to
    # its versionName, and a release candidate would append something too.
    cleaned = cleaned.split("-", 1)[0].split("+", 1)[0]
    # str.split never returns an empty list — "" splits to [""], which the digit
    # test below refuses — so a fourth part is the only thing to count. The
    # Kotlin side reads the same way, deliberately.
    #
    # ASCII digits and nothing else. str.isdigit() is true for "²" and int() is
    # not, so the promise to answer None rather than raise was one this could not
    # keep; Kotlin's Char.isDigit() has the same gap in the other direction —
    # toIntOrNull() reads "٣" as 3 — and both sides now spell out 0-9.
    # The length cap is the other half of that agreement: Python's int has no
    # ceiling and Kotlin's toIntOrNull() gives up above 2^31, so "9999999999.0.0"
    # is a version on one side and not on the other. Nine digits is more than any
    # real version has ever needed.
    parts = cleaned.split(".")
    if len(parts) > 3 or not all(
        p.isascii() and p.isdigit() and len(p) <= MAX_VERSION_DIGITS for p in parts
    ):
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


def required_checksum(name: str, checksums: str) -> str:
    """What the release says ``name`` must hash to, or a refusal to install it.

    Checking the download against this list is not a defence against a
    compromised release — the list ships beside the file it describes — but it is
    the one that catches what actually happens: a truncated download, a proxy
    serving an error page, a half-written file. What comes down the wire is about
    to replace the running program, so "probably fine" is not a standard it can
    be held to.
    """
    expected = parse_checksums(checksums).get(name)
    if expected is None:
        raise UpdateError(f"{CHECKSUMS_NAME} in that release does not mention {name}")
    return expected


# --- I/O: fetching ----------------------------------------------------------

class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follows redirects, but never off https.

    GitHub answers an asset URL with a redirect to its object store, so
    redirects have to be followed — and the default handler follows them
    anywhere, http included. These bytes become the running executable; a
    downgrade to plain http would put a man in the middle of that, and the
    checksum is no defence because it travels the same way. The Android client
    has refused non-https redirects from the start; this is the same rule on the
    side that has more to lose.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise UpdateError(f"refusing a redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


#: The default opener for everything in here: https in, https out.
HTTPS_OPENER: Opener = urllib.request.build_opener(_HttpsOnlyRedirects()).open


def _request(url: str) -> urllib.request.Request:
    # The scheme really is checked, which is what the suppressions on the opener
    # calls have always claimed. Every URL comes from a constant or from a
    # release document whose asset URLs were matched against DOWNLOAD_PREFIX, so
    # this is the last line of a defence rather than the first.
    if urllib.parse.urlsplit(url).scheme != "https":
        raise UpdateError(f"refusing to fetch {url} — only https is allowed")
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def _read(url: str, *, opener: Opener, timeout: float, limit: int) -> bytes:
    request = _request(url)
    with opener(request, timeout=timeout) as response:  # noqa: S310 - scheme checked above
        # read(limit + 1), so a body exactly at the limit is still detectable as
        # oversized rather than silently truncated into something that then fails
        # its checksum for no visible reason.
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise UpdateError(f"{url} is larger than expected — refusing it")
    return payload


def fetch_latest(
    *, opener: Opener = HTTPS_OPENER, timeout: float = 10.0
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
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise UpdateError(f"could not reach GitHub: {exc}") from exc

    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"the release API answered with unreadable JSON: {exc}") from exc
    return parse_release(document)


#: How much of the asset is held at once while it is written to disk.
CHUNK_BYTES = 1 << 16


def download_to(
    release: Release,
    dest: Path,
    name: str = ASSET_NAME,
    *,
    opener: Opener = HTTPS_OPENER,
    timeout: float = 120.0,
) -> Path:
    """Stream one asset into ``dest``, and keep it only if it matches its checksum.

    Our own prefix, https only, a ceiling on the size — and the bytes go to the
    file as they arrive rather than through a ``bytes`` object first. The cap is
    300 MB, and holding that in memory to write it out again is a spike this app
    has no reason to ask a machine for; the phone side has streamed to a file
    since it was written, for the smaller version of exactly this reason.
    Nothing partial is left behind: a download that fails anywhere, including at
    the checksum, deletes what it wrote.
    """
    url = release.url(name)
    if url is None:
        raise UpdateError(f"release {release.tag} has no {name}")
    sums_url = release.url(CHECKSUMS_NAME)
    if sums_url is None:
        raise UpdateError(f"release {release.tag} ships no {CHECKSUMS_NAME} to check against")

    try:
        checksums = _read(sums_url, opener=opener, timeout=timeout, limit=MAX_JSON_BYTES)
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise UpdateError(f"download failed: {exc}") from exc
    expected = required_checksum(name, checksums.decode("utf-8", "replace"))

    written = 0
    # Everything that can refuse the URL happens before the file exists: between
    # opening it and entering the `with` there must be nothing that can raise, or
    # the handle is leaked and — on Windows, where an open file cannot be
    # unlinked — the half-written .new is left beside the .exe for good.
    request = _request(url)
    try:
        out = dest.open("wb")
    except OSError as exc:
        raise UpdateError(f"could not write the new build next to the old one: {exc}") from exc

    try:
        # Two nested try blocks, and the order is the point: the inner `finally`
        # closes the file before any handler below runs, because _discard() on a
        # handle that is still open does nothing at all on Windows and leaves the
        # half-written .new beside the .exe.
        try:
            with opener(request, timeout=timeout) as response:  # noqa: S310 - scheme checked
                while True:
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_ASSET_BYTES:
                        raise UpdateError(f"{url} is larger than expected — refusing it")
                    try:
                        out.write(chunk)
                    except OSError as exc:
                        # A full disk is not a failed download, and telling the
                        # user the network let them down sends them to look at
                        # the wrong thing entirely. It is also much the likelier
                        # of the two here: 55 MB arriving onto a machine somebody
                        # has been meaning to clear out.
                        raise UpdateError(
                            f"could not write the new build next to the old one: {exc}"
                        ) from exc
                # On disk before the rename, not merely in a buffer the OS has
                # promised to write eventually. The next steps rename the running
                # .exe aside and this file into its place — renames the filesystem
                # can commit while the contents behind them are still pending, so
                # a power cut in that window leaves the name of the app pointing
                # at a short file. That is the one way left to end up with a
                # directory that has no working executable in it, and it costs
                # one call.
                #
                # Under the write handler, not beside it: writes are buffered, so
                # a full disk is reported here far more often than at any
                # individual write, and this is the very failure that must not
                # read as a network one.
                try:
                    out.flush()
                    os.fsync(out.fileno())
                    # Closed here rather than left to the handle's own cleanup,
                    # for the same reason as the fsync: close() writes too, and a
                    # disk error escaping it would land in the network handler
                    # below and be reported as a failed download.
                    out.close()
                except OSError as exc:
                    raise UpdateError(
                        f"could not write the new build next to the old one: {exc}"
                    ) from exc
        finally:
            # close() is idempotent, so this is only about the error paths — and
            # it has to happen before _discard() below, because on Windows an
            # open file cannot be unlinked and the .new would stay for good.
            with contextlib.suppress(OSError):
                out.close()
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        _discard(dest)
        raise UpdateError(f"download failed: {exc}") from exc
    except BaseException:
        # Includes the size refusal above, and a Ctrl-C in a console run.
        _discard(dest)
        raise

    # Hashed from the file, after it is on disk, rather than from the bytes as
    # they went past. What is about to be renamed onto the name of the app is the
    # file, so the file is what has to match — a write that landed wrong would
    # otherwise pass a check of something that no longer exists. The phone has
    # always hashed the file it wrote; this is the same rule, and reading 55 MB
    # back costs a fraction of fetching them.
    try:
        actual = sha256_file(dest)
    except OSError as exc:
        _discard(dest)
        raise UpdateError(f"could not read the download back: {exc}") from exc
    if actual != expected:
        _discard(dest)
        raise UpdateError(
            f"{name} does not match its checksum — the download is corrupt or tampered with"
        )
    return dest


def sha256_file(path: Path) -> str:
    """The hex digest of a file, read in pieces so its size never matters."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discard(path: Path) -> bool:
    """Remove a file we no longer want, saying whether one was there. Never raises."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        # An antivirus reading the file it is about to be asked about, most
        # likely. Not being able to tidy up is never a reason to fail.
        log.info("could not remove %s: %s", path, exc)
        return False
    return True


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


def staged_for(exe: Path) -> Path:
    """Where a download lands before it becomes ``exe``.

    Beside the executable rather than in a temp directory, because the last step
    is :func:`os.replace` and that is only atomic within one volume — and the
    whole point of the staging file is that the swap either happens or does not.
    """
    return exe.with_name(exe.name + ".new")


def probe_for(exe: Path) -> Path:
    """The file :func:`writable` creates to find out whether it may write here."""
    return exe.with_name(exe.name + ".writetest")


def backup_for(exe: Path) -> Path:
    """Where the outgoing build is parked until a later start deletes it.

    "The next start" would be a promise only the windowed app keeps: clearing it
    is a call, and ``--headless`` did not make it, so a machine that only ever
    runs headless kept every previous build for ever.
    """
    return exe.with_name(exe.stem + ".old" + exe.suffix)


#: Answers already given by :func:`writable`, keyed by the path asked about.
_writable_cache: dict[Path, bool] = {}


def writable(exe: Path, *, recheck: bool = False) -> bool:
    """Whether the swap can even be attempted, asked by doing rather than guessing.

    ``os.access`` reports the DACL and lies about the two cases that matter on
    Windows — a directory under ``Program Files`` protected by UAC virtualisation,
    and one where the ACL allows what the token does not. Creating a file is the
    only honest answer.

    Which is why the answer is kept. The dashboard asks for the update status
    every three seconds and this sits behind it, so the honest answer was being
    bought twenty times a minute with a real file created and deleted beside the
    running ``.exe`` — for something that cannot change while the program runs,
    in the one directory where a virus scanner is watching hardest.
    """
    if not recheck and exe in _writable_cache:
        return _writable_cache[exe]
    probe = probe_for(exe)
    try:
        probe.touch()
        probe.unlink()
    except OSError:
        _writable_cache[exe] = False
        return False
    _writable_cache[exe] = True
    return True


def clear_leftovers(exe: Path | None = None) -> bool:
    """Delete what a previous update left beside the app. Never raises.

    Three files, all only removable once nothing is using them, which at start-up
    is exactly the case: the build that was replaced, a download that never
    finished, and the probe :func:`writable` writes. ``download_to`` removes its
    own — but only for a failure it lives to see; killed mid-download, or the
    machine losing power, it leaves tens of megabytes named ``.exe.new`` that
    nothing would ever look at again. The probe is a nuisance rather than a
    problem — an empty file — but it is ours, it is unexplainable to anyone who
    finds it, and this is the one moment it is safe to remove.

    Returns whether anything was actually removed. An antivirus still reading
    yesterday's build is not a reason to fail to start today's, so a file that
    will not go yet simply stays for the next start.
    """
    exe = exe or running_executable()
    if exe is None:
        return False
    _discard(probe_for(exe))
    staged = _discard(staged_for(exe))
    if staged:
        log.info("removed an unfinished download: %s", staged_for(exe).name)
    backup = _discard(backup_for(exe))
    if backup:
        log.info("removed the previous build: %s", backup_for(exe).name)
    return staged or backup


def install_staged(staged: Path, exe: Path) -> Path:
    """Put an already-downloaded build in place of ``exe``, and say where the old one went.

    Three steps, each undone if the next one fails, because the failure mode this
    guards against is the one that leaves no working executable at all: the user
    is left with an app that will not start and no way to be told why.
    """
    backup = backup_for(exe)

    # A leftover from an update that failed halfway would make this rename fail on
    # Windows, where os.replace onto an existing file is fine but the *source*
    # being locked is not. Clear it first; it is ours either way, and if it will
    # not go the rename below says so out loud rather than a bare `pass` here.
    _discard(backup)

    try:
        os.replace(exe, backup)          # renaming a running .exe is allowed
    except OSError as exc:
        _discard(staged)
        raise UpdateError(f"could not move the running build aside: {exc}") from exc

    try:
        os.replace(staged, exe)
    except OSError as exc:
        # Put the old build back, or there is nothing left to run.
        try:
            os.replace(backup, exe)
        except OSError:  # pragma: no cover - the disk is in real trouble
            log.exception("could not restore %s after a failed update", exe)
        _discard(staged)
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
