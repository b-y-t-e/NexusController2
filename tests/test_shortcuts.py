"""Shortcuts in the Start menu and on the desktop.

No shell is touched here: ``WScript.Shell`` is behind a three-method interface
and the tests hand in a fake. What is worth pinning is the identity rule — a
``.lnk`` under our name that starts *another* copy of the app is that copy's, not
ours to report or delete — and that the desktop folder is asked for rather than
guessed, because OneDrive moves it on a great many machines.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nexus_server import shortcuts


class FakeShell:
    """Two folders and a dictionary of shortcuts."""

    #: Distinguishes "use the default" from "Windows named no folder at all".
    UNSET = object()

    def __init__(self, tmp_path: Path, *, desktop=UNSET) -> None:
        self.folders = {
            shortcuts.START_MENU: tmp_path / "Start Menu" / "Programs",
            shortcuts.DESKTOP: tmp_path / "Desktop" if desktop is self.UNSET else desktop,
        }
        for folder in self.folders.values():
            if folder is not None:
                folder.mkdir(parents=True, exist_ok=True)
        self.created: list[tuple[Path, Path]] = []
        self.refuse = False
        #: How many trips to the shell were made — one per status refresh is the
        #: budget, and the dashboard asks on a timer.
        self.trips = 0

    def folder(self, place: str) -> Path | None:
        return self.folders.get(place)

    def targets_of(self, links) -> dict[Path, str | None]:
        self.trips += 1
        return {
            link: (link.read_text(encoding="utf-8").strip() if link.is_file() else None)
            for link in links
        }

    def create(self, link: Path, exe: Path) -> None:
        if self.refuse:
            raise OSError("the folder is managed by policy")
        link.write_text(str(exe), encoding="utf-8")
        self.created.append((link, exe))


EXE = Path(r"C:\Program Files\NexusController\NexusController.exe")


@pytest.fixture(autouse=True)
def on_windows(monkeypatch):
    monkeypatch.setattr(shortcuts.os, "name", "nt")


@pytest.fixture()
def shell(tmp_path):
    return FakeShell(tmp_path)


class TestSupported:
    def test_a_source_checkout_cannot_make_one(self, monkeypatch):
        """The target would have to be an interpreter, a -m and a directory, and
        it would rot the moment the checkout moved."""
        assert not shortcuts.supported(None)

    def test_and_neither_can_anything_that_is_not_windows(self, monkeypatch):
        monkeypatch.setattr(shortcuts.os, "name", "posix")
        assert not shortcuts.supported(EXE)


class TestCreatingAndRemoving:
    @pytest.mark.parametrize("place", shortcuts.PLACES)
    def test_it_goes_where_the_shell_says(self, place, shell):
        shortcuts.create(place, EXE, host=shell)
        link = shell.folder(place) / shortcuts.LINK_NAME
        assert link.is_file()
        assert shortcuts.exists(place, EXE, host=shell)

    def test_removing_takes_it_away(self, shell):
        shortcuts.create(shortcuts.DESKTOP, EXE, host=shell)
        shortcuts.remove(shortcuts.DESKTOP, EXE, host=shell)
        assert not (shell.folder(shortcuts.DESKTOP) / shortcuts.LINK_NAME).exists()
        assert not shortcuts.exists(shortcuts.DESKTOP, EXE, host=shell)

    def test_removing_one_that_is_not_there_is_not_an_error(self, shell):
        shortcuts.remove(shortcuts.DESKTOP, EXE, host=shell)

    def test_another_copy_s_shortcut_is_left_alone(self, shell):
        """It is very likely the copy the user actually runs."""
        link = shell.folder(shortcuts.DESKTOP) / shortcuts.LINK_NAME
        link.write_text(r"D:\elsewhere\NexusController.exe", encoding="utf-8")
        assert not shortcuts.exists(shortcuts.DESKTOP, EXE, host=shell)
        shortcuts.remove(shortcuts.DESKTOP, EXE, host=shell)
        assert link.is_file()

    def test_a_shell_that_refuses_raises_rather_than_lying(self, shell):
        shell.refuse = True
        with pytest.raises(OSError):
            shortcuts.create(shortcuts.START_MENU, EXE, host=shell)
        assert not shortcuts.exists(shortcuts.START_MENU, EXE, host=shell)

    def test_a_folder_windows_will_not_name_is_an_error_not_a_crash(self, tmp_path):
        """GetFolderPath('Desktop') can come back empty on a stripped profile."""
        shell = FakeShell(tmp_path, desktop=None)
        assert not shortcuts.exists(shortcuts.DESKTOP, EXE, host=shell)
        with pytest.raises(OSError):
            shortcuts.create(shortcuts.DESKTOP, EXE, host=shell)


class TestStatus:
    def test_both_places_are_answered_in_one_trip_to_the_shell(self, shell):
        """Every trip is a PowerShell process of a few hundred milliseconds, and
        the dashboard asks for this on a timer."""
        shortcuts.create(shortcuts.START_MENU, EXE, host=shell)
        shell.trips = 0
        answer = shortcuts.status(EXE, host=shell)
        assert answer == {shortcuts.START_MENU: True, shortcuts.DESKTOP: False}
        assert shell.trips == 1

    def test_a_source_checkout_asks_nothing_at_all(self, shell):
        assert shortcuts.status(None, host=shell) == {
            shortcuts.START_MENU: False, shortcuts.DESKTOP: False
        }
        assert shell.trips == 0


class TestOwnership:
    def test_the_case_of_a_path_is_not_a_difference(self):
        assert shortcuts.owns(EXE, str(EXE).upper()) == (os.name == "nt")

    def test_quotes_and_spacing_are_not_either(self):
        assert shortcuts.owns(EXE, f'  "{EXE}"  ')

    def test_a_different_program_is_not_ours(self):
        assert not shortcuts.owns(EXE, r"D:\elsewhere\NexusController.exe")

    def test_nothing_at_all_is_not_ours(self):
        assert not shortcuts.owns(EXE, None)
        assert not shortcuts.owns(EXE, "")
        assert not shortcuts.owns(None, str(EXE))
