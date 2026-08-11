"""Virtual controller devices.

The server never talks to ViGEm directly; it talks to a :class:`VirtualPad`.
That indirection is what makes the input pipeline testable on a machine with no
ViGEmBus driver installed — :class:`FakeBackend` records what would have been sent.

Axis convention on the wire is "+Y = up" (see ``docs/PROTOCOL.md``). XInput uses
the same convention, DualShock 4 uses the opposite one, and that single sign flip
lives in :class:`DualShock4Pad` and nowhere else.
"""

from __future__ import annotations

import abc
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol as TypingProtocol

from . import buzz
from .protocol import (
    AXIS_MAX,
    Button,
    DPad,
    DeviceType,
    Feature,
    InputState,
    axis_to_float,
)

log = logging.getLogger(__name__)

#: Called with ``(large_motor, small_motor)``, both ``0``…``255``.
RumbleCallback = Callable[[int, int], None]
#: Called with ``(r, g, b)``.
LedCallback = Callable[[int, int, int], None]

#: A DS4 reports its triggers as buttons too, once pressed past this point.
DS4_TRIGGER_BUTTON_THRESHOLD = 32


class VirtualPad(abc.ABC):
    """A virtual game controller presented to Windows."""

    device_type: DeviceType
    features: Feature = Feature.NONE

    @abc.abstractmethod
    def apply(self, state: InputState) -> None:
        """Push one input frame to the device."""

    @abc.abstractmethod
    def reset(self) -> None:
        """Release every button and centre every axis."""

    def close(self) -> None:
        """Release the underlying device. Idempotent."""


# --- shared translation helpers ---------------------------------------------

def resolve_buttons(device_type: DeviceType, buttons_low: int) -> int:
    """Return the gamepad ``buttons_low`` bits to apply for this device type.

    For Buzz clients the wire carries *semantic* buzz bits, which must be
    translated to RPCS3's default gamepad bindings; every other device type sends
    gamepad bits already.
    """
    if device_type is DeviceType.BUZZ:
        return buzz.translate_buttons(buttons_low)
    return buttons_low & 0xFF


def dpad_direction(buttons_high: int) -> tuple[int, int]:
    """Reduce the four d-pad bits to an ``(x, y)`` pair in ``-1``…``1``.

    ``y`` is positive for *up*. Opposite directions cancel, which is what real
    hardware does and what keeps an 8-way hat from receiving an impossible value.
    """
    x = (1 if buttons_high & DPad.RIGHT else 0) - (1 if buttons_high & DPad.LEFT else 0)
    y = (1 if buttons_high & DPad.UP else 0) - (1 if buttons_high & DPad.DOWN else 0)
    return x, y


# --- ViGEm-backed implementations -------------------------------------------

class _VGamepadPad(VirtualPad):
    """Common plumbing for the two ViGEm device types."""

    def __init__(self, gamepad, device_type: DeviceType) -> None:
        self._gamepad = gamepad
        self._lock = threading.Lock()
        self._closed = False
        self.device_type = device_type

    def _apply_locked(self, state: InputState) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def apply(self, state: InputState) -> None:
        with self._lock:
            if self._closed:
                return
            self._apply_locked(state)
            self._gamepad.update()

    def reset(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._gamepad.reset()
            self._gamepad.update()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._gamepad.reset()
                self._gamepad.update()
            except Exception:  # noqa: BLE001 - the device may already be gone
                log.debug("reset during close failed", exc_info=True)
            try:
                self._gamepad.unregister_notification()
            except Exception:  # noqa: BLE001
                log.debug("unregister_notification failed", exc_info=True)
            # vgamepad frees the ViGEm target in __del__; dropping the reference
            # is the documented way to disconnect the virtual pad.
            self._gamepad = None


class Xbox360Pad(_VGamepadPad):
    """A virtual Xbox 360 pad. Also used for Buzz, with translated buttons."""

    _BUTTON_MAP: list[tuple[Button, str]] = [
        (Button.SOUTH, "XUSB_GAMEPAD_A"),
        (Button.EAST, "XUSB_GAMEPAD_B"),
        (Button.WEST, "XUSB_GAMEPAD_X"),
        (Button.NORTH, "XUSB_GAMEPAD_Y"),
        (Button.LEFT_SHOULDER, "XUSB_GAMEPAD_LEFT_SHOULDER"),
        (Button.RIGHT_SHOULDER, "XUSB_GAMEPAD_RIGHT_SHOULDER"),
        (Button.BACK, "XUSB_GAMEPAD_BACK"),
        (Button.START, "XUSB_GAMEPAD_START"),
    ]
    _HIGH_MAP: list[tuple[DPad, str]] = [
        (DPad.LEFT_THUMB, "XUSB_GAMEPAD_LEFT_THUMB"),
        (DPad.RIGHT_THUMB, "XUSB_GAMEPAD_RIGHT_THUMB"),
        (DPad.UP, "XUSB_GAMEPAD_DPAD_UP"),
        (DPad.DOWN, "XUSB_GAMEPAD_DPAD_DOWN"),
        (DPad.LEFT, "XUSB_GAMEPAD_DPAD_LEFT"),
        (DPad.RIGHT, "XUSB_GAMEPAD_DPAD_RIGHT"),
        (DPad.GUIDE, "XUSB_GAMEPAD_GUIDE"),
    ]

    def __init__(self, gamepad, xusb_button, device_type: DeviceType = DeviceType.XBOX360):
        super().__init__(gamepad, device_type)
        self.features = Feature.RUMBLE
        self._buttons = [(bit, getattr(xusb_button, name)) for bit, name in self._BUTTON_MAP]
        self._high = [(bit, getattr(xusb_button, name)) for bit, name in self._HIGH_MAP]

    def _apply_locked(self, state: InputState) -> None:
        gp = self._gamepad
        gp.left_joystick_float(axis_to_float(state.lx), axis_to_float(state.ly))
        gp.right_joystick_float(axis_to_float(state.rx), axis_to_float(state.ry))
        gp.left_trigger(state.left_trigger)
        gp.right_trigger(state.right_trigger)

        low = resolve_buttons(self.device_type, state.buttons_low)
        for bit, native in self._buttons:
            gp.press_button(native) if low & bit else gp.release_button(native)
        for bit, native in self._high:
            gp.press_button(native) if state.buttons_high & bit else gp.release_button(native)


class DualShock4Pad(_VGamepadPad):
    """A virtual DualShock 4.

    Two things differ from the Xbox path and both were broken in the original
    implementation: the d-pad is an 8-way *hat* rather than four independent bits
    (``DS4_BUTTON_DPAD_*`` live in ``DS4_DPAD_DIRECTIONS``, not ``DS4_BUTTONS``),
    and the Y axes are inverted relative to XInput.
    """

    _BUTTON_MAP: list[tuple[Button, str]] = [
        (Button.SOUTH, "DS4_BUTTON_CROSS"),
        (Button.EAST, "DS4_BUTTON_CIRCLE"),
        (Button.WEST, "DS4_BUTTON_SQUARE"),
        (Button.NORTH, "DS4_BUTTON_TRIANGLE"),
        (Button.LEFT_SHOULDER, "DS4_BUTTON_SHOULDER_LEFT"),
        (Button.RIGHT_SHOULDER, "DS4_BUTTON_SHOULDER_RIGHT"),
        (Button.BACK, "DS4_BUTTON_SHARE"),
        (Button.START, "DS4_BUTTON_OPTIONS"),
    ]
    _HIGH_MAP: list[tuple[DPad, str]] = [
        (DPad.LEFT_THUMB, "DS4_BUTTON_THUMB_LEFT"),
        (DPad.RIGHT_THUMB, "DS4_BUTTON_THUMB_RIGHT"),
    ]
    #: ``(x, y)`` with ``y`` positive for up → ``DS4_DPAD_DIRECTIONS`` member name.
    _DPAD_MAP: dict[tuple[int, int], str] = {
        (0, 0): "DS4_BUTTON_DPAD_NONE",
        (0, 1): "DS4_BUTTON_DPAD_NORTH",
        (1, 1): "DS4_BUTTON_DPAD_NORTHEAST",
        (1, 0): "DS4_BUTTON_DPAD_EAST",
        (1, -1): "DS4_BUTTON_DPAD_SOUTHEAST",
        (0, -1): "DS4_BUTTON_DPAD_SOUTH",
        (-1, -1): "DS4_BUTTON_DPAD_SOUTHWEST",
        (-1, 0): "DS4_BUTTON_DPAD_WEST",
        (-1, 1): "DS4_BUTTON_DPAD_NORTHWEST",
    }

    def __init__(self, gamepad, ds4_buttons, ds4_dpad, ds4_special):
        super().__init__(gamepad, DeviceType.DUALSHOCK4)
        self.features = Feature.RUMBLE | Feature.LED
        self._buttons = [(bit, getattr(ds4_buttons, name)) for bit, name in self._BUTTON_MAP]
        self._high = [(bit, getattr(ds4_buttons, name)) for bit, name in self._HIGH_MAP]
        self._dpad = {key: getattr(ds4_dpad, name) for key, name in self._DPAD_MAP.items()}
        self._trigger_left = ds4_buttons.DS4_BUTTON_TRIGGER_LEFT
        self._trigger_right = ds4_buttons.DS4_BUTTON_TRIGGER_RIGHT
        self._ps_button = ds4_special.DS4_SPECIAL_BUTTON_PS

    def _apply_locked(self, state: InputState) -> None:
        gp = self._gamepad
        # DS4 axes run 0 (up/left) … 255 (down/right), so Y is negated here.
        gp.left_joystick_float(axis_to_float(state.lx), -axis_to_float(state.ly))
        gp.right_joystick_float(axis_to_float(state.rx), -axis_to_float(state.ry))
        gp.left_trigger(state.left_trigger)
        gp.right_trigger(state.right_trigger)

        low = state.buttons_low
        for bit, native in self._buttons:
            gp.press_button(native) if low & bit else gp.release_button(native)
        for bit, native in self._high:
            gp.press_button(native) if state.buttons_high & bit else gp.release_button(native)

        # A real DS4 also latches L2/R2 as digital buttons.
        for value, native in (
            (state.left_trigger, self._trigger_left),
            (state.right_trigger, self._trigger_right),
        ):
            if value >= DS4_TRIGGER_BUTTON_THRESHOLD:
                gp.press_button(native)
            else:
                gp.release_button(native)

        if state.buttons_high & DPad.GUIDE:
            gp.press_special_button(self._ps_button)
        else:
            gp.release_special_button(self._ps_button)

        gp.directional_pad(self._dpad[dpad_direction(state.buttons_high)])


# --- test double ------------------------------------------------------------

@dataclass
class FakePadState:
    """Everything :class:`FakePad` has been told to do, in a comparable form."""

    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    left_trigger: int = 0
    right_trigger: int = 0
    buttons_low: int = 0
    buttons_high: int = 0
    dpad: tuple[int, int] = (0, 0)


class FakePad(VirtualPad):
    """A :class:`VirtualPad` that records instead of touching hardware."""

    def __init__(self, device_type: DeviceType) -> None:
        self.device_type = device_type
        self.features = Feature.RUMBLE | (
            Feature.LED if device_type is DeviceType.DUALSHOCK4 else Feature.NONE
        )
        self.state = FakePadState()
        self.frames: list[InputState] = []
        self.reset_count = 0
        self.closed = False

    def apply(self, state: InputState) -> None:
        if self.closed:
            raise RuntimeError("apply() called on a closed pad")
        self.frames.append(state)
        y_sign = -1.0 if self.device_type is DeviceType.DUALSHOCK4 else 1.0
        self.state = FakePadState(
            lx=axis_to_float(state.lx),
            ly=y_sign * axis_to_float(state.ly),
            rx=axis_to_float(state.rx),
            ry=y_sign * axis_to_float(state.ry),
            left_trigger=state.left_trigger,
            right_trigger=state.right_trigger,
            buttons_low=resolve_buttons(self.device_type, state.buttons_low),
            buttons_high=state.buttons_high,
            dpad=dpad_direction(state.buttons_high),
        )

    def reset(self) -> None:
        self.reset_count += 1
        self.state = FakePadState()

    def close(self) -> None:
        self.closed = True


# --- backends ---------------------------------------------------------------

class PadBackend(TypingProtocol):
    """Creates :class:`VirtualPad` instances."""

    def create(
        self,
        device_type: DeviceType,
        on_rumble: RumbleCallback | None = None,
        on_led: LedCallback | None = None,
    ) -> VirtualPad:
        ...


class DriverUnavailableError(RuntimeError):
    """Raised when ViGEmBus is missing or the virtual bus cannot be opened."""


class FakeBackend:
    """In-memory backend used by the test suite and by ``--simulate``."""

    def __init__(self) -> None:
        self.created: list[FakePad] = []

    def create(
        self,
        device_type: DeviceType,
        on_rumble: RumbleCallback | None = None,
        on_led: LedCallback | None = None,
    ) -> VirtualPad:
        pad = FakePad(device_type)
        pad.on_rumble = on_rumble  # type: ignore[attr-defined]
        pad.on_led = on_led  # type: ignore[attr-defined]
        self.created.append(pad)
        return pad


class VGamepadBackend:
    """Real ViGEm devices, via the ``vgamepad`` package."""

    def __init__(self) -> None:
        try:
            import vgamepad  # noqa: PLC0415 - imported lazily so tests need no driver
        except Exception as exc:  # noqa: BLE001
            raise DriverUnavailableError(str(exc)) from exc
        self._vg = vgamepad

    def create(
        self,
        device_type: DeviceType,
        on_rumble: RumbleCallback | None = None,
        on_led: LedCallback | None = None,
    ) -> VirtualPad:
        vg = self._vg
        try:
            if device_type is DeviceType.DUALSHOCK4:
                raw = vg.VDS4Gamepad()
                pad: VirtualPad = DualShock4Pad(
                    raw, vg.DS4_BUTTONS, vg.DS4_DPAD_DIRECTIONS, vg.DS4_SPECIAL_BUTTONS
                )
            else:
                raw = vg.VX360Gamepad()
                pad = Xbox360Pad(raw, vg.XUSB_BUTTON, device_type)
        except Exception as exc:  # noqa: BLE001
            raise DriverUnavailableError(f"could not create {device_type.label}: {exc}") from exc

        if on_rumble is not None or on_led is not None:
            self._register_notification(raw, on_rumble, on_led)
        return pad

    @staticmethod
    def _register_notification(
        raw, on_rumble: RumbleCallback | None, on_led: LedCallback | None
    ) -> None:
        # vgamepad validates this signature by equality, so it must match exactly.
        def callback(client, target, large_motor, small_motor, led_number, user_data):
            try:
                if on_rumble is not None:
                    on_rumble(int(large_motor), int(small_motor))
                if on_led is not None:
                    value = int(led_number) & 0xFF
                    on_led(value, value, value)
            except Exception:  # noqa: BLE001 - never let an exception cross into ViGEm
                log.exception("rumble/LED callback failed")

        try:
            raw.register_notification(callback_function=callback)
        except Exception:  # noqa: BLE001
            log.warning("force feedback unavailable for this device", exc_info=True)


def default_backend() -> PadBackend:
    """The real backend, or a clear error explaining that the driver is missing."""
    return VGamepadBackend()
