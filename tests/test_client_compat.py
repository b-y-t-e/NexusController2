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

    def test_the_button_bits_are_the_ones_the_phone_sets(self):
        """`MouseButton.LEFT.bit` is 1 and `RIGHT.bit` is 2 in TrackpadGestures.kt,
        asserted there against the same numbers. The trackpad, its button bar and
        NetworkController all build the mask from that enum, so these two bits are
        the whole agreement — and swapping them would silently swap the buttons on
        a PC rather than fail anywhere."""
        assert (MouseDelta.LEFT, MouseDelta.RIGHT) == (1, 2)

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


# --- configuration documents (§10) ------------------------------------------

#: Verbatim output of the Android client's `ConfigCodec.encode` for a freshly
#: defaulted Xbox pad on a 2400×1080 screen. Copied from the Kotlin side; do not
#: regenerate it from Python, or this stops proving anything.
ANDROID_DEFAULT_XBOX = """
{
  "v": 1,
  "type": "XBOX360",
  "name": "Player 1",
  "screen": { "w": 2400, "h": 1080 },
  "layout": {
    "L_STICK": { "x": 0.15, "y": 0.7,  "s": 1.2, "r": 0.0 },
    "R_STICK": { "x": 0.85, "y": 0.7,  "s": 1.2, "r": 0.0 },
    "DPAD":    { "x": 0.36, "y": 0.68, "s": 1.0, "r": 0.0 },
    "FACE":    { "x": 0.64, "y": 0.68, "s": 1.0, "r": 0.0 },
    "L1":      { "x": 0.1,  "y": 0.17, "s": 0.9, "r": 0.0 },
    "R1":      { "x": 0.9,  "y": 0.17, "s": 0.9, "r": 0.0 },
    "L2":      { "x": 0.1,  "y": 0.34, "s": 0.9, "r": 0.0 },
    "R2":      { "x": 0.9,  "y": 0.34, "s": 0.9, "r": 0.0 },
    "SHARE":   { "x": 0.42, "y": 0.3,  "s": 0.9, "r": 0.0 },
    "OPTIONS": { "x": 0.58, "y": 0.3,  "s": 0.9, "r": 0.0 },
    "PS":      { "x": 0.5,  "y": 0.42, "s": 1.0, "r": 0.0 }
  },
  "settings": {
    "haptics": true, "hapticStrength": 0.85, "gyro": false,
    "gyroSensitivity": 0.4, "touchVibration": true, "theme": "Dark"
  }
}
"""

#: Kotlin: `Protocol.configJson("{\"v\":1}")`.
CONFIG_TINY = bytes([0x06, 0x00, 0x07]) + b'{"v":1}'


class TestAndroidConfigDocument:
    def test_the_client_document_parses(self):
        from nexus_server.padconfig import PadConfig

        config = PadConfig.from_json(ANDROID_DEFAULT_XBOX)
        assert config.device_type is DeviceType.XBOX360
        assert config.name == "Player 1"
        assert config.screen == (2400, 1080)

    def test_every_component_survives(self):
        from nexus_server.padconfig import PadConfig, component_ids

        config = PadConfig.from_json(ANDROID_DEFAULT_XBOX)
        assert set(config.layout) == component_ids(DeviceType.XBOX360)

    def test_placements_are_read_exactly(self):
        from nexus_server.padconfig import PadConfig

        layout = PadConfig.from_json(ANDROID_DEFAULT_XBOX).layout
        assert layout["L_STICK"] == {"x": 0.15, "y": 0.7, "s": 1.2, "r": 0.0}
        assert layout["PS"]["y"] == 0.42

    def test_integer_valued_floats_are_accepted(self):
        """Kotlin writes `0.0` where a strict reader might expect `0`."""
        from nexus_server.padconfig import PadConfig

        assert PadConfig.from_json(ANDROID_DEFAULT_XBOX).layout["DPAD"]["r"] == 0.0

    def test_settings_are_read(self):
        from nexus_server.padconfig import PadConfig

        settings = PadConfig.from_json(ANDROID_DEFAULT_XBOX).settings
        assert settings["haptics"] is True
        assert settings["hapticStrength"] == 0.85
        assert settings["theme"] == "Dark"

    def test_the_pc_default_matches_the_client_default(self):
        """A slot that has not reported yet must preview what a phone would show."""
        from nexus_server.padconfig import PadConfig

        client = PadConfig.from_json(ANDROID_DEFAULT_XBOX)
        ours = PadConfig.default(DeviceType.XBOX360)
        assert ours.layout == client.layout

    def test_the_document_round_trips_through_the_server(self):
        from nexus_server.padconfig import PadConfig

        original = PadConfig.from_json(ANDROID_DEFAULT_XBOX)
        assert PadConfig.from_json(original.encode_body()).layout == original.layout

    def test_body_is_comfortably_within_the_limit(self):
        from nexus_server.padconfig import MAX_CONFIG_BYTES, PadConfig

        assert len(PadConfig.from_json(ANDROID_DEFAULT_XBOX).encode_body()) < MAX_CONFIG_BYTES


class TestConfigFramingAgreement:
    def test_tiny_document_framing(self):
        import struct

        assert CONFIG_TINY[0] == P.ClientOpcode.CONFIG
        assert struct.unpack(">H", CONFIG_TINY[1:3])[0] == len(CONFIG_TINY) - 3
        assert CONFIG_TINY[3:] == b'{"v":1}'

    def test_server_framing_mirrors_the_client(self):
        """Both directions use `opcode | uint16 length | UTF-8 body`."""
        framed = P.encode_set_config(b'{"v":1}')
        assert framed[0] == P.ServerOpcode.SET_CONFIG
        assert framed[1:] == CONFIG_TINY[1:]

    def test_length_counts_bytes_not_characters(self):
        import struct

        body = '{"n":"ąę"}'.encode("utf-8")
        framed = P.encode_set_config(body)
        assert struct.unpack(">H", framed[1:3])[0] == len(body) > len('{"n":"ąę"}')


# --- version strings, which are not bytes but are just as shared -------------

#: Exactly the cases `UpdateCheckTest.kt` asserts on. Not a wire format, but the
#: same kind of agreement: both sides read a release tag and compare it against
#: their own version, and a rule that holds on one side only means a phone and a
#: PC disagreeing about whether an update exists. The two languages fail
#: differently here — Python's `isdigit()` accepts "²" where `int()` does not,
#: Kotlin's `toIntOrNull()` reads "٣" as 3 and gives up past 2^31 — so the shared
#: rule is spelled out rather than inherited: 0-9 only, at most three parts, at
#: most `MAX_VERSION_DIGITS` digits each.
VERSION_VECTORS: tuple[tuple[str, tuple[int, int, int] | None], ...] = (
    ("2.1.0", (2, 1, 0)),
    ("v2.1.0", (2, 1, 0)),
    ("2.1", (2, 1, 0)),
    ("3", (3, 0, 0)),
    ("2.1.0-legacy", (2, 1, 0)),
    ("", None),
    ("latest", None),
    ("2.1.0.0", None),
    ("2.x", None),
    ("2.²", None),
    ("٢.1.0", None),
    ("2.٣", None),
    ("9999999999.0.0", None),
    ("1.2.1234567890", None),
)


class TestVersionRulesAgreement:
    """The update check reads the same tag on both sides; it must read it the same."""

    @pytest.mark.parametrize(("text", "expected"), VERSION_VECTORS)
    def test_the_python_side_reads_what_the_kotlin_side_reads(self, text, expected):
        from nexus_server import updates

        assert updates.parse_version(text) == expected

    def test_the_digit_cap_is_the_number_both_sides_carry(self):
        """`UpdateCheck.MAX_VERSION_DIGITS` says 9 too; a change on one side alone
        makes a version that exists for one of the two."""
        from nexus_server import updates

        assert updates.MAX_VERSION_DIGITS == 9
        assert updates.parse_version("9" * 9 + ".0.0") == (999999999, 0, 0)
        assert updates.parse_version("9" * 10 + ".0.0") is None
