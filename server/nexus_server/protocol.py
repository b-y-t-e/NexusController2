"""Wire protocol v2 — pure encoding/decoding, no I/O and no side effects.

Everything in this module is deliberately free of sockets, threads and hardware so
it can be exercised exhaustively by unit tests. See ``docs/PROTOCOL.md`` for the
normative description of the format.
"""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass, field
from typing import ClassVar, Final

PROTOCOL_VERSION: Final = 0x02

DEFAULT_TCP_PORT: Final = 6000
DEFAULT_DISCOVERY_PORT: Final = 6001

#: Longest fixed-size payload we ever read in one go (INPUT).
INPUT_PAYLOAD_SIZE: Final = 16

MAX_TOKEN_LEN: Final = 64
MAX_NAME_LEN: Final = 32
MAX_TEXT_LEN: Final = 255


class ProtocolError(ValueError):
    """Raised when a peer sends something that does not conform to the protocol."""


class ClientOpcode(enum.IntEnum):
    INPUT = 0x01
    TEXT = 0x02
    MOUSE = 0x04
    SCROLL = 0x05
    HELLO = 0x10
    PING = 0xF0


class ServerOpcode(enum.IntEnum):
    RUMBLE = 0x03
    WELCOME = 0x11
    LED = 0x12
    REJECT = 0x1F
    PONG = 0xF1


class DeviceType(enum.IntEnum):
    XBOX360 = 0x00
    DUALSHOCK4 = 0x01
    BUZZ = 0x02

    @property
    def label(self) -> str:
        return {
            DeviceType.XBOX360: "Xbox 360",
            DeviceType.DUALSHOCK4: "DualShock 4",
            DeviceType.BUZZ: "Buzz (PS3)",
        }[self]


class RejectReason(enum.IntEnum):
    BAD_VERSION = 0x01
    BAD_TOKEN = 0x02
    SERVER_FULL = 0x03
    MALFORMED = 0x04
    UNAUTHENTICATED = 0x05
    RATE_LIMITED = 0x06

    @property
    def message(self) -> str:
        return {
            RejectReason.BAD_VERSION: "Unsupported protocol version",
            RejectReason.BAD_TOKEN: "Invalid pairing token",
            RejectReason.SERVER_FULL: "Server full",
            RejectReason.MALFORMED: "Malformed handshake",
            RejectReason.UNAUTHENTICATED: "Handshake required first",
            RejectReason.RATE_LIMITED: "Too many failed attempts",
        }[self]


class Feature(enum.IntFlag):
    NONE = 0x00
    RUMBLE = 0x01
    LED = 0x02


# --- buttons_low, as sent by a gamepad-style client -------------------------

class Button(enum.IntFlag):
    """``buttons_low`` bits for :attr:`DeviceType.XBOX360` / :attr:`DeviceType.DUALSHOCK4`."""

    NONE = 0x00
    SOUTH = 0x01       # A / Cross
    EAST = 0x02        # B / Circle
    WEST = 0x04        # X / Square
    NORTH = 0x08       # Y / Triangle
    LEFT_SHOULDER = 0x10
    RIGHT_SHOULDER = 0x20
    BACK = 0x40        # Back / Share
    START = 0x80       # Start / Options


class DPad(enum.IntFlag):
    """``buttons_high`` bits — identical for every device type."""

    NONE = 0x00
    LEFT_THUMB = 0x01
    RIGHT_THUMB = 0x02
    UP = 0x04
    DOWN = 0x08
    LEFT = 0x10
    RIGHT = 0x20
    GUIDE = 0x40
    RESERVED = 0x80


class InputFlag(enum.IntFlag):
    NONE = 0x00
    MOUSE_MODE = 0x01
    GYRO_VALID = 0x02


AXIS_MIN: Final = -127
AXIS_MAX: Final = 127


def clamp_axis(value: int) -> int:
    """Clamp a raw axis byte to the legal range.

    ``-128`` is not a legal wire value; clients that emit it (a common off-by-one
    when converting an unsigned 0..255 range) are silently corrected rather than
    disconnected.
    """
    return max(AXIS_MIN, min(AXIS_MAX, value))


def axis_to_float(value: int) -> float:
    """Map a wire axis byte to ``-1.0``…``1.0``."""
    return clamp_axis(value) / float(AXIS_MAX)


def axis_to_int16(value: int) -> int:
    """Map a wire axis byte to the XInput ``-32768``…``32767`` range."""
    return int(round(axis_to_float(value) * 32767))


def _as_signed(byte: int) -> int:
    return byte - 256 if byte > 127 else byte


# --- messages ---------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Hello:
    version: int
    device_type: DeviceType
    token: str
    name: str

    def encode(self) -> bytes:
        token = self.token.encode("ascii")
        name = self.name.encode("utf-8")[:MAX_NAME_LEN]
        if len(token) > MAX_TOKEN_LEN:
            raise ProtocolError(f"token too long: {len(token)}")
        return bytes(
            [ClientOpcode.HELLO, self.version, int(self.device_type), len(token)]
        ) + token + bytes([len(name)]) + name

    @classmethod
    def decode_body(cls, body: bytes) -> "Hello":
        """Decode a HELLO *without* its leading opcode byte."""
        if len(body) < 3:
            raise ProtocolError("HELLO truncated")
        version, raw_type, token_len = body[0], body[1], body[2]
        if token_len > MAX_TOKEN_LEN:
            raise ProtocolError(f"token length {token_len} exceeds maximum")
        if len(body) < 3 + token_len + 1:
            raise ProtocolError("HELLO truncated in token")
        token = body[3:3 + token_len]
        name_len = body[3 + token_len]
        if name_len > MAX_NAME_LEN:
            raise ProtocolError(f"name length {name_len} exceeds maximum")
        name_start = 4 + token_len
        if len(body) < name_start + name_len:
            raise ProtocolError("HELLO truncated in name")
        name = body[name_start:name_start + name_len]
        try:
            device_type = DeviceType(raw_type)
        except ValueError as exc:
            raise ProtocolError(f"unknown device type 0x{raw_type:02x}") from exc
        return cls(
            version=version,
            device_type=device_type,
            token=token.decode("ascii", errors="replace"),
            name=sanitize_name(name.decode("utf-8", errors="replace")),
        )


def sanitize_name(name: str) -> str:
    """Strip control characters and ``|`` (the discovery field separator)."""
    cleaned = "".join(ch for ch in name if ch.isprintable() and ch != "|")
    return cleaned.strip()[:MAX_NAME_LEN]


@dataclass(frozen=True, slots=True)
class InputState:
    """One decoded INPUT message."""

    lx: int = 0
    ly: int = 0
    rx: int = 0
    ry: int = 0
    buttons_low: int = 0
    buttons_high: int = 0
    left_trigger: int = 0
    right_trigger: int = 0
    roll: int = 0
    pitch: int = 0
    flags: InputFlag = InputFlag.NONE

    @property
    def mouse_mode(self) -> bool:
        return bool(self.flags & InputFlag.MOUSE_MODE)

    @property
    def gyro_valid(self) -> bool:
        return bool(self.flags & InputFlag.GYRO_VALID)

    def encode(self) -> bytes:
        """Encode the 16-byte payload (without the opcode). Used by tests and tools."""
        return struct.pack(
            ">4b4B2hB3x",
            clamp_axis(self.lx), clamp_axis(self.ly),
            clamp_axis(self.rx), clamp_axis(self.ry),
            self.buttons_low & 0xFF, self.buttons_high & 0xFF,
            self.left_trigger & 0xFF, self.right_trigger & 0xFF,
            self.roll, self.pitch,
            int(self.flags) & 0xFF,
        )

    @classmethod
    def decode(cls, payload: bytes) -> "InputState":
        if len(payload) != INPUT_PAYLOAD_SIZE:
            raise ProtocolError(
                f"INPUT payload must be {INPUT_PAYLOAD_SIZE} bytes, got {len(payload)}"
            )
        lx, ly, rx, ry, bl, bh, lt, rt, roll, pitch, flags = struct.unpack(
            ">4b4B2hB3x", payload
        )
        return cls(
            lx=clamp_axis(lx), ly=clamp_axis(ly),
            rx=clamp_axis(rx), ry=clamp_axis(ry),
            buttons_low=bl, buttons_high=bh,
            left_trigger=lt, right_trigger=rt,
            roll=roll, pitch=pitch,
            flags=InputFlag(flags & 0x03),
        )


@dataclass(frozen=True, slots=True)
class MouseDelta:
    dx: int
    dy: int
    buttons: int

    # ClassVar, not a field — a bare ``Final`` annotation would make these three
    # into dataclass fields and silently break construction.
    LEFT: ClassVar[int] = 0x01
    RIGHT: ClassVar[int] = 0x02
    MIDDLE: ClassVar[int] = 0x04

    @classmethod
    def decode(cls, payload: bytes) -> "MouseDelta":
        if len(payload) != 3:
            raise ProtocolError("MOUSE payload must be 3 bytes")
        return cls(_as_signed(payload[0]), _as_signed(payload[1]), payload[2])


@dataclass(frozen=True, slots=True)
class ScrollDelta:
    dx: int
    dy: int

    @classmethod
    def decode(cls, payload: bytes) -> "ScrollDelta":
        if len(payload) != 2:
            raise ProtocolError("SCROLL payload must be 2 bytes")
        return cls(_as_signed(payload[0]), _as_signed(payload[1]))


# --- server → client encoders ----------------------------------------------

def encode_welcome(slot: int, features: Feature) -> bytes:
    if not 0 <= slot <= 255:
        raise ProtocolError(f"slot out of range: {slot}")
    return bytes([ServerOpcode.WELCOME, slot, int(features) & 0xFF])


def encode_reject(reason: RejectReason) -> bytes:
    return bytes([ServerOpcode.REJECT, int(reason)])


def encode_rumble(large: int, small: int) -> bytes:
    return bytes([ServerOpcode.RUMBLE, max(0, min(255, large)), max(0, min(255, small))])


def encode_led(r: int, g: int, b: int) -> bytes:
    return bytes(
        [ServerOpcode.LED, max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))]
    )


def encode_pong(seq: int) -> bytes:
    return bytes([ServerOpcode.PONG]) + struct.pack(">I", seq & 0xFFFFFFFF)


def decode_ping(payload: bytes) -> int:
    if len(payload) != 4:
        raise ProtocolError("PING payload must be 4 bytes")
    return struct.unpack(">I", payload)[0]


# --- discovery --------------------------------------------------------------

DISCOVERY_REQUEST: Final = b"NEXUSPAD_DISCOVER_V2"
DISCOVERY_PREFIX: Final = "NEXUSPAD_SERVER_V2"


def encode_discovery_response(name: str, port: int, token_required: bool) -> bytes:
    return "|".join(
        [DISCOVERY_PREFIX, sanitize_name(name), str(port), "1" if token_required else "0"]
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class DiscoveryResponse:
    name: str
    port: int
    token_required: bool

    @classmethod
    def decode(cls, data: bytes) -> "DiscoveryResponse":
        parts = data.decode("utf-8", errors="replace").split("|")
        if len(parts) != 4 or parts[0] != DISCOVERY_PREFIX:
            raise ProtocolError("not a Nexus discovery response")
        try:
            port = int(parts[2])
        except ValueError as exc:
            raise ProtocolError(f"bad port {parts[2]!r}") from exc
        if not 1 <= port <= 65535:
            raise ProtocolError(f"port out of range: {port}")
        return cls(name=parts[1], port=port, token_required=parts[3] == "1")


# --- pairing payload --------------------------------------------------------

QR_PREFIX: Final = "NEXUSPAD2"


def encode_pairing_payload(ip: str, port: int, token: str) -> str:
    if ":" in ip:
        raise ProtocolError("IPv6 literals are not supported in the pairing payload")
    return f"{QR_PREFIX}:{ip}:{port}:{token}"


@dataclass(frozen=True, slots=True)
class PairingPayload:
    ip: str
    port: int
    token: str

    @classmethod
    def decode(cls, text: str) -> "PairingPayload":
        parts = text.strip().split(":")
        if len(parts) != 4 or parts[0] != QR_PREFIX:
            raise ProtocolError("not a Nexus pairing payload")
        try:
            port = int(parts[2])
        except ValueError as exc:
            raise ProtocolError(f"bad port {parts[2]!r}") from exc
        if not 1 <= port <= 65535:
            raise ProtocolError(f"port out of range: {port}")
        return cls(ip=parts[1], port=port, token=parts[3])


#: Payload length for every fixed-size client opcode, excluding the opcode byte.
FIXED_PAYLOAD_SIZES: Final[dict[int, int]] = {
    ClientOpcode.INPUT: INPUT_PAYLOAD_SIZE,
    ClientOpcode.MOUSE: 3,
    ClientOpcode.SCROLL: 2,
    ClientOpcode.PING: 4,
}
