"""Desktop-control gating and pad-button → keystroke bindings."""

import pytest

from nexus_server.desktop import (
    DesktopControl,
    FakeDesktop,
    KeyBindingEngine,
    gyro_to_mouse,
)
from nexus_server.protocol import Button, DPad, MouseDelta, ScrollDelta


@pytest.fixture()
def backend():
    return FakeDesktop()


class TestDesktopControlGating:
    def test_disabled_by_default(self, backend):
        """Remote mouse/keyboard is opt-in — v1 had it always on for everyone."""
        control = DesktopControl(backend)
        assert control.enabled is False
        assert control.handle_mouse(0, MouseDelta(5, 5, 0)) is False
        assert backend.moves == []

    def test_enabled_allows_the_locked_slot(self, backend):
        control = DesktopControl(backend, enabled=True, slot=0)
        assert control.handle_mouse(0, MouseDelta(5, -3, MouseDelta.LEFT)) is True
        assert backend.moves == [(5, -3)]
        assert backend.mouse_buttons["left"] is True

    def test_other_slots_are_refused(self, backend):
        control = DesktopControl(backend, enabled=True, slot=0)
        assert control.handle_mouse(1, MouseDelta(5, 5, 0)) is False
        assert control.handle_text(1, "rm -rf") is False
        assert control.handle_scroll(2, ScrollDelta(1, 1)) is False
        assert backend.typed == []

    def test_lock_can_move_to_another_slot(self, backend):
        control = DesktopControl(backend, enabled=True, slot=2)
        assert control.handle_text(0, "no") is False
        assert control.handle_text(2, "yes") is True
        assert backend.typed == ["yes"]

    def test_unavailable_backend_never_allows(self):
        control = DesktopControl(None, enabled=True, slot=0)
        assert control.available is False
        assert control.allows(0) is False
        assert control.handle_text(0, "x") is False
        control.release_all()  # must not raise

    def test_all_three_mouse_buttons(self, backend):
        control = DesktopControl(backend, enabled=True)
        control.handle_mouse(0, MouseDelta(0, 0, MouseDelta.LEFT | MouseDelta.MIDDLE))
        assert backend.mouse_buttons == {"left": True, "right": False, "middle": True}

    def test_scroll(self, backend):
        control = DesktopControl(backend, enabled=True)
        assert control.handle_scroll(0, ScrollDelta(-2, 3)) is True
        assert backend.scrolls == [(-2, 3)]

    def test_release_all(self, backend):
        control = DesktopControl(backend, enabled=True)
        control.handle_mouse(0, MouseDelta(0, 0, MouseDelta.LEFT))
        control.release_all()
        assert backend.mouse_buttons["left"] is False
        assert backend.release_all_count == 1


class TestFakeDesktopBackend:
    def test_records_keys(self, backend):
        backend.set_key("space", True)
        assert backend.keys == {"space": True}


class TestKeyBindingEngine:
    def test_press_and_release_edges(self):
        engine = KeyBindingEngine({"a": "space"})
        assert engine.update(int(Button.SOUTH), 0) == [("space", True)]
        assert engine.update(0, 0) == [("space", False)]

    def test_held_button_does_not_repeat(self):
        """At 66 frames/s a repeat would spam the key."""
        engine = KeyBindingEngine({"a": "space"})
        engine.update(int(Button.SOUTH), 0)
        for _ in range(10):
            assert engine.update(int(Button.SOUTH), 0) == []

    def test_high_byte_buttons(self):
        engine = KeyBindingEngine({"up": "w", "guide": "esc"})
        events = dict(engine.update(0, int(DPad.UP | DPad.GUIDE)))
        assert events == {"w": True, "esc": True}

    def test_multiple_bindings_at_once(self):
        engine = KeyBindingEngine({"a": "1", "b": "2"})
        assert dict(engine.update(int(Button.SOUTH | Button.EAST), 0)) == {"1": True, "2": True}

    def test_unbound_buttons_are_ignored(self):
        engine = KeyBindingEngine({"a": "space"})
        assert engine.update(int(Button.START), 0) == []

    def test_unknown_button_names_are_dropped(self):
        engine = KeyBindingEngine({"nonsense": "x", "a": "space"})
        assert engine.bindings == {"a": "space"}

    def test_empty_key_is_dropped(self):
        assert KeyBindingEngine({"a": ""}).bindings == {}

    def test_masked_buttons_prevents_double_fire(self):
        """A bound button must not also reach the game as a pad button."""
        engine = KeyBindingEngine({"a": "space", "up": "w"})
        low, high = engine.masked_buttons(
            int(Button.SOUTH | Button.EAST), int(DPad.UP | DPad.DOWN)
        )
        assert low == int(Button.EAST)
        assert high == int(DPad.DOWN)

    def test_masking_is_a_no_op_without_bindings(self):
        engine = KeyBindingEngine()
        assert engine.masked_buttons(0xFF, 0xFF) == (0xFF, 0xFF)
        assert engine.active is False

    def test_release_frees_everything_held(self):
        engine = KeyBindingEngine({"a": "space", "b": "ctrl"})
        engine.update(int(Button.SOUTH | Button.EAST), 0)
        assert sorted(engine.release()) == [("ctrl", False), ("space", False)]
        assert engine.release() == []

    def test_rebinding_resets_cleanly(self):
        engine = KeyBindingEngine({"a": "space"})
        engine.update(int(Button.SOUTH), 0)
        engine.set_bindings({"b": "enter"})
        assert engine.update(int(Button.EAST), 0) == [("enter", True)]


class TestGyroToMouse:
    def test_inside_deadzone_produces_no_movement(self):
        assert gyro_to_mouse(100, 100, 0, 0) == (0, 0)

    def test_outside_deadzone_moves(self):
        dx, dy = gyro_to_mouse(1000, -1000, 0, 0)
        assert dx > 0 and dy < 0

    def test_centre_is_subtracted(self):
        assert gyro_to_mouse(5000, 5000, 5000, 5000) == (0, 0)

    def test_scaling(self):
        assert gyro_to_mouse(2000, 0, 0, 0, deadzone=100, divisor=100) == (20, 0)
