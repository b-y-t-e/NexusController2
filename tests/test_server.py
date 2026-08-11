"""End-to-end tests: a real TCP socket in, a recorded virtual pad out."""

import socket
import struct
import threading

import pytest

from nexus_server import protocol as P
from nexus_server.buzz import BuzzButton
from nexus_server.protocol import (
    Button,
    DeviceType,
    DPad,
    Hello,
    InputFlag,
    InputState,
    RejectReason,
)

from .conftest import free_port, wait_for


class Client:
    """A minimal protocol client for tests."""

    def __init__(self, server, *, token: str | None = None,
                 device_type: DeviceType = DeviceType.XBOX360, name: str = "Tester"):
        self.sock = socket.create_connection((server.bind_ip, server.settings.port), timeout=3)
        self.sock.settimeout(3)
        self.token = server.settings.token if token is None else token
        self.device_type = device_type
        self.name = name
        self.slot: int | None = None

    def hello(self, *, version: int = P.PROTOCOL_VERSION) -> bytes:
        self.sock.sendall(Hello(version, self.device_type, self.token, self.name).encode())
        return self.read_message()

    def connect(self):
        reply = self.hello()
        assert reply[0] == P.ServerOpcode.WELCOME, f"rejected: {reply!r}"
        self.slot = reply[1]
        self.features = reply[2]
        return self

    def send_input(self, **kwargs):
        self.sock.sendall(bytes([P.ClientOpcode.INPUT]) + InputState(**kwargs).encode())

    def send_raw(self, payload: bytes):
        self.sock.sendall(payload)

    def read_exact(self, count: int) -> bytes:
        chunks = b""
        while len(chunks) < count:
            chunk = self.sock.recv(count - len(chunks))
            if not chunk:
                raise ConnectionError("closed")
            chunks += chunk
        return chunks

    def read_message(self) -> bytes:
        opcode = self.read_exact(1)[0]
        sizes = {
            P.ServerOpcode.WELCOME: 2,
            P.ServerOpcode.REJECT: 1,
            P.ServerOpcode.RUMBLE: 2,
            P.ServerOpcode.LED: 3,
            P.ServerOpcode.PONG: 4,
        }
        return bytes([opcode]) + self.read_exact(sizes[opcode])

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def pad_for(server, slot: int):
    return wait_for(lambda: server.slots.sessions[slot].pad)


class TestHandshake:
    def test_valid_handshake_is_accepted(self, server):
        with Client(server) as client:
            reply = client.hello()
            assert reply[0] == P.ServerOpcode.WELCOME
            assert reply[1] == 0
            assert reply[2] & P.Feature.RUMBLE

    def test_wrong_token_is_rejected(self, server):
        with Client(server, token="wrong") as client:
            assert client.hello() == P.encode_reject(RejectReason.BAD_TOKEN)

    def test_wrong_version_is_rejected(self, server):
        with Client(server) as client:
            assert client.hello(version=0x01) == P.encode_reject(RejectReason.BAD_VERSION)

    def test_token_can_be_disabled(self, server):
        server.settings.require_token = False
        with Client(server, token="anything at all") as client:
            assert client.hello()[0] == P.ServerOpcode.WELCOME

    def test_non_hello_first_is_rejected(self, server):
        with Client(server) as client:
            client.send_raw(bytes([P.ClientOpcode.INPUT]) + InputState().encode())
            assert client.read_message() == P.encode_reject(RejectReason.MALFORMED)

    def test_unknown_device_type_is_rejected(self, server):
        with Client(server) as client:
            client.send_raw(bytes([0x10, 0x02, 0x7F, 0x00, 0x00]))
            assert client.read_message() == P.encode_reject(RejectReason.MALFORMED)

    def test_server_full(self, server):
        clients = [Client(server).connect() for _ in range(4)]
        try:
            with Client(server) as extra:
                assert extra.read_message() == P.encode_reject(RejectReason.SERVER_FULL)
        finally:
            for client in clients:
                client.close()

    def test_slots_are_reused_after_disconnect(self, server):
        first = Client(server).connect()
        assert first.slot == 0
        first.close()
        wait_for(lambda: not server.slots.sessions[0].connected)
        with Client(server).connect() as second:
            assert second.slot == 0

    def test_repeated_bad_tokens_get_rate_limited(self, server):
        for _ in range(5):
            with Client(server, token="bad") as client:
                assert client.hello()[1] == RejectReason.BAD_TOKEN
        with Client(server) as blocked:
            assert blocked.read_message() == P.encode_reject(RejectReason.RATE_LIMITED)

    def test_successful_handshake_clears_the_failure_count(self, server):
        for _ in range(3):
            with Client(server, token="bad") as client:
                client.hello()
        with Client(server).connect():
            pass
        for _ in range(3):
            with Client(server, token="bad") as client:
                assert client.hello()[1] == RejectReason.BAD_TOKEN

    @pytest.mark.parametrize("device_type", list(DeviceType))
    def test_every_device_type_connects(self, server, backend, device_type):
        with Client(server, device_type=device_type).connect() as client:
            assert backend.created[-1].device_type is device_type
            assert server.slots.sessions[client.slot].device_type is device_type

    def test_client_name_is_recorded(self, server):
        with Client(server, name="Ania").connect() as client:
            assert server.slots.sessions[client.slot].name == "Ania"

    def test_empty_name_falls_back_to_the_slot_number(self, server):
        with Client(server, name="").connect() as client:
            assert server.slots.sessions[client.slot].name == "Player 1"


class TestInput:
    def test_buttons_reach_the_pad(self, server):
        with Client(server).connect() as client:
            client.send_input(buttons_low=int(Button.SOUTH))
            pad = pad_for(server, client.slot)
            wait_for(lambda: pad.state.buttons_low == int(Button.SOUTH))

    def test_axes_reach_the_pad(self, server):
        with Client(server).connect() as client:
            client.send_input(lx=127, ly=-127)
            pad = pad_for(server, client.slot)
            wait_for(lambda: pad.state.lx == pytest.approx(1.0))
            assert pad.state.ly == pytest.approx(-1.0)

    def test_triggers_reach_the_pad(self, server):
        with Client(server).connect() as client:
            client.send_input(left_trigger=200, right_trigger=10)
            pad = pad_for(server, client.slot)
            wait_for(lambda: pad.state.left_trigger == 200)
            assert pad.state.right_trigger == 10

    def test_dpad_reaches_the_pad(self, server):
        with Client(server).connect() as client:
            client.send_input(buttons_high=int(DPad.UP | DPad.LEFT))
            pad = pad_for(server, client.slot)
            wait_for(lambda: pad.state.dpad == (-1, 1))

    def test_guide_button_is_reachable(self, server):
        """v1 used this bit for a mode flag, so Guide could never be pressed."""
        with Client(server).connect() as client:
            client.send_input(buttons_high=int(DPad.GUIDE))
            pad = pad_for(server, client.slot)
            wait_for(lambda: pad.state.buttons_high & DPad.GUIDE)

    def test_buzz_buttons_are_translated(self, server):
        with Client(server, device_type=DeviceType.BUZZ).connect() as client:
            client.send_input(buttons_low=int(BuzzButton.RED))
            pad = pad_for(server, client.slot)
            wait_for(lambda: pad.state.buttons_low == int(Button.RIGHT_SHOULDER))

    def test_buzz_yellow_is_the_a_button(self, server):
        with Client(server, device_type=DeviceType.BUZZ).connect() as client:
            client.send_input(buttons_low=int(BuzzButton.YELLOW))
            pad = pad_for(server, client.slot)
            wait_for(lambda: pad.state.buttons_low == int(Button.SOUTH))

    def test_packet_counters_advance(self, server):
        with Client(server).connect() as client:
            for _ in range(10):
                client.send_input(lx=1)
            wait_for(lambda: server.slots.sessions[client.slot].packet_count >= 10)
            assert server.total_packets >= 10

    def test_visuals_are_published(self, server):
        with Client(server).connect() as client:
            client.send_input(lx=127, left_trigger=255)
            wait_for(lambda: server.slots.sessions[client.slot].visuals.lx > 0.9)
            snapshot = server.snapshot()["players"][client.slot]
            assert snapshot["visuals"]["lt"] == pytest.approx(1.0)

    def test_four_players_drive_four_independent_pads(self, server, backend):
        clients = [Client(server).connect() for _ in range(4)]
        try:
            for index, client in enumerate(clients):
                client.send_input(buttons_low=1 << index)
            for index, client in enumerate(clients):
                pad = pad_for(server, client.slot)
                wait_for(lambda pad=pad, index=index: pad.state.buttons_low == 1 << index)
            assert len({id(p) for p in backend.created}) == 4
        finally:
            for client in clients:
                client.close()

    def test_malformed_input_does_not_kill_the_connection(self, server):
        with Client(server).connect() as client:
            client.send_input(lx=5)
            client.send_raw(bytes([P.ClientOpcode.PING]) + struct.pack(">I", 7))
            assert client.read_message() == P.encode_pong(7)


class TestPingAndRumble:
    def test_ping_pong_preserves_the_sequence(self, server):
        with Client(server).connect() as client:
            for seq in (0, 1, 65535, 0xFFFFFFFF):
                client.send_raw(bytes([P.ClientOpcode.PING]) + struct.pack(">I", seq))
                assert client.read_message() == P.encode_pong(seq)

    def test_rumble_is_forwarded(self, server):
        with Client(server).connect() as client:
            pad = pad_for(server, client.slot)
            pad.on_rumble(200, 100)
            assert client.read_message() == P.encode_rumble(200, 100)

    def test_rumble_is_suppressed_when_haptics_are_off(self, server):
        server.settings.haptics = False
        with Client(server).connect() as client:
            pad = pad_for(server, client.slot)
            pad.on_rumble(200, 100)
            client.sock.settimeout(0.2)
            client.send_raw(bytes([P.ClientOpcode.PING]) + struct.pack(">I", 1))
            assert client.read_message() == P.encode_pong(1)

    def test_led_is_forwarded(self, server):
        with Client(server, device_type=DeviceType.DUALSHOCK4).connect() as client:
            pad = pad_for(server, client.slot)
            pad.on_led(1, 2, 3)
            assert client.read_message() == P.encode_led(1, 2, 3)


class TestDesktopControl:
    def test_text_is_ignored_while_disabled(self, server, desktop_backend):
        with Client(server).connect() as client:
            client.send_raw(bytes([P.ClientOpcode.TEXT, 5]) + b"hello")
            client.send_raw(bytes([P.ClientOpcode.PING]) + struct.pack(">I", 1))
            client.read_message()
            assert desktop_backend.typed == []

    def test_text_is_typed_once_enabled(self, server, desktop_backend):
        server.desktop.enabled = True
        with Client(server).connect() as client:
            client.send_raw(bytes([P.ClientOpcode.TEXT, 5]) + b"hello")
            wait_for(lambda: desktop_backend.typed == ["hello"])

    def test_only_the_locked_slot_may_type(self, server, desktop_backend):
        server.desktop.enabled = True
        server.desktop.slot = 0
        first = Client(server).connect()
        second = Client(server).connect()
        try:
            second.send_raw(bytes([P.ClientOpcode.TEXT, 3]) + b"bad")
            second.send_raw(bytes([P.ClientOpcode.PING]) + struct.pack(">I", 1))
            second.read_message()
            assert desktop_backend.typed == []
        finally:
            first.close()
            second.close()

    def test_mouse_and_scroll(self, server, desktop_backend):
        server.desktop.enabled = True
        with Client(server).connect() as client:
            client.send_raw(bytes([P.ClientOpcode.MOUSE, 0xFB, 0x05, 0x01]))
            client.send_raw(bytes([P.ClientOpcode.SCROLL, 0x00, 0x02]))
            wait_for(lambda: desktop_backend.moves == [(-5, 5)])
            wait_for(lambda: desktop_backend.scrolls == [(0, 2)])
            assert desktop_backend.mouse_buttons["left"] is True

    def test_mouse_mode_moves_the_cursor_instead_of_the_pad(self, server, desktop_backend):
        server.desktop.enabled = True
        with Client(server).connect() as client:
            client.send_input(roll=0, pitch=0, flags=InputFlag.MOUSE_MODE)
            client.send_input(roll=4000, pitch=0, flags=InputFlag.MOUSE_MODE, lx=127)
            wait_for(lambda: any(dx > 0 for dx, _ in desktop_backend.moves))
            pad = pad_for(server, client.slot)
            assert pad.state.lx == 0.0

    def test_key_bindings_fire_edges_and_mask_the_pad(self, server, desktop_backend):
        server.settings.key_bindings = {"0": {"a": "space"}}
        server.desktop.enabled = True
        with Client(server).connect() as client:
            client.send_input(buttons_low=int(Button.SOUTH))
            wait_for(lambda: desktop_backend.keys.get("space") is True)
            pad = pad_for(server, client.slot)
            assert pad.state.buttons_low == 0
            client.send_input(buttons_low=0)
            wait_for(lambda: desktop_backend.keys.get("space") is False)

    def test_bound_keys_are_released_on_disconnect(self, server, desktop_backend):
        server.settings.key_bindings = {"0": {"a": "space"}}
        server.desktop.enabled = True
        client = Client(server).connect()
        client.send_input(buttons_low=int(Button.SOUTH))
        wait_for(lambda: desktop_backend.keys.get("space") is True)
        client.close()
        wait_for(lambda: desktop_backend.keys.get("space") is False)


class TestLifecycle:
    def test_snapshot_shape(self, server):
        snapshot = server.snapshot()
        assert snapshot["running"] is True
        assert snapshot["capacity"] == 4
        assert snapshot["connected"] == 0
        assert len(snapshot["players"]) == 4
        assert snapshot["name"] == "test-pc"

    def test_disconnect_releases_the_pad(self, server):
        client = Client(server).connect()
        pad = pad_for(server, client.slot)
        client.close()
        wait_for(lambda: pad.closed)
        assert server.slots.sessions[0].connected is False

    def test_stop_closes_every_client(self, server):
        client = Client(server).connect()
        server.stop()
        assert server.running is False
        with pytest.raises((ConnectionError, OSError)):
            client.read_exact(1)

    def test_stop_is_idempotent(self, server):
        server.stop()
        server.stop()

    def test_restart_on_the_same_port(self, server):
        port = server.settings.port
        server.stop()
        server.start()
        assert server.settings.port == port
        with Client(server).connect():
            pass

    def test_bind_failure_reports_a_clear_error(self, settings, backend, desktop):
        from nexus_server.server import ControllerServer

        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", settings.port))
        blocker.listen(1)
        server = ControllerServer(settings, backend, desktop)
        try:
            with pytest.raises(OSError):
                server.start()
            assert server.last_error and str(settings.port) in server.last_error
            assert server.running is False
        finally:
            blocker.close()

    def test_unknown_opcode_closes_the_connection(self, server):
        with Client(server).connect() as client:
            client.send_raw(bytes([0x99]))
            with pytest.raises((ConnectionError, OSError)):
                client.read_exact(1)

    def test_abrupt_disconnect_frees_the_slot(self, server):
        client = Client(server).connect()
        client.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()  # sends RST rather than FIN
        wait_for(lambda: server.slots.sessions[0].reserved is False)


class TestDiscovery:
    @pytest.fixture()
    def discovery_server(self, settings, backend, desktop):
        from nexus_server.server import ControllerServer

        settings.discovery_enabled = True
        instance = ControllerServer(settings, backend, desktop)
        instance.start()
        yield instance
        instance.stop()

    def test_responds_to_a_probe(self, discovery_server):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        try:
            sock.sendto(
                P.DISCOVERY_REQUEST,
                ("127.0.0.1", discovery_server.settings.discovery_port),
            )
            data, _ = sock.recvfrom(512)
        finally:
            sock.close()
        response = P.DiscoveryResponse.decode(data)
        assert response.name == "test-pc"
        assert response.port == discovery_server.settings.port
        assert response.token_required is True

    def test_ignores_unrelated_datagrams(self, discovery_server):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.4)
        try:
            sock.sendto(b"hello?", ("127.0.0.1", discovery_server.settings.discovery_port))
            with pytest.raises(socket.timeout):
                sock.recvfrom(512)
        finally:
            sock.close()


def test_concurrent_connects_never_share_a_slot(server):
    """Hammer accept() to make sure the slot race is really gone."""
    results: list[int] = []
    errors: list[Exception] = []
    # Clients must stay referenced: if one is collected mid-test its socket
    # closes, the server frees the slot, and a later thread legitimately reuses
    # it — which would look exactly like the race we are testing for.
    clients: list[Client] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def worker():
        try:
            barrier.wait()
            client = Client(server).connect()
            with lock:
                clients.append(client)
                results.append(client.slot)
            client.send_input(lx=1)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert not errors, errors
        assert sorted(results) == [0, 1, 2, 3]
    finally:
        for client in clients:
            client.close()
