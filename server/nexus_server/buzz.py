"""Buzz! buzzer support.

Two separate things live here.

1. :data:`BUZZ_TO_XINPUT` — the mapping actually used at runtime. RPCS3 emulates
   the Buzz! dongle itself (as USB device ``054C:0002``, *"Logitech Buzz(tm)
   Controller V1"*) and feeds each of its four buzzers from an ordinary pad:
   buzzer *N* reads the pad configured as player *N*. So all we have to do on the
   PC is present a normal virtual Xbox 360 pad whose buttons match RPCS3's default
   ``buzz.yml`` bindings. No HID driver, no ViGEm extension, nothing exotic.

2. :func:`build_hid_report` / :func:`parse_led_report` — a reference
   implementation of the real dongle's wire format, kept because it is the part of
   the Buzz protocol that is easy to get wrong and expensive to rediscover. It is
   fully covered by tests against a real hardware capture. Nothing in the running
   server calls it today; it exists so a future native-HID backend has a verified
   starting point.

References:
  * ``rpcs3/Emu/Io/Buzz.cpp``, ``rpcs3/Emu/Io/buzz_config.h``
  * hardware capture: https://gist.github.com/Lewiscowles1986/eef220dac6f0549e4702393a7b9351f6
"""

from __future__ import annotations

import enum
from typing import Final

from .protocol import Button


class BuzzButton(enum.IntFlag):
    """Semantic Buzz bits as they appear in ``buttons_low`` in Buzz mode."""

    NONE = 0x00
    RED = 0x01
    YELLOW = 0x02
    GREEN = 0x04
    ORANGE = 0x08
    BLUE = 0x10


#: Colour order used by the hardware report and by RPCS3's ``buzz_btn`` enum.
#: This order is *not* alphabetical and *not* the on-screen order — do not "tidy" it.
HID_COLOUR_ORDER: Final[tuple[BuzzButton, ...]] = (
    BuzzButton.RED,
    BuzzButton.YELLOW,
    BuzzButton.GREEN,
    BuzzButton.ORANGE,
    BuzzButton.BLUE,
)

#: RPCS3 ``buzz_config.h`` defaults, expressed in our XInput-facing button bits.
#: Red=R1, Yellow=Cross, Green=Circle, Orange=Square, Blue=Triangle.
BUZZ_TO_XINPUT: Final[dict[BuzzButton, Button]] = {
    BuzzButton.RED: Button.RIGHT_SHOULDER,
    BuzzButton.YELLOW: Button.SOUTH,
    BuzzButton.GREEN: Button.EAST,
    BuzzButton.ORANGE: Button.WEST,
    BuzzButton.BLUE: Button.NORTH,
}

#: Human-readable names, for the dashboard and for logs.
BUZZ_LABELS: Final[dict[BuzzButton, str]] = {
    BuzzButton.RED: "Red (buzz)",
    BuzzButton.YELLOW: "Yellow",
    BuzzButton.GREEN: "Green",
    BuzzButton.ORANGE: "Orange",
    BuzzButton.BLUE: "Blue",
}

MAX_BUZZERS: Final = 4


def translate_buttons(buttons_low: int) -> int:
    """Translate semantic Buzz bits into gamepad ``buttons_low`` bits.

    Bits outside the five defined Buzz buttons are ignored, so a client that
    accidentally sets a spare bit cannot press an unrelated gamepad button.
    """
    result = 0
    for buzz_bit, gamepad_bit in BUZZ_TO_XINPUT.items():
        if buttons_low & buzz_bit:
            result |= int(gamepad_bit)
    return result


# --- reference implementation of the real dongle's wire format --------------

HID_REPORT_SIZE: Final = 5
LED_REPORT_SIZE: Final = 8


def build_hid_report(pressed: dict[int, BuzzButton]) -> bytes:
    """Build the 5-byte HID input report the real dongle (and RPCS3) emits.

    ``pressed`` maps a zero-based buzzer index (0…3) to the buttons held on it.

    Layout: bytes 0-1 are two centred analog values (always ``0x7f``), bytes 2-4
    carry 20 button bits — bit ``5 * buzzer + colour`` counted from bit 0 of byte
    2 — and the top nibble of byte 4 is constant ``0xF``.
    """
    buf = bytearray([0x7F, 0x7F, 0x00, 0x00, 0xF0])
    for index, buttons in pressed.items():
        if not 0 <= index < MAX_BUZZERS:
            raise ValueError(f"buzzer index out of range: {index}")
        for colour_offset, colour in enumerate(HID_COLOUR_ORDER):
            if buttons & colour:
                bit = 5 * index + colour_offset
                buf[2 + bit // 8] |= 1 << (bit % 8)
    return bytes(buf)


def parse_led_report(report: bytes) -> list[bool]:
    """Decode the host's 8-byte SET_REPORT lamp command into four booleans.

    Bytes 1-4 hold ``0xFF`` (lamp on) or ``0x00`` (off) for buzzers 1-4.
    RPCS3 receives this report but only logs it.
    """
    if len(report) < 5:
        raise ValueError(f"LED report must be at least 5 bytes, got {len(report)}")
    return [report[i] == 0xFF for i in range(1, 5)]
