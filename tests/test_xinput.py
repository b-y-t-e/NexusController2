"""XInput slot accounting.

Windows exposes exactly four XInput slots for the whole machine. ViGEm creates a
fifth virtual pad without complaining, but no game can see it — verified
experimentally: the fifth device reported success and pressing START on it showed
up on none of the four slots.
"""

import pytest

from nexus_server import xinput
from nexus_server.protocol import DeviceType, Hello, PROTOCOL_VERSION


class TestCapacityWarning:
    def test_no_warning_when_slots_are_free(self):
        assert xinput.capacity_warning(free=2, pending=2) is None

    def test_no_warning_when_exactly_one_slot_is_left(self):
        assert xinput.capacity_warning(free=1, pending=4) is None

    def test_warns_when_full(self):
        warning = xinput.capacity_warning(free=0, pending=5)
        assert warning is not None
        assert "no free slot" in warning
        assert "DualShock 4" in warning  # tell the user what to do about it

    def test_silent_when_xinput_cannot_be_queried(self):
        assert xinput.capacity_warning(free=None, pending=9) is None

    def test_warning_names_the_offending_pad(self):
        assert "pad 5" in xinput.capacity_warning(free=0, pending=5)


class TestSlotQuery:
    def test_limit_is_four(self):
        assert xinput.MAX_XINPUT_SLOTS == 4

    def test_occupied_slots_shape(self):
        slots = xinput.occupied_slots()
        if slots is None:
            pytest.skip("XInput not available on this machine")
        assert slots <= {0, 1, 2, 3}

    def test_free_count_is_consistent(self):
        free = xinput.free_slot_count()
        occupied = xinput.occupied_slots()
        if free is None or occupied is None:
            pytest.skip("XInput not available on this machine")
        assert free == xinput.MAX_XINPUT_SLOTS - len(occupied)
        assert 0 <= free <= xinput.MAX_XINPUT_SLOTS

    def test_available_matches_query(self):
        assert xinput.available() == (xinput.occupied_slots() is not None)


class TestServerWarning:
    """The warning must reach the log and the dashboard, not just a variable."""

    def _connect(self, server, device_type):
        import socket

        sock = socket.create_connection((server.bind_ip, server.settings.port), timeout=3)
        sock.sendall(
            Hello(PROTOCOL_VERSION, device_type, server.settings.token, "t").encode()
        )
        sock.recv(3)
        return sock

    def test_warns_when_xinput_is_full(self, server, monkeypatch):
        monkeypatch.setattr(xinput, "free_slot_count", lambda: 0)
        sock = self._connect(server, DeviceType.XBOX360)
        try:
            assert server.xinput_warning is not None
            assert any("WARNING" in line for line in server.log_messages)
            assert server.snapshot()["xinput_warning"] is not None
        finally:
            sock.close()

    def test_buzz_counts_against_the_limit(self, server, monkeypatch):
        """Buzz mode is an XInput pad underneath, so it occupies a slot too."""
        monkeypatch.setattr(xinput, "free_slot_count", lambda: 0)
        sock = self._connect(server, DeviceType.BUZZ)
        try:
            assert server.xinput_warning is not None
        finally:
            sock.close()

    def test_dualshock_never_warns(self, server, monkeypatch):
        """A DS4 is a HID device; it does not consume an XInput slot."""
        monkeypatch.setattr(xinput, "free_slot_count", lambda: 0)
        sock = self._connect(server, DeviceType.DUALSHOCK4)
        try:
            assert server.xinput_warning is None
        finally:
            sock.close()

    def test_no_warning_when_slots_are_available(self, server, monkeypatch):
        monkeypatch.setattr(xinput, "free_slot_count", lambda: 4)
        sock = self._connect(server, DeviceType.XBOX360)
        try:
            assert server.xinput_warning is None
        finally:
            sock.close()

    def test_warning_clears_on_restart(self, server, monkeypatch):
        monkeypatch.setattr(xinput, "free_slot_count", lambda: 0)
        sock = self._connect(server, DeviceType.XBOX360)
        sock.close()
        assert server.xinput_warning is not None
        server.stop()
        server.start()
        assert server.xinput_warning is None
