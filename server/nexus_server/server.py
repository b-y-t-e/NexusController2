"""The controller server: accepts clients, drives virtual pads, answers discovery."""

from __future__ import annotations

import hmac
import logging
import socket
import threading
import time
from typing import Callable

from . import protocol as P
from .config import Settings
from .desktop import DesktopControl, gyro_to_mouse
from .devices import DriverUnavailableError, PadBackend, VirtualPad
from .netinfo import primary_ip
from .protocol import (
    ClientOpcode,
    DeviceType,
    Feature,
    Hello,
    InputState,
    MouseDelta,
    ProtocolError,
    RejectReason,
    ScrollDelta,
)
from .session import AttemptTracker, PlayerSession, RateLimiter, SlotAllocator, Visuals
from .system import FirewallManager, remove_adb_reverse, setup_adb_reverse

log = logging.getLogger(__name__)

MAX_PLAYERS = 4
HANDSHAKE_TIMEOUT = 5.0
IDLE_TIMEOUT = 30.0
#: Generous ceiling — a phone streams ~66 frames/s, so this only catches floods.
INPUT_RATE_LIMIT = 1000.0

LogSink = Callable[[str], None]


class ControllerServer:
    """Owns the listening socket, the player slots and the discovery responder.

    ``start()`` returns as soon as the listener is up; everything else runs on
    daemon threads. All public methods are safe to call from the GUI thread.
    """

    def __init__(
        self,
        settings: Settings,
        backend: PadBackend,
        desktop: DesktopControl,
        *,
        slots: int = MAX_PLAYERS,
        log_sink: LogSink | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.desktop = desktop
        self.slots = SlotAllocator(slots)
        self._log_sink = log_sink

        self._running = threading.Event()
        self._listener: socket.socket | None = None
        self._discovery: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._attempts = AttemptTracker()
        self._firewall: FirewallManager | None = None

        self.bind_ip = ""
        self.total_packets = 0
        self.last_error: str | None = None
        self.status_messages: list[str] = []

    # -- lifecycle ----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running.is_set()

    @property
    def pairing_payload(self) -> str:
        return P.encode_pairing_payload(
            self.bind_ip or primary_ip(),
            self.settings.port,
            self.settings.token if self.settings.require_token else "",
        )

    def start(self) -> None:
        """Bind the listener and start serving. Raises ``OSError`` if the port is taken."""
        with self._lock:
            if self.running:
                return
            self.status_messages = []
            self.last_error = None
            bind_ip = self.settings.bind_ip or primary_ip()

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # On Windows SO_REUSEADDR does NOT mean "reuse a TIME_WAIT port" — it
            # lets a second process bind a port that is already in use and steal
            # connections from it. SO_EXCLUSIVEADDRUSE is the option that gives
            # the POSIX-like guarantee that a clash is reported as an error.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((bind_ip, self.settings.port))
                listener.listen(MAX_PLAYERS)
            except OSError as exc:
                listener.close()
                self.last_error = f"Cannot bind {bind_ip}:{self.settings.port} — {exc}"
                raise

            listener.settimeout(0.5)
            self._listener = listener
            self.bind_ip = bind_ip
            self._running.set()
            self._log(f"Listening on {bind_ip}:{self.settings.port}")

            if self.settings.manage_firewall:
                self._firewall = FirewallManager(self.settings.port, self.settings.discovery_port)
                self._note(self._firewall.apply())
            if self.settings.manage_adb:
                self._note(setup_adb_reverse(self.settings.port))

            self._spawn(self._accept_loop, "nexus-accept")
            if self.settings.discovery_enabled:
                self._spawn(self._discovery_loop, "nexus-discovery")

    def stop(self) -> None:
        """Stop serving and release every slot. Safe to call when not running."""
        with self._lock:
            if not self.running and self._listener is None:
                return
            self._running.clear()
            for sock in (self._listener, self._discovery):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            self._listener = None
            self._discovery = None

        self.slots.release_all()
        self.desktop.release_all()
        if self._firewall is not None:
            self._firewall.remove()
            self._firewall = None
        if self.settings.manage_adb:
            remove_adb_reverse(self.settings.port)

        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self._threads = []
        self._log("Server stopped")

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _log(self, message: str) -> None:
        log.info(message)
        if self._log_sink is not None:
            try:
                self._log_sink(message)
            except Exception:  # noqa: BLE001 - a broken UI must not kill the server
                log.debug("log sink raised", exc_info=True)

    def _note(self, result) -> None:
        self.status_messages.append(result.message)
        self._log(result.message)

    # -- accept loop --------------------------------------------------------

    def _accept_loop(self) -> None:
        while self.running:
            listener = self._listener
            if listener is None:
                break
            try:
                conn, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            if self._attempts.is_blocked(address[0]):
                self._reject(conn, RejectReason.RATE_LIMITED)
                self._log(f"Blocked {address[0]}: too many failed attempts")
                continue

            session = self.slots.acquire()
            if session is None:
                self._reject(conn, RejectReason.SERVER_FULL)
                self._log(f"Rejected {address[0]}: all {len(self.slots)} slots in use")
                continue

            thread = threading.Thread(
                target=self._serve_client,
                args=(conn, address, session),
                name=f"nexus-client-{session.index}",
                daemon=True,
            )
            thread.start()

    @staticmethod
    def _reject(conn: socket.socket, reason: RejectReason) -> None:
        """Tell the client why it was refused, then close cleanly.

        Closing a socket that still has unread data in its receive queue makes
        the OS send an RST, which discards the reject message we just wrote — so
        the client would see only "connection reset" and could not explain the
        failure to the user. Half-close and drain first.
        """
        try:
            conn.sendall(P.encode_reject(reason))
            conn.shutdown(socket.SHUT_WR)
            conn.settimeout(0.5)
            drained = 0
            while drained < 64 * 1024:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                drained += len(chunk)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # -- per-client handling ------------------------------------------------

    def _serve_client(self, conn: socket.socket, address, session: PlayerSession) -> None:
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(HANDSHAKE_TIMEOUT)
            reader = _SocketReader(conn)

            try:
                hello = self._read_hello(reader)
            except ProtocolError as exc:
                self._attempts.record_failure(address[0])
                self._log(f"Handshake failed from {address[0]}: {exc}")
                self._reject(conn, RejectReason.MALFORMED)
                return
            except (OSError, socket.timeout):
                self._reject(conn, RejectReason.MALFORMED)
                return

            reason = self._validate(hello)
            if reason is not None:
                self._attempts.record_failure(address[0])
                self._log(f"Rejected {address[0]}: {reason.message}")
                self._reject(conn, reason)
                return

            self._attempts.clear(address[0])
            try:
                pad = self.backend.create(
                    hello.device_type,
                    on_rumble=lambda large, small: self._send_rumble(session, large, small),
                    on_led=lambda r, g, b: session.send(P.encode_led(r, g, b)),
                )
            except DriverUnavailableError as exc:
                self.last_error = str(exc)
                self._log(f"Cannot create {hello.device_type.label}: {exc}")
                self._reject(conn, RejectReason.MALFORMED)
                return

            session.attach(conn, address)
            session.pad = pad
            session.device_type = hello.device_type
            session.name = hello.name or f"Player {session.index + 1}"
            session.keys.set_bindings(self.settings.key_bindings.get(str(session.index), {}))

            conn.sendall(P.encode_welcome(session.index, pad.features))
            conn.settimeout(IDLE_TIMEOUT)
            self._log(
                f"Slot {session.index + 1}: {session.name} @ {address[0]} "
                f"as {hello.device_type.label}"
            )
            self._client_loop(reader, session)
        except (OSError, socket.timeout) as exc:
            log.debug("slot %d transport error: %s", session.index, exc)
        except Exception:  # noqa: BLE001 - one bad client must not take down the server
            log.exception("unhandled error serving slot %d", session.index)
        finally:
            name = session.name or f"Slot {session.index + 1}"
            was_connected = session.connected
            self._release_keys(session)
            self.slots.release(session)
            if was_connected:
                self._log(f"{name} disconnected")

    def _validate(self, hello: Hello) -> RejectReason | None:
        if hello.version != P.PROTOCOL_VERSION:
            return RejectReason.BAD_VERSION
        if self.settings.require_token and not hmac.compare_digest(
            hello.token, self.settings.token
        ):
            return RejectReason.BAD_TOKEN
        return None

    def _read_hello(self, reader: "_SocketReader") -> Hello:
        opcode = reader.read(1)[0]
        if opcode != ClientOpcode.HELLO:
            raise ProtocolError(f"expected HELLO, got opcode 0x{opcode:02x}")
        head = reader.read(3)              # version, device type, token length
        token = reader.read(head[2]) if head[2] else b""
        name_len = reader.read(1)
        name = reader.read(name_len[0]) if name_len[0] else b""
        return Hello.decode_body(head + token + name_len + name)

    def _client_loop(self, reader: "_SocketReader", session: PlayerSession) -> None:
        limiter = RateLimiter(INPUT_RATE_LIMIT, INPUT_RATE_LIMIT)
        while self.running and session.connected:
            try:
                opcode = reader.read(1)[0]
            except (OSError, socket.timeout, ConnectionError):
                return

            if opcode == ClientOpcode.INPUT:
                payload = reader.read(P.INPUT_PAYLOAD_SIZE)
                if not limiter.allow():
                    session.dropped_packets += 1
                    continue
                try:
                    state = InputState.decode(payload)
                except ProtocolError as exc:
                    log.debug("slot %d bad INPUT: %s", session.index, exc)
                    continue
                self._apply_input(session, state)

            elif opcode == ClientOpcode.PING:
                seq = P.decode_ping(reader.read(4))
                session.send(P.encode_pong(seq))

            elif opcode == ClientOpcode.TEXT:
                length = reader.read(1)[0]
                text = reader.read(length) if length else b""
                self.desktop.handle_text(session.index, text.decode("utf-8", errors="replace"))

            elif opcode == ClientOpcode.MOUSE:
                self.desktop.handle_mouse(session.index, MouseDelta.decode(reader.read(3)))

            elif opcode == ClientOpcode.SCROLL:
                self.desktop.handle_scroll(session.index, ScrollDelta.decode(reader.read(2)))

            else:
                # Unknown opcodes have unknown lengths, so the stream can no
                # longer be framed — the only safe response is to hang up.
                self._log(f"Slot {session.index + 1}: unknown opcode 0x{opcode:02x}, closing")
                return

    # -- input application --------------------------------------------------

    def _apply_input(self, session: PlayerSession, state: InputState) -> None:
        session.packet_count += 1
        self.total_packets += 1
        session.visuals = Visuals.from_state(state)

        low, high = state.buttons_low, state.buttons_high
        if session.keys.active and self.desktop.allows(session.index):
            for key, pressed in session.keys.update(low, high):
                self.desktop.set_key(session.index, key, pressed)
            low, high = session.keys.masked_buttons(low, high)

        if state.mouse_mode and self.desktop.allows(session.index):
            self._apply_mouse_mode(session, state)
            # Suppress pad output so the cursor and the game do not both react.
            state = InputState(buttons_high=high & int(P.DPad.GUIDE), flags=state.flags)
        elif low != state.buttons_low or high != state.buttons_high:
            state = InputState(
                lx=state.lx, ly=state.ly, rx=state.rx, ry=state.ry,
                buttons_low=low, buttons_high=high,
                left_trigger=state.left_trigger, right_trigger=state.right_trigger,
                roll=state.roll, pitch=state.pitch, flags=state.flags,
            )
        else:
            session.gyro_centre = None

        pad = session.pad
        if pad is not None:
            pad.apply(state)

    def _apply_mouse_mode(self, session: PlayerSession, state: InputState) -> None:
        if session.gyro_centre is None:
            session.gyro_centre = (state.roll, state.pitch)
        dx, dy = gyro_to_mouse(state.roll, state.pitch, *session.gyro_centre)
        buttons = 0
        if state.right_trigger > 100:
            buttons |= MouseDelta.LEFT
        if state.left_trigger > 100:
            buttons |= MouseDelta.RIGHT
        self.desktop.handle_mouse(session.index, MouseDelta(dx, dy, buttons))
        session.mouse_buttons_held = buttons

    def _release_keys(self, session: PlayerSession) -> None:
        for key, pressed in session.keys.release():
            self.desktop.set_key(session.index, key, pressed)
        if session.mouse_buttons_held:
            self.desktop.handle_mouse(session.index, MouseDelta(0, 0, 0))

    def _send_rumble(self, session: PlayerSession, large: int, small: int) -> None:
        if self.settings.haptics and session.connected:
            session.send(P.encode_rumble(large, small))

    # -- discovery ----------------------------------------------------------

    def _discovery_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Deliberately no SO_REUSEADDR: a second instance must fail to bind rather
        # than quietly share the port and answer discovery probes at random.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind(("0.0.0.0", self.settings.discovery_port))
        except OSError as exc:
            self._log(f"Discovery unavailable on UDP {self.settings.discovery_port}: {exc}")
            sock.close()
            return

        sock.settimeout(0.5)
        self._discovery = sock
        response = P.encode_discovery_response(
            self.display_name, self.settings.port, self.settings.require_token
        )
        while self.running:
            try:
                data, sender = sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.strip() == P.DISCOVERY_REQUEST:
                try:
                    sock.sendto(response, sender)
                except OSError as exc:
                    log.debug("discovery reply failed: %s", exc)

    @property
    def display_name(self) -> str:
        return self.settings.server_name or socket.gethostname()

    # -- telemetry ----------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "ip": self.bind_ip,
            "port": self.settings.port,
            "name": self.display_name,
            "players": [s.snapshot() for s in self.slots.sessions],
            "connected": self.slots.connected_count,
            "capacity": len(self.slots),
            "total_packets": self.total_packets,
            "token_required": self.settings.require_token,
            "desktop_control": self.desktop.enabled and self.desktop.available,
            "desktop_available": self.desktop.available,
            "haptics": self.settings.haptics,
            "messages": list(self.status_messages),
            "error": self.last_error,
        }


class _SocketReader:
    """Blocking reader that always returns exactly the requested number of bytes."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def read(self, count: int) -> bytes:
        if count == 0:
            return b""
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = self._sock.recv(remaining)
            if not chunk:
                raise ConnectionError("peer closed the connection")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks) if len(chunks) > 1 else chunks[0]
