"""Shortcuts in the Start menu and on the desktop.

A single-file ``.exe`` has no installer, so the app people download lives in
whatever folder the browser dropped it in and is reachable only from there. The
Start-menu entry is what fixes that, and not mainly because it is a tidy place to
put an icon: **it is what makes Windows Search find the app**, and what "Pin to
Start" and "Pin to taskbar" need something to pin. The desktop shortcut is the
other half, for people who work from the desktop.

Both are ordinary ``.lnk`` files in the user's own profile — no installer, no
elevation, nothing outside ``%APPDATA%`` and the desktop folder — and both are
created through ``WScript.Shell``, the same COM object the shell itself uses, so
what lands there is a real shortcut with an icon and a working directory rather
than something hand-rolled.

Two rules carry over from :mod:`autostart`, for the same reasons:

* **the file system is the state.** Nothing about a shortcut is kept in
  ``settings.json``: a user who deletes the icon is not asking this app to keep
  believing it is there.
* **identity is the program a shortcut starts**, not its name. A ``.lnk`` under
  our name pointing at another copy belongs to that copy — very likely the one
  the user actually runs — so it is neither reported as ours nor deleted by our
  switch.

The path survives an update: the swap keeps the executable's name and place
(see :mod:`updates`), so a shortcut made today still points at the build that
replaces it tomorrow.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Protocol, Sequence

log = logging.getLogger(__name__)

#: What the shortcut is called. The name Windows Search will match on.
LINK_NAME = "Nexus Controller.lnk"

#: The two places, as the page names them.
START_MENU = "start_menu"
DESKTOP = "desktop"
PLACES = (START_MENU, DESKTOP)


class ShortcutHost(Protocol):
    """The three things this needs from the shell, so tests can stand in."""

    def folder(self, place: str) -> Path | None: ...

    def targets_of(self, links: Sequence[Path]) -> dict[Path, str | None]:
        """What each shortcut points at, in **one** trip to the shell."""
        ...

    def create(self, link: Path, exe: Path) -> None: ...


class WindowsShell:
    """The real thing: ``WScript.Shell`` through PowerShell, no extra dependency.

    Every call here starts a PowerShell process, which takes a few hundred
    milliseconds and is why the answers that cannot change are asked for once:
    the dashboard reads this state on a timer, and three processes every refresh
    to answer a question about two files is not a price worth paying.
    """

    def __init__(self) -> None:
        self._desktop: Path | None = None
        self._asked_for_desktop = False

    def folder(self, place: str) -> Path | None:
        if place == START_MENU:
            base = os.environ.get("APPDATA")
            if not base:
                return None
            return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if place == DESKTOP:
            # Asked of Windows rather than assumed to be ~/Desktop: OneDrive
            # moves the desktop into its own folder on a great many machines —
            # and localises the name, so it is not even "Desktop" — and a
            # shortcut written to the old path lands somewhere nobody sees.
            # Kept, because it does not change while the app runs.
            if not self._asked_for_desktop:
                self._asked_for_desktop = True
                answer = self._run("[Environment]::GetFolderPath('Desktop')")
                self._desktop = Path(answer) if answer else None
            return self._desktop
        return None

    def targets_of(self, links: Sequence[Path]) -> dict[Path, str | None]:
        answers: dict[Path, str | None] = {link: None for link in links}
        present = [link for link in links if link.is_file()]
        if not present:
            # The common case — no shortcuts yet — costs nothing at all.
            return answers
        listed = "; ".join(f"'{_quote(link)}'" for link in present)
        # One process for however many shortcuts there are. The name is echoed
        # back with the answer because a shortcut that cannot be read prints
        # nothing, and position alone would then attribute one file's target to
        # another.
        output = self._run(
            "$s = New-Object -ComObject WScript.Shell; "
            f"foreach ($p in @({listed})) {{ \"$p`t\" + $s.CreateShortcut($p).TargetPath }}"
        )
        by_name = {str(link).lower(): link for link in present}
        for line in (output or "").splitlines():
            name, tab, target = line.partition("\t")
            link = by_name.get(name.strip().lower())
            if tab and link is not None:
                answers[link] = target.strip() or None
        return answers

    def create(self, link: Path, exe: Path) -> None:
        script = (
            "$s = New-Object -ComObject WScript.Shell; "
            f"$k = $s.CreateShortcut('{_quote(link)}'); "
            f"$k.TargetPath = '{_quote(exe)}'; "
            f"$k.WorkingDirectory = '{_quote(exe.parent)}'; "
            # The icon comes out of the executable itself, so it keeps working
            # when the app updates in place.
            f"$k.IconLocation = '{_quote(exe)}'; "
            "$k.Description = 'Nexus Controller'; "
            "$k.Save()"
        )
        if self._run(script, expect_output=False) is None:
            raise OSError(f"could not create {link}")

    def _run(self, script: str, *, expect_output: bool = True) -> str | None:
        from . import system  # noqa: PLC0415 - avoids a cycle at import time

        try:
            result = system.run_hidden(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.info("shortcut command failed: %s", exc)
            return None
        if result.returncode != 0:
            log.info("shortcut command refused: %s", (result.stderr or "").strip()[:200])
            return None
        answer = (result.stdout or "").strip()
        return answer if answer or not expect_output else None


def _quote(value) -> str:
    """A path inside a PowerShell single-quoted string."""
    return str(value).replace("'", "''")


def default_host() -> ShortcutHost:
    return WindowsShell()


def supported(exe: Path | None) -> bool:
    """Whether a shortcut can be made here at all.

    The same answer as :func:`autostart.supported`, for the same reason: from a
    source checkout the target would have to be an interpreter, a ``-m`` and a
    working directory, and the shortcut would rot the moment the checkout moved.
    """
    return os.name == "nt" and exe is not None


def link_path(place: str, *, host: ShortcutHost | None = None) -> Path | None:
    folder = (host or default_host()).folder(place)
    return None if folder is None else folder / LINK_NAME


def owns(exe: Path | None, target: str | None) -> bool:
    """Whether a shortcut pointing at ``target`` is this build's.

    ``normcase`` because Windows does not care about the case of a path, and a
    shortcut made by an earlier build — or by hand — is the same shortcut
    whether it says Program Files or PROGRAM FILES.
    """
    if exe is None or not target:
        return False
    return os.path.normcase(target.strip().strip('"')) == os.path.normcase(str(exe))


def status(exe: Path | None, *, host: ShortcutHost | None = None) -> dict[str, bool]:
    """Which places have a shortcut of *this* build's, in one trip to the shell.

    Both places together, because the page asks about both at once and every
    trip is a process: read one at a time this was three of them per refresh, on
    a timer, to answer a question about two files.
    """
    if not supported(exe):
        return {place: False for place in PLACES}
    shell = host or default_host()
    links = {place: link_path(place, host=shell) for place in PLACES}
    targets = shell.targets_of([link for link in links.values() if link is not None])
    return {
        place: link is not None and owns(exe, targets.get(link))
        for place, link in links.items()
    }


def exists(place: str, exe: Path | None, *, host: ShortcutHost | None = None) -> bool:
    """Whether *this* build has a shortcut in ``place``."""
    return status(exe, host=host).get(place, False)


def create(place: str, exe: Path, *, host: ShortcutHost | None = None) -> None:
    shell = host or default_host()
    link = link_path(place, host=shell)
    if link is None:
        raise OSError(f"Windows did not say where {place.replace('_', ' ')} is")
    link.parent.mkdir(parents=True, exist_ok=True)
    shell.create(link, exe)
    log.info("created a shortcut in %s", place)


def remove(place: str, exe: Path | None, *, host: ShortcutHost | None = None) -> None:
    """Delete the shortcut — but only if it is ours.

    Same rule as the read side, and the same reason as in :mod:`autostart`: a
    shortcut under our name that starts a different copy of the app belongs to
    that copy, and deleting it on the way past would be this app tidying away
    somebody else's icon on the strength of a name it happens to share.
    """
    shell = host or default_host()
    link = link_path(place, host=shell)
    if link is None or not link.is_file():
        return
    if not owns(exe, shell.targets_of([link]).get(link)):
        log.info("leaving a shortcut in %s that this build cannot claim", place)
        return
    try:
        link.unlink()
    except OSError as exc:
        raise OSError(f"could not remove the shortcut in {place}: {exc}") from exc
    log.info("removed the shortcut in %s", place)
