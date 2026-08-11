"""Virtual pad behaviour, exercised through FakePad (no ViGEmBus required)."""

import pytest

from nexus_server import devices
from nexus_server.buzz import BuzzButton
from nexus_server.devices import (
    DualShock4Pad,
    FakeBackend,
    FakePad,
    Xbox360Pad,
    dpad_direction,
    resolve_buttons,
)
from nexus_server.protocol import Button, DeviceType, DPad, Feature, InputState


class TestResolveButtons:
    def test_gamepad_types_pass_through(self):
        for device_type in (DeviceType.XBOX360, DeviceType.DUALSHOCK4):
            assert resolve_buttons(device_type, int(Button.SOUTH)) == int(Button.SOUTH)

    def test_buzz_is_translated(self):
        assert resolve_buttons(DeviceType.BUZZ, int(BuzzButton.RED)) == int(Button.RIGHT_SHOULDER)

    def test_buzz_blue_becomes_north_not_left_shoulder(self):
        """Blue is bit 0x10, which on a gamepad would be LB — it must be remapped."""
        assert resolve_buttons(DeviceType.BUZZ, 0x10) == int(Button.NORTH)
        assert resolve_buttons(DeviceType.XBOX360, 0x10) == int(Button.LEFT_SHOULDER)

    def test_high_bits_masked(self):
        assert resolve_buttons(DeviceType.XBOX360, 0x1FF) == 0xFF


class TestDpadDirection:
    @pytest.mark.parametrize(
        ("bits", "expected"),
        [
            (DPad.NONE, (0, 0)),
            (DPad.UP, (0, 1)),
            (DPad.DOWN, (0, -1)),
            (DPad.LEFT, (-1, 0)),
            (DPad.RIGHT, (1, 0)),
            (DPad.UP | DPad.RIGHT, (1, 1)),
            (DPad.DOWN | DPad.LEFT, (-1, -1)),
        ],
    )
    def test_directions(self, bits, expected):
        assert dpad_direction(int(bits)) == expected

    def test_opposites_cancel(self):
        assert dpad_direction(int(DPad.UP | DPad.DOWN)) == (0, 0)
        assert dpad_direction(int(DPad.LEFT | DPad.RIGHT)) == (0, 0)

    def test_unrelated_bits_ignored(self):
        assert dpad_direction(int(DPad.GUIDE | DPad.LEFT_THUMB)) == (0, 0)


class TestFakePad:
    def test_axes_map_to_unit_range(self):
        pad = FakePad(DeviceType.XBOX360)
        pad.apply(InputState(lx=127, ly=-127, rx=0, ry=64))
        assert pad.state.lx == pytest.approx(1.0)
        assert pad.state.ly == pytest.approx(-1.0)
        assert pad.state.rx == 0.0
        assert pad.state.ry == pytest.approx(64 / 127)

    def test_xbox_keeps_y_positive_up(self):
        pad = FakePad(DeviceType.XBOX360)
        pad.apply(InputState(ly=127))
        assert pad.state.ly > 0

    def test_dualshock_inverts_y(self):
        """DS4 axes run 0 = up … 255 = down, the opposite of XInput."""
        pad = FakePad(DeviceType.DUALSHOCK4)
        pad.apply(InputState(ly=127, ry=127))
        assert pad.state.ly < 0 and pad.state.ry < 0

    def test_buzz_pad_translates_buttons(self):
        pad = FakePad(DeviceType.BUZZ)
        pad.apply(InputState(buttons_low=int(BuzzButton.YELLOW)))
        assert pad.state.buttons_low == int(Button.SOUTH)

    def test_reset_clears_state(self):
        pad = FakePad(DeviceType.XBOX360)
        pad.apply(InputState(lx=100, buttons_low=0xFF))
        pad.reset()
        assert pad.state.lx == 0.0 and pad.state.buttons_low == 0
        assert pad.reset_count == 1

    def test_apply_after_close_raises(self):
        pad = FakePad(DeviceType.XBOX360)
        pad.close()
        with pytest.raises(RuntimeError):
            pad.apply(InputState())

    def test_features_advertise_rumble(self):
        assert FakePad(DeviceType.XBOX360).features & Feature.RUMBLE
        assert FakePad(DeviceType.DUALSHOCK4).features & Feature.LED
        assert not FakePad(DeviceType.XBOX360).features & Feature.LED

    def test_frames_are_recorded_in_order(self):
        pad = FakePad(DeviceType.XBOX360)
        for value in (1, 2, 3):
            pad.apply(InputState(lx=value))
        assert [f.lx for f in pad.frames] == [1, 2, 3]


class TestFakeBackend:
    def test_creates_requested_type(self):
        backend = FakeBackend()
        pad = backend.create(DeviceType.DUALSHOCK4)
        assert pad.device_type is DeviceType.DUALSHOCK4
        assert backend.created == [pad]

    def test_callbacks_are_attached(self):
        backend = FakeBackend()
        pad = backend.create(DeviceType.XBOX360, on_rumble=lambda a, b: None)
        assert pad.on_rumble is not None


class _SpyGamepad:
    """Minimal stand-in for a vgamepad device, recording every call."""

    def __init__(self):
        self.pressed = set()
        self.left = (0.0, 0.0)
        self.right = (0.0, 0.0)
        self.triggers = (0, 0)
        self.dpad = None
        self.specials = set()
        self.updates = 0
        self.resets = 0

    def left_joystick_float(self, x, y): self.left = (x, y)
    def right_joystick_float(self, x, y): self.right = (x, y)
    def left_trigger(self, v): self.triggers = (v, self.triggers[1])
    def right_trigger(self, v): self.triggers = (self.triggers[0], v)
    def press_button(self, b): self.pressed.add(b)
    def release_button(self, b): self.pressed.discard(b)
    def press_special_button(self, b): self.specials.add(b)
    def release_special_button(self, b): self.specials.discard(b)
    def directional_pad(self, d): self.dpad = d
    def update(self): self.updates += 1
    def reset(self): self.resets += 1; self.pressed.clear()
    def unregister_notification(self): pass


class _Names:
    """Turns attribute access into the attribute's own name, standing in for the
    vgamepad enums so we can assert on which constant was used."""

    def __getattr__(self, name):
        return name


class TestXbox360Pad:
    def _pad(self):
        spy = _SpyGamepad()
        return spy, Xbox360Pad(spy, _Names())

    def test_buttons_are_pressed_and_released(self):
        spy, pad = self._pad()
        pad.apply(InputState(buttons_low=int(Button.SOUTH)))
        assert "XUSB_GAMEPAD_A" in spy.pressed
        pad.apply(InputState())
        assert "XUSB_GAMEPAD_A" not in spy.pressed

    def test_dpad_and_guide(self):
        spy, pad = self._pad()
        pad.apply(InputState(buttons_high=int(DPad.UP | DPad.GUIDE)))
        assert {"XUSB_GAMEPAD_DPAD_UP", "XUSB_GAMEPAD_GUIDE"} <= spy.pressed

    def test_y_axis_not_inverted(self):
        spy, pad = self._pad()
        pad.apply(InputState(ly=127))
        assert spy.left[1] == pytest.approx(1.0)

    def test_update_called_once_per_frame(self):
        spy, pad = self._pad()
        pad.apply(InputState())
        pad.apply(InputState())
        assert spy.updates == 2

    def test_buzz_device_uses_translated_buttons(self):
        spy = _SpyGamepad()
        pad = Xbox360Pad(spy, _Names(), DeviceType.BUZZ)
        pad.apply(InputState(buttons_low=int(BuzzButton.RED)))
        assert "XUSB_GAMEPAD_RIGHT_SHOULDER" in spy.pressed
        assert "XUSB_GAMEPAD_A" not in spy.pressed

    def test_close_is_idempotent(self):
        spy, pad = self._pad()
        pad.close()
        pad.close()
        pad.apply(InputState())  # must not raise
        assert spy.resets == 1


class TestDualShock4Pad:
    def _pad(self):
        spy = _SpyGamepad()
        return spy, DualShock4Pad(spy, _Names(), _Names(), _Names())

    def test_face_buttons(self):
        spy, pad = self._pad()
        pad.apply(InputState(buttons_low=int(Button.SOUTH | Button.NORTH)))
        assert {"DS4_BUTTON_CROSS", "DS4_BUTTON_TRIANGLE"} <= spy.pressed

    def test_dpad_is_a_hat_not_four_bits(self):
        """The original code looked for DS4_BUTTON_DPAD_* on DS4_BUTTONS, where
        they do not exist — the DS4 d-pad is an 8-way hat."""
        spy, pad = self._pad()
        pad.apply(InputState(buttons_high=int(DPad.UP)))
        assert spy.dpad == "DS4_BUTTON_DPAD_NORTH"
        pad.apply(InputState(buttons_high=int(DPad.UP | DPad.RIGHT)))
        assert spy.dpad == "DS4_BUTTON_DPAD_NORTHEAST"
        pad.apply(InputState())
        assert spy.dpad == "DS4_BUTTON_DPAD_NONE"

    def test_y_axis_inverted(self):
        spy, pad = self._pad()
        pad.apply(InputState(ly=127, ry=-127))
        assert spy.left[1] == pytest.approx(-1.0)
        assert spy.right[1] == pytest.approx(1.0)

    def test_triggers_latch_as_buttons(self):
        spy, pad = self._pad()
        pad.apply(InputState(left_trigger=255, right_trigger=0))
        assert "DS4_BUTTON_TRIGGER_LEFT" in spy.pressed
        assert "DS4_BUTTON_TRIGGER_RIGHT" not in spy.pressed

    def test_trigger_below_threshold_does_not_latch(self):
        spy, pad = self._pad()
        pad.apply(InputState(left_trigger=devices.DS4_TRIGGER_BUTTON_THRESHOLD - 1))
        assert "DS4_BUTTON_TRIGGER_LEFT" not in spy.pressed

    def test_ps_button_is_a_special_button(self):
        spy, pad = self._pad()
        pad.apply(InputState(buttons_high=int(DPad.GUIDE)))
        assert "DS4_SPECIAL_BUTTON_PS" in spy.specials
        pad.apply(InputState())
        assert not spy.specials

    def test_advertises_led(self):
        _, pad = self._pad()
        assert pad.features & Feature.LED
