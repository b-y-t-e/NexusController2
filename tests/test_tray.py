"""What pressing X does, and the icon that decides it.

pystray is never imported here. The decision — hide or really close — is pure,
and it is the whole of the feature that can go wrong in a way the user pays for:
hiding with no icon to come back to leaves a process serving pads that nobody can
reach, and closing when the user expected the tray takes their gamepads away
mid-game.
"""

from __future__ import annotations

import sys
import threading
import time

from nexus_server import tray


class TestCloseDecision:
    def test_the_setting_is_what_normally_decides(self):
        assert tray.decide_close(to_tray=True, tray_running=True, quitting=False).hide
        assert not tray.decide_close(to_tray=False, tray_running=True, quitting=False).hide

    def test_no_icon_means_no_hiding(self):
        """Otherwise the window goes away with nothing to bring it back: the app
        keeps serving pads, invisible, and Task Manager is the only way out."""
        decision = tray.decide_close(to_tray=True, tray_running=False, quitting=False)
        assert not decision.hide
        assert "no tray icon" in decision.reason

    def test_quitting_wins_over_everything(self):
        """The update has already started the new build; a copy of this one
        lingering in the tray holds the port the new one wants."""
        decision = tray.decide_close(to_tray=True, tray_running=True, quitting=True)
        assert not decision.hide
        assert "on purpose" in decision.reason

    def test_every_answer_says_why(self):
        """The reason is logged, and "the window closed" with no explanation is
        the report this feature would otherwise generate."""
        for to_tray in (True, False):
            for running in (True, False):
                for quitting in (True, False):
                    decision = tray.decide_close(
                        to_tray=to_tray, tray_running=running, quitting=quitting
                    )
                    assert decision.reason


class TestStartHidden:
    def test_the_login_entry_starts_in_the_tray(self):
        """Logging in is not asking to be shown a window."""
        assert tray.start_hidden(minimized=True, tray_running=True)

    def test_but_never_with_nowhere_to_be(self):
        """No icon and no window is a process serving pads that the user can
        neither see nor stop — exactly what --headless at login would have been."""
        assert not tray.start_hidden(minimized=True, tray_running=False)

    def test_and_never_when_the_user_started_it_themselves(self):
        assert not tray.start_hidden(minimized=False, tray_running=True)


class TestImage:
    def test_a_missing_logo_still_produces_an_icon(self, tmp_path):
        """A tray that cannot draw itself would take the whole window with it —
        run_gui() creates the icon before webview.start()."""
        image = tray.load_image(tmp_path / "not-there.png")
        assert image.size == (64, 64)

    def test_a_file_that_is_not_an_image_is_not_fatal_either(self, tmp_path):
        broken = tmp_path / "logo.png"
        broken.write_bytes(b"this is not a PNG")
        assert tray.load_image(broken).size == (64, 64)

    def test_the_real_logo_is_used_when_it_is_there(self):
        from pathlib import Path

        logo = Path(__file__).resolve().parents[1] / "docs" / "logo.png"
        assert logo.is_file(), "the tray icon and the .exe icon come from this file"
        assert tray.load_image(logo).mode == "RGBA"


class FakePystray:
    """Just enough pystray to start, or to fail the way a real one fails.

    ``behaviour`` picks which: an icon that comes up, one whose run() raises
    before anything is visible (no notification area — the common case on a
    stripped Windows image), and one that runs but cannot show itself.
    """

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.icon = None
        outer = self

        class Icon:
            def __init__(self, name, image, title, menu):
                self.visible = False
                self.menu = menu
                outer.icon = self

            def run(self, setup=None):
                if outer.behaviour == "no-session":
                    raise RuntimeError("no notification area on this session")
                if outer.behaviour == "cannot-show":
                    raise_on_set(self)
                if setup is not None:
                    setup(self)
                outer.stopped.wait(5)

            def stop(self):
                outer.stopped.set()

        def raise_on_set(icon):
            # visible = True is where a backend without a tray gives up, and it
            # happens inside the setup callback, on pystray's own thread.
            raise RuntimeError("the shell has no notification area")

        self.stopped = threading.Event()
        self.Icon = Icon
        self.Menu = lambda *items: items
        self.MenuItem = lambda label, action, **kwargs: (label, action)


class TestStarting:
    """start() must answer "is there an icon", not "did a thread start"."""

    def _with(self, monkeypatch, module):
        monkeypatch.setitem(sys.modules, "pystray", module)
        return tray.Tray(on_open=lambda: None, on_quit=lambda: None)

    def test_no_pystray_at_all_is_a_no(self, monkeypatch):
        """The dependency is optional: no icon means X quits, and the dashboard
        says so rather than showing a switch that lies."""
        monkeypatch.setitem(sys.modules, "pystray", None)   # import raises
        icon = tray.Tray(on_open=lambda: None, on_quit=lambda: None)
        assert icon.start() is False
        assert not icon.running

    def test_an_icon_that_comes_up_is_a_yes(self, monkeypatch):
        fake = FakePystray("works")
        icon = self._with(monkeypatch, fake)
        try:
            assert icon.start() is True
            assert icon.running
            assert fake.icon.visible, "a custom setup= replaces the one pystray uses to show it"
        finally:
            icon.stop()

    def test_a_run_that_dies_is_answered_without_waiting_out_the_timeout(
        self, monkeypatch
    ):
        """The whole point: --minimized asks this before hiding the window, and a
        thread that has been started is not an icon anybody can click."""
        icon = self._with(monkeypatch, FakePystray("no-session"))
        monkeypatch.setattr(tray.Tray, "READY_TIMEOUT", 5.0)
        started = time.monotonic()
        assert icon.start() is False
        assert not icon.running
        assert time.monotonic() - started < 2.0, "it waited for the deadline instead of the thread"

    def test_an_icon_that_cannot_show_itself_is_a_no_too(self, monkeypatch):
        icon = self._with(monkeypatch, FakePystray("cannot-show"))
        assert icon.start() is False

    def test_the_menu_offers_a_check_when_there_is_one_to_offer(self, monkeypatch):
        """Right-click is where a windowless app is reachable from, and the
        update check is the one thing there that is not about the window."""
        fake = FakePystray("works")
        asked = []
        monkeypatch.setitem(sys.modules, "pystray", fake)
        icon = tray.Tray(
            on_open=lambda: None, on_quit=lambda: None, on_check=lambda: asked.append(1)
        )
        try:
            assert icon.start() is True
            labels = [label for label, _action in fake.icon.menu]
            assert labels == ["Open dashboard", "Check for updates", "Quit"]
            icon._check()
            assert asked == [1]
        finally:
            icon.stop()

    def test_and_leaves_it_out_when_there_is_not(self, monkeypatch):
        """run_headless has no dashboard for an answer to appear in."""
        fake = FakePystray("works")
        monkeypatch.setitem(sys.modules, "pystray", fake)
        icon = tray.Tray(on_open=lambda: None, on_quit=lambda: None)
        try:
            icon.start()
            assert [label for label, _ in fake.icon.menu] == ["Open dashboard", "Quit"]
            icon._check()       # must not raise either
        finally:
            icon.stop()

    def test_a_tray_that_gives_up_later_stops_being_running(self, monkeypatch):
        """run() returns when the backend's loop does, which a session ending can
        do by itself — and the X button reads this to decide what closing means."""
        fake = FakePystray("works")
        icon = self._with(monkeypatch, fake)
        assert icon.start() is True
        fake.stopped.set()          # as if the loop ended on its own
        for _ in range(100):
            if not icon.running:
                break
            time.sleep(0.01)
        assert not icon.running


class TestTrayWithoutASystem:
    def test_a_tray_that_never_started_is_not_running(self):
        icon = tray.Tray(on_open=lambda: None, on_quit=lambda: None)
        assert not icon.running

    def test_stopping_one_that_never_started_is_not_an_error(self):
        tray.Tray(on_open=lambda: None, on_quit=lambda: None).stop()

    def test_a_menu_action_that_raises_does_not_kill_the_thread(self):
        """It runs on pystray's thread: an exception there takes the icon down
        and leaves a hidden window with no way back to it."""
        def explode():
            raise RuntimeError("the window went away")

        icon = tray.Tray(on_open=explode, on_quit=lambda: None)
        icon._open()          # must not raise

    def test_the_thread_holds_its_own_icon(self):
        """stop() clears the attribute, and it can get there before a thread that
        has started but not yet read it — which was an AttributeError in the log
        of every quick close."""
        class FakeIcon:
            def __init__(self):
                self.ran = False

            def run(self, setup=None):
                self.ran = True

        icon = tray.Tray(on_open=lambda: None, on_quit=lambda: None)
        fake = FakeIcon()
        icon._icon = None     # as if stop() had already won the race
        icon._run(fake)
        assert fake.ran
