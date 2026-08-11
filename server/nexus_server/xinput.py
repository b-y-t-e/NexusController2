"""Reading back the XInput slot table.

XInput exposes exactly **four** controller slots for the whole machine. ViGEm will
happily create a fifth virtual pad and report success, but Windows has nowhere to
put it, so it never appears to any game — the phone shows "connected" while every
button does nothing. Physical controllers occupy the same four slots, so one
plugged-in pad leaves room for only three phones.

This module lets the server notice that situation and say so out loud.
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

log = logging.getLogger(__name__)

MAX_XINPUT_SLOTS = 4
ERROR_SUCCESS = 0


class _XInputGamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XInputState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _XInputGamepad)]


def _load() -> ctypes.WinDLL | None:
    if os.name != "nt":
        return None
    for name in ("XInput1_4.dll", "XInput1_3.dll", "XInput9_1_0.dll"):
        try:
            return ctypes.WinDLL(name)
        except OSError:
            continue
    log.debug("no XInput DLL available")
    return None


_XINPUT = _load()


def available() -> bool:
    """True when the XInput slot table can be queried at all."""
    return _XINPUT is not None


def occupied_slots() -> set[int] | None:
    """Slots currently holding a controller, or ``None`` if XInput is unavailable."""
    if _XINPUT is None:
        return None
    occupied: set[int] = set()
    state = _XInputState()
    for index in range(MAX_XINPUT_SLOTS):
        try:
            if _XINPUT.XInputGetState(index, ctypes.byref(state)) == ERROR_SUCCESS:
                occupied.add(index)
        except OSError:  # pragma: no cover - defensive
            log.debug("XInputGetState failed for slot %d", index, exc_info=True)
            return None
    return occupied


def free_slot_count() -> int | None:
    """How many XInput slots are still free, or ``None`` if it cannot be determined."""
    occupied = occupied_slots()
    return None if occupied is None else MAX_XINPUT_SLOTS - len(occupied)


def capacity_warning(free: int | None, pending: int) -> str | None:
    """Explain, if needed, why a pad about to be created will not be usable.

    ``pending`` is how many XInput-backed pads the server is about to be holding,
    including the one being created now.
    """
    if free is None or free > 0:
        return None
    return (
        f"XInput has no free slot — this is virtual pad {pending} and Windows only "
        f"exposes {MAX_XINPUT_SLOTS} controllers in total. It will connect but games "
        "will not see it. Unplug a physical controller, or use DualShock 4 mode, "
        "which does not use an XInput slot."
    )
