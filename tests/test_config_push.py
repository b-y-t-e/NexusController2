"""Central configuration end to end: phone reports, PC edits, PC pushes back."""

import json
import socket
import struct

import pytest

from nexus_server import protocol as P
from nexus_server.padconfig import PadConfig
from nexus_server.protocol import DeviceType, Hello, PROTOCOL_VERSION, ServerOpcode

from .conftest import wait_for


class ConfigClient:
    """A client that speaks the configuration messages."""

    def __init__(self, server, device_type=DeviceType.XBOX360, name="Phone"):
        self.sock = socket.create_connection((server.bind_ip, server.settings.port), timeout=3)
        self.sock.settimeout(3)
        self.sock.sendall(
            Hello(PROTOCOL_VERSION, device_type, server.settings.token, name).encode()
        )
        reply = self._read(3)
        assert reply[0] == ServerOpcode.WELCOME, f"rejected: {reply!r}"
        self.slot = reply[1]

    def _read(self, count):
        chunks = b""
        while len(chunks) < count:
            chunk = self.sock.recv(count - len(chunks))
            if not chunk:
                raise ConnectionError("closed")
            chunks += chunk
        return chunks

    def report(self, config: PadConfig):
        body = config.encode_body()
        self.sock.sendall(bytes([P.ClientOpcode.CONFIG]) + struct.pack(">H", len(body)) + body)

    def report_raw(self, body: bytes):
        self.sock.sendall(bytes([P.ClientOpcode.CONFIG]) + struct.pack(">H", len(body)) + body)

    def read_set_config(self) -> dict:
        opcode = self._read(1)[0]
        assert opcode == ServerOpcode.SET_CONFIG, f"expected SET_CONFIG, got 0x{opcode:02x}"
        length = struct.unpack(">H", self._read(2))[0]
        return json.loads(self._read(length))

    def ping(self, seq=1):
        self.sock.sendall(bytes([P.ClientOpcode.PING]) + struct.pack(">I", seq))
        return self._read(5)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class TestFraming:
    def test_set_config_framing(self):
        framed = P.encode_set_config(b"{}")
        assert framed == bytes([ServerOpcode.SET_CONFIG, 0x00, 0x02]) + b"{}"

    def test_length_is_uint16_not_a_single_byte(self):
        """Layouts run past 255 bytes, which a one-byte length could not express."""
        body = b"x" * 900
        framed = P.encode_set_config(body)
        assert struct.unpack(">H", framed[1:3])[0] == 900

    def test_oversized_body_is_refused(self):
        with pytest.raises(P.ProtocolError, match="too large"):
            P.encode_set_config(b"x" * (P.MAX_CONFIG_BODY + 1))

    def test_decode_length_rejects_oversized(self):
        with pytest.raises(P.ProtocolError, match="too large"):
            P.decode_config_length(struct.pack(">H", P.MAX_CONFIG_BODY + 1))

    def test_decode_length_needs_two_bytes(self):
        with pytest.raises(P.ProtocolError):
            P.decode_config_length(b"\x00")


class TestReporting:
    def test_server_stores_what_the_phone_reports(self, server):
        with ConfigClient(server) as client:
            config = PadConfig.default(DeviceType.XBOX360, name="Ania")
            config.layout["FACE"]["x"] = 0.77
            client.report(config)
            wait_for(lambda: server.slots.sessions[client.slot].config)
            stored = server.slots.sessions[client.slot].config
            assert stored.layout["FACE"]["x"] == 0.77
            assert stored.name == "Ania"

    def test_reported_config_appears_in_the_snapshot(self, server):
        with ConfigClient(server, DeviceType.BUZZ) as client:
            client.report(PadConfig.default(DeviceType.BUZZ))
            wait_for(lambda: server.snapshot()["players"][client.slot]["config"])
            snapshot = server.snapshot()["players"][client.slot]["config"]
            assert snapshot["type"] == "BUZZ"
            assert "BUZZ_RED" in snapshot["layout"]

    def test_sparse_report_is_filled_in(self, server):
        """A phone may report only what it changed; the PC needs the whole picture."""
        with ConfigClient(server) as client:
            sparse = {"v": 1, "type": "XBOX360", "layout": {"FACE": {"x": 0.3, "y": 0.3}}}
            client.report_raw(json.dumps(sparse).encode())
            wait_for(lambda: server.slots.sessions[client.slot].config)
            stored = server.slots.sessions[client.slot].config
            assert stored.layout["FACE"]["x"] == 0.3
            assert "L_STICK" in stored.layout

    def test_bad_config_is_ignored_without_dropping_the_connection(self, server):
        with ConfigClient(server) as client:
            client.report_raw(b"{ this is not json")
            assert client.ping(7) == P.encode_pong(7)
            assert server.slots.sessions[client.slot].config is None

    def test_wrong_schema_version_is_ignored(self, server):
        with ConfigClient(server) as client:
            client.report_raw(json.dumps({"v": 42, "type": "XBOX360"}).encode())
            assert client.ping(1) == P.encode_pong(1)
            assert server.slots.sessions[client.slot].config is None

    def test_empty_body_is_survivable(self, server):
        with ConfigClient(server) as client:
            client.report_raw(b"")
            assert client.ping(2) == P.encode_pong(2)

    def test_config_is_cleared_on_disconnect(self, server):
        client = ConfigClient(server)
        client.report(PadConfig.default())
        wait_for(lambda: server.slots.sessions[client.slot].config)
        client.close()
        wait_for(lambda: server.slots.sessions[0].config is None)


class TestPushing:
    def test_push_reaches_the_phone(self, server):
        with ConfigClient(server) as client:
            config = PadConfig.default(DeviceType.XBOX360)
            config.layout["FACE"]["x"] = 0.42
            assert server.push_config(client.slot, config) is True
            received = client.read_set_config()
            assert received["layout"]["FACE"]["x"] == 0.42
            assert received["v"] == 1

    def test_push_marks_the_slot_pending_until_the_phone_echoes(self, server):
        with ConfigClient(server) as client:
            server.push_config(client.slot, PadConfig.default())
            client.read_set_config()
            assert server.slots.sessions[client.slot].config_pending is True
            client.report(PadConfig.default())
            wait_for(lambda: server.slots.sessions[client.slot].config_pending is False)

    def test_push_to_an_empty_slot_fails_cleanly(self, server):
        assert server.push_config(3, PadConfig.default()) is False

    def test_push_to_a_nonexistent_slot_fails_cleanly(self, server):
        assert server.push_config(99, PadConfig.default()) is False
        assert server.push_config(-1, PadConfig.default()) is False

    def test_buzz_layout_can_be_pushed(self, server):
        with ConfigClient(server, DeviceType.BUZZ) as client:
            server.push_config(client.slot, PadConfig.default(DeviceType.BUZZ))
            received = client.read_set_config()
            assert received["type"] == "BUZZ"
            assert set(received["layout"]) == {
                "BUZZ_RED", "BUZZ_BLUE", "BUZZ_ORANGE", "BUZZ_GREEN", "BUZZ_YELLOW"
            }

    def test_the_same_document_can_go_to_several_phones(self, server):
        """Author once on the PC, apply everywhere — the point of the feature."""
        clients = [ConfigClient(server) for _ in range(3)]
        try:
            config = PadConfig.default()
            config.layout["L_STICK"]["x"] = 0.25
            for client in clients:
                assert server.push_config(client.slot, config) is True
            for client in clients:
                assert client.read_set_config()["layout"]["L_STICK"]["x"] == 0.25
        finally:
            for client in clients:
                client.close()

    def test_pushed_document_omits_the_screen_size(self, server):
        with ConfigClient(server) as client:
            config = PadConfig.default()
            config.screen = (2400, 1080)
            server.push_config(client.slot, config)
            assert "screen" not in client.read_set_config()

    def test_input_still_works_after_a_push(self, server):
        from nexus_server.protocol import Button, InputState

        with ConfigClient(server) as client:
            server.push_config(client.slot, PadConfig.default())
            client.read_set_config()
            client.sock.sendall(
                bytes([P.ClientOpcode.INPUT])
                + InputState(buttons_low=int(Button.SOUTH)).encode()
            )
            pad = wait_for(lambda: server.slots.sessions[client.slot].pad)
            wait_for(lambda: pad.state.buttons_low == int(Button.SOUTH))
