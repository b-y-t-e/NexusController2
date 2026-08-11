"""Buzz! buzzer tests.

The HID expectations below are taken from a real hardware capture referenced by
RPCS3 (https://gist.github.com/Lewiscowles1986/eef220dac6f0549e4702393a7b9351f6),
so a regression here means we have diverged from the actual device.
"""

import pytest

from nexus_server import buzz
from nexus_server.buzz import BuzzButton
from nexus_server.protocol import Button


class TestXInputMapping:
    @pytest.mark.parametrize(
        ("buzz_button", "expected"),
        [
            # RPCS3 buzz_config.h defaults: Red=R1, Yellow=Cross, Green=Circle,
            # Orange=Square, Blue=Triangle.
            (BuzzButton.RED, Button.RIGHT_SHOULDER),
            (BuzzButton.YELLOW, Button.SOUTH),
            (BuzzButton.GREEN, Button.EAST),
            (BuzzButton.ORANGE, Button.WEST),
            (BuzzButton.BLUE, Button.NORTH),
        ],
    )
    def test_default_rpcs3_mapping(self, buzz_button, expected):
        assert buzz.translate_buttons(int(buzz_button)) == int(expected)

    def test_mapping_is_injective(self):
        targets = list(buzz.BUZZ_TO_XINPUT.values())
        assert len(set(targets)) == len(targets)

    def test_multiple_buttons_combine(self):
        pressed = int(BuzzButton.RED | BuzzButton.BLUE)
        assert buzz.translate_buttons(pressed) == int(Button.RIGHT_SHOULDER | Button.NORTH)

    def test_nothing_pressed(self):
        assert buzz.translate_buttons(0) == 0

    def test_undefined_bits_are_ignored(self):
        """A stray high bit must not press an unrelated gamepad button."""
        assert buzz.translate_buttons(0xE0) == 0
        assert buzz.translate_buttons(0xE0 | int(BuzzButton.RED)) == int(Button.RIGHT_SHOULDER)

    def test_all_five_at_once(self):
        every = int(BuzzButton.RED | BuzzButton.YELLOW | BuzzButton.GREEN
                    | BuzzButton.ORANGE | BuzzButton.BLUE)
        result = buzz.translate_buttons(every)
        for target in buzz.BUZZ_TO_XINPUT.values():
            assert result & target

    def test_every_button_has_a_label(self):
        for button in buzz.BUZZ_TO_XINPUT:
            assert buzz.BUZZ_LABELS[button]


class TestHidReport:
    def test_idle_report(self):
        assert buzz.build_hid_report({}) == bytes([0x7F, 0x7F, 0x00, 0x00, 0xF0])

    def test_report_is_five_bytes(self):
        assert len(buzz.build_hid_report({0: BuzzButton.RED})) == buzz.HID_REPORT_SIZE == 5

    @pytest.mark.parametrize(
        ("buzzer", "button", "expected"),
        [
            # Buzzer 1
            (0, BuzzButton.RED, bytes([0x7F, 0x7F, 0x01, 0x00, 0xF0])),
            (0, BuzzButton.YELLOW, bytes([0x7F, 0x7F, 0x02, 0x00, 0xF0])),
            (0, BuzzButton.GREEN, bytes([0x7F, 0x7F, 0x04, 0x00, 0xF0])),
            (0, BuzzButton.ORANGE, bytes([0x7F, 0x7F, 0x08, 0x00, 0xF0])),
            (0, BuzzButton.BLUE, bytes([0x7F, 0x7F, 0x10, 0x00, 0xF0])),
            # Buzzer 2 — capture: "R 7F 7F 20 00 F0 // controller 2 red button"
            (1, BuzzButton.RED, bytes([0x7F, 0x7F, 0x20, 0x00, 0xF0])),
            (1, BuzzButton.ORANGE, bytes([0x7F, 0x7F, 0x00, 0x01, 0xF0])),
            # Buzzer 3
            (2, BuzzButton.RED, bytes([0x7F, 0x7F, 0x00, 0x04, 0xF0])),
            # Buzzer 4 — capture: "R 7F 7F 00 00 F8 // controller 4 blue button"
            (3, BuzzButton.RED, bytes([0x7F, 0x7F, 0x00, 0x80, 0xF0])),
            (3, BuzzButton.YELLOW, bytes([0x7F, 0x7F, 0x00, 0x00, 0xF1])),
            (3, BuzzButton.GREEN, bytes([0x7F, 0x7F, 0x00, 0x00, 0xF2])),
            (3, BuzzButton.ORANGE, bytes([0x7F, 0x7F, 0x00, 0x00, 0xF4])),
            (3, BuzzButton.BLUE, bytes([0x7F, 0x7F, 0x00, 0x00, 0xF8])),
        ],
    )
    def test_matches_hardware_capture(self, buzzer, button, expected):
        assert buzz.build_hid_report({buzzer: button}) == expected

    def test_bit_index_formula(self):
        """Every one of the 20 buttons must occupy its own distinct bit."""
        seen = set()
        for buzzer in range(buzz.MAX_BUZZERS):
            for colour in buzz.HID_COLOUR_ORDER:
                report = buzz.build_hid_report({buzzer: colour})
                # Mask off the constant high nibble of the last byte.
                bits = int.from_bytes(report[2:5], "little") & ~0xF00000
                assert bin(bits).count("1") == 1, (buzzer, colour)
                assert bits not in seen
                seen.add(bits)
        assert len(seen) == 20

    def test_high_nibble_of_last_byte_is_constant(self):
        for buzzer in range(buzz.MAX_BUZZERS):
            for colour in buzz.HID_COLOUR_ORDER:
                assert buzz.build_hid_report({buzzer: colour})[4] & 0xF0 == 0xF0

    def test_multiple_buzzers_simultaneously(self):
        report = buzz.build_hid_report({0: BuzzButton.RED, 3: BuzzButton.BLUE})
        assert report == bytes([0x7F, 0x7F, 0x01, 0x00, 0xF8])

    def test_all_twenty_buttons_at_once(self):
        every = BuzzButton.RED | BuzzButton.YELLOW | BuzzButton.GREEN | BuzzButton.ORANGE | BuzzButton.BLUE
        report = buzz.build_hid_report({i: every for i in range(4)})
        assert report == bytes([0x7F, 0x7F, 0xFF, 0xFF, 0xFF])

    @pytest.mark.parametrize("index", [-1, 4, 99])
    def test_invalid_buzzer_index(self, index):
        with pytest.raises(ValueError, match="out of range"):
            buzz.build_hid_report({index: BuzzButton.RED})

    def test_colour_order_matches_rpcs3(self):
        assert buzz.HID_COLOUR_ORDER == (
            BuzzButton.RED, BuzzButton.YELLOW, BuzzButton.GREEN,
            BuzzButton.ORANGE, BuzzButton.BLUE,
        )


class TestLedReport:
    def test_all_on(self):
        assert buzz.parse_led_report(bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF])) == [True] * 4

    def test_all_off(self):
        assert buzz.parse_led_report(bytes(8)) == [False] * 4

    def test_individual_lamps(self):
        assert buzz.parse_led_report(bytes([0x00, 0xFF, 0x00, 0xFF, 0x00])) == [
            True, False, True, False
        ]

    def test_short_report_rejected(self):
        with pytest.raises(ValueError, match="at least 5 bytes"):
            buzz.parse_led_report(bytes(4))
