"""Starting with Windows, from the user's own Run key.

``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` and nothing else: no
service, no scheduled task, no shortcut written into the Start-up folder. All
three of those need either administrator rights or a file somewhere the user did
not put one, and this app is a LAN tool somebody runs for themselves — the per-user
Run key is exactly that scope, is writable without elevation, and is the one
place Windows itself shows in Task Manager's Start-up tab, so a user who wants it
gone can remove it without this app's help.

The registry *is* the state. Nothing about autostart is kept in ``settings.json``:
two answers to one question drift apart the first time somebody turns it off in
Task Manager, and then the dashboard shows a switch that lies.

Only a frozen build can register itself. From a source checkout the command would
have to name a Python interpreter, a ``-m`` and a working directory, and the
entry would rot the moment the checkout moved — so the answer there is that the
feature is unavailable, which is also true.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

#: Where Windows looks for per-user login commands.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
#: The value name under that key. Also what Task Manager shows as the entry.
VALUE_NAME = "NexusController"
#: What the login entry passes so the app starts in the tray. See :func:`command_for`.
MINIMIZED_FLAG = "--minimized"


def command_for(exe: Path) -> str:
    """The command line to register for ``exe``.

    Quoted, always: the default install lives under ``Program Files``, and an
    unquoted path with a space in it is read by Windows as a program called
    ``C:\\Program`` with arguments — the entry then fails silently at every login.

    ``--minimized`` and nothing else. Logging in is not asking to be shown a
    window, and with an icon in the notification area there is somewhere for the
    app to be instead — the same place the X button puts it. It is not
    ``--headless``: that one has no window and no icon, so the only way to stop
    it is Task Manager. And it is a request, not a promise: if the tray does not
    come up, the window is shown anyway rather than leaving a process nobody can
    reach (see :func:`tray.start_hidden`).
    """
    return f'"{exe}" {MINIMIZED_FLAG}'


class RunKey(Protocol):
    """The three things this needs from the registry, so tests can stand in."""

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class WindowsRunKey:
    """The real thing. Imports ``winreg`` lazily so this module loads anywhere."""

    def _open(self, *, write: bool, create: bool = False):
        import winreg  # noqa: PLC0415 - Windows-only, and only when actually used

        if create:
            # CreateKeyEx, not OpenKey: the Run key is normally there, but it is
            # not guaranteed — a fresh profile or a stripped image can be without
            # one, and OpenKey then raises FileNotFoundError with no way out from
            # inside the app. CreateKeyEx opens the existing key when there is
            # one and creates it when there is not, which is the same key Windows
            # itself would create.
            return winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_WRITE
            )
        access = winreg.KEY_READ | (winreg.KEY_WRITE if write else 0)
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)

    def get(self, name: str) -> str | None:
        import winreg  # noqa: PLC0415

        try:
            with self._open(write=False) as key:
                value, _kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.info("could not read the Run key: %s", exc)
            return None
        return value if isinstance(value, str) else None

    def set(self, name: str, value: str) -> None:
        import winreg  # noqa: PLC0415

        with self._open(write=True, create=True) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg  # noqa: PLC0415

        # No create= here: a Run key that is not there has nothing to remove, and
        # making one on the way to deleting nothing is a footprint for no reason.
        try:
            with self._open(write=True) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


def default_key() -> RunKey:
    return WindowsRunKey()


def supported(exe: Path | None) -> bool:
    """Whether registering anything is possible here at all."""
    return os.name == "nt" and exe is not None


def registered_exe(command: str | None) -> str | None:
    """The executable an entry names, in comparable form, or ``None``.

    The identity of an entry is the program it starts, not the exact text: the
    flags after it are ours to change — ``--minimized`` was added after the first
    build that wrote one of these — and an entry we no longer recognise is an
    entry the switch can neither report nor remove. The path is quoted, so the
    first quoted run is it; ``normcase`` because Windows does not care about the
    case of a path and neither may this.
    """
    if not command:
        return None
    parts = command.strip().split('"')
    # '"C:\\...\\app.exe" --minimized'.split('"') -> ['', 'C:\\...\\app.exe', ' --minimized']
    if len(parts) < 3 or parts[0].strip():
        return None
    return os.path.normcase(parts[1]) or None


def enabled(exe: Path | None, *, key: RunKey | None = None) -> bool:
    """Whether *this* build is the one registered.

    Not merely "is there an entry": after an update the executable is the same
    path, but a copy moved elsewhere would leave an entry pointing at a build
    that is no longer there — and answering yes for someone else's entry would
    have this app quietly take it over on the next toggle.
    """
    if not supported(exe):
        return False
    current = registered_exe((key or default_key()).get(VALUE_NAME))
    return current is not None and current == os.path.normcase(str(exe))


def enable(exe: Path, *, key: RunKey | None = None) -> None:
    (key or default_key()).set(VALUE_NAME, command_for(exe))
    log.info("registered %s to start with Windows", exe.name)


def disable(exe: Path | None, *, key: RunKey | None = None) -> None:
    """Remove the login entry — but only if it is ours.

    The same identity rule the read side applies, and for the same reason: an
    entry under our value name that starts a *different* copy of the app belongs
    to that copy, which is very likely the one the user actually runs. Reporting
    it as "off" and then deleting it on the way past would be this app tidying
    away somebody else's setting on the strength of a name it happens to share.

    ``exe`` of ``None`` — a source checkout — is that same case and not a
    licence: with nothing to compare against there is no way to tell whose entry
    this is, so it stays. :func:`enabled` already answers ``False`` there, so the
    switch is off either way and nothing is being hidden from anybody.
    """
    run_key = key or default_key()
    current = run_key.get(VALUE_NAME)
    if current is None:
        return
    # No executable, no identity: a source checkout cannot tell whether the entry
    # is its own, so removing it would be exactly the tidying-away this refuses to
    # do — and the app it deleted the entry for is the one the user actually runs.
    if exe is None or registered_exe(current) != os.path.normcase(str(exe)):
        log.info("leaving a start-with-Windows entry this build cannot claim: %s", current)
        return
    run_key.delete(VALUE_NAME)
    log.info("removed the start-with-Windows entry")
