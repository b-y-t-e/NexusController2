"""The notification-area icon, and what closing the window means.

Two things live here, and only one of them touches the system.

The **decision** is pure: pressing X either hides the window or ends the app, and
which one it is depends on the setting, on whether the tray icon is actually
running, and on whether the app is quitting on purpose (an update installs the
new build and asks this one to go). Getting that wrong is not cosmetic — a close
that hides with no icon to bring it back leaves a process serving pads with no
way to reach it but Task Manager, and a close that quits when the user expected
the tray takes their gamepads down mid-game.

The **icon** is the I/O: pystray owns a message loop, so it runs on its own
thread, and every menu item hands its work back to whoever created it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

#: What the icon is called in the notification area, on hover.
TITLE = "Nexus Controller"


# --- pure: what a close means ------------------------------------------------

@dataclass(frozen=True)
class CloseDecision:
    """What to do about a close request, and why — the reason is logged."""

    hide: bool
    reason: str


def start_hidden(*, minimized: bool, tray_running: bool) -> bool:
    """Whether the window should start out of sight.

    Only ever true for the login entry, which asks for it, and only when there is
    an icon to reach the window through. A hidden window with no icon is a
    process serving pads that the user cannot see, configure or stop except from
    Task Manager — the same trap ``--headless`` at login would have been.
    """
    return minimized and tray_running


def decide_close(*, to_tray: bool, tray_running: bool, quitting: bool) -> CloseDecision:
    """Whether pressing X should hide the window or let the app end.

    ``quitting`` wins over everything: it is set by the update, which has already
    put the new build in place and started it, and a copy of this one lingering
    in the tray would hold the port the new one wants.

    A tray that is not running is the other absolute. The setting says what the
    user asked for, but hiding into an icon that does not exist is a process
    nobody can see, stop, or reach the dashboard of — so the honest answer to
    "the tray failed to start" is to close normally.
    """
    if quitting:
        return CloseDecision(False, "the app is quitting on purpose")
    if not to_tray:
        return CloseDecision(False, "closing to the tray is turned off")
    if not tray_running:
        return CloseDecision(False, "there is no tray icon to hide into")
    return CloseDecision(True, "hidden to the notification area")


# --- I/O: the icon itself ----------------------------------------------------

def load_image(source: Path | None = None):
    """The icon image, from the bundled logo, or drawn if it is not there.

    A frozen build carries ``logo.png`` beside the web assets; a source checkout
    has it in ``docs/``. Either way this must not be the thing that stops the app
    from starting, so a missing file or a Pillow that cannot read it falls back
    to a plain square rather than raising.
    """
    from PIL import Image, ImageDraw  # noqa: PLC0415

    if source is not None and source.is_file():
        try:
            return Image.open(source).convert("RGBA")
        except OSError as exc:
            log.info("could not read the tray icon %s: %s", source, exc)

    image = Image.new("RGBA", (64, 64), (11, 15, 20, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, 57, 57), radius=14, fill=(34, 211, 238, 255))
    draw.rectangle((20, 22, 26, 42), fill=(11, 15, 20, 255))
    draw.rectangle((38, 22, 44, 42), fill=(11, 15, 20, 255))
    draw.polygon([(26, 22), (38, 42), (38, 22)], fill=(11, 15, 20, 255))
    return image


class Tray:
    """A notification-area icon on its own thread, or nothing at all.

    Nothing at all is a supported outcome: pystray needs a session with a
    notification area, and a machine without one — a service account, a stripped
    Windows install — must still run the app. :attr:`running` is what the close
    decision reads, so a tray that did not start simply means X closes the window
    as it always did.
    """

    #: How long :meth:`start` waits for pystray to say the icon is really up.
    #: Generous, because it is paid only when something is wrong: on a working
    #: machine the callback comes back in milliseconds.
    READY_TIMEOUT = 5.0

    def __init__(
        self,
        *,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        on_check: Callable[[], None] | None = None,
        image_source: Path | None = None,
    ) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        self._on_check = on_check
        self._image_source = image_source
        self._icon = None
        self._thread: threading.Thread | None = None
        #: Set by pystray's own setup callback, which runs once the icon is in
        #: the notification area. Until then there is nothing to hide into.
        self._ready = threading.Event()

    @property
    def running(self) -> bool:
        return (
            self._icon is not None
            and self._ready.is_set()
            and self._thread is not None
            and self._thread.is_alive()
        )

    def start(self) -> bool:
        """Put the icon in the notification area, and wait to see that it is there.

        Waits rather than assuming, because the answer decides whether a window
        is shown: a login with ``--minimized`` hides into this icon, and a thread
        that has been *started* is not an icon anybody can click. pystray's
        backend does its work inside ``run()`` — a session with no notification
        area, a backend that imports and then fails — so the honest signal is its
        own setup callback, which runs once the icon is up.
        """
        try:
            import pystray  # noqa: PLC0415

            items = [
                # default=True: this is what a double-click on the icon does,
                # which is what everyone tries first.
                pystray.MenuItem("Open dashboard", self._open, default=True),
            ]
            if self._on_check is not None:
                # The answer has nowhere to appear in a menu, so this opens the
                # dashboard as well and lets the banner there say it — the same
                # place the automatic check reports to, rather than a second way
                # of telling the user the same news.
                items.append(pystray.MenuItem("Check for updates", self._check))
            items.append(pystray.MenuItem("Quit", self._quit))
            menu = pystray.Menu(*items)
            self._icon = pystray.Icon(
                "nexus-controller", load_image(self._image_source), TITLE, menu
            )
        except Exception as exc:  # noqa: BLE001 - the app runs without a tray
            log.info("no tray icon: %s", exc)
            self._icon = None
            return False

        # pystray owns a message loop, and pywebview owns the main thread.
        # The icon travels as an argument, not through self: stop() clears the
        # attribute, and it can win the race against a thread that has started
        # but not yet read it — which used to be an AttributeError in the log.
        self._thread = threading.Thread(
            target=self._run, args=(self._icon,), name="tray", daemon=True
        )
        try:
            self._thread.start()
        except RuntimeError as exc:
            log.warning("could not start the tray thread: %s", exc)
            self._icon = None
            self._thread = None
            return False

        # In steps, so a run() that fails immediately is answered immediately
        # rather than after the whole timeout — which is the common case: no
        # notification area means no icon, and the app should get on with showing
        # its window.
        deadline = time.monotonic() + self.READY_TIMEOUT
        while not self._ready.wait(0.05):
            if not self._thread.is_alive() or time.monotonic() >= deadline:
                break
        if not self._ready.is_set():
            log.info("the tray icon did not come up")
        return self.running

    def _setup(self, icon) -> None:
        """pystray's own "the loop is up" callback, on its own thread.

        Passing one of these *replaces* pystray's default, which is the single
        line that makes the icon appear — so this has to do it, and only then say
        the tray is running. The order is the whole value of waiting for it:
        `visible = True` is where a session with no notification area fails.
        """
        try:
            icon.visible = True
        except Exception:  # noqa: BLE001 - reported by start() as "no icon"
            log.exception("the tray icon could not be shown")
            return
        self._ready.set()

    def _run(self, icon) -> None:
        try:
            icon.run(setup=self._setup)
        except Exception:  # noqa: BLE001 - a dead tray must not take the app with it
            log.exception("the tray icon stopped")
        finally:
            # A tray that died is not one to hide a window into, and stop() is
            # not the only way this ends: run() returns when the backend's loop
            # does, which a session ending or a shell restart can do by itself.
            self._ready.clear()

    def _open(self, *_args) -> None:
        self._call(self._on_open, "opening the dashboard")

    def _quit(self, *_args) -> None:
        self._call(self._on_quit, "quitting")

    def _check(self, *_args) -> None:
        if self._on_check is not None:
            self._call(self._on_check, "checking for updates")

    def _call(self, action: Callable[[], None], what: str) -> None:
        # These run on pystray's thread and reach into the UI. An exception here
        # kills that thread and takes the icon with it — leaving a hidden window
        # and no way back to it.
        try:
            action()
        except Exception:  # noqa: BLE001
            log.exception("the tray failed while %s", what)

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001 - shutting down; nothing to salvage
                log.debug("could not stop the tray icon", exc_info=True)
