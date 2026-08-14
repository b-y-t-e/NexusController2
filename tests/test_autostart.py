"""Starting with Windows.

No registry is touched here: the Run key is behind a three-method interface and
the tests hand in a dictionary. What is worth pinning is the quoting — an
unquoted path under ``Program Files`` is an entry that fails silently at every
login, which is the worst kind of failure this feature can have — and the rule
that an entry belonging to *another* copy of the app is not ours to report or
overwrite.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nexus_server import autostart


class FakeRunKey:
    """The Run key as a dictionary, with a count of what was written."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.writes = 0
        self.deletes = 0

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.writes += 1
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.deletes += 1
        self.values.pop(name, None)


EXE = Path(r"C:\Program Files\NexusController\NexusController.exe")
COMMAND = f'"{EXE}" --minimized'


class TestCommand:
    def test_the_path_is_quoted(self):
        """Windows reads an unquoted "C:\\Program Files\\..." as a program called
        C:\\Program with arguments, and the entry then does nothing at all — at
        every login, with no error anywhere."""
        assert autostart.command_for(EXE).startswith(f'"{EXE}"')
        assert autostart.command_for(EXE).count('"') == 2

    def test_it_asks_to_start_in_the_tray(self):
        """Logging in is not asking to be shown a window. Not --headless, which
        has neither window nor icon and can only be stopped from Task Manager —
        and even this one is honoured only if the tray icon comes up."""
        assert autostart.command_for(EXE) == COMMAND
        assert "--headless" not in autostart.command_for(EXE)


class TestSupported:
    def test_a_source_checkout_cannot_register_itself(self, monkeypatch):
        monkeypatch.setattr(autostart.os, "name", "nt")
        assert not autostart.supported(None)

    def test_and_neither_can_anything_that_is_not_windows(self, monkeypatch):
        monkeypatch.setattr(autostart.os, "name", "posix")
        assert not autostart.supported(EXE)

    def test_a_frozen_build_on_windows_can(self, monkeypatch):
        monkeypatch.setattr(autostart.os, "name", "nt")
        assert autostart.supported(EXE)


class TestEnabling:
    @pytest.fixture(autouse=True)
    def on_windows(self, monkeypatch):
        monkeypatch.setattr(autostart.os, "name", "nt")

    def test_enabling_writes_the_quoted_command(self):
        key = FakeRunKey()
        autostart.enable(EXE, key=key)
        assert key.values[autostart.VALUE_NAME] == COMMAND
        assert autostart.enabled(EXE, key=key)

    def test_disabling_removes_it(self):
        key = FakeRunKey({autostart.VALUE_NAME: COMMAND})
        autostart.disable(EXE, key=key)
        assert autostart.VALUE_NAME not in key.values
        assert not autostart.enabled(EXE, key=key)

    def test_disabling_something_that_is_not_there_is_not_an_error(self):
        key = FakeRunKey()
        autostart.disable(EXE, key=key)
        assert key.deletes == 0     # nothing to remove, so nothing is asked of the registry

    def test_another_copy_s_entry_is_left_alone(self):
        """The same identity rule the read side applies. An entry under our value
        name that starts a different copy belongs to that copy — very likely the
        one the user actually runs — and deleting it on the way past would be
        this app tidying away somebody else's setting."""
        theirs = r'"D:\elsewhere\NexusController.exe" --minimized'
        key = FakeRunKey({autostart.VALUE_NAME: theirs})
        autostart.disable(EXE, key=key)
        assert key.values[autostart.VALUE_NAME] == theirs
        assert key.deletes == 0

    def test_an_entry_from_a_build_with_different_flags_is_still_ours(self):
        """Identity is the program the entry starts, not the exact text: the
        flags after it are ours to change, and an entry we stop recognising is
        one the switch can neither report nor remove."""
        key = FakeRunKey({autostart.VALUE_NAME: f'"{EXE}"'})
        assert autostart.enabled(EXE, key=key)
        autostart.disable(EXE, key=key)
        assert autostart.VALUE_NAME not in key.values

    def test_a_source_checkout_removes_nothing(self):
        """No .exe means no identity to compare against, and the entry that is
        there was written by a packaged build — quite possibly the copy the user
        actually starts Windows with."""
        key = FakeRunKey({autostart.VALUE_NAME: COMMAND})
        autostart.disable(None, key=key)
        assert key.values[autostart.VALUE_NAME] == COMMAND
        assert key.deletes == 0

    def test_an_entry_that_is_not_a_quoted_path_is_nobody_s(self):
        """Unquoted is how this feature fails silently; it is also not something
        this app ever wrote, so it is not this app's to delete."""
        key = FakeRunKey({autostart.VALUE_NAME: str(EXE)})
        assert not autostart.enabled(EXE, key=key)
        autostart.disable(EXE, key=key)
        assert key.deletes == 0

    def test_an_entry_for_another_copy_is_not_ours(self):
        """A build somewhere else registered under our name: reporting it as "on"
        would make the switch describe a program this one is not, and the next
        toggle would silently take the entry over."""
        key = FakeRunKey({autostart.VALUE_NAME: r'"D:\elsewhere\NexusController.exe"'})
        assert not autostart.enabled(EXE, key=key)

    def test_surrounding_whitespace_is_not_a_difference(self):
        key = FakeRunKey({autostart.VALUE_NAME: f'  {COMMAND}  '})
        assert autostart.enabled(EXE, key=key)

    @pytest.mark.skipif(os.name != "nt", reason="normcase only folds case on Windows")
    def test_neither_is_the_case_of_the_path(self):
        """Windows does not care, so neither may this: answering "somebody else's
        entry" about our own would have the next toggle rewrite it."""
        key = FakeRunKey({autostart.VALUE_NAME: COMMAND.upper()})
        assert autostart.enabled(EXE, key=key)

    def test_nothing_registered_means_off(self):
        assert not autostart.enabled(EXE, key=FakeRunKey())

    def test_a_source_checkout_is_never_reported_as_on(self):
        key = FakeRunKey({autostart.VALUE_NAME: f'"{EXE}"'})
        assert not autostart.enabled(None, key=key)
