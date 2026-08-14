"""Mouse and keyboard injection, and pad-button → keystroke bindings.

This is the only part of the server that can act on the PC outside of a game, so
it is deliberately gated: :class:`DesktopControl` refuses every request unless the
feature has been switched on *and* the request comes from the slot holding the
desktop lock. In the original implementation any device on the network could type
into the machine as Administrator with no opt-in at all.
"""

from __future__ import annotations

import abc
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Iterable

from .protocol import Button, DPad, MouseDelta, ScrollDelta

log = logging.getLogger(__name__)

#: What Windows returns when UIPI refuses injected input — see
#: :meth:`PynputDesktop._report_blocked`. Not a failure of ours, and not
#: permanent: it lasts exactly as long as the elevated window keeps focus.
ACCESS_DENIED = 5
#: How often that is worth saying. It happens once per message otherwise, and
#: messages arrive a hundred times a second.
BLOCKED_REPORT_SECONDS = 30.0


class DesktopBackend(abc.ABC):
    """Something that can move the cursor and press keys."""

    @abc.abstractmethod
    def move(self, dx: int, dy: int) -> None: ...

    @abc.abstractmethod
    def scroll(self, dx: int, dy: int) -> None: ...

    @abc.abstractmethod
    def set_mouse_button(self, button: str, pressed: bool) -> None: ...

    @abc.abstractmethod
    def type_text(self, text: str) -> None: ...

    @abc.abstractmethod
    def set_key(self, key: str, pressed: bool) -> None: ...

    def release_all(self) -> None:
        """Release anything currently held. Called when a client goes away."""


@dataclass
class FakeDesktop(DesktopBackend):
    """Records requests instead of performing them."""

    moves: list[tuple[int, int]]
    scrolls: list[tuple[int, int]]
    mouse_buttons: dict[str, bool]
    typed: list[str]
    keys: dict[str, bool]
    release_all_count: int = 0

    def __init__(self) -> None:
        self.moves = []
        self.scrolls = []
        self.mouse_buttons = {}
        self.typed = []
        self.keys = {}
        self.release_all_count = 0

    def move(self, dx: int, dy: int) -> None:
        self.moves.append((dx, dy))

    def scroll(self, dx: int, dy: int) -> None:
        self.scrolls.append((dx, dy))

    def set_mouse_button(self, button: str, pressed: bool) -> None:
        self.mouse_buttons[button] = pressed

    def type_text(self, text: str) -> None:
        self.typed.append(text)

    def set_key(self, key: str, pressed: bool) -> None:
        self.keys[key] = pressed

    def release_all(self) -> None:
        self.release_all_count += 1
        self.mouse_buttons = {k: False for k in self.mouse_buttons}
        self.keys = {k: False for k in self.keys}


def relative_mover():
    """A function that nudges the cursor by (dx, dy), or ``None`` off Windows.

    Why this exists at all: ``pynput``'s relative move is
    ``position = position + (dx, dy)`` — a ``GetCursorPos`` followed by a
    ``SetCursorPos``. Read-modify-write of a value the whole system writes to,
    a hundred times a second, from a socket thread. Two ways that shows up as a
    cursor that jumps:

    * ``SetCursorPos`` is not synchronous with the input queue, so a burst of
      messages can read a position the last write has not landed in yet. The
      new absolute position is then computed from a stale base, and the pointer
      snaps back over ground it had already covered.
    * it fights the mouse on the desk. Nudge the real mouse while the phone is
      moving the cursor and every message teleports the pointer back to where
      *this* side thought it was.

    ``SendInput`` with ``MOUSEEVENTF_MOVE`` is what a mouse driver does: a
    relative event handed to the same queue as every other input, accumulated in
    order by the OS, with nothing read back. It also picks up the pointer speed
    and acceleration the user has set, which is what makes it feel like their
    mouse rather than like a remote desktop.
    """
    if os.name != "nt":
        return None
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    # Pointer-sized, and it matters: on 64-bit a DWORD here mis-sizes the
    # structure and SendInput rejects every event with no other symptom.
    ULONG_PTR = wintypes.WPARAM

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]

    INPUT_MOUSE = 0
    MOUSEEVENTF_MOVE = 0x0001

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT

    def move(dx: int, dy: int) -> None:
        event = INPUT(
            type=INPUT_MOUSE,
            union=_INPUTUNION(mi=MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, 0)),
        )
        if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
            raise OSError(ctypes.get_last_error(), "SendInput refused a mouse move")

    return move


class PynputDesktop(DesktopBackend):
    """Real input injection via ``pynput``."""

    def __init__(self) -> None:
        from pynput.keyboard import Controller as KeyboardController, Key  # noqa: PLC0415
        from pynput.mouse import Button as MouseButton, Controller as MouseController  # noqa: PLC0415

        self._keyboard = KeyboardController()
        self._mouse = MouseController()
        self._Key = Key
        self._mouse_buttons = {
            "left": MouseButton.left,
            "right": MouseButton.right,
            "middle": MouseButton.middle,
        }
        self._held_mouse: set[str] = set()
        self._held_keys: set[str] = set()
        #: Windows only; see :func:`relative_mover`. None means pynput's own way.
        self._relative_move = relative_mover()
        self._blocked_reported = 0.0

    def move(self, dx: int, dy: int) -> None:
        if not (dx or dy):
            return
        if self._relative_move is not None:
            try:
                self._relative_move(dx, dy)
                return
            except OSError as exc:
                if getattr(exc, "winerror", None) == ACCESS_DENIED:
                    # Not a broken backend: Windows refuses injected input while
                    # the *focused* window belongs to a program running as
                    # administrator (UIPI). It comes back the moment that window
                    # loses focus, so this must not be a one-way switch — the
                    # next message tries again, and only the message to the user
                    # is rate-limited.
                    self._report_blocked()
                else:
                    log.warning("falling back to pynput for cursor movement: %s", exc)
                    self._relative_move = None
        self._mouse.move(dx, dy)

    def _report_blocked(self) -> None:
        now = time.monotonic()
        if now - self._blocked_reported < BLOCKED_REPORT_SECONDS:
            return
        self._blocked_reported = now
        log.warning(
            "Windows is refusing injected input: the window in front belongs to a "
            "program running as administrator. Start Nexus Controller as "
            "administrator too, or click the desktop first."
        )

    def scroll(self, dx: int, dy: int) -> None:
        if dx or dy:
            self._mouse.scroll(dx, dy)

    def set_mouse_button(self, button: str, pressed: bool) -> None:
        native = self._mouse_buttons.get(button)
        if native is None:
            return
        if pressed and button not in self._held_mouse:
            self._mouse.press(native)
            self._held_mouse.add(button)
        elif not pressed and button in self._held_mouse:
            self._mouse.release(native)
            self._held_mouse.discard(button)

    def type_text(self, text: str) -> None:
        for char in text:
            if char == "\b":
                self._tap(self._Key.backspace)
            elif char in "\r\n":
                self._tap(self._Key.enter)
            elif char == "\t":
                self._tap(self._Key.tab)
            else:
                self._keyboard.type(char)

    def _tap(self, key) -> None:
        self._keyboard.press(key)
        self._keyboard.release(key)

    def _resolve(self, key: str):
        if len(key) == 1:
            return key
        return getattr(self._Key, key.lower(), None)

    def set_key(self, key: str, pressed: bool) -> None:
        native = self._resolve(key)
        if native is None:
            log.debug("unknown key name %r", key)
            return
        if pressed and key not in self._held_keys:
            self._keyboard.press(native)
            self._held_keys.add(key)
        elif not pressed and key in self._held_keys:
            self._keyboard.release(native)
            self._held_keys.discard(key)

    def release_all(self) -> None:
        for button in list(self._held_mouse):
            self.set_mouse_button(button, False)
        for key in list(self._held_keys):
            self.set_key(key, False)


def create_backend() -> DesktopBackend | None:
    """The real backend, or ``None`` when ``pynput`` is unavailable."""
    try:
        return PynputDesktop()
    except Exception as exc:  # noqa: BLE001
        log.warning("desktop control unavailable: %s", exc)
        return None


# --- gating -----------------------------------------------------------------

class DesktopControl:
    """Gates a :class:`DesktopBackend` behind an explicit opt-in and a slot lock."""

    def __init__(self, backend: DesktopBackend | None, *, enabled: bool = False, slot: int = 0):
        self._backend = backend
        self.enabled = enabled
        self.slot = slot

    @property
    def available(self) -> bool:
        return self._backend is not None

    def allows(self, slot: int) -> bool:
        return self.available and self.enabled and slot == self.slot

    def handle_mouse(self, slot: int, delta: MouseDelta) -> bool:
        """Apply a mouse message. Returns ``True`` if it was actually applied."""
        if not self.allows(slot):
            return False
        assert self._backend is not None
        self._backend.move(delta.dx, delta.dy)
        self._backend.set_mouse_button("left", bool(delta.buttons & MouseDelta.LEFT))
        self._backend.set_mouse_button("right", bool(delta.buttons & MouseDelta.RIGHT))
        self._backend.set_mouse_button("middle", bool(delta.buttons & MouseDelta.MIDDLE))
        return True

    def handle_scroll(self, slot: int, delta: ScrollDelta) -> bool:
        if not self.allows(slot):
            return False
        assert self._backend is not None
        self._backend.scroll(delta.dx, delta.dy)
        return True

    def handle_text(self, slot: int, text: str) -> bool:
        if not self.allows(slot):
            return False
        assert self._backend is not None
        self._backend.type_text(text)
        return True

    def set_key(self, slot: int, key: str, pressed: bool) -> bool:
        if not self.allows(slot):
            return False
        assert self._backend is not None
        self._backend.set_key(key, pressed)
        return True

    def release_all(self) -> None:
        if self._backend is not None:
            self._backend.release_all()


# --- pad button → keyboard key ----------------------------------------------

#: Names used by the dashboard's key-binding UI, mapped to protocol bits.
BUTTON_BITS: dict[str, tuple[str, int]] = {
    "a": ("low", int(Button.SOUTH)),
    "b": ("low", int(Button.EAST)),
    "x": ("low", int(Button.WEST)),
    "y": ("low", int(Button.NORTH)),
    "lb": ("low", int(Button.LEFT_SHOULDER)),
    "rb": ("low", int(Button.RIGHT_SHOULDER)),
    "back": ("low", int(Button.BACK)),
    "start": ("low", int(Button.START)),
    "l3": ("high", int(DPad.LEFT_THUMB)),
    "r3": ("high", int(DPad.RIGHT_THUMB)),
    "up": ("high", int(DPad.UP)),
    "down": ("high", int(DPad.DOWN)),
    "left": ("high", int(DPad.LEFT)),
    "right": ("high", int(DPad.RIGHT)),
    "guide": ("high", int(DPad.GUIDE)),
}


class KeyBindingEngine:
    """Turns held-button state into keyboard press/release *edges*.

    Feeding the same state twice must not produce a second press — games and text
    fields both misbehave when a key repeats at the input frame rate.
    """

    def __init__(self, bindings: dict[str, str] | None = None) -> None:
        self._bindings: dict[str, str] = {}
        self._held: set[str] = set()
        # update() runs on the client thread while release() and set_bindings()
        # can be called from the dashboard thread, so each of them has to be
        # atomic with respect to the others.
        #
        # This does NOT order them: an update() that starts after release() will
        # still record a press. What stops that press from reaching the keyboard
        # is the caller — the gate is shut before the reset, see
        # ControllerServer.reset_desktop_state. Do not weaken that order on the
        # strength of this lock.
        self._lock = threading.Lock()
        self.set_bindings(bindings or {})

    def set_bindings(self, bindings: dict[str, str]) -> list[tuple[str, bool]]:
        """Replace the bindings, returning the keys that must now be released.

        Rebinding a button that is *held* used to strand the old key: the held
        set is keyed by button name, so after the swap the release looked up the
        new key, and nothing ever let go of the old one.
        """
        cleaned = {
            name.lower(): key
            for name, key in bindings.items()
            if name.lower() in BUTTON_BITS and isinstance(key, str) and key
        }
        with self._lock:
            kept = {name for name in self._held if cleaned.get(name) == self._bindings.get(name)}
            # Two buttons can share a key. Releasing it because one of them was
            # rebound would let go of a key the other is still holding down, so
            # only keys nothing still holds are stranded.
            still_held = {self._bindings[name] for name in kept if name in self._bindings}
            stranded = []
            for name in sorted(self._held - kept):
                key = self._bindings.get(name)
                if key is not None and key not in still_held:
                    stranded.append((key, False))
                    still_held.add(key)
            self._held = kept
            self._bindings = cleaned
        return stranded

    @property
    def bindings(self) -> dict[str, str]:
        return dict(self._bindings)

    @property
    def active(self) -> bool:
        return bool(self._bindings)

    def update(self, buttons_low: int, buttons_high: int) -> list[tuple[str, bool]]:
        """Return the ``(key, pressed)`` transitions caused by this frame."""
        events: list[tuple[str, bool]] = []
        with self._lock:
            for name, key in self._bindings.items():
                which, bit = BUTTON_BITS[name]
                source = buttons_low if which == "low" else buttons_high
                is_down = bool(source & bit)
                was_down = name in self._held
                if is_down and not was_down:
                    self._held.add(name)
                    events.append((key, True))
                elif not is_down and was_down:
                    self._held.discard(name)
                    events.append((key, False))
        return events

    def release(self) -> list[tuple[str, bool]]:
        """Release everything this engine is holding, e.g. on disconnect."""
        with self._lock:
            events = [
                (self._bindings[name], False)
                for name in sorted(self._held)
                if name in self._bindings
            ]
            self._held.clear()
        return events

    def masked_buttons(self, buttons_low: int, buttons_high: int) -> tuple[int, int]:
        """Strip bound buttons from the pad state so they do not double-fire."""
        low, high = buttons_low, buttons_high
        for name in self._bindings:
            which, bit = BUTTON_BITS[name]
            if which == "low":
                low &= ~bit
            else:
                high &= ~bit
        return low & 0xFF, high & 0xFF


def gyro_to_mouse(
    roll: int, pitch: int, centre_roll: int, centre_pitch: int, *, deadzone: int = 300, divisor: int = 200
) -> tuple[int, int]:
    """Convert a gyro reading into a relative cursor delta."""
    dx = roll - centre_roll
    dy = pitch - centre_pitch
    return (
        int(dx / divisor) if abs(dx) > deadzone else 0,
        int(dy / divisor) if abs(dy) > deadzone else 0,
    )
