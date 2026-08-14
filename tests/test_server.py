"""End-to-end tests: a real TCP socket in, a recorded virtual pad out."""

import socket
import struct
import threading
import time

import pytest

from nexus_server import protocol as P
from nexus_server import server as server_module
from nexus_server.buzz import BuzzButton
from nexus_server.protocol import (
    MAX_PLAYERS,
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
        clients = [Client(server).connect() for _ in range(MAX_PLAYERS)]
        try:
            with Client(server) as extra:
                assert extra.read_message() == P.encode_reject(RejectReason.SERVER_FULL)
        finally:
            for client in clients:
                client.close()

    def test_every_slot_gets_its_own_pad(self, server, backend):
        """A full house is ``MAX_PLAYERS`` distinct slots and distinct devices.

        The one failure mode worth a test here is two clients sharing a slot —
        they would then drive the same virtual pad and fight over it.
        """
        clients = [Client(server).connect() for _ in range(MAX_PLAYERS)]
        try:
            assert sorted(c.slot for c in clients) == list(range(MAX_PLAYERS))
            wait_for(lambda: all(s.pad is not None for s in server.slots.sessions))
            pads = [s.pad for s in server.slots.sessions]
            assert len({id(pad) for pad in pads}) == MAX_PLAYERS
            assert len(backend.created) == MAX_PLAYERS
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

    def test_a_returning_phone_takes_the_next_free_slot(self, server):
        """The slot a phone had is not "its" slot; it goes back in the pool.

        The sequence that worries people: player 1 drops out, somebody else
        connects and is given slot 1, and then the first phone comes back. It
        must land on the lowest slot that is *actually* free — never share slot 1
        with the phone that took it, and never be turned away because the slot it
        used to have is occupied.
        """
        first = Client(server).connect()
        second = Client(server).connect()
        assert (first.slot, second.slot) == (0, 1)

        first.close()
        wait_for(lambda: not server.slots.sessions[0].connected)

        with Client(server).connect() as third, Client(server).connect() as returning:
            assert third.slot == 0, "the freed slot goes to whoever asks next"
            assert returning.slot == 2, "the returning phone gets the next free slot"
            assert len({third.slot, returning.slot, second.slot}) == 3
        second.close()

    def test_a_slot_is_reclaimed_when_the_phone_stops_answering(self, server, monkeypatch):
        """A phone that vanishes without closing must not hold its slot for long.

        Pulling the battery, walking out of range or killing the app leaves the
        TCP connection open as far as this machine is concerned: no FIN arrives,
        so the only evidence is silence. Until that is noticed the slot stays
        taken *and* its virtual pad stays plugged in — the game sees a pad frozen
        on its last frame, and the phone coming back is given a second slot
        beside its own ghost.

        The client sends at least a heartbeat every 250 ms (PROTOCOL.md §9), so
        silence for ``IDLE_TIMEOUT`` is not a quiet moment; it is a peer that is
        gone.
        """
        monkeypatch.setattr(server_module, "IDLE_TIMEOUT", 0.4)
        client = Client(server).connect()
        try:
            pad = pad_for(server, 0)
            # Deliberately no close(): closing would send a FIN and prove nothing.
            wait_for(lambda: not server.slots.sessions[0].reserved, timeout=4.0)
            assert pad.closed, "the virtual pad goes with the session"
            with Client(server).connect() as returning:
                assert returning.slot == 0
        finally:
            client.close()

    def test_repeated_bad_tokens_get_rate_limited(self, server):
        for _ in range(5):
            with Client(server, token="bad") as client:
                assert client.hello()[1] == RejectReason.BAD_TOKEN
        with Client(server) as blocked:
            assert blocked.read_message() == P.encode_reject(RejectReason.RATE_LIMITED)

    def test_a_silent_connection_holds_no_slot(self, server):
        """Connecting and saying nothing must not cost a player slot.

        Four sockets that never send a HELLO used to reserve every slot for the
        whole handshake timeout and could repeat forever, locking real phones out.
        """
        silent = [
            socket.create_connection((server.bind_ip, server.settings.port), timeout=3)
            for _ in range(4)
        ]
        try:
            # Wait until the server has actually taken all four, otherwise this
            # would pass simply by asserting before the accept loop got round to
            # them — which is exactly the regression it is meant to catch.
            wait_for(lambda: server.handshakes_in_flight == 4)
            assert all(not s.reserved for s in server.slots.sessions)
            with Client(server).connect() as client:
                assert client.slot == 0
        finally:
            for sock in silent:
                sock.close()

    def test_handshakes_in_flight_are_capped(self, server, monkeypatch):
        """And the refusal is SERVER_FULL, never RATE_LIMITED.

        The phone treats RATE_LIMITED as permanent and stops reconnecting, so
        answering a transient queue overflow with it would let a burst of
        connections lock an innocent phone out until the user intervened.
        """
        # The gate is built once, in __init__, so shrink the real thing.
        server._handshake_capacity = 2
        server._handshake_gate = threading.Semaphore(2)
        silent = [
            socket.create_connection((server.bind_ip, server.settings.port), timeout=3)
            for _ in range(2)
        ]
        try:
            wait_for(lambda: server.handshakes_in_flight == 2)
            with Client(server) as extra:
                assert extra.read_message() == P.encode_reject(RejectReason.SERVER_FULL)
        finally:
            for sock in silent:
                sock.close()

    def test_a_trickling_peer_cannot_stall_the_handshake_forever(self, server, monkeypatch):
        """The socket timeout is per-recv and restarts on every byte.

        A peer sending one byte at a time under that interval never times out, so
        it could hold a place in the handshake queue indefinitely and never be
        counted as silent either — the cap and the counter both became decorative.
        The bound has to cover the whole exchange.
        """
        monkeypatch.setattr(server_module, "HANDSHAKE_TIMEOUT", 0.6)
        sock = socket.create_connection((server.bind_ip, server.settings.port), timeout=5)
        sock.settimeout(5)
        payload = Hello(
            P.PROTOCOL_VERSION, DeviceType.XBOX360, server.settings.token, "Drip"
        ).encode()

        def drip() -> None:
            # One byte every 0.2 s: always inside the per-recv window, never
            # inside a deadline for the whole handshake. Sending the lot takes
            # far longer than HANDSHAKE_TIMEOUT.
            for byte in payload:
                try:
                    sock.sendall(bytes([byte]))
                except OSError:
                    return
                time.sleep(0.2)

        sender = threading.Thread(target=drip, daemon=True)
        sender.start()
        try:
            # The server must give up *while the peer is still dripping*. Waiting
            # for the whole drip would pass without a deadline too — the bytes do
            # eventually all arrive — so the margin is what makes this a test.
            assert 0.2 * len(payload) > 4.0, "the drip must outlast the deadline"
            wait_for(lambda: server.handshakes_in_flight == 0, timeout=2.0)
        finally:
            sock.close()
            sender.join(timeout=2)

    def test_the_queue_place_is_given_back(self, server):
        """A settled connection must not keep one — neither a served client...

        ...nor a refused one, which used to hold its place for the whole polite
        drain and so could crowd out real phones with slow refusals.
        """
        with Client(server, token="wrong") as refused:
            refused.hello()
        wait_for(lambda: server.handshakes_in_flight == 0)

        with Client(server).connect():
            wait_for(lambda: server.handshakes_in_flight == 0)

    def test_connect_then_close_is_not_a_failed_attempt(self, server):
        """Port scans and health checks look exactly like this.

        Counting them would let any such probe from an address block a phone
        sharing it for a minute.
        """
        for _ in range(6):
            sock = socket.create_connection((server.bind_ip, server.settings.port), timeout=3)
            sock.close()
        # The failures, if any were being counted, are recorded on the client
        # threads. Asserting before those have run would let this pass against
        # the very code it exists to reject.
        wait_for(lambda: server.handshakes_in_flight == 0)
        with Client(server).connect() as client:
            assert client.slot == 0

    def _go_silent(self, server, times: int) -> None:
        """Open ``times`` connections that say nothing until the server gives up."""
        for _ in range(times):
            sock = socket.create_connection((server.bind_ip, server.settings.port), timeout=3)
            sock.settimeout(3)
            try:
                sock.recv(16)   # blocks until the server gives up on the handshake
            except OSError:
                pass
            sock.close()

    def test_silence_is_rate_limited_separately(self, server, monkeypatch):
        """Repeated silence is still abuse, and eventually blocked.

        With SERVER_FULL, not RATE_LIMITED: the client treats the latter as a
        final verdict and stops reconnecting, so answering silence with it would
        give back exactly the permanent lockout the separate counter exists to
        avoid — a slow network would still cost the phone its connection.
        """
        # The real 5 s handshake window would make this test take 25 s; the
        # server reads the constant per connection, so shrinking it is enough.
        monkeypatch.setattr(server_module, "HANDSHAKE_TIMEOUT", 0.1)
        server._silent.max_attempts = 3
        self._go_silent(server, 3)
        with Client(server) as blocked:
            assert blocked.read_message() == P.encode_reject(RejectReason.SERVER_FULL)

    def test_a_flaky_phone_is_not_locked_out_by_the_token_limit(self, server, monkeypatch):
        """Wi-Fi dropping between connect and HELLO looks exactly like an attack.

        The phone retries every three seconds and treats RATE_LIMITED as final,
        so counting silence on the same five-strike budget as a wrong token would
        let a bad minute of Wi-Fi lock a phone out of its own PC for good.
        """
        monkeypatch.setattr(server_module, "HANDSHAKE_TIMEOUT", 0.1)
        self._go_silent(server, 6)
        with Client(server).connect() as client:
            assert client.slot == 0

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

    def test_a_trackpad_drag_survives_the_input_heartbeat(self, server, desktop_backend):
        """Holding a button on the trackpad must not be undone by a pad frame.

        In trackpad mode the phone sends two streams at once: MOUSE messages from
        the finger, and ordinary INPUT frames carrying the mouse-mode flag — at
        least one every 250 ms, because the pad heartbeats even when nothing is
        touched. Both end up at handle_mouse, whose button field is *absolute*,
        so the heartbeat used to arrive with no buttons set and let go of the
        button the finger was still holding. Dragging a window or selecting text
        could therefore never last longer than one heartbeat.
        """
        server.desktop.enabled = True
        with Client(server).connect() as client:
            client.send_raw(bytes([P.ClientOpcode.MOUSE, 0x00, 0x00, 0x01]))
            wait_for(lambda: desktop_backend.mouse_buttons.get("left") is True)

            client.send_input(flags=int(InputFlag.MOUSE_MODE))
            client.send_raw(bytes([P.ClientOpcode.PING]) + struct.pack(">I", 1))
            client.read_message()      # the frame has certainly been handled by now
            assert desktop_backend.mouse_buttons["left"] is True, "the drag was dropped"

            client.send_raw(bytes([P.ClientOpcode.MOUSE, 0x00, 0x00, 0x00]))
            wait_for(lambda: desktop_backend.mouse_buttons.get("left") is False)

    def test_a_selection_holds_the_button_across_the_whole_drag(
        self, server, desktop_backend
    ):
        """The end-to-end shape of "tap and a half": press, travel, release.

        The phone sends the button state with *every* mouse message, so a long
        selection is a stream of MOUSE frames all carrying the same held button,
        interleaved with the pad's own heartbeat. What matters here is that the
        button is down for the whole journey and up exactly once at the end.
        """
        server.desktop.enabled = True
        with Client(server).connect() as client:
            client.send_raw(bytes([P.ClientOpcode.MOUSE, 0x00, 0x00, 0x01]))
            wait_for(lambda: desktop_backend.mouse_buttons.get("left") is True)

            for _ in range(20):
                client.send_raw(bytes([P.ClientOpcode.MOUSE, 0x05, 0x02, 0x01]))
                client.send_input(flags=int(InputFlag.MOUSE_MODE))
            client.send_raw(bytes([P.ClientOpcode.PING]) + struct.pack(">I", 7))
            client.read_message()

            assert desktop_backend.mouse_buttons["left"] is True
            travelled = sum(dx for dx, _ in desktop_backend.moves)
            assert travelled == 100, "every step of the drag should have moved"

            client.send_raw(bytes([P.ClientOpcode.MOUSE, 0x00, 0x00, 0x00]))
            wait_for(lambda: desktop_backend.mouse_buttons.get("left") is False)

    def test_a_held_mouse_button_is_released_when_the_phone_goes_away(
        self, server, desktop_backend
    ):
        """A phone that drops mid-drag must not leave the button down on the PC.

        Nothing on the desktop recovers from this by itself: the button stays
        pressed, every mouse move keeps selecting or dragging, and the person at
        the keyboard has to click to break it. The gyro path already released
        what it held; the trackpad's own buttons were tracked nowhere.
        """
        server.desktop.enabled = True
        client = Client(server).connect()
        client.send_raw(bytes([P.ClientOpcode.MOUSE, 0x00, 0x00, 0x03]))
        wait_for(lambda: desktop_backend.mouse_buttons.get("left") is True)
        assert desktop_backend.mouse_buttons["right"] is True

        client.close()
        wait_for(lambda: not server.slots.sessions[0].connected)
        assert desktop_backend.mouse_buttons["left"] is False
        assert desktop_backend.mouse_buttons["right"] is False

    def test_mouse_mode_moves_the_cursor_instead_of_the_pad(self, server, desktop_backend):
        server.desktop.enabled = True
        with Client(server).connect() as client:
            gyro = int(InputFlag.MOUSE_MODE | InputFlag.GYRO_VALID)
            client.send_input(roll=0, pitch=0, flags=gyro)
            client.send_input(roll=4000, pitch=0, flags=gyro, lx=127)
            wait_for(lambda: any(dx > 0 for dx, _ in desktop_backend.moves))
            pad = pad_for(server, client.slot)
            assert pad.state.lx == 0.0

    def test_a_finger_on_the_trackpad_stops_the_tilt_fighting_it(
        self, server, desktop_backend
    ):
        """Both streams reach one cursor in trackpad mode.

        Tilt is unavoidable while somebody holds a phone to swipe on it, so the
        pad's drift was being added to every stroke of the finger and the cursor
        wandered off between them. The finger is what the user is aiming with.
        """
        server.desktop.enabled = True
        gyro = int(InputFlag.MOUSE_MODE | InputFlag.GYRO_VALID)
        with Client(server).connect() as client:
            client.send_input(roll=0, pitch=0, flags=gyro)      # latch the centre
            client.send_raw(bytes([P.ClientOpcode.MOUSE, 5, 0, 0]))
            wait_for(lambda: (5, 0) in desktop_backend.moves)
            before = len(desktop_backend.moves)

            # Well outside the deadzone: without the rule this moves the cursor.
            for _ in range(5):
                client.send_input(roll=8000, pitch=0, flags=gyro)
            wait_for(lambda: len(desktop_backend.moves) > before)
            assert all(
                (dx, dy) == (0, 0) for dx, dy in desktop_backend.moves[before:]
            ), "the tilt moved the cursor while the finger was on the glass"

    def test_and_the_tilt_takes_over_again_once_the_finger_is_gone(
        self, server, desktop_backend, monkeypatch
    ):
        server.desktop.enabled = True
        gyro = int(InputFlag.MOUSE_MODE | InputFlag.GYRO_VALID)
        monkeypatch.setattr("nexus_server.server.GYRO_YIELD_SECONDS", 0.0)
        with Client(server).connect() as client:
            client.send_input(roll=0, pitch=0, flags=gyro)
            client.send_raw(bytes([P.ClientOpcode.MOUSE, 5, 0, 0]))
            wait_for(lambda: (5, 0) in desktop_backend.moves)
            client.send_input(roll=8000, pitch=0, flags=gyro)
            wait_for(lambda: any(dx > 0 for dx, _ in desktop_backend.moves[1:]))

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

    def test_moving_the_desktop_lock_releases_a_held_key(self, server, desktop_backend):
        """The slot losing the lock can no longer send a release of its own.

        Its release path runs through the same gate that just shut, so unless the
        server lets go here the key stays physically down until a restart.
        """
        server.settings.key_bindings = {"0": {"a": "space"}}
        server.desktop.enabled = True
        with Client(server).connect() as client:
            client.send_input(buttons_low=int(Button.SOUTH))
            wait_for(lambda: desktop_backend.keys.get("space") is True)

            server.desktop.slot = 1
            server.reset_desktop_state()

            assert desktop_backend.keys.get("space") is False
            assert server.slots.sessions[0].keys.release() == []

    def test_moving_the_desktop_lock_releases_held_mouse_buttons(self, server, desktop_backend):
        """Gyro mouse mode holds buttons through a different path than key binds."""
        server.desktop.enabled = True
        with Client(server).connect() as client:
            session = server.slots.sessions[0]
            # right_trigger > 100 is "left mouse button down" in mouse mode.
            client.send_input(flags=InputFlag.MOUSE_MODE, right_trigger=255)
            wait_for(lambda: desktop_backend.mouse_buttons.get("left") is True)
            wait_for(lambda: session.mouse_buttons_held != 0)

            server.desktop.slot = 1
            server.reset_desktop_state()

            assert desktop_backend.mouse_buttons["left"] is False
            assert session.mouse_buttons_held == 0

    def test_a_phone_with_the_sensor_off_never_steers_the_cursor(
        self, server, desktop_backend
    ):
        """GYRO_VALID is the client saying whether roll and pitch mean anything.

        The trackpad sends mouse-mode frames whatever the sensor is doing, so
        without honouring the flag the cursor is driven by two things at once —
        and a centre latched from a switched-off sensor's zeroes made the cursor
        bolt across the screen the moment it was switched on again.
        """
        server.desktop.enabled = True
        with Client(server).connect() as client:
            session = server.slots.sessions[0]
            client.send_input(flags=int(InputFlag.MOUSE_MODE), roll=4000, pitch=-4000)
            client.send_raw(bytes([P.ClientOpcode.PING]) + struct.pack(">I", 1))
            client.read_message()
            # A (0, 0) delta is recorded but moves nothing — the real backend
            # skips it. What must never appear is an actual displacement.
            assert [m for m in desktop_backend.moves if m != (0, 0)] == []
            assert session.gyro_centre is None, "an invalid reading is not a centre"

            client.send_input(
                flags=int(InputFlag.MOUSE_MODE | InputFlag.GYRO_VALID),
                roll=0, pitch=0,
            )
            client.send_input(
                flags=int(InputFlag.MOUSE_MODE | InputFlag.GYRO_VALID),
                roll=4000, pitch=0,
            )
            wait_for(lambda: desktop_backend.moves and desktop_backend.moves[-1][0] > 0)

    def test_disabling_desktop_control_releases_a_held_key(self, server, desktop_backend):
        server.settings.key_bindings = {"0": {"a": "space"}}
        server.desktop.enabled = True
        with Client(server).connect() as client:
            client.send_input(buttons_low=int(Button.SOUTH))
            wait_for(lambda: desktop_backend.keys.get("space") is True)

            server.desktop.enabled = False
            server.reset_desktop_state()

            assert desktop_backend.keys.get("space") is False

    def test_leaving_mouse_mode_recentres_the_gyro(self, server, desktop_backend):
        """A bound button still held on the way out must not keep a stale centre.

        The reset used to live in the branch that only runs when no bound button
        changed the frame, so holding one across the transition preserved the old
        centre and the cursor jumped on the next entry into mouse mode.
        """
        server.settings.key_bindings = {"0": {"a": "space"}}
        server.desktop.enabled = True
        with Client(server).connect() as client:
            session = server.slots.sessions[0]
            client.send_input(
                roll=1000, flags=int(InputFlag.MOUSE_MODE | InputFlag.GYRO_VALID)
            )
            wait_for(lambda: session.gyro_centre == (1000, 0))

            # Out of mouse mode, holding the bound button.
            client.send_input(roll=1000, buttons_low=int(Button.SOUTH))
            wait_for(lambda: session.gyro_centre is None)


class TestLifecycle:
    def test_snapshot_shape(self, server):
        snapshot = server.snapshot()
        assert snapshot["running"] is True
        assert snapshot["capacity"] == MAX_PLAYERS
        assert snapshot["connected"] == 0
        assert len(snapshot["players"]) == MAX_PLAYERS
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

    def test_the_reply_never_carries_the_token(self, discovery_server):
        """Discovery answers anybody who asks, so it must give nothing away.

        The probe is an unauthenticated UDP broadcast: every device on the
        network — the neighbour sharing the Wi-Fi included — can send one and
        read the answer. Putting the pairing token in that reply would hand it to
        all of them and make pairing decoration. The reply says *whether* a token
        is needed, never what it is; the token travels in the QR code alone (§8).
        """
        token = discovery_server.settings.token
        assert token, "the fixture is supposed to have pairing on"

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

        assert token.encode() not in data
        assert P.DiscoveryResponse.decode(data).token_required is True

    def test_a_phone_that_only_scanned_is_refused_until_it_pairs(self, discovery_server):
        """The whole point of the flag the reply *does* carry.

        A server found by scanning gives the phone an address and nothing else,
        so the first connection it can make is one with an empty token — and it
        has to be refused, or the QR code would be securing nothing. Once paired,
        the phone dials the same address with the token it kept, and is let in.
        """
        with Client(discovery_server, token="") as unpaired:
            assert unpaired.hello() == P.encode_reject(RejectReason.BAD_TOKEN)

        with Client(discovery_server) as paired:
            assert paired.hello()[0] == P.ServerOpcode.WELCOME

    def test_with_pairing_off_the_scan_alone_is_enough(self, discovery_server):
        """And then the reply says so, so the phone can stop asking for a code."""
        discovery_server.settings.require_token = False
        with Client(discovery_server, token="") as scanned:
            assert scanned.hello()[0] == P.ServerOpcode.WELCOME

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
