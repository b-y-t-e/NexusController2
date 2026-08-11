"""Wire-format tests. These pin down the bytes both sides must agree on."""

import struct

import pytest

from nexus_server import protocol as P
from nexus_server.protocol import (
    Button,
    DeviceType,
    DPad,
    Feature,
    Hello,
    InputFlag,
    InputState,
    MouseDelta,
    ProtocolError,
    RejectReason,
    ScrollDelta,
)


class TestHello:
    def test_round_trip(self):
        hello = Hello(P.PROTOCOL_VERSION, DeviceType.BUZZ, "deadbeef" * 4, "Ania")
        decoded = Hello.decode_body(hello.encode()[1:])
        assert decoded == hello

    def test_exact_bytes(self):
        encoded = Hello(0x02, DeviceType.DUALSHOCK4, "ab", "Pi").encode()
        assert encoded == bytes([0x10, 0x02, 0x01, 0x02]) + b"ab" + bytes([0x02]) + b"Pi"

    @pytest.mark.parametrize("device_type", list(DeviceType))
    def test_every_device_type_round_trips(self, device_type):
        hello = Hello(2, device_type, "t", "n")
        assert Hello.decode_body(hello.encode()[1:]).device_type is device_type

    def test_empty_token_and_name(self):
        decoded = Hello.decode_body(Hello(2, DeviceType.XBOX360, "", "").encode()[1:])
        assert decoded.token == "" and decoded.name == ""

    def test_unknown_device_type_rejected(self):
        with pytest.raises(ProtocolError, match="unknown device type"):
            Hello.decode_body(bytes([0x02, 0x7F, 0x00, 0x00]))

    @pytest.mark.parametrize(
        "body",
        [b"", b"\x02", b"\x02\x00", b"\x02\x00\x04ab", b"\x02\x00\x00\x05ab"],
        ids=["empty", "one-byte", "two-bytes", "short-token", "short-name"],
    )
    def test_truncated_rejected(self, body):
        with pytest.raises(ProtocolError):
            Hello.decode_body(body)

    def test_oversized_lengths_rejected(self):
        with pytest.raises(ProtocolError, match="token length"):
            Hello.decode_body(bytes([0x02, 0x00, 200]) + b"x" * 200 + b"\x00")

    def test_name_is_sanitised(self):
        raw = bytes([0x02, 0x00, 0x00, 0x09]) + "a|b\x00c\ndef".encode()
        assert "|" not in Hello.decode_body(raw).name
        assert "\n" not in Hello.decode_body(raw).name

    def test_token_longer_than_maximum_refused_on_encode(self):
        with pytest.raises(ProtocolError, match="token too long"):
            Hello(2, DeviceType.XBOX360, "x" * 65, "n").encode()


class TestInputState:
    def test_round_trip(self):
        state = InputState(
            lx=-100, ly=127, rx=1, ry=-127,
            buttons_low=int(Button.SOUTH | Button.START),
            buttons_high=int(DPad.UP | DPad.GUIDE),
            left_trigger=255, right_trigger=7,
            roll=-1234, pitch=32000,
            flags=InputFlag.MOUSE_MODE | InputFlag.GYRO_VALID,
        )
        assert InputState.decode(state.encode()) == state

    def test_payload_is_exactly_sixteen_bytes(self):
        assert len(InputState().encode()) == P.INPUT_PAYLOAD_SIZE == 16

    def test_reserved_bytes_are_zero(self):
        assert InputState(lx=5, roll=9).encode()[13:] == b"\x00\x00\x00"

    def test_neutral_frame_is_all_zero(self):
        assert InputState().encode() == bytes(16)

    def test_negative_axes_are_signed(self):
        assert InputState(lx=-1, ly=-127).encode()[:2] == b"\xff\x81"

    def test_minus_128_is_clamped_not_rejected(self):
        # Clients converting an unsigned 0..255 range routinely emit -128.
        assert InputState(lx=-128).encode()[0] == 0x81
        assert InputState.decode(b"\x80" + bytes(15)).lx == -127

    def test_out_of_range_axis_is_clamped(self):
        assert InputState(lx=5000, ry=-5000).encode()[0:1] == b"\x7f"
        assert InputState(lx=5000).lx == 5000  # the dataclass itself is not lossy
        assert InputState.decode(InputState(lx=5000).encode()).lx == 127

    def test_gyro_is_big_endian_int16(self):
        payload = InputState(roll=-2, pitch=258).encode()
        assert payload[8:12] == b"\xff\xfe\x01\x02"
        assert struct.unpack(">hh", payload[8:12]) == (-2, 258)

    def test_flags_helpers(self):
        assert InputState(flags=InputFlag.MOUSE_MODE).mouse_mode is True
        assert InputState(flags=InputFlag.MOUSE_MODE).gyro_valid is False
        assert InputState(flags=InputFlag.GYRO_VALID).gyro_valid is True

    def test_unknown_flag_bits_are_masked_off(self):
        payload = bytearray(16)
        payload[12] = 0xFF
        assert InputState.decode(bytes(payload)).flags == (
            InputFlag.MOUSE_MODE | InputFlag.GYRO_VALID
        )

    @pytest.mark.parametrize("size", [0, 15, 17, 32])
    def test_wrong_payload_size_rejected(self, size):
        with pytest.raises(ProtocolError, match="16 bytes"):
            InputState.decode(bytes(size))

    def test_mode_flags_no_longer_collide_with_buttons(self):
        """v1 put mouse mode in buttons_high 0x40, making Guide unreachable."""
        state = InputState(buttons_high=int(DPad.GUIDE), flags=InputFlag.MOUSE_MODE)
        decoded = InputState.decode(state.encode())
        assert decoded.buttons_high & DPad.GUIDE
        assert decoded.mouse_mode


class TestAxisConversion:
    @pytest.mark.parametrize(
        ("raw", "expected"), [(0, 0.0), (127, 1.0), (-127, -1.0), (-128, -1.0), (200, 1.0)]
    )
    def test_axis_to_float(self, raw, expected):
        assert P.axis_to_float(raw) == pytest.approx(expected)

    def test_centre_is_exactly_zero(self):
        assert P.axis_to_float(0) == 0.0
        assert P.axis_to_int16(0) == 0

    def test_extremes_reach_int16_limits(self):
        assert P.axis_to_int16(127) == 32767
        assert P.axis_to_int16(-127) == -32767


class TestServerMessages:
    def test_welcome(self):
        assert P.encode_welcome(2, Feature.RUMBLE | Feature.LED) == b"\x11\x02\x03"

    def test_welcome_rejects_bad_slot(self):
        with pytest.raises(ProtocolError):
            P.encode_welcome(999, Feature.NONE)

    @pytest.mark.parametrize("reason", list(RejectReason))
    def test_every_reject_reason_encodes_and_has_a_message(self, reason):
        encoded = P.encode_reject(reason)
        assert encoded[0] == P.ServerOpcode.REJECT
        assert RejectReason(encoded[1]) is reason
        assert reason.message

    def test_rumble_clamps(self):
        assert P.encode_rumble(-5, 900) == b"\x03\x00\xff"

    def test_led(self):
        assert P.encode_led(1, 2, 300) == b"\x12\x01\x02\xff"

    def test_ping_pong_round_trip(self):
        pong = P.encode_pong(0xDEADBEEF)
        assert pong[0] == P.ServerOpcode.PONG
        assert P.decode_ping(pong[1:]) == 0xDEADBEEF

    def test_ping_sequence_wraps(self):
        assert P.decode_ping(P.encode_pong(0x1_0000_0001)[1:]) == 1

    def test_ping_payload_must_be_four_bytes(self):
        with pytest.raises(ProtocolError):
            P.decode_ping(b"\x00")


class TestMouseAndScroll:
    def test_mouse_decodes_signed_deltas(self):
        delta = MouseDelta.decode(bytes([0xFF, 0x05, 0x03]))
        assert (delta.dx, delta.dy) == (-1, 5)
        assert delta.buttons & MouseDelta.LEFT and delta.buttons & MouseDelta.RIGHT

    def test_scroll_decodes_signed_deltas(self):
        assert ScrollDelta.decode(bytes([0x80, 0x7F])) == ScrollDelta(-128, 127)

    @pytest.mark.parametrize("size", [0, 2, 4])
    def test_mouse_wrong_size(self, size):
        with pytest.raises(ProtocolError):
            MouseDelta.decode(bytes(size))


class TestDiscovery:
    def test_round_trip(self):
        raw = P.encode_discovery_response("Gaming-PC", 6000, True)
        response = P.DiscoveryResponse.decode(raw)
        assert response == P.DiscoveryResponse("Gaming-PC", 6000, True)

    def test_separator_stripped_from_name(self):
        raw = P.encode_discovery_response("evil|name|6000|0", 6000, False)
        assert P.DiscoveryResponse.decode(raw).port == 6000

    @pytest.mark.parametrize(
        "raw",
        [b"", b"garbage", b"NEXUSPAD_SERVER_V2|a|b|c", b"OTHER|a|1|0", b"NEXUSPAD_SERVER_V2|a|1"],
    )
    def test_malformed_rejected(self, raw):
        with pytest.raises(ProtocolError):
            P.DiscoveryResponse.decode(raw)

    def test_port_range_validated(self):
        with pytest.raises(ProtocolError, match="out of range"):
            P.DiscoveryResponse.decode(b"NEXUSPAD_SERVER_V2|pc|70000|1")

    def test_request_constant_is_stable(self):
        assert P.DISCOVERY_REQUEST == b"NEXUSPAD_DISCOVER_V2"


class TestPairingPayload:
    def test_round_trip(self):
        text = P.encode_pairing_payload("192.168.1.10", 6000, "ab" * 16)
        assert text == "NEXUSPAD2:192.168.1.10:6000:" + "ab" * 16
        assert P.PairingPayload.decode(text) == P.PairingPayload("192.168.1.10", 6000, "ab" * 16)

    def test_whitespace_tolerated(self):
        assert P.PairingPayload.decode("  NEXUSPAD2:10.0.0.1:6000:tok \n").ip == "10.0.0.1"

    @pytest.mark.parametrize(
        "text", ["", "192.168.1.10", "NEXUSPAD1:1.2.3.4:6000:t", "NEXUSPAD2:1.2.3.4:x:t", "NEXUSPAD2:a:b"]
    )
    def test_garbage_rejected(self, text):
        with pytest.raises(ProtocolError):
            P.PairingPayload.decode(text)

    def test_ipv6_refused(self):
        with pytest.raises(ProtocolError, match="IPv6"):
            P.encode_pairing_payload("::1", 6000, "t")


def test_opcodes_do_not_collide():
    client = {int(o) for o in P.ClientOpcode}
    server = {int(o) for o in P.ServerOpcode}
    assert len(client) == len(P.ClientOpcode)
    assert len(server) == len(P.ServerOpcode)
    # Directional overlap is harmless, but a shared value would be confusing.
    assert client & server == set()
