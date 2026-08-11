"""Cross-language compatibility with the Android client.

Every byte string below is a golden vector copied from the Kotlin unit tests in
``android/app/src/test/java/com/nexuscontroller/pad/ProtocolTest.kt``. The Kotlin
suite asserts that its ``Protocol`` object *produces* these bytes; this suite
asserts that the Python server *decodes* them into the intended meaning.

If the two implementations ever drift apart, one of the two suites goes red.
Keep the vectors here identical to the ones asserted on the Kotlin side — do not
regenerate them from Python code, or the test becomes a tautology.
"""

import pytest

from nexus_server import protocol as P
from nexus_server.buzz import BuzzButton
from nexus_server.devices import FakePad, resolve_buttons
from nexus_server.protocol import (
    Button,
    DeviceType,
    DPad,
    Hello,
    InputFlag,
    InputState,
    MouseDelta,
    ScrollDelta,
)

# --- golden vectors ---------------------------------------------------------

HELLO_XBOX = bytes([0x10, 0x02, 0x00, 3]) + b"abc" + bytes([3]) + b"Pad"
HELLO_BUZZ_EMPTY = bytes([0x10, 0x02, 0x02, 0x00, 0x00])

#: Kotlin: input(XBOX360, 127,127,127,127, 0,0, 0,0) — UI neutral.
INPUT_NEUTRAL = bytes([0x01]) + bytes(16)

#: Kotlin: input(XBOX360, 255,255, 0,0, ...) — UI extremes.
#: UI y grows downwards, so ui=255 becomes wire -127 and ui=0 becomes wire +127.
INPUT_EXTREMES = bytes([0x01, 127, 0x81, 0x81, 127]) + bytes(12)

#: Kotlin: buttonsLow=0xA5, buttonsHigh=0x5A, lt=200, rt=255,
#: gyroRoll=-300, gyroPitch=1000, flags=FLAG_GYRO_VALID.
INPUT_FULL = bytes(
    [0x01, 0, 0, 0, 0, 0xA5, 0x5A, 200, 255, 0xFE, 0xD4, 0x03, 0xE8, 0x02, 0, 0, 0]
)

#: Kotlin Buzz mode: only the semantic bits survive, everything else is zeroed.
INPUT_BUZZ_RED = bytes([0x01, 0, 0, 0, 0, 0x01]) + bytes(11)
INPUT_BUZZ_ALL = bytes([0x01, 0, 0, 0, 0, 0x1F]) + bytes(11)

PING_ONE = bytes([0xF0, 0x00, 0x00, 0x00, 0x01])
PING_DEADBEEF = bytes([0xF0, 0xDE, 0xAD, 0xBE, 0xEF])
TEXT_HI = bytes([0x02, 0x02]) + b"hi"
MOUSE_VECTOR = bytes([0x04, 0x81, 0x7F, 0x03])
SCROLL_VECTOR = bytes([0x05, 0x81, 0x03])


def body(packet: bytes) -> bytes:
    """Strip the opcode byte."""
    return packet[1:]


class TestHello:
    def test_xbox_hello_decodes(self):
        hello = Hello.decode_body(body(HELLO_XBOX))
        assert hello.version == P.PROTOCOL_VERSION
        assert hello.device_type is DeviceType.XBOX360
        assert hello.token == "abc"
        assert hello.name == "Pad"

    def test_buzz_hello_with_no_token_or_name(self):
        hello = Hello.decode_body(body(HELLO_BUZZ_EMPTY))
        assert hello.device_type is DeviceType.BUZZ
        assert hello.token == ""
        assert hello.name == ""

    def test_opcode_matches(self):
        assert HELLO_XBOX[0] == P.ClientOpcode.HELLO

    @pytest.mark.parametrize(
        ("wire", "expected"),
        [(0x00, DeviceType.XBOX360), (0x01, DeviceType.DUALSHOCK4), (0x02, DeviceType.BUZZ)],
    )
    def test_device_type_wire_values_agree(self, wire, expected):
        packet = bytes([0x10, 0x02, wire, 0x00, 0x00])
        assert Hello.decode_body(body(packet)).device_type is expected

    def test_python_encoder_reproduces_the_kotlin_vector(self):
        """Both sides must emit the same bytes, not merely accept each other's."""
        assert Hello(0x02, DeviceType.XBOX360, "abc", "Pad").encode() == HELLO_XBOX


class TestInput:
    def test_packet_is_seventeen_bytes_with_the_opcode(self):
        for packet in (INPUT_NEUTRAL, INPUT_EXTREMES, INPUT_FULL, INPUT_BUZZ_RED):
            assert len(packet) == 1 + P.INPUT_PAYLOAD_SIZE

    def test_neutral_is_exactly_centred(self):
        """The v1 client sent ui-127 as -1, giving every stick a permanent bias."""
        state = InputState.decode(body(INPUT_NEUTRAL))
        assert (state.lx, state.ly, state.rx, state.ry) == (0, 0, 0, 0)
        assert P.axis_to_float(state.lx) == 0.0

    def test_extremes_decode_to_full_deflection(self):
        state = InputState.decode(body(INPUT_EXTREMES))
        assert (state.lx, state.ly) == (127, -127)
        assert (state.rx, state.ry) == (-127, 127)

    def test_ui_down_becomes_wire_negative_y(self):
        """UI y grows downwards; the wire is '+ = up'. Verified through the pad."""
        pad = FakePad(DeviceType.XBOX360)
        pad.apply(InputState.decode(body(INPUT_EXTREMES)))
        assert pad.state.ly == pytest.approx(-1.0)   # UI 255 = thumb pulled down
        assert pad.state.ry == pytest.approx(1.0)    # UI 0   = thumb pushed up

    def test_all_fields_land_at_the_documented_offsets(self):
        state = InputState.decode(body(INPUT_FULL))
        assert state.buttons_low == 0xA5
        assert state.buttons_high == 0x5A
        assert state.left_trigger == 200
        assert state.right_trigger == 255
        assert state.roll == -300
        assert state.pitch == 1000
        assert state.flags == InputFlag.GYRO_VALID

    def test_reserved_tail_is_zero(self):
        for packet in (INPUT_NEUTRAL, INPUT_FULL, INPUT_BUZZ_ALL):
            assert packet[14:] == b"\x00\x00\x00"

    def test_flag_bit_values_agree(self):
        assert int(InputFlag.MOUSE_MODE) == 0x01
        assert int(InputFlag.GYRO_VALID) == 0x02

    def test_python_encoder_reproduces_the_kotlin_vectors(self):
        assert bytes([0x01]) + InputState().encode() == INPUT_NEUTRAL
        assert bytes([0x01]) + InputState(lx=127, ly=-127, rx=-127, ry=127).encode() == INPUT_EXTREMES
        assert bytes([0x01]) + InputState(
            buttons_low=0xA5, buttons_high=0x5A,
            left_trigger=200, right_trigger=255,
            roll=-300, pitch=1000, flags=InputFlag.GYRO_VALID,
        ).encode() == INPUT_FULL


class TestButtonAgreement:
    @pytest.mark.parametrize(
        ("bit", "button"),
        [
            (0x01, Button.SOUTH), (0x02, Button.EAST), (0x04, Button.WEST), (0x08, Button.NORTH),
            (0x10, Button.LEFT_SHOULDER), (0x20, Button.RIGHT_SHOULDER),
            (0x40, Button.BACK), (0x80, Button.START),
        ],
    )
    def test_buttons_low_bits(self, bit, button):
        assert int(button) == bit

    @pytest.mark.parametrize(
        ("bit", "dpad"),
        [
            (0x01, DPad.LEFT_THUMB), (0x02, DPad.RIGHT_THUMB),
            (0x04, DPad.UP), (0x08, DPad.DOWN), (0x10, DPad.LEFT), (0x20, DPad.RIGHT),
            (0x40, DPad.GUIDE),
        ],
    )
    def test_buttons_high_bits(self, bit, dpad):
        assert int(dpad) == bit

    def test_guide_is_a_real_button_now(self):
        state = InputState.decode(body(bytes([0x01, 0, 0, 0, 0, 0, 0x40]) + bytes(10)))
        assert state.buttons_high & DPad.GUIDE
        assert state.flags == InputFlag.NONE


class TestBuzz:
    def test_red_only_sets_the_red_bit(self):
        state = InputState.decode(body(INPUT_BUZZ_RED))
        assert state.buttons_low == int(BuzzButton.RED)
        assert (state.lx, state.ly, state.buttons_high, state.left_trigger) == (0, 0, 0, 0)

    def test_red_maps_to_the_rpcs3_default(self):
        state = InputState.decode(body(INPUT_BUZZ_RED))
        assert resolve_buttons(DeviceType.BUZZ, state.buttons_low) == int(Button.RIGHT_SHOULDER)

    def test_all_five_buttons_translate(self):
        state = InputState.decode(body(INPUT_BUZZ_ALL))
        translated = resolve_buttons(DeviceType.BUZZ, state.buttons_low)
        for expected in (
            Button.RIGHT_SHOULDER, Button.SOUTH, Button.EAST, Button.WEST, Button.NORTH
        ):
            assert translated & expected

    def test_buzz_mask_is_five_bits(self):
        assert int(
            BuzzButton.RED | BuzzButton.YELLOW | BuzzButton.GREEN
            | BuzzButton.ORANGE | BuzzButton.BLUE
        ) == 0x1F

    def test_buzz_frame_leaves_the_pad_sticks_centred(self):
        pad = FakePad(DeviceType.BUZZ)
        pad.apply(InputState.decode(body(INPUT_BUZZ_ALL)))
        assert (pad.state.lx, pad.state.ly, pad.state.rx, pad.state.ry) == (0.0, 0.0, 0.0, 0.0)
        assert pad.state.left_trigger == 0


class TestOtherOpcodes:
    def test_ping_sequences(self):
        assert P.decode_ping(body(PING_ONE)) == 1
        assert P.decode_ping(body(PING_DEADBEEF)) == 0xDEADBEEF

    def test_pong_mirrors_the_ping_encoding(self):
        assert P.encode_pong(1) == bytes([0xF1, 0, 0, 0, 1])

    def test_text(self):
        assert TEXT_HI[0] == P.ClientOpcode.TEXT
        assert TEXT_HI[1] == 2
        assert TEXT_HI[2:].decode("utf-8") == "hi"

    def test_mouse(self):
        delta = MouseDelta.decode(body(MOUSE_VECTOR))
        assert (delta.dx, delta.dy) == (-127, 127)
        assert delta.buttons & MouseDelta.LEFT and delta.buttons & MouseDelta.RIGHT

    def test_scroll(self):
        assert ScrollDelta.decode(body(SCROLL_VECTOR)) == ScrollDelta(-127, 3)

    def test_opcodes_agree(self):
        assert (INPUT_NEUTRAL[0], TEXT_HI[0], MOUSE_VECTOR[0], SCROLL_VECTOR[0], PING_ONE[0]) == (
            P.ClientOpcode.INPUT, P.ClientOpcode.TEXT,
            P.ClientOpcode.MOUSE, P.ClientOpcode.SCROLL, P.ClientOpcode.PING,
        )


class TestServerAcceptsClientVectors:
    """Feed the real server the exact bytes the phone would send."""

    @pytest.mark.parametrize(
        ("hello_vector", "input_vector", "device_type", "expected_low"),
        [
            (HELLO_XBOX, INPUT_FULL, DeviceType.XBOX360, 0xA5),
            (HELLO_BUZZ_EMPTY, INPUT_BUZZ_RED, DeviceType.BUZZ, int(Button.RIGHT_SHOULDER)),
        ],
    )
    def test_round_trip(self, server, hello_vector, input_vector, device_type, expected_low):
        import socket

        from .conftest import wait_for

        # Rewrite the token to the one this server expects; the rest is untouched.
        hello = Hello.decode_body(body(hello_vector))
        packet = Hello(
            hello.version, hello.device_type, server.settings.token, hello.name
        ).encode()

        sock = socket.create_connection((server.bind_ip, server.settings.port), timeout=3)
        try:
            sock.sendall(packet)
            reply = sock.recv(3)
            assert reply[0] == P.ServerOpcode.WELCOME, f"rejected: {reply!r}"
            slot = reply[1]
            assert server.slots.sessions[slot].device_type is device_type

            sock.sendall(input_vector)
            pad = wait_for(lambda: server.slots.sessions[slot].pad)
            wait_for(lambda: pad.state.buttons_low == expected_low)
        finally:
            sock.close()
