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
from .padconfig import PadConfig
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
from . import xinput
from .session import AttemptTracker, PlayerSession, RateLimiter, SlotAllocator, Visuals
from .system import FirewallManager, remove_adb_reverse, setup_adb_reverse

log = logging.getLogger(__name__)

MAX_PLAYERS = P.MAX_PLAYERS
HANDSHAKE_TIMEOUT = 5.0
#: How long a settled client may say nothing before its slot is reclaimed.
#:
#: A phone that walks out of range, runs out of battery or is killed by the OS
#: never sends a FIN, so silence is the only evidence that it has gone — and
#: until it is acted on, the slot stays taken *and* the virtual pad stays plugged
#: in, frozen on its last frame. The phone reconnecting meanwhile is handed a
#: second slot beside its own ghost.
#:
#: The client sends on change with a heartbeat every 250 ms (PROTOCOL.md §9), so
#: this is roughly thirty missed beats: comfortably longer than a Wi-Fi hiccup,
#: which the TCP connection itself rides out, and far shorter than the 30 s this
#: used to be, when the ghost outlived most attempts to reconnect.
IDLE_TIMEOUT = 8.0
#: How many connections may be mid-handshake at once. A peer that connects and
#: then says nothing costs no player slot (those are handed out only after a
#: valid HELLO), but it still costs a thread, so the queue is capped too.
MAX_PENDING_HANDSHAKES = 8
#: How many refusals may be politely drained at the same time.
MAX_PENDING_REJECTS = 8
#: Connections per source IP per minute that may go quiet before it is treated as
#: abuse. Deliberately far looser than the limit on bad credentials.
SILENT_ATTEMPT_LIMIT = 30
#: Wall-clock ceiling on draining one refused connection.
REJECT_DRAIN_SECONDS = 1.0
#: Generous ceiling. A phone sends on *change*, polling its own controls every
#: 4 ms, so a frantic thumb peaks near 250 frames/s and a still pad sends four.
#: This only catches floods.
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
        # A connection that goes quiet is counted separately, and far more
        # tolerantly, than a wrong token. Wi-Fi that drops between connect() and
        # HELLO produces exactly the same trace as an attack, and the phone
        # retries every 3 s — five strikes would let a flaky network lock a
        # legitimate phone out of its own PC, permanently, since RATE_LIMITED is
        # a verdict the client treats as final.
        self._silent = AttemptTracker(max_attempts=SILENT_ATTEMPT_LIMIT)
        self._firewall: FirewallManager | None = None
        # A gate, not a counter: stop() swaps in a fresh one, so a handshake
        # thread that finishes afterwards releases the *old* gate and cannot
        # hand the new server a place it never took.
        self._handshake_capacity = MAX_PENDING_HANDSHAKES
        self._handshake_gate = threading.Semaphore(self._handshake_capacity)
        self._reject_slots = threading.Semaphore(MAX_PENDING_REJECTS)

        self.bind_ip = ""
        self.total_packets = 0
        self.last_error: str | None = None
        self.xinput_warning: str | None = None
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
            self.xinput_warning = None
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
                # Room for every player plus the handshakes that may be queued
                # behind them, so a burst is refused by us — with a reason the
                # phone can show — rather than dropped by the kernel.
                listener.listen(MAX_PLAYERS + MAX_PENDING_HANDSHAKES)
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

        # Handshake threads are daemons: at shutdown some may never reach the
        # finally that gives their place back. A fresh gate starts the next
        # start() at full capacity while the old one absorbs the late releases.
        self._handshake_gate = threading.Semaphore(self._handshake_capacity)

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

            # Every rejection below is dispatched to a short-lived thread: the
            # polite close in _reject() can take REJECT_DRAIN_SECONDS, and doing
            # that here would stall the accept loop — which is exactly what a
            # flood of bad connections wants.
            if self._attempts.is_blocked(address[0]):
                # Bad credentials, repeatedly: RATE_LIMITED is the right verdict
                # and the client is right to treat it as final.
                self._reject_async(conn, RejectReason.RATE_LIMITED)
                self._log(f"Blocked {address[0]}: too many failed attempts")
                continue

            if self._silent.is_blocked(address[0]):
                # Silence is different, and answering it with RATE_LIMITED undid
                # the whole point of counting it separately: the phone stops
                # reconnecting for good, which is precisely what a phone with bad
                # Wi-Fi must not have happen to it. SERVER_FULL says "not now".
                self._reject_async(conn, RejectReason.SERVER_FULL)
                self._log(f"Rejected {address[0]}: too many connections went quiet")
                continue

            if not self.slots.has_free():
                self._reject_async(conn, RejectReason.SERVER_FULL)
                self._log(f"Rejected {address[0]}: all {len(self.slots)} slots in use")
                continue

            gate = self._begin_handshake()
            if gate is None:
                # SERVER_FULL, not RATE_LIMITED: the queue being busy is a
                # transient capacity problem, and the phone treats RATE_LIMITED
                # as permanent — it sets handshakeBlocked and stops auto
                # reconnecting until the user intervenes. Turning a burst of
                # connections into a permanent lockout of an innocent phone is
                # exactly the denial of service this cap exists to prevent.
                self._reject_async(conn, RejectReason.SERVER_FULL)
                self._log(f"Rejected {address[0]}: too many handshakes in flight")
                continue

            thread = threading.Thread(
                target=self._serve_client,
                args=(conn, address, gate),
                name=f"nexus-handshake-{address[0]}",
                daemon=True,
            )
            try:
                thread.start()
            except RuntimeError as exc:
                # Out of OS threads. Letting this escape would end the accept
                # loop while `running` stayed true — the server would look alive
                # and quietly refuse everything from then on.
                gate.release()
                self._log(f"Cannot serve {address[0]}: {exc}")
                self._reject_async(conn, RejectReason.SERVER_FULL)

    @property
    def handshakes_in_flight(self) -> int:
        """How many connections are mid-handshake. Reads the gate's own count."""
        return self._handshake_capacity - self._handshake_gate._value  # noqa: SLF001

    def _begin_handshake(self) -> threading.Semaphore | None:
        """Take a place in the handshake queue, or ``None`` when it is full.

        The caller keeps the gate it was given and releases *that* one, so a
        thread outliving a restart cannot credit the new server.
        """
        gate = self._handshake_gate
        return gate if gate.acquire(blocking=False) else None

    def _reject_bounded(self, conn: socket.socket, reason: RejectReason) -> None:
        """Reject on *this* thread, but only if the drain budget allows it.

        Handshake failures used to drain straight from the client thread, which
        had already given up its place in the handshake queue by then — so the
        number of sockets being politely drained at once was bounded by nothing.
        The permit is what actually caps it; the queue caps something else.
        """
        if not self._reject_slots.acquire(blocking=False):
            self._close_quietly(conn)
            return
        try:
            self._reject(conn, reason)
        finally:
            self._reject_slots.release()

    @staticmethod
    def _close_quietly(conn: socket.socket) -> None:
        try:
            conn.close()
        except OSError:
            pass

    def _reject_async(self, conn: socket.socket, reason: RejectReason) -> None:
        """Reject on a helper thread, or bluntly when too many are already busy.

        The courtesy of a drained close is worth one thread, not an unbounded
        number of them: a flood of bad connections would otherwise spawn one
        thread each, and each of those can be held open by a peer that trickles
        bytes. Past the cap the socket is simply closed — a peer behaving that
        badly has forfeited the explanation.
        """
        if not self._reject_slots.acquire(blocking=False):
            self._close_quietly(conn)
            return

        def run() -> None:
            try:
                self._reject(conn, reason)
            finally:
                self._reject_slots.release()

        try:
            threading.Thread(target=run, name="nexus-reject", daemon=True).start()
        except RuntimeError:
            # The thread never ran, so its finally never will either: release the
            # permit here or the cap leaks one place on every failure until no
            # refusal can be explained at all.
            self._reject_slots.release()
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _reject(conn: socket.socket, reason: RejectReason) -> None:
        """Tell the client why it was refused, then close cleanly.

        Closing a socket that still has unread data in its receive queue makes
        the OS send an RST, which discards the reject message we just wrote — so
        the client would see only "connection reset" and could not explain the
        failure to the user. Half-close and drain first.

        The drain has a wall-clock deadline as well as a byte ceiling. The
        per-recv timeout alone is not a bound: a peer trickling one byte at a
        time refreshes it on every call and can keep the thread for as long as it
        likes.
        """
        try:
            conn.sendall(P.encode_reject(reason))
            conn.shutdown(socket.SHUT_WR)
            conn.settimeout(0.5)
            deadline = time.monotonic() + REJECT_DRAIN_SECONDS
            drained = 0
            while drained < 64 * 1024 and time.monotonic() < deadline:
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

    def _serve_client(self, conn: socket.socket, address, gate: threading.Semaphore) -> None:
        session: PlayerSession | None = None
        #: Set while this connection still occupies a place in the handshake
        #: queue; a settled client must not keep one for its whole session.
        pending = True

        def leave_queue() -> None:
            """Give up the place in the handshake queue as soon as it is settled.

            Before the refusal is written, not after: draining a rejected peer
            can take REJECT_DRAIN_SECONDS, and holding a queue place for that
            long would let slow refusals crowd out real phones.
            """
            nonlocal pending
            if pending:
                gate.release()
                pending = False

        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(HANDSHAKE_TIMEOUT)
            # A deadline for the whole handshake, not just for each read of it.
            reader = _SocketReader(conn, deadline=time.monotonic() + HANDSHAKE_TIMEOUT)

            try:
                hello = self._read_hello(reader)
            except ProtocolError as exc:
                leave_queue()
                self._attempts.record_failure(address[0])
                self._log(f"Handshake failed from {address[0]}: {exc}")
                self._reject_bounded(conn, RejectReason.MALFORMED)
                return
            except socket.timeout as exc:
                # Holding the socket open and saying nothing is what an attempt
                # to exhaust the handshake queue looks like — but it is also what
                # a phone whose Wi-Fi dropped mid-handshake looks like, so it
                # goes in its own, much more forgiving bucket.
                leave_queue()
                self._silent.record_failure(address[0])
                log.debug("handshake from %s never arrived: %s", address[0], exc)
                # No REJECT: MALFORMED says "your HELLO was wrong", and nothing
                # arrived to be wrong. A peer that sent nothing has nothing to
                # be told, so the socket is simply closed.
                self._close_quietly(conn)
                return
            except OSError as exc:
                # Connect-then-close, deliberately *not* counted at all. That is
                # what a port scanner, a health check and half the network tooling
                # on a LAN do, it costs the server nothing, and counting it would
                # let any such probe lock a phone sharing that IP out for a minute.
                leave_queue()
                log.debug("connection from %s went away before HELLO: %s", address[0], exc)
                # The peer has already gone; there is nobody left to explain to.
                self._close_quietly(conn)
                return

            reason = self._validate(hello)
            if reason is not None:
                leave_queue()
                self._attempts.record_failure(address[0])
                self._log(f"Rejected {address[0]}: {reason.message}")
                self._reject_bounded(conn, reason)
                return

            self._attempts.clear(address[0])
            self._silent.clear(address[0])
            leave_queue()

            # Only an authenticated peer costs a player slot. The reservation is
            # still atomic — SlotAllocator.acquire() is the compare-and-set that
            # the v1 race was missing — it simply happens one step later, so an
            # unauthenticated connection can no longer hold a slot hostage for
            # the length of the handshake timeout.
            if not self.running:
                # stop() can land in the middle of a handshake. Going on would
                # create a virtual pad and occupy a slot on a server that has
                # already released everything, leaving a phantom device behind.
                self._reject_bounded(conn, RejectReason.SERVER_FULL)
                return

            session = self.slots.acquire()
            if session is None:
                self._log(f"Rejected {address[0]}: all {len(self.slots)} slots in use")
                self._reject_bounded(conn, RejectReason.SERVER_FULL)
                return
            if not self.running:
                # stop() can land between the check above and this line, after
                # release_all() has already run — the slot would then stay
                # reserved for the life of the process.
                self.slots.release(session)
                session = None
                self._reject_bounded(conn, RejectReason.SERVER_FULL)
                return
            threading.current_thread().name = f"nexus-client-{session.index}"

            self._warn_if_no_xinput_slot(hello.device_type)
            try:
                pad = self.backend.create(
                    hello.device_type,
                    on_rumble=lambda large, small: self._send_rumble(session, large, small),
                    on_led=lambda r, g, b: session.send(P.encode_led(r, g, b)),
                )
            except DriverUnavailableError as exc:
                self.last_error = str(exc)
                self._log(f"Cannot create {hello.device_type.label}: {exc}")
                self._reject_bounded(conn, RejectReason.MALFORMED)
                return

            session.attach(conn, address)
            session.pad = pad
            session.device_type = hello.device_type
            session.name = hello.name or f"Player {session.index + 1}"
            session.keys.set_bindings(self.settings.key_bindings.get(str(session.index), {}))

            # Through session.send(), not conn.sendall(): the pad exists by now,
            # so a rumble callback can already be writing from a ViGEm thread and
            # the two writes must not interleave.
            if not session.send(P.encode_welcome(session.index, pad.features)):
                return
            reader.clear_deadline()
            conn.settimeout(IDLE_TIMEOUT)
            self._log(
                f"Slot {session.index + 1}: {session.name} @ {address[0]} "
                f"as {hello.device_type.label}"
            )
            self._client_loop(reader, session)
        except (OSError, socket.timeout) as exc:
            log.debug("transport error from %s: %s", address[0], exc)
        except Exception:  # noqa: BLE001 - one bad client must not take down the server
            log.exception("unhandled error serving %s", address[0])
        finally:
            leave_queue()
            if session is not None:
                name = session.name or f"Slot {session.index + 1}"
                was_connected = session.connected
                self._release_keys(session)
                # release() closes the socket, but only the one it was given in
                # attach(); anything that failed before that owns its own socket.
                if not was_connected:
                    self._close_quietly(conn)
                self.slots.release(session)
                if was_connected:
                    self._log(f"{name} disconnected")
            else:
                self._close_quietly(conn)

    def _warn_if_no_xinput_slot(self, device_type: DeviceType) -> None:
        """Say so loudly when a pad is about to be created that games cannot see.

        ViGEm reports success for a fifth XInput device, so without this check the
        failure is completely silent: the phone says "connected" and nothing works.
        """
        if device_type is DeviceType.DUALSHOCK4:
            return  # a DS4 is a HID device and does not consume an XInput slot
        pending = 1 + sum(
            1
            for session in self.slots.sessions
            if session.connected and session.device_type is not DeviceType.DUALSHOCK4
        )
        warning = xinput.capacity_warning(xinput.free_slot_count(), pending)
        if warning is not None:
            self.xinput_warning = warning
            self._log(f"WARNING: {warning}")

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
                delta = MouseDelta.decode(reader.read(3))
                self._move_cursor(session, delta.dx, delta.dy, touch=delta.buttons)

            elif opcode == ClientOpcode.SCROLL:
                self.desktop.handle_scroll(session.index, ScrollDelta.decode(reader.read(2)))

            elif opcode == ClientOpcode.CONFIG:
                length = P.decode_config_length(reader.read(2))
                self._receive_config(session, reader.read(length) if length else b"{}")

            else:
                # Unknown opcodes have unknown lengths, so the stream can no
                # longer be framed — the only safe response is to hang up.
                self._log(f"Slot {session.index + 1}: unknown opcode 0x{opcode:02x}, closing")
                return

    # -- configuration ------------------------------------------------------

    def _receive_config(self, session: PlayerSession, body: bytes) -> None:
        """Store what the phone says it currently looks like.

        A malformed document is logged and dropped: a phone with a broken config
        must not lose its connection over cosmetics.
        """
        try:
            session.config = PadConfig.from_json(body).filled()
        except ProtocolError as exc:
            self._log(f"Slot {session.index + 1}: ignoring bad config ({exc})")
            return
        session.config_pending = False
        self._log(f"Slot {session.index + 1}: layout reported ({session.config.device_type.label})")

    def push_config(self, slot: int, config: PadConfig) -> bool:
        """Send a configuration to one connected phone.

        Returns ``False`` when the slot is empty or the write fails. The phone
        echoes a CONFIG back, which is what clears ``config_pending``.
        """
        if not 0 <= slot < len(self.slots):
            return False
        session = self.slots.sessions[slot]
        if not session.connected:
            return False
        try:
            message = P.encode_set_config(config.encode_body())
        except ProtocolError as exc:
            self._log(f"Cannot push config to slot {slot + 1}: {exc}")
            return False
        session.config_pending = True
        if not session.send(message):
            session.config_pending = False
            return False
        self._log(f"Slot {slot + 1}: configuration pushed from PC")
        return True

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
        else:
            # Unconditionally, so that leaving mouse mode always re-centres the
            # gyro: hanging this off the "no bound button is held" branch meant a
            # bound button still down at that moment kept a stale centre, and the
            # cursor jumped the next time mouse mode was entered.
            session.gyro_centre = None
            if low != state.buttons_low or high != state.buttons_high:
                state = InputState(
                    lx=state.lx, ly=state.ly, rx=state.rx, ry=state.ry,
                    buttons_low=low, buttons_high=high,
                    left_trigger=state.left_trigger, right_trigger=state.right_trigger,
                    roll=state.roll, pitch=state.pitch, flags=state.flags,
                )

        pad = session.pad
        if pad is not None:
            pad.apply(state)

    def _move_cursor(
        self,
        session: PlayerSession,
        dx: int,
        dy: int,
        *,
        touch: int | None = None,
        triggers: int | None = None,
    ) -> None:
        """The single place a cursor message is built, from both button sources.

        A phone in trackpad mode drives the cursor down two streams at once:
        MOUSE messages from the finger, and mouse-mode INPUT frames from the pad,
        which keep arriving as a heartbeat even when nothing is touched. Both
        carry an *absolute* button field, so whichever spoke last used to define
        the truth — and the heartbeat, holding no triggers, let go of the button
        the finger was still pressing. Dragging anything was impossible.

        Each source therefore keeps its own state on the session, and what
        reaches the desktop is the union: a button stays down while *anything*
        is holding it, and only the source that pressed it can let it go.
        """
        if touch is not None:
            session.touch_buttons = touch
        if triggers is not None:
            session.trigger_buttons = triggers
        self.desktop.handle_mouse(
            session.index, MouseDelta(dx, dy, session.mouse_buttons_held)
        )

    def _apply_mouse_mode(self, session: PlayerSession, state: InputState) -> None:
        # Only steer from the gyro when the client says the reading means
        # something. A phone with the sensor switched off sends zeroes, and
        # latching a centre on those made the cursor bolt across the screen the
        # moment the sensor was switched back on mid-session: the first real
        # reading was then a huge distance from a centre of (0, 0). The buttons
        # below are unaffected — a trigger is a trigger either way.
        if state.gyro_valid:
            if session.gyro_centre is None:
                session.gyro_centre = (state.roll, state.pitch)
            dx, dy = gyro_to_mouse(state.roll, state.pitch, *session.gyro_centre)
        else:
            session.gyro_centre = None
            dx, dy = 0, 0
        buttons = 0
        if state.right_trigger > 100:
            buttons |= MouseDelta.LEFT
        if state.left_trigger > 100:
            buttons |= MouseDelta.RIGHT
        self._move_cursor(session, dx, dy, triggers=buttons)

    def reset_desktop_state(self) -> None:
        """Drop every key and mouse button the desktop feature is holding down.

        Must be called whenever the gate itself moves — switching the feature off
        or handing the desktop lock to another slot. The per-slot release path
        goes through :meth:`DesktopControl.set_key`, which the gate refuses once
        the slot no longer holds the lock, so without this a key held at that
        moment stays pressed on the PC until the app is restarted.

        **Call this only after the gate has already been shut.** Nothing here
        stops an input frame arriving mid-reset and pressing the key again; what
        stops it is that :meth:`DesktopControl.allows` is already refusing that
        slot by the time this runs. The lock inside
        :class:`~.desktop.KeyBindingEngine` makes each step atomic, not ordered.
        """
        for session in self.slots.sessions:
            session.keys.release()
            session.touch_buttons = 0
            session.trigger_buttons = 0
        self.desktop.release_all()

    def _release_keys(self, session: PlayerSession) -> None:
        """Let go of everything this slot is holding on the PC, on its way out.

        A phone that drops mid-drag — Wi-Fi gone, battery flat, app killed —
        leaves the button pressed otherwise, and nothing on the desktop recovers
        from that by itself: every movement keeps selecting or dragging until
        somebody clicks. The trackpad's buttons were tracked nowhere at all
        before, so only the gyro trigger-click was ever released here.
        """
        for key, pressed in session.keys.release():
            self.desktop.set_key(session.index, key, pressed)
        if session.mouse_buttons_held:
            self._move_cursor(session, 0, 0, touch=0, triggers=0)

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
            "xinput_warning": self.xinput_warning,
            "xinput_free": xinput.free_slot_count(),
        }


class _SocketReader:
    """Blocking reader that always returns exactly the requested number of bytes.

    Optionally under a wall-clock deadline. The socket's own timeout is per-recv
    and restarts on every byte that arrives, so a peer trickling one byte at a
    time never times out at all — it can hold a place in the handshake queue for
    as long as it likes, and never be counted as a silent connection either. The
    handshake needs a bound on the *whole* exchange, not on its quietest moment.
    """

    def __init__(self, sock: socket.socket, deadline: float | None = None) -> None:
        self._sock = sock
        self._deadline = deadline

    def clear_deadline(self) -> None:
        """Drop the bound once the handshake is over; a session is long-lived."""
        self._deadline = None

    def read(self, count: int) -> bytes:
        if count == 0:
            return b""
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            if self._deadline is not None:
                left = self._deadline - time.monotonic()
                if left <= 0:
                    raise socket.timeout("handshake took too long")
                # Never lengthen the socket's own timeout, only shorten it.
                self._sock.settimeout(min(self._sock.gettimeout() or left, left))
            chunk = self._sock.recv(remaining)
            if not chunk:
                raise ConnectionError("peer closed the connection")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks) if len(chunks) > 1 else chunks[0]
